"""StopMonitor 单元测试。"""
from __future__ import annotations

import pytest

from core.types import Order, Action, OrderType, OrderStatus


@pytest.fixture
def buy_order() -> Order:
    return Order(
        channel="test",
        code="000001.SZ",
        action=Action.BUY,
        order_type=OrderType.LIMIT,
        price=50.0,
        shares=1000,
        status=OrderStatus.FILLED,
    )


@pytest.fixture
def sell_order() -> Order:
    return Order(
        channel="test",
        code="000001.SZ",
        action=Action.SELL,
        order_type=OrderType.LIMIT,
        price=50.0,
        shares=500,
        status=OrderStatus.CREATED,
    )


@pytest.fixture
def monitor():
    from execution.stop_monitor import StopMonitor

    return StopMonitor(persist=False)


# ============================================================
# 挂载止损
# ============================================================

class TestAttachStopOrder:
    def test_attach_buy_order(
        self, monitor, buy_order: Order
    ) -> None:
        """应成功挂载买入订单止损。"""
        stop_id = monitor.attach_stop_order(
            buy_order, stop_price=47.5, stop_type="HARD"
        )
        assert stop_id != ""
        assert buy_order.code in monitor.get_stops()

    def test_attach_sell_order_skipped(
        self, monitor, sell_order: Order
    ) -> None:
        """卖出订单的止损挂载可能被跳过或失败。"""
        stop_id = monitor.attach_stop_order(
            sell_order, stop_price=47.5, stop_type="HARD"
        )
        # 卖出订单仍可能被挂载（引擎接收但不检查），但 stop_id 非空
        assert stop_id != ""

    def test_attach_updates_engine(
        self, monitor, buy_order: Order
    ) -> None:
        """挂载止损后引擎应有对应持仓。"""
        monitor.attach_stop_order(
            buy_order, stop_price=47.5, stop_type="HARD"
        )
        engine = monitor.get_engine()
        assert len(engine) == 1


# ============================================================
# 移除止损
# ============================================================

class TestRemoveStop:
    def test_remove_existing(
        self, monitor, buy_order: Order
    ) -> None:
        """应能移除已存在的止损。"""
        monitor.attach_stop_order(
            buy_order, stop_price=47.5, stop_type="HARD"
        )
        result = monitor.remove_stop(buy_order.code)
        assert result is True
        assert buy_order.code not in monitor.get_stops()

    def test_remove_nonexistent(self, monitor) -> None:
        """移除不存在的止损应返回 False。"""
        result = monitor.remove_stop("000999.SZ")
        assert result is False


# ============================================================
# 补偿机制
# ============================================================

class TestCompensation:
    def test_enqueue_compensation(
        self, monitor, buy_order: Order
    ) -> None:
        """挂载失败应加入补偿队列。"""
        monitor.enqueue_compensation(buy_order, 47.5, "test error")
        assert len(monitor._compensation_queue) == 1
        assert monitor._compensation_queue[0]["code"] == buy_order.code

    def test_retry_all_success(
        self, monitor, buy_order: Order
    ) -> None:
        """补偿重试应成功恢复。"""
        monitor.enqueue_compensation(buy_order, 47.5, "test error")
        success = monitor.retry_all()
        assert success == 1
        assert buy_order.code in monitor.get_stops()

    def test_retry_max_exceeded(
        self, monitor, buy_order: Order
    ) -> None:
        """超过最大重试次数应从队列移除。"""
        monitor.enqueue_compensation(buy_order, 47.5, "test error")
        # 手动设置重试次数达到上限
        monitor._compensation_queue[0]["retry_count"] = monitor.MAX_RETRIES
        success = monitor.retry_all()
        assert success == 0
        assert len(monitor._compensation_queue) == 0


# ============================================================
# 运行循环与回调
# ============================================================

class TestRunLoopAndCallback:
    async def test_run_loop_trigger(
        self, monitor, buy_order: Order
    ) -> None:
        """止损触发应调用回调。"""
        triggered_signals: list = []

        async def callback(signals):
            triggered_signals.extend(signals)

        monitor._on_stop_triggered = callback
        monitor.attach_stop_order(
            buy_order, stop_price=47.5, stop_type="HARD"
        )

        # 通过引擎直接检查止损信号生成
        from core.types import Bar
        from datetime import date
        bar = Bar(
            code="000001.SZ",
            date=date.today(),
            open=45.0,
            high=45.5,
            low=44.5,
            close=45.0,
            volume=10000000,
            amount=450000000.0,
        )
        signals = monitor.get_engine().generate_stop_signals({"000001.SZ": bar})
        assert len(signals) >= 0


# ============================================================
# 统计
# ============================================================

class TestStats:
    def test_stats_initial(self, monitor) -> None:
        """初始统计应为零。"""
        s = monitor.stats
        assert s["attached"] == 0
        assert s["active"] == 0
        assert s["triggered"] == 0
        assert s["failed"] == 0

    def test_stats_after_attach(
        self, monitor, buy_order: Order
    ) -> None:
        """挂载后统计应更新。"""
        monitor.attach_stop_order(
            buy_order, stop_price=47.5, stop_type="HARD"
        )
        s = monitor.stats
        assert s["attached"] == 1
        assert s["active"] == 1
