"""
河图 (HeTu) 抽象接口

所有可插拔组件的 ABC 定义: Broker, DataProvider, Strategy 等。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from core.types import (
    Order, Position, Trade, ChannelResult, ComponentHealth, HealthStatus,
    Bar, Quote,
)


class Broker(ABC):
    name: str = ""

    @abstractmethod
    async def submit_order(self, order: Order) -> ChannelResult: ...
    @abstractmethod
    async def cancel_order(self, order_id: Any) -> ChannelResult: ...
    @abstractmethod
    async def get_balance(self) -> dict: ...
    @abstractmethod
    async def get_positions(self) -> list[Position]: ...
    @abstractmethod
    async def get_trades(self) -> list[Trade]: ...
    @abstractmethod
    async def query_order(self, order_id: Any) -> Order | None: ...
    async def health_check(self) -> ComponentHealth:
        return ComponentHealth(name=self.name, status=HealthStatus.HEALTHY)


class DataProviderABC(ABC):
    name: str = ""

    @abstractmethod
    async def get_daily_bars(self, codes: list[str], count: int = 60) -> dict[str, list[Bar]]: ...
    @abstractmethod
    async def get_realtime_quote(self, codes: list[str]) -> dict[str, Quote]: ...


# Alias for backward compatibility
DataProvider = DataProviderABC
