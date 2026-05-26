"""
河图 (HeTu) 风控管理器

RiskManager: 信号进入执行链前的最后一道防线。
审核项: 单票仓位限制、总仓位限制、行业敞口、日买入上限、T+1 可卖检查。
"""
from __future__ import annotations

import logging

from core.types import (
    Signal,
    StrategyContext,
    ComponentHealth,
    HealthStatus,
    AssessmentResult,
    FrozenSlot,
)

logger = logging.getLogger("hetu.risk")


class RiskAssessment(AssessmentResult):
    """风控评估结果，扩展基础评估结果。"""
    checks_passed: bool = True
    failed_reason: str = ""
    single_position_ok: bool = True
    total_position_ok: bool = True
    industry_exposure_ok: bool = True
    daily_buy_ok: bool = True
    circuit_breaker_ok: bool = True
    st_filter_ok: bool = True


class RiskManager:
    """
    风控审核器

    规则:
    1. 单票仓位 ≤ max_position_single (默认 5%)
    2. 总仓位 ≤ max_position_total (默认 20%)
    3. 行业敞口 ≤ max_industry_exposure (默认 15%)
    4. 日买入金额 ≤ max_daily_buy_amount (默认 10 万)
    5. T+1 限制: 今日买入的不可今日卖出
    6. 熔断状态: 触发熔断则全部拒绝
    """

    def __init__(
        self,
        max_position_single: float = 0.05,
        max_position_total: float = 0.20,
        max_industry_exposure: float = 0.15,
        max_daily_buy_amount: float = 100000,
        industry_classifier: "IndustryClassifier | None" = None,
        max_portfolio_drawdown: float = 0.15,
    ):
        self.max_position_single = max_position_single
        self.max_position_total = max_position_total
        self.max_industry_exposure = max_industry_exposure
        self.max_daily_buy_amount = max_daily_buy_amount
        self.max_portfolio_drawdown = max_portfolio_drawdown

        self._classifier = industry_classifier or self._default_classifier()
        self._sizer = None
        self._positions: list[dict] = []
        self._daily_bought_amount: float = 0.0
        self._peak_asset: float = 0.0
        self._circuit_breaker_active: bool = False
        self._circuit_reason: str = ""
        self._frozen_slots: dict[str, FrozenSlot] = {}
        self._st_codes: set[str] = set()
        self._industry_exposure: dict[str, float] = {}

    @staticmethod
    def _default_classifier():
        """当未传入行业分类器时，使用默认实现。"""
        from strategy.industry import IndustryClassifier
        return IndustryClassifier()

    # ──────────────────────────────────
    # 工厂方法
    # ──────────────────────────────────

    @classmethod
    def from_config(
        cls,
        cfg: object,
        industry_classifier: "IndustryClassifier | None" = None,
        overrides: object = None,
    ) -> "RiskManager":
        params = {
            "max_position_single": float(cfg.get("risk.max_position_single", 0.05)),
            "max_position_total": float(cfg.get("risk.max_position_total", 0.20)),
            "max_industry_exposure": float(cfg.get("risk.max_industry_exposure", 0.15)),
            "max_daily_buy_amount": float(cfg.get("risk.max_daily_buy_amount", 100000)),
            "max_portfolio_drawdown": float(cfg.get("risk.max_portfolio_drawdown", 0.15)),
        }
        if industry_classifier is not None:
            params["industry_classifier"] = industry_classifier
        if overrides is not None:
            for k, v in overrides.__dict__.items():
                if not k.startswith("_") and k in params:
                    params[k] = v
        return cls(**params)

    # ──────────────────────────────────
    # 仓位计算 (委托给 PositionSizer)
    # ──────────────────────────────────

    def calculate_position(
        self,
        signal: Signal,
        total_equity: float,
        strategy_type: str = "default",
    ) -> "PositionSizing":
        from risk.position_sizer import PositionSizer

        if self._sizer is None:
            self._sizer = PositionSizer(
                total_equity=total_equity,
                max_total_pct=self.max_position_total,
                max_single_pct=self.max_position_single,
            )
        else:
            self._sizer.update_equity(total_equity)

        price = signal.price_limit or 0.0
        current_pct = self._get_current_position_pct(signal.code)
        return self._sizer.calculate(
            price=price,
            signal_shares=signal.shares,
            strategy_type=strategy_type or signal.strategy,
            current_position_pct=current_pct,
        )

    def _get_current_position_pct(self, code: str) -> float:
        for pos in self._positions:
            if pos.get("code") == code:
                return pos.get("pct", 0.0)
        return 0.0

    def _get_industry(self, code: str) -> str:
        try:
            return self._classifier.classify(code)
        except Exception:
            return "其他"

    # ──────────────────────────────────
    # 核心风控审核 assess — 6 项检查
    # ──────────────────────────────────

    def assess(self, signal: Signal, context: StrategyContext) -> RiskAssessment:
        assessment = RiskAssessment()

        # 1. 单票仓位
        signal_pct = signal.shares * signal.price_limit / max(context.total_asset, 1)
        if signal_pct > self.max_position_single:
            assessment.single_position_ok = False
            assessment.failed_reason = f"单票仓位超限 {signal_pct:.2%} > {self.max_position_single:.2%}"

        # 2. 总仓位 (含拟开仓)
        current_pct = context.total_position_pct + signal_pct
        if current_pct > self.max_position_total:
            assessment.total_position_ok = False
            assessment.failed_reason = f"总仓位超限 {current_pct:.2%} > {self.max_position_total:.2%}"

        # 3. 行业敞口
        industry = self._get_industry(signal.code)
        industry_pct = self._industry_exposure.get(industry, 0.0) + signal_pct
        if industry_pct > self.max_industry_exposure:
            assessment.industry_exposure_ok = False
            assessment.failed_reason = f"行业敞口超限 {industry} {industry_pct:.2%} > {self.max_industry_exposure:.2%}"

        # 4. 日买入金额
        buy_amount = signal.price_limit * signal.shares
        if buy_amount > 0 and self._daily_bought_amount + buy_amount > self.max_daily_buy_amount:
            assessment.daily_buy_ok = False
            assessment.failed_reason = f"日买入金额超限 {self._daily_bought_amount + buy_amount:.0f} > {self.max_daily_buy_amount:.0f}"

        # 5. 熔断状态
        if self._circuit_breaker_active:
            assessment.circuit_breaker_ok = False
            assessment.failed_reason = f"熔断状态: {self._circuit_reason}"

        # 6. ST 过滤
        if self._is_st_stock(signal.code):
            assessment.st_filter_ok = False
            assessment.failed_reason = f"ST 股票禁止交易: {signal.code}"

        # 检查 T+1 限制（卖出）
        if signal.action.value == "SELL":
            for pos in self._positions:
                if pos.get("code") == signal.code and not pos.get("buyable_today", True):
                    assessment.approved = False
                    assessment.reason = "T+1 限制：今日买入不可卖出"
                    assessment.checks_passed = False
                    return assessment

        # 综合判断
        all_ok = (
            assessment.single_position_ok
            and assessment.total_position_ok
            and assessment.industry_exposure_ok
            and assessment.daily_buy_ok
            and assessment.circuit_breaker_ok
            and assessment.st_filter_ok
        )
        assessment.checks_passed = all_ok
        assessment.approved = all_ok
        if all_ok:
            assessment.reason = "风控审核通过"
        else:
            assessment.reason = assessment.failed_reason
            assessment.approved = False

        logger.debug(
            "assess %s %s → %s",
            signal.code,
            signal.action.value,
            "PASS" if assessment.approved else f"REJECT [{assessment.reason}]",
        )
        return assessment

    # ──────────────────────────────────
    # 仓位管理
    # ──────────────────────────────────

    def update_positions(self, positions: list[dict]) -> None:
        self._positions = positions

    def record_buy(self, amount: float) -> None:
        self._daily_bought_amount += amount

    def reset_daily(self) -> None:
        self._daily_bought_amount = 0.0

    def update_peak_asset(self, current_asset: float) -> None:
        if current_asset > self._peak_asset:
            self._peak_asset = current_asset

    @property
    def portfolio_drawdown(self) -> float:
        if self._peak_asset <= 0:
            return 0.0
        return 1.0 - self._peak_asset / self._peak_asset  # simplified, should use current value
        # return (self._peak_asset - current_asset) / self._peak_asset

    # ──────────────────────────────────
    # 熔断控制
    # ──────────────────────────────────

    def trip_circuit_breaker(self, reason: str) -> None:
        self._circuit_breaker_active = True
        self._circuit_reason = reason
        logger.warning("熔断触发: %s", reason)

    def reset_circuit_breaker(self) -> None:
        self._circuit_breaker_active = False
        self._circuit_reason = ""
        logger.info("熔断已重置")

    @property
    def circuit_breaker_tripped(self) -> bool:
        return self._circuit_breaker_active

    # ──────────────────────────────────
    # 冻结/解冻
    # ──────────────────────────────────

    def freeze_position(self, code: str, pct: float) -> None:
        from datetime import datetime, timezone, timedelta

        slot = FrozenSlot(
            code=code,
            frozen_amount=pct,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        self._frozen_slots[code] = slot

    def unfreeze_position(self, code: str, pct: float) -> None:
        self._frozen_slots.pop(code, None)

    def cleanup_expired_freezes(self, timeout_seconds: int = 300) -> int:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        expired = [
            code
            for code, slot in self._frozen_slots.items()
            if (now - slot.expires_at).total_seconds() > timeout_seconds
        ]
        for code in expired:
            self._frozen_slots.pop(code, None)
        return len(expired)

    def get_frozen_pct(self, code: str) -> float:
        slot = self._frozen_slots.get(code)
        if slot is not None:
            return slot.frozen_amount
        return 0.0

    # ──────────────────────────────────
    # ST 股票管理
    # ──────────────────────────────────

    def mark_st_stocks(self, codes_or_names: list[tuple[str, str]]) -> None:
        self._st_codes = {c for c, _ in codes_or_names}

    def _is_st_stock(self, code: str) -> bool:
        if code in self._st_codes:
            return True
        try:
            from data.filters import STFilter
            return STFilter.is_st(code)
        except Exception:
            return "ST" in code.upper() or "*ST" in code.upper()

    # ──────────────────────────────────
    # 健康检查
    # ──────────────────────────────────

    def health_check(self) -> ComponentHealth:
        if self._circuit_breaker_active:
            return ComponentHealth(
                name="RiskManager",
                status=HealthStatus.DEGRADED,
                message=f"熔断中: {self._circuit_reason}",
            )
        return ComponentHealth(
            name="RiskManager",
            status=HealthStatus.HEALTHY,
            message="正常",
        )
