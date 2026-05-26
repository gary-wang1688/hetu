"""
河图 (HeTu) 回测指标计算

计算核心绩效指标:
- 累计收益率 / 年化收益率
- 夏普比率
- 最大回撤
- 胜率 / 盈亏比
- 卡玛比率
- 年化波动率
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

logger = logging.getLogger("hetu.backtest.metrics")


@dataclass
class BacktestMetrics:
    """回测绩效指标"""
    total_return: float = 0.0
    annual_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    profit_loss_ratio: float = 0.0
    calmar_ratio: float = 0.0
    annual_volatility: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    avg_trade_return: float = 0.0
    benchmark_return: float = 0.0
    alpha: float = 0.0
    beta: float = 0.0
    information_ratio: float = 0.0


class PerformanceMetrics:
    """性能指标计算器"""

    TRADING_DAYS_PER_YEAR: int = 252

    @classmethod
    def calc(cls, equity_series: pd.Series, trades: list[dict] | None = None,
             benchmark_series: pd.Series | None = None) -> BacktestMetrics:
        """
        计算全部绩效指标

        Args:
            equity_series: 权益曲线 (index: date)
            trades: 交易记录列表
            benchmark_series: 基准曲线

        Returns:
            BacktestMetrics
        """
        metrics = BacktestMetrics()

        if equity_series.empty or len(equity_series) < 2:
            return metrics

        # 收益率序列
        returns = equity_series.pct_change().dropna()

        if returns.empty:
            return metrics

        # 累计收益
        metrics.total_return = (equity_series.iloc[-1] / equity_series.iloc[0]) - 1

        # 年化收益
        n_days = len(returns)
        if n_days > 0:
            metrics.annual_return = (1 + metrics.total_return) ** (cls.TRADING_DAYS_PER_YEAR / n_days) - 1

        # 年化波动率
        metrics.annual_volatility = returns.std() * np.sqrt(cls.TRADING_DAYS_PER_YEAR)

        # 夏普比率 (假设无风险利率 = 0.03)
        risk_free = 0.03 / cls.TRADING_DAYS_PER_YEAR
        excess = returns - risk_free
        if metrics.annual_volatility > 0:
            metrics.sharpe_ratio = (excess.mean() * cls.TRADING_DAYS_PER_YEAR) / metrics.annual_volatility

        # 最大回撤
        cumulative = equity_series / equity_series.iloc[0]
        rolling_max = cumulative.cummax()
        drawdowns = cumulative / rolling_max - 1
        metrics.max_drawdown = drawdowns.min()

        # 卡玛比率
        if abs(metrics.max_drawdown) > 0:
            metrics.calmar_ratio = metrics.annual_return / abs(metrics.max_drawdown)

        # 交易统计
        if trades:
            metrics.total_trades = len(trades)
            winning = [t for t in trades if t.get("pnl", 0) > 0]
            losing = [t for t in trades if t.get("pnl", 0) <= 0]
            metrics.winning_trades = len(winning)
            metrics.losing_trades = len(losing)
            metrics.win_rate = metrics.winning_trades / max(metrics.total_trades, 1)

            if winning:
                avg_win = np.mean([t.get("pnl", 0) for t in winning])
            else:
                avg_win = 0.0
            if losing:
                avg_loss = abs(np.mean([t.get("pnl", 0) for t in losing]))
            else:
                avg_loss = 0.0
            metrics.profit_loss_ratio = avg_win / max(avg_loss, 0.01)

            if metrics.total_trades > 0:
                metrics.avg_trade_return = np.mean([t.get("pnl_pct", 0) for t in trades])

        # 基准比较
        if benchmark_series is not None and not benchmark_series.empty:
            benchmark_returns = benchmark_series.pct_change().dropna()
            if len(benchmark_returns) > 0:
                metrics.benchmark_return = (benchmark_series.iloc[-1] / benchmark_series.iloc[0]) - 1

                # Alpha & Beta
                if len(returns) == len(benchmark_returns):
                    aligned = pd.DataFrame({"strategy": returns.values, "benchmark": benchmark_returns.values}).dropna()
                    if len(aligned) > 1:
                        cov = np.cov(aligned["strategy"], aligned["benchmark"])
                        metrics.beta = cov[0, 1] / max(cov[1, 1], 1e-10)
                        metrics.alpha = metrics.annual_return - risk_free * cls.TRADING_DAYS_PER_YEAR - metrics.beta * (
                            metrics.benchmark_return - risk_free * cls.TRADING_DAYS_PER_YEAR
                        )

                        # 信息比率
                        tracking_error = (returns - benchmark_returns).std() * np.sqrt(cls.TRADING_DAYS_PER_YEAR)
                        if tracking_error > 0:
                            metrics.information_ratio = (metrics.annual_return - metrics.benchmark_return) / tracking_error

        return metrics

    @classmethod
    def max_drawdown(cls, equity_series: pd.Series) -> float:
        """计算最大回撤"""
        if equity_series.empty:
            return 0.0
        cumulative = equity_series / equity_series.iloc[0]
        rolling_max = cumulative.cummax()
        drawdowns = cumulative / rolling_max - 1
        return float(drawdowns.min())

    @classmethod
    def sharpe_ratio(cls, returns: pd.Series, risk_free: float = 0.03) -> float:
        """计算夏普比率"""
        if returns.empty:
            return 0.0
        daily_rf = risk_free / cls.TRADING_DAYS_PER_YEAR
        excess = returns - daily_rf
        if excess.std() > 0:
            return float(excess.mean() / excess.std() * np.sqrt(cls.TRADING_DAYS_PER_YEAR))
        return 0.0

    @classmethod
    def annual_return(cls, total_return: float, n_days: int) -> float:
        """计算年化收益率"""
        if n_days <= 0:
            return 0.0
        return float((1 + total_return) ** (cls.TRADING_DAYS_PER_YEAR / n_days) - 1)

    @classmethod
    def win_rate(cls, trades: list[dict]) -> float:
        """计算胜率"""
        if not trades:
            return 0.0
        winning = sum(1 for t in trades if t.get("pnl", 0) > 0)
        return winning / len(trades)
