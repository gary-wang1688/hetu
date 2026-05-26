"""
河图 (HeTu) 熔断机制

v2.1: 明确熔断作用域（全市场/单策略/系统），
允许全市场熔断执行而单策略正常，反之亦然。

触发条件:
1. 单日亏损 > 3% -> 暂停新开仓 (YELLOW)
2. 单日亏损 > 5% -> 平掉所有持仓 (ORANGE)
3. 连续 N 笔止损 -> 暂停策略 (RED, 策略级)
4. 基准指数暴跌 > 3% -> 全市场熔断 (RED, 市场级)
5. 数据源异常 > 阈值 -> 暂停自动交易 (RED, 系统级)

所有熔断可手动覆盖。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger("hetu.cb")


class BreakerScope(str, Enum):
    """v2.1: 熔断作用域"""
    MARKET = "MARKET"
    STRATEGY = "STRATEGY"
    SYSTEM = "SYSTEM"


class BreakerLevel(Enum):
    """熔断级别"""
    GREEN = 0
    YELLOW = 1
    ORANGE = 2
    RED = 3


@dataclass
class BreakerState:
    """熔断状态"""
    level: BreakerLevel = BreakerLevel.GREEN
    scope: BreakerScope = BreakerScope.STRATEGY
    reason: str = ""
    triggered_at: float = 0.0
    cooldown_seconds: int = 300
    override: bool = False
    override_by: str = ""

    def is_market_wide(self) -> bool:
        return self.scope == BreakerScope.MARKET


class CircuitBreaker:
    """
    熔断器

    规则:
    - GREEN  -> 正常交易
    - YELLOW -> 不新开仓 (daily_pnl < -3%)
    - ORANGE -> 只平仓不开仓 (daily_pnl < -5%)
    - RED    -> 全部平仓 + 暂停 (连续 5 笔止损 或 数据源故障)
    """

    def __init__(self):
        self._state = BreakerState()
        self._total_assets: float = 0.0
        self._daily_pnl: float = 0.0
        self._daily_pnl_pct: float = 0.0
        self._consecutive_stops: int = 0
        self._consecutive_errors: int = 0
        self._benchmark_tripped: bool = False

        # 阈值
        self.yellow_threshold: float = -0.03
        self.orange_threshold: float = -0.05
        self.red_stop_count: int = 5
        self.red_error_count: int = 3

    @classmethod
    def from_config(cls, cfg: object) -> "CircuitBreaker":
        breaker = cls()
        breaker.yellow_threshold = float(cfg.get("risk.daily_loss_pct", 0.03))
        breaker.red_stop_count = int(cfg.get("risk.consecutive_stops", 5))
        breaker.red_error_count = int(cfg.get("risk.error_threshold", 3))
        return breaker

    # ──────────────────────────────────
    # 更新
    # ──────────────────────────────────

    def update_assets(self, total_assets: float, daily_pnl: float) -> None:
        self._total_assets = total_assets
        self._daily_pnl = daily_pnl
        if total_assets > 0:
            self._daily_pnl_pct = daily_pnl / (total_assets - daily_pnl) if (total_assets - daily_pnl) > 0 else 0.0

    def record_stop_loss(self) -> None:
        self._consecutive_stops += 1

    def record_trade_closed(self) -> None:
        self._consecutive_stops = 0

    def record_error(self) -> None:
        self._consecutive_errors += 1

    def clear_errors(self) -> None:
        self._consecutive_errors = 0

    # ──────────────────────────────────
    # 评估
    # ──────────────────────────────────

    def evaluate(self) -> BreakerLevel:
        if self._state.override:
            return self._state.level

        # RED 数据源异常
        if self._consecutive_errors >= self.red_error_count:
            self._set_level(BreakerLevel.RED, f"连续 {self._consecutive_errors} 次数据源异常", BreakerScope.SYSTEM)
            return BreakerLevel.RED

        # RED 基准暴跌
        if self._benchmark_tripped:
            self._set_level(BreakerLevel.RED, "基准指数暴跌", BreakerScope.MARKET)
            return BreakerLevel.RED

        # RED 连续止损
        if self._consecutive_stops >= self.red_stop_count:
            self._set_level(BreakerLevel.RED, f"连续 {self._consecutive_stops} 笔止损", BreakerScope.STRATEGY)
            return BreakerLevel.RED

        # ORANGE 日亏损 > 5%
        if self._daily_pnl_pct <= self.orange_threshold:
            self._set_level(BreakerLevel.ORANGE, f"日亏损 {self._daily_pnl_pct:.2%} > {abs(self.orange_threshold):.0%}", BreakerScope.STRATEGY)
            return BreakerLevel.ORANGE

        # YELLOW 日亏损 > 3%
        if self._daily_pnl_pct <= self.yellow_threshold:
            self._set_level(BreakerLevel.YELLOW, f"日亏损 {self._daily_pnl_pct:.2%} > {abs(self.yellow_threshold):.0%}", BreakerScope.STRATEGY)
            return BreakerLevel.YELLOW

        return BreakerLevel.GREEN

    # ──────────────────────────────────
    # 权限查询
    # ──────────────────────────────────

    def can_open_new(self) -> bool:
        level = self.evaluate()
        return level in (BreakerLevel.GREEN,)

    def can_hold(self) -> bool:
        level = self.evaluate()
        return level in (BreakerLevel.GREEN, BreakerLevel.YELLOW)

    def must_liquidate(self) -> bool:
        level = self.evaluate()
        return level in (BreakerLevel.RED,)

    # ──────────────────────────────────
    # 手动控制
    # ──────────────────────────────────

    def override(self, level: BreakerLevel, by: str = "manual") -> None:
        self._state.level = level
        self._state.override = True
        self._state.override_by = by
        self._state.triggered_at = time.time()
        self._state.reason = f"手动覆盖 [{by}] -> {level.name}"
        logger.warning("熔断手动覆盖: %s -> %s by %s", level.name, by, level.name)

    def reset_override(self) -> None:
        self._state.override = False
        self._state.override_by = ""
        logger.info("熔断覆盖已重置")

    def reset_all(self) -> None:
        self._state = BreakerState()
        self._consecutive_stops = 0
        self._consecutive_errors = 0
        self._benchmark_tripped = False
        logger.info("熔断完全重置")

    # ──────────────────────────────────
    # 基准暴跌报告
    # ──────────────────────────────────

    def report_benchmark_drop(
        self, drop_pct: float, threshold: float = 0.03
    ) -> bool:
        if abs(drop_pct) >= threshold:
            self._benchmark_tripped = True
            logger.warning("基准指数暴跌 %.2f%% >= %.2f%%", drop_pct * 100, threshold * 100)
            return True
        return False

    # ──────────────────────────────────
    # 内部方法
    # ──────────────────────────────────

    def _set_level(
        self,
        level: BreakerLevel,
        reason: str,
        scope: BreakerScope = BreakerScope.STRATEGY,
    ) -> None:
        if self._state.override:
            return
        self._state.level = level
        self._state.reason = reason
        self._state.scope = scope
        self._state.triggered_at = time.time()
        log_fn = logger.error if level == BreakerLevel.RED else logger.warning
        log_fn("熔断 %s [%s]: %s", level.name, scope.value, reason)

    # ──────────────────────────────────
    # 属性
    # ──────────────────────────────────

    @property
    def state(self) -> BreakerState:
        self._state.level = self.evaluate()
        return self._state

    @property
    def level(self) -> BreakerLevel:
        return self.evaluate()

    @property
    def summary(self) -> dict:
        return {
            "level": self._state.level.name,
            "scope": self._state.scope.value,
            "reason": self._state.reason,
            "daily_pnl_pct": round(self._daily_pnl_pct, 4),
            "consecutive_stops": self._consecutive_stops,
            "consecutive_errors": self._consecutive_errors,
            "override": self._state.override,
            "triggered_at": self._state.triggered_at,
        }
