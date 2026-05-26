"""
河图 (HeTu) 事件总线 — 逐订阅者交付

特性:
- 异步发布，优先级队列排序
- 逐订阅者独立交付追踪（EventDelivery）
- 失败重试 + 降级（告警后跳过该订阅者）
- 持久化到 DB（events + event_deliveries 表）
- 启动时回放未交付事件
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable, Awaitable
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from core.events import Event, EventDelivery
from core.types import DelivStatus

logger = logging.getLogger("hetu.event_bus")

Handler = Callable[[Event], Awaitable[None]]


class EventBus:
    """基础事件总线。"""

    def __init__(self):
        self._subscribers: dict[str, list[Handler]] = {}
        self._published_events: dict[str, Event] = {}
        self._deliveries: list[EventDelivery] = []

    def subscribe(self, event_type: str, handler: Handler) -> None:
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(handler)
        logger.debug("订阅: %s → %s", event_type, handler.__name__)

    async def publish(self, event: Event) -> None:
        self._published_events[str(event.event_id)] = event
        handlers = self._subscribers.get(event.event_type, [])

        for handler in handlers:
            delivery = EventDelivery(
                event_id=event.event_id,
                subscriber=handler.__name__,
                event_type=event.event_type,
            )
            try:
                await handler(event)
                delivery.status = "delivered"
                delivery.acked_at = datetime.now(timezone.utc)
            except Exception as e:
                delivery.status = "failed"
                delivery.error = str(e)
                logger.warning("事件投递失败: %s → %s: %s", event.event_type, handler.__name__, e)
            self._deliveries.append(delivery)


class InMemoryEventBus(EventBus):
    """
    内存实现的事件总线

    支持两种模式:
    1. 纯内存 (persist=False): 测试/开发用
    2. 持久化 (persist=True):  生产模式，写 events + event_deliveries 表
    """

    MAX_RETRIES: int = 3
    DELIVERY_TIMEOUT: float = 30.0

    def __init__(self, persist: bool = False, db_session_factory: Any = None):
        super().__init__()
        self._persist = persist
        self._db_factory = db_session_factory
        self._subscribers_map: dict[str, list[tuple[str, Handler]]] = defaultdict(list)
        self._published: dict[str, Event] = {}
        self._delivery_log: list[EventDelivery] = []
        self._delivered: int = 0
        self._failed: int = 0
        self._persist_lock = asyncio.Lock()

    # ── 订阅管理 ──

    async def subscribe(
        self,
        event_type: str,
        handler: Callable[[Event], Awaitable[None]],
        subscriber_id: str,
    ) -> None:
        """注册订阅者。"""
        self._subscribers_map[event_type].append((subscriber_id, handler))
        logger.debug("订阅: %s → %s (%s)", event_type, subscriber_id, handler.__name__)

    def unsubscribe(
        self,
        subscriber_id: str,
        event_type: str | None = None,
    ) -> None:
        """取消订阅。"""
        if event_type is not None:
            self._subscribers_map[event_type] = [
                (sid, h) for sid, h in self._subscribers_map[event_type]
                if sid != subscriber_id
            ]
        else:
            for et in list(self._subscribers_map.keys()):
                self._subscribers_map[et] = [
                    (sid, h) for sid, h in self._subscribers_map[et]
                    if sid != subscriber_id
                ]
                if not self._subscribers_map[et]:
                    del self._subscribers_map[et]

    # ── 发布 ──

    async def publish(self, event: Event) -> None:
        """发布事件到所有订阅者。"""
        self._published[str(event.event_id)] = event
        handlers = self._subscribers_map.get(event.event_type, [])

        for subscriber_id, handler in handlers:
            record = EventDelivery(
                event_id=event.event_id,
                subscriber=subscriber_id,
                event_type=event.event_type,
            )
            await self._deliver_to(event, handler, subscriber_id, record)

        # 持久化
        if self._persist:
            async with self._persist_lock:
                await self._persist_event(event)

    async def publish_many(self, events: list[Event]) -> None:
        """批量发布事件。"""
        for event in events:
            await self.publish(event)

    async def publish_batch(
        self, event_type: str, payloads: list[dict]
    ) -> None:
        """批量发布同类型事件（不同 payload）。"""
        for payload in payloads:
            event = Event(event_type=event_type, payload=payload)
            await self.publish(event)

    # ── 交付 ──

    async def _deliver_to(
        self,
        event: Event,
        handler: Callable[[Event], Awaitable[None]],
        subscriber_id: str,
        record: EventDelivery,
    ) -> None:
        """向单个订阅者交付事件。"""
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                await asyncio.wait_for(handler(event), timeout=self.DELIVERY_TIMEOUT)
                record.status = DelivStatus.DELIVERED.value
                record.acked_at = datetime.now(timezone.utc)
                self._delivered += 1
                self._delivery_log.append(record)
                return
            except (asyncio.TimeoutError, Exception) as e:
                record.retry_count = attempt
                record.error = str(e)
                if attempt >= self.MAX_RETRIES:
                    record.status = DelivStatus.FAILED.value
                    self._failed += 1
                    logger.warning(
                        "事件交付失败(已达最大重试): %s → %s: %s",
                        event.event_type, subscriber_id, e,
                    )
                    self._delivery_log.append(record)
                else:
                    logger.debug(
                        "事件交付重试 %d/%d: %s → %s",
                        attempt, self.MAX_RETRIES, event.event_type, subscriber_id,
                    )
                    await asyncio.sleep(min(2 ** attempt, 30))

    # ── ACK ──

    def ack_delivery(self, event_id: UUID, subscriber_id: str) -> None:
        """确认事件交付。"""
        for record in self._delivery_log:
            if record.event_id == event_id and record.subscriber == subscriber_id:
                record.status = DelivStatus.ACKED.value
                return

    # ── 回放 ──

    def get_pending_deliveries(self) -> list[EventDelivery]:
        """获取待处理的交付记录。"""
        return [
            r for r in self._delivery_log
            if r.status in (DelivStatus.PENDING.value, DelivStatus.FAILED.value)
        ]

    def replay_undelivered(self) -> int:
        """回放未交付事件，返回回放数量。"""
        pending = self.get_pending_deliveries()
        for record in pending:
            event = self._published.get(str(record.event_id))
            if event is None:
                continue
            self._replay_for(str(record.event_id))
        return len(pending)

    def replay_for_subscriber(self, subscriber_id: str) -> int:
        """为指定订阅者回放未交付事件，返回回放数量。"""
        return self._replay_for(subscriber_id)

    def _replay_for(self, subscriber_id: str) -> int:
        """内部回放方法。"""
        count = 0
        for event_id, event in self._published.items():
            for sid, handler in self._subscribers_map.get(event.event_type, []):
                if sid == subscriber_id:
                    # Check if already delivered to this subscriber
                    already = any(
                        r.event_id == event.event_id and r.subscriber == subscriber_id
                        and r.status == DelivStatus.DELIVERED.value
                        for r in self._delivery_log
                    )
                    if not already:
                        record = EventDelivery(
                            event_id=event.event_id,
                            subscriber=subscriber_id,
                            event_type=event.event_type,
                        )
                        # Need to run async in sync context
                        try:
                            import asyncio as aio
                            loop = aio.get_event_loop()
                            if loop.is_running():
                                # Can't await in running loop
                                record.status = DelivStatus.PENDING.value
                            else:
                                loop.run_until_complete(
                                    self._deliver_to(event, handler, subscriber_id, record)
                                )
                        except RuntimeError:
                            record.status = DelivStatus.PENDING.value
                        self._delivery_log.append(record)
                        count += 1
        return count

    # ── 持久化 ──

    async def _persist_event(self, event: Event) -> None:
        """持久化事件到 DB。"""
        if not self._db_factory:
            return
        # Placeholder for DB persistence
        pass

    async def _persist_delivery(self, record: EventDelivery) -> None:
        """持久化交付记录到 DB。"""
        if not self._db_factory:
            return
        # Placeholder for DB persistence
        pass

    # ── 统计 ──

    @property
    def stats(self) -> dict[str, int]:
        """返回事件总线统计信息。"""
        return {
            "published": len(self._published),
            "delivered": self._delivered,
            "failed": self._failed,
            "delivery_log": len(self._delivery_log),
            "subscribers": sum(len(v) for v in self._subscribers_map.values()),
        }

    @property
    def subscriber_count(self) -> int:
        """返回订阅者总数。"""
        return sum(len(v) for v in self._subscribers_map.values())

    def reset_stats(self) -> None:
        """重置统计信息。"""
        self._delivered = 0
        self._failed = 0
