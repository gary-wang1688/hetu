"""
河图 (HeTu) 订单管理器

OrderManager: 订单生命周期管理
- 从信号创建订单
- 状态更新 (含状态机校验)
- 批量查询
- 超时撤单
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

from core.types import Signal, Order, OrderType, OrderStatus
from execution.order import OrderStateMachine, is_active

logger = logging.getLogger("hetu.orders")


class OrderManager:
    """
    订单管理器

    内部维护订单 StateMachine，支持 DB 持久化（可选）。
    """

    def __init__(self, persist: bool = False, db_session_factory=None):
        self._orders: dict[UUID, Order] = {}
        self._channel_map: dict[str, UUID] = {}
        self._state_machines: dict[UUID, OrderStateMachine] = {}
        self._persist = persist
        self._db_factory = db_session_factory

    def create_order(
        self,
        signal: Signal,
        order_type: OrderType = OrderType.LIMIT,
        price: float = 0.0,
        shares: int = 0,
        correlation_id: UUID | None = None,
    ) -> Order:
        """从信号创建订单。"""
        order_id = uuid4()
        order = Order(
            order_id=order_id,
            signal_id=signal.signal_id,
            channel=getattr(signal, "channel", "mx"),
            code=signal.code,
            action=signal.action,
            order_type=order_type,
            price=price if price > 0 else signal.price_limit,
            shares=shares if shares > 0 else signal.shares,
            status=OrderStatus.CREATED,
            strategy_name=signal.strategy,
            correlation_id=correlation_id or uuid4(),
        )
        self._orders[order_id] = order
        self._state_machines[order_id] = OrderStateMachine(order_id)
        logger.info(f"Order created: {order_id} {order.code} {order.action.value} {order.shares}")
        if self._persist:
            self._persist_order(order)
        return order

    def update_status(
        self,
        order_id: UUID,
        new_status: OrderStatus,
        channel_order_id: str | None = None,
        error: str | None = None,
    ) -> bool:
        """更新订单状态，走状态机校验。"""
        order = self._orders.get(order_id)
        if order is None:
            logger.warning(f"Order not found: {order_id}")
            return False

        sm = self._state_machines.get(order_id)
        if sm is None:
            sm = OrderStateMachine(order_id, order.status)
            self._state_machines[order_id] = sm

        try:
            old_status = order.status
            sm.transition(new_status, note=f"ch={channel_order_id}")
            order.status = new_status
            order.updated_at = datetime.now(timezone.utc)

            if channel_order_id:
                order.channel_order_id = channel_order_id
                self._channel_map[channel_order_id] = order_id

            if error:
                order.error_msg = error

            if self._persist:
                self._update_order_db(order)

            logger.info(f"Order {order_id}: {old_status.value} -> {new_status.value}")
            return True
        except Exception as e:
            logger.error(f"Status update failed for {order_id}: {e}")
            if error:
                order.error_msg = str(e)
            return False

    def set_channel_id(self, order_id: UUID, channel_order_id: str) -> None:
        """设置通道订单 ID。"""
        if order_id in self._orders:
            self._orders[order_id].channel_order_id = channel_order_id
            self._channel_map[channel_order_id] = order_id

    def get_order(self, order_id: UUID) -> Order | None:
        """按 UUID 查询订单。"""
        return self._orders.get(order_id)

    def get_by_channel_id(self, channel_order_id: str) -> Order | None:
        """按通道订单 ID 查询。"""
        order_id = self._channel_map.get(channel_order_id)
        if order_id is None:
            return None
        return self._orders.get(order_id)

    def get_active_orders(self) -> list[Order]:
        """获取所有活跃订单。"""
        return [o for o in self._orders.values() if is_active(o.status)]

    def get_orders_by_correlation(self, correlation_id: UUID) -> list[Order]:
        """按关联 ID 查询订单。"""
        return [o for o in self._orders.values() if o.correlation_id == correlation_id]

    def get_history(self, order_id: UUID) -> list[dict]:
        """获取订单状态变更历史。"""
        sm = self._state_machines.get(order_id)
        if sm is None:
            return []
        return sm.get_history()

    def cancel_order(self, order_id: UUID) -> bool:
        """撤单。"""
        return self.update_status(order_id, OrderStatus.CANCELLED)

    def cancel_all_active(self) -> int:
        """撤销所有活跃订单。返回取消数量。"""
        count = 0
        for order_id in list(self._orders.keys()):
            order = self._orders[order_id]
            if is_active(order.status):
                if self.cancel_order(order_id):
                    count += 1
        return count

    def cancel_timeout_orders(self, max_age_seconds: int = 30) -> int:
        """撤销超时订单。返回取消数量。"""
        now = datetime.now(timezone.utc)
        count = 0
        for order_id, order in list(self._orders.items()):
            if not is_active(order.status):
                continue
            elapsed = (now - order.created_at).total_seconds()
            if elapsed > max_age_seconds:
                if self.update_status(
                    order_id,
                    OrderStatus.CANCELLED,
                    error=f"Timeout after {max_age_seconds}s",
                ):
                    count += 1
        return count

    def _persist_order(self, order: Order) -> None:
        """持久化订单到数据库。"""
        if not self._db_factory:
            return
        try:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            async def _insert():
                session = self._db_factory()
                try:
                    import sqlalchemy as sa
                    await session.execute(
                        sa.text("INSERT INTO hetu_orders (order_id, signal_id, channel, "
                                "code, action, order_type, price, shares, status, "
                                "strategy_name, correlation_id, created_at, updated_at) "
                                "VALUES (:oid, :sid, :ch, :code, :act, :ot, :pr, :sh, "
                                ":st, :sn, :cid, :ct, :ut)"),
                        {
                            "oid": str(order.order_id),
                            "sid": str(order.signal_id) if order.signal_id else None,
                            "ch": order.channel,
                            "code": order.code,
                            "act": order.action.value,
                            "ot": order.order_type.value,
                            "pr": order.price,
                            "sh": order.shares,
                            "st": order.status.value,
                            "sn": order.strategy_name,
                            "cid": str(order.correlation_id),
                            "ct": order.created_at,
                            "ut": order.updated_at,
                        },
                    )
                    await session.commit()
                finally:
                    await session.close()

            loop.run_until_complete(_insert())
        except Exception as e:
            logger.warning(f"Failed to persist order: {e}")

    def _update_order_db(self, order: Order) -> None:
        """更新数据库中的订单状态。"""
        if not self._db_factory:
            return
        try:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            async def _update():
                session = self._db_factory()
                try:
                    import sqlalchemy as sa
                    await session.execute(
                        sa.text("UPDATE hetu_orders SET status = :st, channel_order_id = :coid, "
                                "error_msg = :err, updated_at = :ut WHERE order_id = :oid"),
                        {
                            "st": order.status.value,
                            "coid": order.channel_order_id,
                            "err": order.error_msg,
                            "ut": order.updated_at,
                            "oid": str(order.order_id),
                        },
                    )
                    await session.commit()
                finally:
                    await session.close()

            loop.run_until_complete(_update())
        except Exception as e:
            logger.warning(f"Failed to update order in DB: {e}")

    @property
    def stats(self) -> dict:
        """返回订单管理器统计。"""
        return {
            "total": len(self._orders),
            "active": len(self.get_active_orders()),
            "filled": sum(1 for o in self._orders.values() if o.status == OrderStatus.FILLED),
            "cancelled": sum(1 for o in self._orders.values() if o.status == OrderStatus.CANCELLED),
            "rejected": sum(1 for o in self._orders.values() if o.status == OrderStatus.REJECTED),
        }
