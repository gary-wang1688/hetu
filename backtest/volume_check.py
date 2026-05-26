"""
河图 (HeTu) 成交量校验器

回测成交量约束:
- 单笔委托不超过日均成交量的 1%
- 涨停无法买入
- 跌停无法卖出
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from core.types import Order, Bar

logger = logging.getLogger("hetu.backtest.volume")


@dataclass
class VolumeCheckResult:
    """成交量校验结果"""
    passed: bool = True
    max_shares: int = 0
    reason: str = ""


class VolumeChecker:
    """成交量校验器

    确保回测中的委托量不超过实际市场流动性约束。
    """

    LIMIT_UP_THRESHOLD: float = 0.095  # 涨停阈值 9.5%
    LIMIT_DOWN_THRESHOLD: float = -0.095  # 跌停阈值 -9.5%
    VOLUME_RATIO: float = 0.01  # 单笔不超过日均的 1%

    def __init__(self, volume_ratio: float = 0.01) -> None:
        self.volume_ratio = volume_ratio
        self._daily_volumes: dict[str, pd.Series] = {}

    def set_data(self, bars: pd.DataFrame) -> None:
        """设置日线数据（含 volume 列）"""
        if bars.empty or "volume" not in bars.columns:
            return
        for code, group in bars.groupby("code"):
            self._daily_volumes[code] = group.set_index("date")["volume"]
        self._bars = bars

    def check(self, order: Order, date_str: str, bar: Bar | None = None, pre_close: float = 0.0) -> VolumeCheckResult:
        """
        校验委托是否可执行

        Args:
            order: 委托订单
            date_str: 交易日期
            bar: 当日 Bar 数据（可选，用于涨跌停判断）
            pre_close: 前收盘价

        Returns:
            VolumeCheckResult (passed, max_shares, reason)
        """
        # 涨跌停不可交易
        if bar is not None and bar.close > 0 and pre_close > 0:
            pct_change = (bar.close - pre_close) / pre_close
            if order.action.value == "BUY" and pct_change >= self.LIMIT_UP_THRESHOLD:
                return VolumeCheckResult(
                    passed=False,
                    max_shares=0,
                    reason=f"涨停无法买入 ({pct_change:.1%})",
                )
            if order.action.value == "SELL" and pct_change <= self.LIMIT_DOWN_THRESHOLD:
                return VolumeCheckResult(
                    passed=False,
                    max_shares=0,
                    reason=f"跌停无法卖出 ({pct_change:.1%})",
                )

        # 成交量约束
        if order.code in self._daily_volumes:
            vol_series = self._daily_volumes[order.code]
            if date_str in vol_series.index:
                daily_vol = vol_series[date_str]
                max_shares = int(daily_vol * self.volume_ratio)
                max_shares = max(max_shares, 100)  # 至少允许 1 手

                if order.shares > max_shares:
                    return VolumeCheckResult(
                        passed=False,
                        max_shares=max_shares,
                        reason=f"委托量 {order.shares} > 上限 {max_shares} (日均{self.volume_ratio:.1%})",
                    )

        return VolumeCheckResult(passed=True, max_shares=order.shares)

    def get_limit_up_set(self, bars: pd.DataFrame, date_str: str) -> set[str]:
        """获取当日涨停的股票代码集合"""
        if bars.empty:
            return set()

        today_bars = bars[bars["date"] == date_str]
        if today_bars.empty:
            return set()

        limit_up: set[str] = set()
        for _, row in today_bars.iterrows():
            if "pre_close" in bars.columns and row.get("pre_close", 0) > 0:
                pct = (row["close"] - row["pre_close"]) / row["pre_close"]
                if pct >= self.LIMIT_UP_THRESHOLD:
                    limit_up.add(str(row.get("code", "")))

        return limit_up

    def get_limit_down_set(self, bars: pd.DataFrame, date_str: str) -> set[str]:
        """获取当日跌停的股票代码集合"""
        if bars.empty:
            return set()

        today_bars = bars[bars["date"] == date_str]
        if today_bars.empty:
            return set()

        limit_down: set[str] = set()
        for _, row in today_bars.iterrows():
            if "pre_close" in bars.columns and row.get("pre_close", 0) > 0:
                pct = (row["close"] - row["pre_close"]) / row["pre_close"]
                if pct <= self.LIMIT_DOWN_THRESHOLD:
                    limit_down.add(str(row.get("code", "")))

        return limit_down
