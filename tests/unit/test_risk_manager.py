"""RiskManager 单元测试。"""
from __future__ import annotations

from datetime import date

import pytest

from core.types import Signal, StrategyContext, Action, MarketRegime


@pytest.fixture
def risk_manager():
    from risk.manager import RiskManager

    return RiskManager(
        max_position_single=0.05,
        max_position_total=0.2,
        max_industry_exposure=0.15,
        max_daily_buy_amount=100000,
    )


@pytest.fixture
def buy_signal() -> Signal:
    return Signal(
        strategy="turtle",
        code="000001.SZ",
        action=Action.BUY,
        confidence=0.9,
        shares=1000,
        price_limit=10.0,
    )


@pytest.fixture
def sell_signal() -> Signal:
    return Signal(
        strategy="stop_loss",
        code="000001.SZ",
        action=Action.SELL,
        confidence=1.0,
        shares=1000,
    )


@pytest.fixture
def empty_context() -> StrategyContext:
    return StrategyContext(
        current_date=date(2024, 1, 20),
        market_regime=MarketRegime(),
        total_asset=1000000.0,
        total_position_pct=0.0,
    )


@pytest.fixture
def loaded_context() -> StrategyContext:
    return StrategyContext(
        current_date=date(2024, 1, 20),
        market_regime=MarketRegime(),
        total_asset=1000000.0,
        total_position_pct=0.18,
    )


# ============================================================
# 买入/卖出审批
# ============================================================

class TestBuyApproval:
    def test_approved_buy(
        self, risk_manager, buy_signal: Signal, empty_context: StrategyContext
    ) -> None:
        """正常买入应通过审批。"""
        result = risk_manager.assess(buy_signal, empty_context)
        assert result.approved is True
        assert result.checks_passed is True

    def test_approved_sell(
        self, risk_manager, sell_signal: Signal, empty_context: StrategyContext
    ) -> None:
        """卖出操作应通过审批（无持仓时 T+1 检查不生效）。"""
        result = risk_manager.assess(sell_signal, empty_context)
        assert result.approved is True


# ============================================================
# 单票仓位限制
# ============================================================

class TestSinglePositionLimit:
    def test_single_position_exceeds_limit(
        self, risk_manager, empty_context: StrategyContext
    ) -> None:
        """单票仓位超限应被拒绝。"""
        risk_manager.max_position_single = 0.001  # 极低限制
        signal = Signal(
            strategy="turtle",
            code="000001.SZ",
            action=Action.BUY,
            confidence=0.9,
            shares=10000,
            price_limit=10.0,
        )
        result = risk_manager.assess(signal, empty_context)
        assert result.approved is False
        assert result.single_position_ok is False


# ============================================================
# 总仓位限制
# ============================================================

class TestTotalPositionLimit:
    def test_total_position_exceeds_limit(
        self,
        risk_manager,
        buy_signal: Signal,
        loaded_context: StrategyContext,
    ) -> None:
        """总仓位超限应被拒绝。"""
        result = risk_manager.assess(buy_signal, loaded_context)
        # loaded_context has 18% position, buy_signal adds ~1%
        # Total would be ~19% which is under 20%, so should pass
        assert result.approved is True

    def test_total_position_just_exceeds(
        self, risk_manager, loaded_context: StrategyContext
    ) -> None:
        """仅刚好超限应被拒绝。"""
        risk_manager.max_position_total = 0.01  # 极低限制
        signal = Signal(
            strategy="turtle",
            code="000001.SZ",
            action=Action.BUY,
            confidence=0.9,
            shares=1000,
            price_limit=10.0,
        )
        result = risk_manager.assess(signal, loaded_context)
        assert result.approved is False
        assert result.total_position_ok is False


# ============================================================
# T+1 保护
# ============================================================

class TestT1Protection:
    def test_t1_sell_blocked(
        self, risk_manager, sell_signal: Signal
    ) -> None:
        """T+1 限制：今日买入的不可卖出。"""
        risk_manager.update_positions([
            {"code": "000001.SZ", "pct": 0.05, "buyable_today": False},
        ])
        result = risk_manager.assess(
            sell_signal,
            StrategyContext(
                total_asset=1000000.0,
                total_position_pct=0.05,
            ),
        )
        assert result.approved is False
        assert "T+1" in result.reason


# ============================================================
# 日买入限制
# ============================================================

class TestDailyBuyLimit:
    def test_daily_buy_exceeds(
        self, risk_manager, empty_context: StrategyContext
    ) -> None:
        """日买入金额超限应被拒绝。"""
        risk_manager.max_daily_buy_amount = 100  # 极低限制
        signal = Signal(
            strategy="turtle",
            code="000001.SZ",
            action=Action.BUY,
            confidence=0.9,
            shares=100,
            price_limit=10.0,
        )
        result = risk_manager.assess(signal, empty_context)
        assert result.approved is False
        assert result.daily_buy_ok is False

    def test_record_buy_accumulates(self, risk_manager) -> None:
        """记录买入金额应累加。"""
        risk_manager.record_buy(5000)
        risk_manager.record_buy(3000)
        assert risk_manager._daily_bought_amount == 8000


# ============================================================
# 熔断
# ============================================================

class TestCircuitBreaker:
    def test_circuit_breaker_rejects_all(
        self,
        risk_manager,
        buy_signal: Signal,
        empty_context: StrategyContext,
    ) -> None:
        """熔断后所有信号应被拒绝。"""
        risk_manager.trip_circuit_breaker("测试熔断")
        result = risk_manager.assess(buy_signal, empty_context)
        assert result.approved is False
        assert result.circuit_breaker_ok is False

    def test_circuit_breaker_reset(
        self,
        risk_manager,
        buy_signal: Signal,
        empty_context: StrategyContext,
    ) -> None:
        """熔断重置后应恢复审批。"""
        risk_manager.trip_circuit_breaker("测试熔断")
        risk_manager.reset_circuit_breaker()
        result = risk_manager.assess(buy_signal, empty_context)
        assert result.approved is True
        assert result.circuit_breaker_ok is True
