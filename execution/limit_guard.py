"""
河图 (HeTu) 涨跌停应急守卫

LimitGuard: 监控持仓中是否出现涨跌停，触发后生成应急预案。
v2.1: 策略审核后新增——A 股 T+1+涨跌停制度下必须的尾部风险保护。

规则:
- 持仓中出现跌停 → 次日开盘强制减半仓（SELL）
- 持仓连续 2 日跌停 → 清仓（SELL 全部）
- 持仓中出现涨停 → 标记监控，不强制操作（让利润跑）
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from core.types import Signal, Action

logger = logging.getLogger("hetu.limit_guard")


@dataclass
class LimitAlert:
    """涨跌停预警记录"""
    code: str = ""
    alert_type: str = ""  # "limit_up" | "limit_down"
    shares: int = 0
    entry_price: float = 0.0
    limit_price: float = 0.0
    hit_date: str = ""
    consecutive_days: int = 1


class LimitGuard:
    """
    涨跌停应急预案守卫。

    用法:
        guard = LimitGuard()
        # 盘后报告涨跌停
        guard.report(code="000001.SZ", last_price=47.5, pre_close=50.0,
                     limit_down_pct=0.10, shares=1000, entry_price=50.0)
        # 次日开盘获取应急预案
        signals = guard.get_emergency_signals()
    """

    DEFAULT_LIMIT_PCT: float = 0.1    # 主板 10%
    GEM_LIMIT_PCT: float = 0.2         # 创业板/科创板 20%
    BJ_LIMIT_PCT: float = 0.3          # 北交所 30%

    def __init__(self, limit_down_pct: float | None = None):
        self._limit_down_pct = limit_down_pct or self.DEFAULT_LIMIT_PCT
        self._alerts: dict[str, LimitAlert] = {}
        self._daily_hits: dict[str, int] = defaultdict(int)

    def report(
        self,
        code: str,
        last_price: float,
        pre_close: float,
        limit_down_pct: float | None = None,
        shares: int = 0,
        entry_price: float = 0.0,
    ) -> LimitAlert | None:
        """报告涨跌停检测。返回预警记录或 None。"""
        if pre_close <= 0:
            return None

        pct = limit_down_pct or self._detect_limit_pct(code)
        change_pct = (last_price - pre_close) / pre_close

        # 跌停检测
        if change_pct <= -pct + 0.001:
            return self._record_alert(code, "limit_down", shares, entry_price, last_price)

        # 涨停检测（不强制操作，仅记录）
        if change_pct >= pct - 0.001:
            return self._record_alert(code, "limit_up", shares, entry_price, last_price)

        return None

    def _detect_limit_pct(self, code: str) -> float:
        """根据股票代码规则判断涨跌停幅度。"""
        if ".BJ" in code or code.startswith("8"):
            return self.BJ_LIMIT_PCT
        if ".SZ" in code and code.startswith("300"):
            return self.GEM_LIMIT_PCT
        if ".SH" in code and code.startswith("688"):
            return self.GEM_LIMIT_PCT
        return self.DEFAULT_LIMIT_PCT

    def _record_alert(
        self,
        code: str,
        alert_type: str,
        shares: int,
        entry_price: float,
        limit_price: float,
    ) -> LimitAlert:
        """记录预警，支持连续触发计数。"""
        today = str(date.today())
        existing = self._alerts.get(code)
        if existing and existing.alert_type == alert_type:
            existing.consecutive_days += 1
            existing.hit_date = today
            return existing

        alert = LimitAlert(
            code=code,
            alert_type=alert_type,
            shares=shares,
            entry_price=entry_price,
            limit_price=limit_price,
            hit_date=today,
            consecutive_days=1,
        )
        self._alerts[code] = alert
        logger.warning(f"Limit {alert_type}: {code} @ {limit_price} (day {alert.consecutive_days})")
        return alert

    def get_emergency_signals(self) -> list[Signal]:
        """生成应急预案信号。"""
        signals: list[Signal] = []
        today = str(date.today())

        for code, alert in list(self._alerts.items()):
            if alert.alert_type == "limit_up":
                # 涨停不强制操作
                continue

            if alert.alert_type == "limit_down":
                if alert.consecutive_days >= 2:
                    # 连续两日跌停 → 清仓
                    signals.append(Signal(
                        strategy="limit_guard",
                        code=code,
                        action=Action.SELL,
                        confidence=1.0,
                        reason=f"连续 {alert.consecutive_days} 日跌停，强制清仓",
                        shares=alert.shares,
                        price_limit=alert.limit_price,
                    ))
                else:
                    # 一日跌停 → 减半仓
                    half = max(100, alert.shares // 200 * 100)
                    if alert.shares > half:
                        signals.append(Signal(
                            strategy="limit_guard",
                            code=code,
                            action=Action.SELL,
                            confidence=0.9,
                            reason="跌停第 1 日，强制减半仓",
                            shares=alert.shares - half,
                            price_limit=alert.limit_price,
                        ))
        return signals

    def reset_daily(self) -> None:
        """每日重置计数（非连续跌停的股票清除）。"""
        for code in list(self._alerts.keys()):
            alert = self._alerts[code]
            if alert.consecutive_days == 0:
                del self._alerts[code]

    def has_limit_down(self) -> bool:
        """是否存在跌停预警。"""
        return any(a.alert_type == "limit_down" for a in self._alerts.values())

    def get_alerts(self) -> dict[str, LimitAlert]:
        """获取所有预警记录。"""
        return dict(self._alerts)

    def clear(self) -> None:
        """清除所有预警。"""
        self._alerts.clear()
        self._daily_hits.clear()
