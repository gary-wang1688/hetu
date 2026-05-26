"""
河图 (HeTu) 订单状态机

状态转换:
    CREATED → SUBMITTED → PARTIAL → FILLED
                  ↓           ↓
               REJECTED    CANCELLED
"""

from __future__ import annotations

from typing import Any

from core.types import OrderStatus

# ---------------------------------------------------------------
# 状态转换表
# ---------------------------------------------------------------
TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.CREATED:   {OrderStatus.SUBMITTED, OrderStatus.REJECTED, OrderStatus.CANCELLED},
    OrderStatus.SUBMITTED: {OrderStatus.PARTIAL, OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED},
    OrderStatus.PARTIAL:   {OrderStatus.PARTIAL, OrderStatus.FILLED, OrderStatus.CANCELLED},
    OrderStatus.FILLED:    set(),
    OrderStatus.CANCELLED: set(),
    OrderStatus.REJECTED:  set(),
}


def can_transition(from_status: OrderStatus, to_status: OrderStatus) -> bool:
    """判断状态转换是否合法。"""
    return to_status in TRANSITIONS.get(from_status, set())


def is_terminal(status: OrderStatus) -> bool:
    """是否为终态（FILLED / CANCELLED / REJECTED）。"""
    return status in {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED}


def is_active(status: OrderStatus) -> bool:
    """是否为活跃状态（CREATED / SUBMITTED / PARTIAL）。"""
    return status in {OrderStatus.CREATED, OrderStatus.SUBMITTED, OrderStatus.PARTIAL}


# ---------------------------------------------------------------
# 订单状态机
# ---------------------------------------------------------------
class OrderStateMachine:
    """
    订单状态机

    管理单个订单的状态变更，确保:
    - 转换合法性
    - 终态不可变
    - 变更审计日志
    """

    def __init__(self, order_id: Any, initial_status: OrderStatus = OrderStatus.CREATED):
        self.order_id = order_id
        self._status: OrderStatus = initial_status
        self._history: list[dict] = []

    @property
    def status(self) -> OrderStatus:
        return self._status

    def transition(self, new_status: OrderStatus | str, note: str = "") -> bool:
        if isinstance(new_status, str):
            try:
                new_status = OrderStatus(new_status)
            except ValueError:
                return False
        if not can_transition(self._status, new_status):
            return False
        if is_terminal(self._status):
            return False
        old = self._status
        self._status = new_status
        self._history.append({
            "from": old.value,
            "to": new_status.value,
            "note": note,
        })
        return True

    def get_history(self) -> list[dict]:
        return list(self._history)

    def __repr__(self) -> str:
        return f"OrderStateMachine({self.order_id}, {self._status.value})"
