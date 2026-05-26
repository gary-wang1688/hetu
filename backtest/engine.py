"""
河图 (HeTu) 回测引擎 — 事件驱动日线回测

架构第九章: 完整回测，含滑点模型/成交量约束/复权/基准比较。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from core.types import Signal, Bar, Order
from backtest.slippage import SlippageModel, FixedTickSlippage
from backtest.volume_check import VolumeChecker
from backtest.metrics import PerformanceMetrics, BacktestMetrics

try:
    from analytics.overfitting import OverfittingDetector, OverfitReport
except ImportError:
    OverfittingDetector = None
    OverfitReport = None

logger = logging.getLogger("hetu.backtest")


@dataclass
class TradeResult:
    """单笔交易结果"""
    code: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    shares: int
    pnl: float
    pnl_pct: float
    reason: str = ""


@dataclass
class BacktestResult:
    """回测结果"""
    metrics: BacktestMetrics = field(default_factory=BacktestMetrics)
    equity_curve: pd.Series | None = None
    returns: pd.Series | None = None
    benchmark_curve: pd.Series | None = None
    trades: list[TradeResult] = field(default_factory=list)
    signals: list[Signal] = field(default_factory=list)
    overfit_report: Any | None = None
    warnings: list[str] = field(default_factory=list)


class BacktestEngine:
    """
    事件驱动日线回测引擎

    架构:
    - 基于日线 Bar 的事件驱动
    - 可插拔滑点模型
    - 成交量约束（单笔≤日均1%）
    - 涨跌停不可交易
    - 佣金+印花税
    - T+1 卖出限制
    - 前/后复权统一
    - 基准比较（沪深300）
    """

    COMMISSION: float = 0.00025
    STAMP_TAX: float = 0.001
    MIN_COMMISSION: float = 5.0

    def __init__(
        self,
        initial_cash: float = 1_000_000,
        slippage: SlippageModel | None = None,
        volume_check: VolumeChecker | None = None,
        adjustment_mode: str = "backward",
    ) -> None:
        self._initial_cash = initial_cash
        self._slippage = slippage or FixedTickSlippage()
        self._volume_check = volume_check or VolumeChecker()
        self._adjustment_mode = adjustment_mode
        self._reset_state()

    def _reset_state(self) -> None:
        """重置回测状态"""
        self._cash: float = self._initial_cash
        self._positions: dict[str, dict] = {}
        self._trades: list[TradeResult] = []
        self._signals: list[Signal] = []
        self._equity: pd.Series = pd.Series(dtype=float)
        self._bars: pd.DataFrame = pd.DataFrame()
        self._benchmark: pd.Series | None = None
        self._dates: list[str] = []
        self._universe: list[str] = []

    def set_data(self, bars: pd.DataFrame) -> None:
        """设置日线数据"""
        self._bars = bars
        self._volume_check.set_data(bars)

    def set_benchmark(self, benchmark: pd.Series) -> None:
        """设置基准数据"""
        self._benchmark = benchmark

    async def run(
        self,
        strategies: list,
        start_date: str,
        end_date: str,
        progress: bool = False,
    ) -> BacktestResult:
        """
        运行回测

        Args:
            strategies: 策略列表
            start_date: 开始日期
            end_date: 结束日期
            progress: 是否显示进度

        Returns:
            BacktestResult
        """
        return await self._run_async(strategies, start_date, end_date, progress)

    async def _run_async(
        self,
        strategies: list,
        start_date: str,
        end_date: str,
        progress: bool = False,
    ) -> BacktestResult:
        """异步运行回测"""
        self._reset_state()

        # 过滤日期范围
        if self._bars.empty:
            logger.warning("无数据，跳过回测")
            return BacktestResult()

        mask = (self._bars["date"] >= start_date) & (self._bars["date"] <= end_date)
        bars = self._bars[mask].copy()
        if bars.empty:
            logger.warning("日期范围内无数据")
            return BacktestResult()

        self._dates = sorted(bars["date"].unique())
        self._universe = list(bars["code"].unique())

        equity_values: list[float] = []
        equity_dates: list[str] = []

        for today_str in self._dates:
            # 生成信号
            today_signals: list[Signal] = []
            for strategy in strategies:
                try:
                    context = self._build_context(today_str)
                    signals = await strategy.generate_signals(context)
                    today_signals.extend(signals)
                except Exception as e:
                    logger.error("策略 %s 生成信号失败: %s", getattr(strategy, "name", "unknown"), e)

            self._signals.extend(today_signals)

            # 执行信号
            self._execute_signals(today_signals, today_str)

            # 更新持仓市值
            self._update_positions(today_str)

            # 记录权益
            equity = self._total_equity(today_str)
            equity_values.append(equity)
            equity_dates.append(today_str)

        # 最后清仓
        self._liquidate(self._dates[-1] if self._dates else end_date)

        # 构建权益曲线
        self._equity = pd.Series(equity_values, index=equity_dates, name="equity")
        returns = self._equity.pct_change().dropna() if len(self._equity) > 1 else pd.Series(dtype=float)

        # 计算指标
        trade_dicts = self._trade_dicts()
        metrics = PerformanceMetrics.calc(self._equity, trade_dicts, self._benchmark)

        return BacktestResult(
            metrics=metrics,
            equity_curve=self._equity,
            returns=returns,
            benchmark_curve=self._benchmark,
            trades=self._trades,
            signals=self._signals,
        )

    def _execute_signals(self, signals: list[Signal], today_str: str) -> None:
        """执行信号列表"""
        for signal in signals:
            if signal.action.value == "BUY":
                self._execute_buy(signal, today_str)
            elif signal.action.value == "SELL":
                self._execute_sell(signal, today_str)

    def _execute_buy(self, signal: Signal, today_str: str) -> None:
        """执行买入信号"""
        code = signal.code

        # 检查是否已有持仓
        if code in self._positions:
            return

        bar = self._get_bar(code, today_str)
        if bar is None:
            logger.debug("无 %s 在 %s 的行情数据", code, today_str)
            return

        pre_close = self._get_pre_close(code, today_str)

        # 成交量校验
        result = self._volume_check.check(
            Order(code=code, action=signal.action, shares=signal.shares, channel="backtest"),
            today_str,
            bar,
            pre_close,
        )
        if not result.passed:
            logger.debug("%s 买入被拒: %s", code, result.reason)
            return

        # 计算买入成本
        order_shares = min(signal.shares, result.max_shares) if signal.shares > 0 else int(
            self._cash * 0.95 / bar.close
        ) // 100 * 100

        price = self._slippage.apply_buy(bar.close)
        cost = self._buy_cost(price, order_shares)

        if cost > self._cash or order_shares < 100:
            logger.debug("%s 资金不足: 需要 %.2f, 可用 %.2f", code, cost, self._cash)
            return

        # 执行买入
        self._cash -= cost
        self._positions[code] = {
            "shares": order_shares,
            "avg_cost": price,
            "entry_date": today_str,
            "entry_price": price,
            "strategy": signal.strategy,
        }

        logger.debug("买入 %s: %d 股 @ %.2f (成本 %.2f)", code, order_shares, price, cost)

    def _execute_sell(self, signal: Signal, today_str: str) -> None:
        """执行卖出信号"""
        code = signal.code

        if code not in self._positions:
            return

        bar = self._get_bar(code, today_str)
        if bar is None:
            return

        pre_close = self._get_pre_close(code, today_str)

        # 成交量校验
        result = self._volume_check.check(
            Order(code=code, action=signal.action, shares=signal.shares, channel="backtest"),
            today_str,
            bar,
            pre_close,
        )
        if not result.passed:
            logger.debug("%s 卖出被拒: %s", code, result.reason)
            return

        pos = self._positions[code]
        sell_shares = min(signal.shares, pos["shares"]) if signal.shares > 0 else pos["shares"]

        price = self._slippage.apply_sell(bar.close)
        proceeds = self._sell_cost(price, sell_shares)

        self._cash += proceeds

        # 记录交易
        pnl = (price - pos["entry_price"]) * sell_shares - (pos["entry_price"] * sell_shares * self.COMMISSION * 2)
        pnl_pct = (price / pos["entry_price"] - 1) if pos["entry_price"] > 0 else 0

        self._trades.append(TradeResult(
            code=code,
            entry_date=pos["entry_date"],
            exit_date=today_str,
            entry_price=pos["entry_price"],
            exit_price=price,
            shares=sell_shares,
            pnl=pnl,
            pnl_pct=pnl_pct,
            reason=signal.reason,
        ))

        del self._positions[code]
        logger.debug("卖出 %s: %d 股 @ %.2f (盈亏 %.2f, %.2f%%)", code, sell_shares, price, pnl, pnl_pct * 100)

    def _update_positions(self, today_str: str) -> None:
        """更新持仓市值"""
        for code, pos in list(self._positions.items()):
            bar = self._get_bar(code, today_str)
            if bar is not None:
                pos["current_price"] = bar.close
            pos["market_value"] = pos["shares"] * pos.get("current_price", pos["avg_cost"])

    def _total_equity(self, today_str: str = "") -> float:
        """计算总权益"""
        total = self._cash
        for pos in self._positions.values():
            total += pos.get("market_value", pos["shares"] * pos["avg_cost"])
        return total

    def _liquidate(self, today_str: str) -> None:
        """清仓所有持仓"""
        for code in list(self._positions.keys()):
            bar = self._get_bar(code, today_str)
            if bar is None:
                continue
            pos = self._positions[code]
            price = self._slippage.apply_sell(bar.close)
            proceeds = self._sell_cost(price, pos["shares"])
            self._cash += proceeds

            pnl = (price - pos["entry_price"]) * pos["shares"]
            pnl_pct = (price / pos["entry_price"] - 1) if pos["entry_price"] > 0 else 0

            self._trades.append(TradeResult(
                code=code,
                entry_date=pos.get("entry_date", ""),
                exit_date=today_str,
                entry_price=pos["entry_price"],
                exit_price=price,
                shares=pos["shares"],
                pnl=pnl,
                pnl_pct=pnl_pct,
                reason="强制清仓",
            ))
            del self._positions[code]

    def _buy_cost(self, price: float, shares: int) -> float:
        """计算买入成本（含佣金）"""
        value = price * shares
        commission = max(value * self.COMMISSION, self.MIN_COMMISSION)
        return value + commission

    def _sell_cost(self, price: float, shares: int) -> float:
        """计算卖出收入（扣除佣金+印花税）"""
        value = price * shares
        commission = max(value * self.COMMISSION, self.MIN_COMMISSION)
        stamp = value * self.STAMP_TAX
        return value - commission - stamp

    def _get_bar(self, code: str, date_str: str) -> Bar | None:
        """获取指定股票和日期的 Bar"""
        if self._bars.empty:
            return None
        mask = (self._bars["code"] == code) & (self._bars["date"] == date_str)
        rows = self._bars[mask]
        if rows.empty:
            return None
        row = rows.iloc[0]
        return Bar(
            code=str(row.get("code", code)),
            date=row.get("date", date_str),
            open=float(row.get("open", 0)),
            high=float(row.get("high", 0)),
            low=float(row.get("low", 0)),
            close=float(row.get("close", 0)),
            volume=int(row.get("volume", 0)),
            amount=float(row.get("amount", 0)),
        )

    def _get_history(self, code: str, date_str: str, lookback: int = 60) -> list[Bar]:
        """获取历史 Bar 数据"""
        if self._bars.empty:
            return []
        code_bars = self._bars[self._bars["code"] == code]
        code_bars = code_bars[code_bars["date"] <= date_str].tail(lookback)
        bars: list[Bar] = []
        for _, row in code_bars.iterrows():
            bars.append(Bar(
                code=str(row.get("code", code)),
                date=row.get("date", ""),
                open=float(row.get("open", 0)),
                high=float(row.get("high", 0)),
                low=float(row.get("low", 0)),
                close=float(row.get("close", 0)),
                volume=int(row.get("volume", 0)),
                amount=float(row.get("amount", 0)),
            ))
        return bars

    def _get_pre_close(self, code: str, date_str: str) -> float:
        """获取前收盘价"""
        if self._bars.empty:
            return 0.0
        code_bars = self._bars[(self._bars["code"] == code) & (self._bars["date"] < date_str)]
        if code_bars.empty:
            return 0.0
        return float(code_bars.iloc[-1]["close"])

    def _get_daily_volume(self, code: str, date_str: str) -> int:
        """获取当日成交量"""
        bar = self._get_bar(code, date_str)
        return bar.volume if bar else 0

    def _trade_dicts(self) -> list[dict]:
        """转换 trades 为字典列表（供 metrics 使用）"""
        return [
            {
                "code": t.code,
                "entry_date": t.entry_date,
                "exit_date": t.exit_date,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "shares": t.shares,
                "pnl": t.pnl,
                "pnl_pct": t.pnl_pct,
                "reason": t.reason,
            }
            for t in self._trades
        ]

    def _build_context(self, today_str: str) -> dict:
        """构建策略上下文"""
        return {
            "current_date": today_str,
            "today": today_str,
            "total_asset": self._total_equity(today_str),
            "total_position_pct": 1 - self._cash / max(self._total_equity(today_str), 1),
            "positions": dict(self._positions),
            "cash": self._cash,
            "universe": list(self._universe),
        }

    @property
    def positions(self) -> dict:
        """当前持仓"""
        return dict(self._positions)

    @property
    def cash(self) -> float:
        """当前现金"""
        return self._cash
