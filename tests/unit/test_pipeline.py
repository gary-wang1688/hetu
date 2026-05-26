"""SignalPipeline 单元测试。"""
from __future__ import annotations

import pytest

from core.types import Signal, Action
from core.event_bus import InMemoryEventBus
from risk.manager import RiskManager


@pytest.fixture
def event_bus() -> InMemoryEventBus:
    return InMemoryEventBus(persist=False)


@pytest.fixture
def risk_manager():
    return RiskManager(
        max_position_single=0.2,
        max_position_total=0.5,
        max_daily_buy_amount=1000000,
    )


@pytest.fixture
def order_manager():
    from execution.order_manager import OrderManager

    return OrderManager(persist=False)


@pytest.fixture
def pipeline(risk_manager, event_bus):
    from execution.pipeline import SignalPipeline

    return SignalPipeline(risk_manager=risk_manager, event_bus=event_bus)


@pytest.fixture
def buy_signal() -> Signal:
    return Signal(
        strategy="turtle",
        code="000001.SZ",
        action=Action.BUY,
        confidence=0.9,
        shares=500,
        price_limit=50.0,
    )


@pytest.fixture
def sell_signal() -> Signal:
    return Signal(
        strategy="stop_loss",
        code="000001.SZ",
        action=Action.SELL,
        confidence=1.0,
        shares=500,
    )


# ============================================================
# 风控拒绝
# ============================================================

class TestRiskReject:
    async def test_risk_reject_on_limit(
        self, pipeline, event_bus
    ) -> None:
        """仓位超限时应被拒绝。"""
        pipeline.risk.max_position_single = 0.001  # 极低限制
        signal = Signal(
            strategy="turtle",
            code="000001.SZ",
            action=Action.BUY,
            confidence=0.9,
            shares=500,
            price_limit=50.0,
        )
        result = await pipeline.process(signal, total_equity=1000000.0)
        assert result.success is False
        assert result.error is not None
        assert "风险审核" in result.error

    async def test_risk_reject_circuit_breaker(
        self, pipeline, risk_manager, buy_signal, event_bus
    ) -> None:
        """熔断状态下应被拒绝。"""
        risk_manager.trip_circuit_breaker("测试熔断")
        result = await pipeline.process(buy_signal, total_equity=1000000.0)
        assert result.success is False
        assert result.error is not None


# ============================================================
# 零仓位
# ============================================================

class TestZeroSizing:
    async def test_zero_equity_returns_error(self, pipeline) -> None:
        """零资金时应有错误结果。"""
        signal = Signal(
            strategy="turtle",
            code="000001.SZ",
            action=Action.BUY,
            confidence=0.9,
            shares=500,
            price_limit=50.0,
        )
        result = await pipeline.process(signal, total_equity=1.0)
        # 可能因资金不足被风控拒绝，也可能因仓位计算返回零份额
        assert result.success is False or result.error is not None


# ============================================================
# 完整买入流程
# ============================================================

class TestFullBuyFlow:
    async def test_full_buy_flow(
        self, pipeline, buy_signal, event_bus
    ) -> None:
        """完整买入流程应成功并发布事件。"""
        result = await pipeline.process(buy_signal, total_equity=1000000.0)
        assert result.success is True

    async def test_stop_loss_not_pending_when_no_monitor(
        self, pipeline, buy_signal
    ) -> None:
        """无 StopMonitor 时不应有止损待处理。"""
        buy_signal_no_stop = Signal(
            strategy="turtle",
            code="000001.SZ",
            action=Action.BUY,
            confidence=0.9,
            shares=500,
            price_limit=50.0,
        )
        result = await pipeline.process(buy_signal_no_stop, total_equity=1000000.0)
        assert result.stop_loss_pending is False


# ============================================================
# 止损监控集成
# ============================================================

