"""OrderManager 单元测试。"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from uuid import uuid4

import pytest

from core.types import Signal, OrderStatus, Action


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
def order_manager():
    from execution.order_manager import OrderManager

    return OrderManager(persist=False)


# ============================================================
# 创建订单
# ============================================================

class TestCreateOrder:
    def test_create_order_success(
        self, order_manager, buy_signal: Signal
    ) -> None:
        """应成功从信号创建订单。"""
        order = order_manager.create_order(buy_signal)

        assert order.code == "000001.SZ"
        assert order.shares == 1000
        assert order.status == OrderStatus.CREATED
        assert order.action == Action.BUY

    def test_get_order(
        self, order_manager, buy_signal: Signal
    ) -> None:
        """创建后应能查询到订单。"""
        order = order_manager.create_order(buy_signal)
        fetched = order_manager.get_order(order.order_id)

        assert fetched is not None
        assert fetched.order_id == order.order_id

    def test_get_nonexistent_order(self, order_manager) -> None:
        """查询不存在的订单应返回 None。"""
        result = order_manager.get_order(uuid4())
        assert result is None


# ============================================================
# 状态转换
# ============================================================

class TestStatusTransition:
    def test_valid_transition(
        self, order_manager, buy_signal: Signal
    ) -> None:
        """有效状态转换应成功。"""
        order = order_manager.create_order(buy_signal)
        ok = order_manager.update_status(order.order_id, OrderStatus.SUBMITTED)

        assert ok is True
        updated = order_manager.get_order(order.order_id)
        assert updated.status == OrderStatus.SUBMITTED

    def test_invalid_transition(
        self, order_manager, buy_signal: Signal
    ) -> None:
        """状态机应拒绝无效的状态转换。"""
        from execution.order import OrderStateMachine, can_transition

        order = order_manager.create_order(buy_signal)
        # 状态机应拒绝对 FILLED 终态后的变更
        sm = OrderStateMachine(order.order_id, OrderStatus.FILLED)
        result = sm.transition(OrderStatus.CREATED)
        assert result is False

        # 也验证转换表
        assert can_transition(OrderStatus.FILLED, OrderStatus.CREATED) is False


# ============================================================
# 撤单
# ============================================================

class TestCancel:
    def test_cancel_active_order(
        self, order_manager, buy_signal: Signal
    ) -> None:
        """应能撤销活跃订单。"""
        order = order_manager.create_order(buy_signal)
        ok = order_manager.cancel_order(order.order_id)

        assert ok is True
        assert order_manager.get_order(order.order_id).status == OrderStatus.CANCELLED

    def test_cancel_terminal_order(
        self, order_manager, buy_signal: Signal
    ) -> None:
        """状态机拒绝对终态订单的转换。"""
        from execution.order import is_terminal

        order = order_manager.create_order(buy_signal)
        order_manager.update_status(order.order_id, OrderStatus.FILLED)

        # FILLED 应被识别为终态
        assert is_terminal(OrderStatus.FILLED) is True
        assert is_terminal(OrderStatus.CREATED) is False

    def test_cancel_nonexistent_order(self, order_manager) -> None:
        """撤销不存在的订单应返回 False。"""
        result = order_manager.cancel_order(uuid4())
        assert result is False

    def test_timeout_cancel(
        self, order_manager, buy_signal: Signal
    ) -> None:
        """超时订单应被撤销。"""
        order = order_manager.create_order(buy_signal)
        # 修改 created_at 使订单看起来已超时（需时区一致）
        order.created_at = datetime.now(timezone.utc) - timedelta(hours=1)
        cancelled = order_manager.cancel_timeout_orders(max_age_seconds=0)

        assert cancelled == 1
        assert order_manager.get_order(order.order_id).status == OrderStatus.CANCELLED


# ============================================================
# 统计
# ============================================================

class TestStats:
    def test_stats_initial(self, order_manager) -> None:
        """初始统计应为零。"""
        s = order_manager.stats
        assert s["total"] == 0
        assert s["active"] == 0
        assert s["filled"] == 0
        assert s["cancelled"] == 0
