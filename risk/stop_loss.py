"""
河图 (HeTu) 止损引擎 — T+1 感知

止损策略:
- 固定止损: -X% 无条件止损
- 移动止损: 从最高价回撤 -X% 止损
- 时间止损: 持有超过 N 天未达目标则触发

T+1 感知:
- 今日买入的股票不生成止损信号（因为 T+1 不可卖出）
- 但可预计算止损价供明日使用
"""
from __future__ import annotations

import logging
from datetime import date
from enum import Enum
from uuid import UUID, uuid4

from core.types import Signal, Bar, StopType, Action

logger = logging.getLogger("hetu.stop_loss")


class StopAction(Enum):
    """止损动作"""
    NONE = "none"
    ALERT = "alert"
    HARD_STOP = "hard"
    TRAIL_STOP = "trail"


class StopRule:
    """单个止损规则"""
    def __init__(
        self,
        stop_type: StopType,
        threshold: float = 0.0,
        trail_pct: float = 0.0,
        max_hold_days: int = 20,
    ):
        self.stop_type = stop_type
        self.threshold = threshold
        self.trail_pct = trail_pct
        self.max_hold_days = max_hold_days


class StopLossEngine:
    """
    T+1 感知止损引擎

    使用方式:
        engine = StopLossEngine(hard_threshold=0.08, trail_threshold=0.04)
        engine.add_position("000001.SZ", entry_price=50.0, entry_date="2024-01-15")
        action = engine.check("000001.SZ", current_bar)
        if action == StopAction.HARD_STOP:
            # 生成卖出信号
    """

    def __init__(
        self,
        hard_threshold: float = 0.05,
        trail_threshold: float = 0.08,
        trail_pct: float = 0.04,
        max_hold_days: int = 20,
    ):
        self._hard_threshold = hard_threshold
        self._trail_pct = trail_pct
        self._positions: dict[str, dict] = {}
        self._rules: list[StopRule] = [
            StopRule(stop_type=StopType.HARD, threshold=hard_threshold),
            StopRule(stop_type=StopType.TRAILING, trail_pct=trail_threshold),
            StopRule(stop_type=StopType.TIME, max_hold_days=max_hold_days),
        ]

    # ──────────────────────────────────
    # 持仓管理
    # ──────────────────────────────────

    def add_position(
        self,
        code: str,
        entry_price: float,
        shares: int = 0,
        entry_date: str = "",
        position_id: "UUID | None" = None,
        stop_price: "float | None" = None,
    ) -> None:
        self._positions[code] = {
            "entry_price": entry_price,
            "shares": shares,
            "entry_date": entry_date or date.today().isoformat(),
            "position_id": position_id or uuid4(),
            "highest_price": entry_price,
            "hard_stop": stop_price or entry_price * (1 - self._hard_threshold),
            "trail_stop": entry_price * (1 - self._trail_pct),
            "bought_today": True if entry_date and entry_date == date.today().isoformat() else False,
        }
        logger.debug("add_position %s @ %.2f", code, entry_price)

    def remove_position(self, code: str) -> None:
        self._positions.pop(code, None)

    # ──────────────────────────────────
    # 最高价更新 (移动止损用)
    # ──────────────────────────────────

    def update_highest(self, code: str, current_high: float) -> None:
        info = self._positions.get(code)
        if not info:
            return
        if current_high > info["highest_price"]:
            info["highest_price"] = current_high
            info["trail_stop"] = current_high * (1 - self._trail_pct)

    # ──────────────────────────────────
    # 单个检查
    # ──────────────────────────────────

    def check(self, code: str, bar: Bar, today: "str | None" = None) -> StopAction:
        info = self._positions.get(code)
        if not info:
            return StopAction.NONE

        current_price = bar.close
        today_str = today or date.today().isoformat()

        # T+1: 今日买入不生成止损信号
        if today_str and info.get("bought_today", False):
            return StopAction.NONE

        # 1) 硬止损
        if current_price <= info["hard_stop"]:
            return StopAction.HARD_STOP

        # 2) 移动止损
        self.update_highest(code, bar.high)
        if current_price <= info["trail_stop"]:
            return StopAction.TRAIL_STOP

        # 3) 时间止损
        for rule in self._rules:
            if rule.stop_type == StopType.TIME:
                try:
                    entry_dt = date.fromisoformat(info["entry_date"])
                    days_held = (date.today() - entry_dt).days
                    if days_held >= rule.max_hold_days:
                        return StopAction.ALERT
                except (ValueError, TypeError):
                    pass

        return StopAction.NONE

    # ──────────────────────────────────
    # 批量检查
    # ──────────────────────────────────

    def check_all(
        self, bars: dict[str, Bar], today: "str | None" = None
    ) -> dict[str, StopAction]:
        result: dict[str, StopAction] = {}
        for code in self._positions:
            bar = bars.get(code)
            if bar is None:
                continue
            action = self.check(code, bar, today)
            result[code] = action
        return result

    # ──────────────────────────────────
    # 生成止损信号
    # ──────────────────────────────────

    def generate_stop_signals(
        self, bars: dict[str, Bar], today: "str | None" = None
    ) -> list[Signal]:
        signals: list[Signal] = []
        today_str = today or date.today().isoformat()

        for code, info in self._positions.items():
            bar = bars.get(code)
            if bar is None:
                continue

            # T+1: 今日买入不生成止损信号
            if today_str and info.get("bought_today", False):
                continue

            current_price = bar.close

            # 硬止损
            if current_price <= info["hard_stop"]:
                signals.append(
                    Signal(
                        strategy="stop_loss",
                        code=code,
                        action=Action.SELL,
                        reason=f"硬止损 {current_price:.2f} ≤ {info['hard_stop']:.2f}",
                        shares=info["shares"],
                        confidence=1.0,
                    )
                )
                continue

            # 移动止损
            self.update_highest(code, bar.high)
            if current_price <= info["trail_stop"]:
                signals.append(
                    Signal(
                        strategy="stop_loss",
                        code=code,
                        action=Action.SELL,
                        reason=f"移动止损 {current_price:.2f} ≤ {info['trail_stop']:.2f} (最高 {info['highest_price']:.2f})",
                        shares=info["shares"],
                        confidence=0.95,
                    )
                )
                continue

            # 时间止损
            for rule in self._rules:
                if rule.stop_type == StopType.TIME:
                    try:
                        entry_dt = date.fromisoformat(info["entry_date"])
                        days_held = (date.today() - entry_dt).days
                        if days_held >= rule.max_hold_days:
                            signals.append(
                                Signal(
                                    strategy="stop_loss",
                                    code=code,
                                    action=Action.SELL,
                                    reason=f"时间止损 持有 {days_held} 天",
                                    shares=info["shares"],
                                    confidence=0.7,
                                )
                            )
                    except (ValueError, TypeError):
                        pass

        if signals:
            logger.info("生成 %d 个止损信号", len(signals))
        return signals

    # ──────────────────────────────────
    # 止损价格更新
    # ──────────────────────────────────

    def update_stop_prices(self, quotes: dict[str, float]) -> None:
        for code, price in quotes.items():
            info = self._positions.get(code)
            if not info:
                continue
            self.update_highest(code, price)

    # ──────────────────────────────────
    # 查询
    # ──────────────────────────────────

    def get_stop_levels(self, code: str) -> "dict[str, float] | None":
        info = self._positions.get(code)
        if not info:
            return None
        return {
            "hard_stop": info["hard_stop"],
            "trail_stop": info["trail_stop"],
        }

    def get_all_positions(self) -> dict[str, dict]:
        return dict(self._positions)

    def __len__(self) -> int:
        return len(self._positions)