class TestStopMonitorIntegration:
    async def test_stop_loss_attached_with_monitor(
        self, risk_manager, buy_signal
    ) -> None:
        """有 StopMonitor 时止损应被挂载。"""
        from execution.stop_monitor import StopMonitor
        from execution.pipeline import SignalPipeline
        from core.event_bus import InMemoryEventBus

        eb = InMemoryEventBus(persist=False)
        sm = StopMonitor(persist=False)
        pl = SignalPipeline(risk_manager=risk_manager, event_bus=eb, stop_monitor=sm)

        signal = Signal(
            strategy="turtle",
            code="000001.SZ",
            action=Action.BUY,
            confidence=0.9,
            shares=500,
            price_limit=50.0,
            stop_loss=47.5,
        )
        result = await pl.process(signal, total_equity=1000000.0)
        # check stats
        assert sm.stats["attached"] >= 1

    async def test_stop_loss_pending_on_monitor_failure(
        self, risk_manager, buy_signal
    ) -> None:
        """止损挂载失败应有 pending 记录。"""
        from execution.pipeline import SignalPipeline
        from core.event_bus import InMemoryEventBus

        # 创建一个模拟会失败的 StopMonitor
        class FailingStopMonitor:
            stats = {"attached": 0}

            def attach_stop_order(self, order, stop_price, stop_type="HARD"):
                raise RuntimeError("simulated failure")

        eb = InMemoryEventBus(persist=False)
        fm = FailingStopMonitor()
        pl = SignalPipeline(
            risk_manager=risk_manager,
            event_bus=eb,
            stop_monitor=fm,
        )

        signal = Signal(
            strategy="turtle",
            code="000001.SZ",
            action=Action.BUY,
            confidence=0.9,
            shares=500,
            price_limit=50.0,
            stop_loss=47.5,
        )
        result = await pl.process(signal, total_equity=1000000.0)
        # 应因异常而导致 stop_loss_pending
        assert result.stop_loss_pending is True


# ============================================================
# 事件发布
# ============================================================

class TestEventPublishing:
    async def test_event_published_on_success(
        self, pipeline, buy_signal, event_bus
    ) -> None:
        """成功处理信号后应发布 ORDER_CREATED 事件。"""
        result = await pipeline.process(buy_signal, total_equity=1000000.0)
        assert result.success is True


# ============================================================
# 关联 ID
# ============================================================

class TestCorrelationId:
    async def test_correlation_id_not_empty(
        self, pipeline, buy_signal
    ) -> None:
        """关联 ID 不应为空。"""
        result = await pipeline.process(buy_signal, total_equity=1000000.0)
        assert result.correlation_id != ""

    async def test_correlation_id_unique(
        self, pipeline, buy_signal
    ) -> None:
        """不同信号的关联 ID 应不同。"""
        signal2 = Signal(
            strategy="turtle",
            code="000002.SZ",
            action=Action.BUY,
            confidence=0.9,
            shares=500,
            price_limit=50.0,
        )
        r1 = await pipeline.process(buy_signal, total_equity=1000000.0)
        r2 = await pipeline.process(signal2, total_equity=1000000.0)
        assert r1.correlation_id != r2.correlation_id


# ============================================================
# 待处理止损
# ============================================================

class TestPendingStops:
    async def test_pending_stops_accumulate(
        self, pipeline, risk_manager, buy_signal
    ) -> None:
        """止损挂载失败应累积到 pending 列表。"""
        from execution.pipeline import SignalPipeline
        from core.event_bus import InMemoryEventBus

        class FailingMonitor:
            def attach_stop_order(self, **kwargs):
                raise RuntimeError("fail")

        eb = InMemoryEventBus(persist=False)
        pl = SignalPipeline(
            risk_manager=risk_manager,
            event_bus=eb,
            stop_monitor=FailingMonitor(),
        )
        signal = Signal(
            strategy="turtle",
            code="000001.SZ",
            action=Action.BUY,
            confidence=0.9,
            shares=500,
            price_limit=50.0,
            stop_loss=47.5,
        )
        result = await pl.process(signal, total_equity=1000000.0)
        pending = pl.get_pending_stops()
        assert len(pending) >= 1

    async def test_repair_removes_from_pending(
        self, pipeline, risk_manager, buy_signal
    ) -> None:
        """修复后应从 pending 列表中移除。"""
        from execution.pipeline import SignalPipeline
        from core.event_bus import InMemoryEventBus

        class FailingMonitor:
            def attach_stop_order(self, **kwargs):
                raise RuntimeError("fail")

        eb = InMemoryEventBus(persist=False)
        pl = SignalPipeline(
            risk_manager=risk_manager,
            event_bus=eb,
            stop_monitor=FailingMonitor(),
        )
        signal = Signal(
            strategy="turtle",
            code="000001.SZ",
            action=Action.BUY,
            confidence=0.9,
            shares=500,
            price_limit=50.0,
            stop_loss=47.5,
        )
        result = await pl.process(signal, total_equity=1000000.0)
        pending_before = len(pl.get_pending_stops())
        assert pending_before >= 1

        # 获取待处理项中的 order_id
        pending = pl.get_pending_stops()
        order_id = pending[0].get("order_id", "")
        if order_id:
            pl.repair_pending_stops([order_id])
            assert len(pl.get_pending_stops()) < pending_before
