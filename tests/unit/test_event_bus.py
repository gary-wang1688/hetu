"""InMemoryEventBus 单元测试。"""
from __future__ import annotations

from uuid import uuid4

import pytest

from core.events import Event
from core.event_bus import InMemoryEventBus
from core.types import EventType, DelivStatus


@pytest.fixture
def bus() -> InMemoryEventBus:
    return InMemoryEventBus(persist=False)


@pytest.fixture
def test_event() -> Event:
    return Event(
        event_id=uuid4(),
        event_type=EventType.SIGNAL_EXECUTED,
        payload={"code": "000001.SZ", "shares": 1000},
    )


# ============================================================
# 发布/订阅
# ============================================================

class TestPublishSubscribe:
    async def test_subscribe_and_publish(
        self, bus: InMemoryEventBus, test_event: Event
    ) -> None:
        """单个订阅者应收到发布的事件。"""
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        await bus.subscribe(
            EventType.SIGNAL_EXECUTED.value, handler, "sub_a"
        )
        await bus.publish(test_event)

        assert len(received) == 1
        assert received[0].event_id == test_event.event_id
        assert received[0].payload == test_event.payload

    async def test_multiple_subscribers(
        self, bus: InMemoryEventBus, test_event: Event
    ) -> None:
        """多个订阅者都应收到事件。"""
        received_a: list[Event] = []
        received_b: list[Event] = []

        async def handler_a(event: Event) -> None:
            received_a.append(event)

        async def handler_b(event: Event) -> None:
            received_b.append(event)

        await bus.subscribe(
            EventType.SIGNAL_EXECUTED.value, handler_a, "sub_a"
        )
        await bus.subscribe(
            EventType.SIGNAL_EXECUTED.value, handler_b, "sub_b"
        )
        await bus.publish(test_event)

        assert len(received_a) == 1
        assert len(received_b) == 1

    async def test_unsubscribe(
        self, bus: InMemoryEventBus, test_event: Event
    ) -> None:
        """取消订阅后不应再收到事件。"""
        received_b: list[Event] = []

        async def handler_a(event: Event) -> None:
            pass

        async def handler_b(event: Event) -> None:
            received_b.append(event)

        await bus.subscribe(
            EventType.SIGNAL_EXECUTED.value, handler_a, "sub_a"
        )
        await bus.subscribe(
            EventType.SIGNAL_EXECUTED.value, handler_b, "sub_b"
        )
        bus.unsubscribe("sub_a", EventType.SIGNAL_EXECUTED.value)
        await bus.publish(test_event)

        assert len(received_b) == 1

    async def test_publish_no_subscribers(self, bus: InMemoryEventBus) -> None:
        """无订阅者时发布不应抛出异常。"""
        event = Event(
            event_type=EventType.SIGNAL_EXECUTED,
            payload={"code": "000002.SZ"},
        )
        # 不应抛出异常
        await bus.publish(event)


# ============================================================
# 重试机制
# ============================================================

class TestDeliveryRetry:
    async def test_delivery_failure_then_success(
        self, bus: InMemoryEventBus
    ) -> None:
        """处理函数首次失败后重试成功。"""
        call_count = []

        async def unreliable_handler(event: Event) -> None:
            call_count.append(1)
            if len(call_count) == 1:
                raise RuntimeError("simulated failure")

        await bus.subscribe(
            EventType.SIGNAL_EXECUTED.value, unreliable_handler, "unreliable"
        )
        await bus.publish(
            Event(event_type=EventType.SIGNAL_EXECUTED, payload={})
        )

        # 应成功交付（重试后成功）
        assert bus._delivered >= 1

    async def test_delivery_max_retries_exhausted(
        self, bus: InMemoryEventBus
    ) -> None:
        """超过最大重试次数后标记为失败。"""
        async def always_fail(event: Event) -> None:
            raise RuntimeError("always fail")

        await bus.subscribe(
            EventType.SIGNAL_EXECUTED.value, always_fail, "failer"
        )
        await bus.publish(
            Event(event_type=EventType.SIGNAL_EXECUTED, payload={})
        )

        # 应有失败记录
        assert bus._failed >= 1


# ============================================================
# ACK 确认
# ============================================================

class TestAckDelivery:
    async def test_ack_updates_status(
        self, bus: InMemoryEventBus, test_event: Event
    ) -> None:
        """ACK 应更新交付记录状态。"""
        async def handler(event: Event) -> None:
            pass

        await bus.subscribe(
            EventType.SIGNAL_EXECUTED.value, handler, "sub_ack"
        )
        await bus.publish(test_event)

        bus.ack_delivery(test_event.event_id, "sub_ack")

        # 交付记录应标记为 ACKED
        for record in bus._delivery_log:
            if record.event_id == test_event.event_id and record.subscriber == "sub_ack":
                assert record.status == DelivStatus.ACKED.value
                return
        pytest.fail("未找到对应的交付记录")

    async def test_ack_nonexistent(self, bus: InMemoryEventBus) -> None:
        """ACK 不存在的交付不应抛出异常。"""
        # 不应抛出异常
        bus.ack_delivery(uuid4(), "nonexistent")


# ============================================================
# 回放
# ============================================================

class TestReplay:
    async def test_replay_undelivered(self, bus: InMemoryEventBus) -> None:
        """回放应处理未交付的事件。"""
        async def failing_handler(event: Event) -> None:
            raise RuntimeError("delivery failed")

        bus.MAX_RETRIES = 1  # Speed up test
        await bus.subscribe(
            EventType.SIGNAL_EXECUTED.value, failing_handler, "failer"
        )
        event = Event(event_type=EventType.SIGNAL_EXECUTED, payload={})
        await bus.publish(event)

        # 应该有失败的交付记录
        failed_before = len(bus.get_pending_deliveries())
        assert failed_before >= 1

        # 回放
        replayed = bus.replay_undelivered()
        assert replayed >= 1

    async def test_replay_no_pending(self, bus: InMemoryEventBus) -> None:
        """无待交付时回放返回 0。"""
        result = bus.replay_undelivered()
        assert result == 0


# ============================================================
# 统计
# ============================================================

class TestStats:
    def test_stats_initial(self, bus: InMemoryEventBus) -> None:
        """初始统计应为零。"""
        s = bus.stats
        assert s["published"] == 0
        assert s["delivered"] == 0
        assert s["failed"] == 0
        assert s["subscribers"] == 0

    async def test_stats_after_publish(
        self, bus: InMemoryEventBus, test_event: Event
    ) -> None:
        """发布事件后统计应更新。"""
        async def handler(event: Event) -> None:
            pass

        await bus.subscribe(
            EventType.SIGNAL_EXECUTED.value, handler, "stats_sub"
        )
        await bus.publish(test_event)

        s = bus.stats
        assert s["published"] == 1
        assert s["delivered"] >= 1

    async def test_subscriber_count(self, bus: InMemoryEventBus) -> None:
        """订阅者计数应正确。"""
        async def handler(event: Event) -> None:
            pass

        assert bus.subscriber_count == 0

        await bus.subscribe(
            EventType.SIGNAL_EXECUTED.value, handler, "sub1"
        )
        assert bus.subscriber_count == 1

        await bus.subscribe(
            EventType.SIGNAL_RECEIVED.value, handler, "sub2"
        )
        assert bus.subscriber_count == 2
