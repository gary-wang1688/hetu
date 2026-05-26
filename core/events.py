"""事件定义。"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID, uuid4


@dataclass
class Event:
    event_id: UUID = field(default_factory=uuid4)
    event_type: str = ""
    payload: dict = field(default_factory=dict)
    source: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class EventDelivery:
    event_id: UUID
    subscriber: str
    event_type: str = ""
    status: str = "pending"
    retry_count: int = 0
    acked_at: datetime | None = None
    error: str | None = None
