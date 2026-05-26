"""河图 (HeTu) 日交易全链路编排器。"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Any

from core.config import Config
from core.types import Signal, Action, Order, OrderType, StrategyContext
from core.types import MarketRegime, Position
from execution.report_types import StageReport
from strategy.registry import REGISTRY

logger = logging.getLogger("hetu.execution")


class DailyPipeline:
    def __init__(self, cfg: Config):
        self._cfg = cfg
        self._broker = None
        self._data_provider = None
        self._screener = None
        self._search_client = None
        self._llm_client = None
        self._pusher = None
        self._risk_manager = None
        self._strategies: dict[str, Any] = {}
        self._current_positions: list[Position] = []
        self._today = date.today().isoformat()

    @classmethod
    def from_config(cls, cfg: Config) -> DailyPipeline:
        return cls(cfg)

    # ================================================================
    # 全链路
    # ================================================================

    async def run_full(self, *, strategy: str = "", auto_post: bool = False, live: bool = False):
        report = type("Report", (), {"stage": "FULL", "signals": []})()
        if not live:
            logger.info("DRY-RUN 模式，不提交真实订单")
        try:
            await self._init_all()
            stage1 = await self.run_pre_open(strategy=strategy)
            if stage1.signals:
                stage2 = await self.run_open(stage1.signals)
            else:
                stage2 = StageReport(stage="OPEN", success=False, errors=["无信号"])
            stage3 = await self.run_intraday()
            stage4 = await self.run_post_close(auto_post=auto_post)
        except Exception as e:
            logger.exception("流水线异常: %s", e)
        finally:
            await self._cleanup()
        return report

    async def _init_all(self):
        await self._init_broker()
        await self._init_data_provider()
        await self._init_screener()
        await self._init_search_client()
        await self._init_llm_client()
        await self._init_pusher()
        if not self._risk_manager:
            from risk.manager import RiskManager
            self._risk_manager = RiskManager.from_config(self._cfg)

    # ================================================================
    # 四阶段
    # ================================================================

    async def run_pre_open(self, *, strategy: str = ""):
        logger.info("--- [PRE_OPEN] ---")
        stock_pools = await self._screen_stocks(strategy=strategy)
        signals = await self._generate_signals(stock_pools)
        stage = StageReport(stage="PRE_OPEN", details={"signals": len(signals)})
        stage.signals = signals
        return stage

    async def run_open(self, signals: list[Signal]):
        logger.info("--- [OPEN] 提交 %d 个信号 ---", len(signals))
        stage = StageReport(stage="OPEN")
        if not self._broker:
            stage.errors.append("Broker 未初始化")
            return stage
        for sig in signals:
            order = Order(channel="mx", code=sig.code, action=sig.action,
                          order_type=OrderType.LIMIT, price=sig.price_limit,
                          shares=sig.shares, signal_id=sig.signal_id,
                          strategy_name=sig.strategy)
            result = await self._broker.submit_order(order)
            stage.details[sig.code] = "OK" if result.success else result.error
        return stage

    async def run_intraday(self):
        logger.info("--- [INTRADAY] ---")
        stage = StageReport(stage="INTRADAY")
        if self._broker:
            pos = await self._broker.get_positions()
            self._current_positions = pos
            stage.details["positions"] = len(pos)
        return stage

    async def run_post_close(self, *, auto_post: bool = False):
        logger.info("--- [POST_CLOSE] ---")
        stage = StageReport(stage="POST_CLOSE")
        if self._broker:
            bal = await self._broker.get_balance()
            stage.details["balance"] = bal
        return stage

    # ================================================================
    # 盘中监控循环
    # ================================================================

    async def _run_monitor_loop(self, scheduler, *, strategy: str = ""):
        """盘中监控循环 — 按时间节点触发信号。"""
        await self._init_all()
        last_minute = (-1, -1)
        ticks = 0
        while True:
            now = datetime.now()
            now_hm = (now.hour, now.minute)
            if now_hm >= (15, 0):
                logger.info("收盘，停止监控 (%d ticks)", ticks)
                break
            if now_hm != last_minute:
                last_minute = now_hm
                tasks = scheduler.list_tasks()
                for t in tasks:
                    if t.time.hour == now_hm[0] and t.time.minute == now_hm[1]:
                        logger.info("⏰ %02d:%02d 触发 %s", now_hm[0], now_hm[1], t.name)
                        try:
                            pools = await self._screen_stocks(strategy=strategy)
                            signals = await self._generate_signals(pools, intraday=True)
                            if signals:
                                approved = []
                                if self._risk_manager:
                                    bal = await self._broker.get_balance() if self._broker else {}
                                    for sig in signals:
                                        ctx = self._build_context(bal)
                                        a = await self._risk_manager.assess(sig, ctx)
                                        if a.approved:
                                            approved.append(sig)
                                if approved:
                                    await self.run_open(approved)
                        except Exception as e:
                            logger.error("节点异常 %s: %s", t.name, e)
            ticks += 1
            await asyncio.sleep(30)

    # ================================================================
    # 内部
    # ================================================================

    async def _init_broker(self):
        if self._broker is None:
            from execution.channels.mx_broker import MXBroker
            self._broker = MXBroker.from_config(self._cfg)
    async def _init_data_provider(self):
        if self._data_provider is None:
            from data.sources.mx_data import MXDataProvider
            self._data_provider = MXDataProvider.from_config(self._cfg)
    async def _init_screener(self):
        if self._screener is None:
            from data.sources.mx_screening import MXScreener
            self._screener = MXScreener.from_config(self._cfg)
    async def _init_search_client(self):
        if self._search_client is None:
            from data.sources.mx_search import MXSearchClient
            self._search_client = MXSearchClient.from_config(self._cfg)
    async def _init_llm_client(self):
        if self._llm_client is None:
            from notify.llm_client import LLMClient
            self._llm_client = LLMClient.from_config(self._cfg)
    async def _init_pusher(self):
        if self._pusher is None:
            from notify.push import MessagePusher
            self._pusher = MessagePusher.from_config(self._cfg)

    async def _screen_stocks(self, *, strategy: str = ""):
        pools = {}
        names = [strategy] if strategy else list(REGISTRY.keys())
        for name in names:
            cond = self._cfg.get(f"strategy.{name}.screen_condition", "")
            if cond and self._screener:
                codes = await self._screener.screen_codes(cond)
                pools[name] = codes or []
            else:
                pools[name] = []
        return pools

    async def _generate_signals(self, stock_pools: dict[str, list[str]], *, intraday: bool = False):
        signals: list[Signal] = []
        for name, pool in stock_pools.items():
            if not pool:
                continue
            cls = REGISTRY.get(name)
            if not cls:
                continue
            try:
                strategy = cls(cfg=self._cfg) if hasattr(cls, 'from_config') else cls()
                bars_dict = {}
                if self._data_provider:
                    bars_dict = await self._data_provider.get_daily_bars(pool, count=60)
                for code in pool[:5]:
                    bars = bars_dict.get(code, [])
                    if not bars:
                        continue
                    bar, history = bars[-1], bars[:-1]
                    ctx = StrategyContext(current_date=date.today(), today=self._today)
                    sigs = await strategy.on_bar(bar, history, ctx)
                    for sig in (sigs or []):
                        if sig.action != Action.BUY:
                            continue
                        sig.strategy = name
                        if self._risk_manager:
                            sizing = self._risk_manager.calculate_position(sig, 200000, strategy_type=name)
                            sig.shares = sizing.adjusted_shares if hasattr(sizing, 'adjusted_shares') else 100
                        sig.price_limit = bar.close
                        signals.append(sig)
            except Exception as e:
                logger.error("策略 %s 异常: %s", name, e)
        return signals

    def _build_context(self, bal: dict) -> StrategyContext:
        return StrategyContext(
            current_date=date.today(),
            today=self._today,
            total_asset=bal.get("total_assets", 0),
            total_position_pct=bal.get("total_pos_pct", 0) / 100.0 if bal else 0,
            market_regime=MarketRegime(trend="RANGING"),
        )

    async def _cleanup(self):
        for comp in [self._broker, self._data_provider, self._screener,
                      self._search_client, self._llm_client, self._pusher]:
            if comp and hasattr(comp, "close"):
                try: await comp.close()
                except Exception: pass
