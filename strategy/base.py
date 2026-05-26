"""策略基类。"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from core.types import Bar, Signal, StrategyContext


@dataclass
class ParamSchema:
    name: str
    default: Any
    type: str = "float"
    min_val: float | None = None
    max_val: float | None = None
    description: str = ""


class BaseStrategy(ABC):
    name: str = ""

    @abstractmethod
    async def on_bar(self, bar: Bar, history: list[Bar], ctx: StrategyContext) -> list[Signal]:
        ...

    @abstractmethod
    def get_rationale(self) -> str:
        """策略理性说明。"""
        ...

    @abstractmethod
    def get_param_schema(self) -> list[ParamSchema]:
        ...

    @classmethod
    def from_config(cls, cfg: object) -> BaseStrategy:
        """从 YAML 配置创建策略实例。"""
        return cls()
