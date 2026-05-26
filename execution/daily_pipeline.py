"""
河图 (HeTu) 日交易全链路流水线

四阶段自动化闭环:
    PRE_OPEN   — 盘前：选股 → 数据加载 → 信号生成 → 风控预审
    OPEN       — 开盘：提交订单 → 确认成交
    INTRADAY   — 盘中：持仓监控 → 止损触发 → 熔断检查
    POST_CLOSE — 盘后：持仓对账 → P&L计算 → 复盘报告 → 总结

用法:
    # 方式1: 编程调用
    pipeline = DailyPipeline.from_config(cfg)
    report = await pipeline.run_full()

    # 方式2: 单阶段执行
    signals = await pipeline.run_pre_open()
    await pipeline.run_open(signals)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from core.types import (
    Signal, StrategyContext,
    MarketRegime, Position,
)
from core.config import Config

logger = logging.getLogger("hetu.daily")


# ============================================================
# 报告类型
# ============================================================
@dataclass
class StageReport:
    """单阶段执行报告。"""
    stage: str = ""
    success: bool = True
    elapsed_ms: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


@dataclass
class DailyReport:
    """完整交易日报告。"""
    date: str = ""
    account_name: str = ""
    initial_capital: float = 0.0
    total_assets: float = 0.0
    avail_balance: float = 0.0
    total_pos_value: float = 0.0
    daily_pnl: float = 0.0
    total_pnl: float = 0.0
    nav: float = 0.0
    positions: list[dict[str, Any]] = field(default_factory=list)
    signals_generated: int = 0
    signals_approved: int = 0
    orders_submitted: int = 0
    orders_filled: int = 0
    stop_events: int = 0
    news_summary: str = ""
    stages: list[StageReport] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    summary: str = ""


# ============================================================
# 日交易流水线
# ============================================================
class DailyPipeline:
    """日交易全链路流水线。

    整合以下模块:
        - MXScreener     (筛选股票)
        - MXDataProvider (行情数据)
        - StrategyRunner (信号生成)
        - RiskManager    (风控审核)
        - MXBroker       (交易执行)
        - MXSearchClient (资讯审核)
        - DailyReport    (复盘报告)

    用法:
        pipeline = DailyPipeline.from_config(cfg)
        report = await pipeline.run_full()
    """

    _STRATEGY_CLASSES: dict[str, type] | None = None

    def __init__(self, cfg: Config):
        self._cfg = cfg
        self._today = date.today().isoformat()

        # 核心组件
        self._broker: Any = None
        self._screener: Any = None
        self._data_provider: Any = None
        self._search_client: Any = None
        self._risk_manager: Any = None
        self._runner: Any = None

        # 状态
        self._strategies: dict[str, Any] = {}
        self._current_positions: list[Position] = []
        self._prev_balance: dict = {}

    @classmethod
    def from_config(cls, cfg: Config, _overrides=None) -> "DailyPipeline":
        pipeline = cls(cfg)
        pipeline._init_components()
        return pipeline

    # ============================================================
    # 全链路
    # ============================================================
    async def run_full(self, auto_post: bool = False, strategy: str = "") -> DailyReport:
        logger.info("开始日交易全链路: %s", self._today)

        report = DailyReport(date=self._today)
        try:
            # 阶段 1: 盘前
            pre = await self.run_pre_open(strategy)
            report.stages.append(pre)
            report.signals_generated = pre.details.get("signals_generated", 0)
            report.signals_approved = pre.details.get("signals_approved", 0)

            # 阶段 2: 开盘
            signals = pre.details.get("signals", [])
            open_rpt = await self.run_open(signals)
            report.stages.append(open_rpt)
            report.orders_submitted = open_rpt.details.get("orders_submitted", 0)

            # 阶段 3: 盘中
            intra = await self.run_intraday()
            report.stages.append(intra)
            report.stop_events = intra.details.get("stop_events", 0)

            # 阶段 4: 盘后
            post = await self.run_post_close(auto_post)
            report.stages.append(post)
            report.total_assets = post.details.get("total_assets", 0.0)
            report.daily_pnl = post.details.get("daily_pnl", 0.0)
            report.orders_filled = post.details.get("orders_filled", 0)
            report.summary = post.details.get("summary", "")
            report.positions = post.details.get("positions", [])

        except Exception as e:
            report.warnings.append(f"链路异常: {e}")
            logger.exception("日交易链路异常")

        # 清理
        await self._cleanup()
        return report

    # ============================================================
    # 阶段 1: 盘前
    # ============================================================
    async def run_pre_open(self, strategy: str = "") -> StageReport:
        rpt = StageReport(stage="PRE_OPEN")
        t0 = datetime.now(timezone.utc)
        try:
            # 1. 选股
            stock_pools = await self._screen_stocks(strategy)
            rpt.details["stock_pools"] = stock_pools

            # 2. 生成信号
            signals = await self._generate_signals(stock_pools)
            rpt.details["signals"] = signals
            rpt.details["signals_generated"] = len(signals)

            # 3. 风控预审
            approved = []
            if self._risk_manager:
                ctx = StrategyContext(current_date=date.today())
                for sig in signals:
                    if self._risk_manager.assess(sig, ctx):
                        approved.append(sig)
            else:
                approved = signals
            rpt.details["signals_approved"] = len(approved)
            rpt.details["signals"] = approved

        except Exception as e:
            rpt.success = False
            rpt.errors.append(f"盘前异常: {e}")
            logger.exception("盘前阶段失败")
        finally:
            rpt.elapsed_ms = (datetime.now(timezone.utc) - t0).total_seconds() * 1000
        return rpt

    # ============================================================
    # 阶段 2: 开盘
    # ============================================================
    async def run_open(self, signals: list[Signal]) -> StageReport:
        rpt = StageReport(stage="OPEN")
        t0 = datetime.now(timezone.utc)
        try:
            if not self._broker:
                rpt.errors.append("未配置 Broker")
                rpt.success = False
                return rpt

            submitted = 0
            for sig in signals:
                try:
                    if hasattr(self._broker, "submit_order"):
                        result = await self._broker.submit_order(sig)
                        if result and getattr(result, "success", False):
                            submitted += 1
                except Exception as e:
                    rpt.errors.append(f"提交失败 {sig.code}: {e}")
            rpt.details["orders_submitted"] = submitted
        except Exception as e:
            rpt.success = False
            rpt.errors.append(f"开盘异常: {e}")
            logger.exception("开盘阶段失败")
        finally:
            rpt.elapsed_ms = (datetime.now(timezone.utc) - t0).total_seconds() * 1000
        return rpt

    # ============================================================
    # 阶段 3: 盘中
    # ============================================================
    async def run_intraday(self) -> StageReport:
        rpt = StageReport(stage="INTRADAY")
        t0 = datetime.now(timezone.utc)
        try:
            if self._broker and hasattr(self._broker, "get_positions"):
                positions = await self._broker.get_positions()
                self._current_positions = positions
                rpt.details["positions_count"] = len(positions)
        except Exception as e:
            rpt.success = False
            rpt.errors.append(f"盘中异常: {e}")
            logger.exception("盘中阶段失败")
        finally:
            rpt.elapsed_ms = (datetime.now(timezone.utc) - t0).total_seconds() * 1000
        return rpt

    # ============================================================
    # 阶段 4: 盘后
    # ============================================================
    async def run_post_close(self, auto_post: bool = False) -> StageReport:
        rpt = StageReport(stage="POST_CLOSE")
        t0 = datetime.now(timezone.utc)
        try:
            # 持仓对账
            bal = {}
            if self._broker and hasattr(self._broker, "get_balance"):
                bal = await self._broker.get_balance()

            total_assets = bal.get("total_assets", 0.0)
            prev = self._prev_balance.get("total_assets", total_assets)
            daily_pnl = total_assets - prev
            total_pnl = total_assets - bal.get("initial_capital", total_assets)

            rpt.details["total_assets"] = total_assets
            rpt.details["daily_pnl"] = daily_pnl
            rpt.details["total_pnl"] = total_pnl
            rpt.details["balance"] = bal

            positions_dicts = []
            if self._broker and hasattr(self._broker, "get_positions"):
                positions = await self._broker.get_positions()
                positions_dicts = [p.model_dump() if hasattr(p, "model_dump") else p.__dict__ for p in positions]
            rpt.details["positions"] = positions_dicts

            # 资讯聚合
            news = ""
            if self._search_client and self._current_positions:
                news = await self._gather_news(self._current_positions)

            # 构建总结
            summary = self._build_summary(bal, positions_dicts, daily_pnl, total_pnl, news)
            rpt.details["summary"] = summary

            if auto_post:
                await self._post_summary(summary)

            # 保存前日余额
            self._prev_balance = bal

        except Exception as e:
            rpt.success = False
            rpt.errors.append(f"盘后异常: {e}")
            logger.exception("盘后阶段失败")
        finally:
            rpt.elapsed_ms = (datetime.now(timezone.utc) - t0).total_seconds() * 1000
        return rpt

    # ============================================================
    # 组件初始化
    # ============================================================
    def _init_components(self) -> None:
        self._init_broker()
        self._init_screener()
        self._init_data_provider()
        self._init_search_client()

    def _init_broker(self) -> None:
        logger.debug("_init_broker")

    def _init_screener(self) -> None:
        logger.debug("_init_screener")

    def _init_data_provider(self) -> None:
        logger.debug("_init_data_provider")

    def _init_search_client(self) -> None:
        logger.debug("_init_search_client")

    # ============================================================
    # 策略注册
    # ============================================================
    @classmethod
    def _get_strategy_class(cls, name: str) -> type | None:
        if cls._STRATEGY_CLASSES is None:
            cls._STRATEGY_CLASSES = {}
        return cls._STRATEGY_CLASSES.get(name)

    @classmethod
    def list_strategies(cls) -> list[str]:
        if cls._STRATEGY_CLASSES is None:
            cls._STRATEGY_CLASSES = {}
        return list(cls._STRATEGY_CLASSES.keys())

    # ============================================================
    # 内部辅助
    # ============================================================
    async def _screen_stocks(self, strategy: str = "") -> dict[str, list[str]]:
        if self._screener and hasattr(self._screener, "screen"):
            return await self._screener.screen(strategy)
        return {"default": self._default_universe()}

    def _default_universe(self) -> list[str]:
        return []

    async def _generate_signals(self, stock_pools: dict[str, list[str]]) -> list[Signal]:
        return []

    async def _gather_news(self, positions: list[Position]) -> str:
        return ""

    def _build_summary(
        self,
        bal: dict,
        positions: list[dict],
        daily_pnl: float,
        total_pnl: float,
        news: str,
    ) -> str:
        parts = [f"## 河图日交易报告 ({self._today})"]
        parts.append(f"- 总资产: {bal.get('total_assets', 0):,.2f}")
        parts.append(f"- 日盈亏: {daily_pnl:+,.2f}")
        parts.append(f"- 累计盈亏: {total_pnl:+,.2f}")
        parts.append(f"- 持仓数: {len(positions)}")
        if news:
            parts.append(f"\n### 资讯摘要\n{news}")
        return "\n".join(parts)

    async def _post_summary(self, summary: str) -> bool:
        logger.info("推送复盘总结:\n%s", summary)
        return True

    def _build_context(self, bal: dict) -> StrategyContext:
        return StrategyContext(
            current_date=date.today(),
            total_asset=bal.get("total_assets", 0.0),
            market_regime=MarketRegime(),
        )

    async def _cleanup(self) -> None:
        logger.debug("_cleanup")
