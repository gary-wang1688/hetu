"""
河图 (HeTu) 滑点模型

可插拔滑点模拟:
1. FixedTickSlippage — 固定 tick 滑点（最常用）
2. VolumeBasedSlippage — 基于成交量比例的滑点
3. ZeroSlippage — 零滑点（测试用）
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from core.types import Order


class SlippageModel(ABC):
    """滑点模型基类"""

    @abstractmethod
    def apply(self, order: Order, price: float) -> float:
        """应用滑点，返回实际成交价"""
        ...

    @abstractmethod
    def apply_buy(self, price: float) -> float:
        """买入滑点 (价格上移)"""
        ...

    @abstractmethod
    def apply_sell(self, price: float) -> float:
        """卖出滑点 (价格下移)"""
        ...


class FixedTickSlippage(SlippageModel):
    """固定 tick 滑点模型

    默认 1 tick = 0.01 元（A 股最小变动单位）
    """

    def __init__(self, ticks: int = 1, tick_size: float = 0.01) -> None:
        self._ticks = ticks
        self._tick_size = tick_size

    def apply(self, order: Order, price: float) -> float:
        if order.action.value == "BUY":
            return self.apply_buy(price)
        else:
            return self.apply_sell(price)

    def apply_buy(self, price: float) -> float:
        return round(price + self._ticks * self._tick_size, 2)

    def apply_sell(self, price: float) -> float:
        return round(price - self._ticks * self._tick_size, 2)

    def __repr__(self) -> str:
        return f"FixedTickSlippage(ticks={self._ticks}, tick_size={self._tick_size})"


class VolumeBasedSlippage(SlippageModel):
    """基于成交量的滑点模型

    滑点 = base_slippage * sqrt(order_volume / avg_daily_volume)
    """

    def __init__(self, base_slippage: float = 0.0005) -> None:
        self._base_slippage = base_slippage
        self._avg_volume: dict[str, float] = {}

    def set_avg_volume(self, avg_volume: dict[str, float]) -> None:
        """设置日均成交量"""
        self._avg_volume = avg_volume

    def apply(self, order: Order, price: float) -> float:
        import math

        avg_vol = self._avg_volume.get(order.code, 1e9)
        vol_ratio = order.shares / max(avg_vol, 1)
        slippage_pct = self._base_slippage * math.sqrt(max(vol_ratio, 0))

        if order.action.value == "BUY":
            return round(price * (1 + slippage_pct), 2)
        else:
            return round(price * (1 - slippage_pct), 2)

    def apply_buy(self, price: float) -> float:
        return self.apply(Order(code="", action="BUY", shares=0, channel=""), price)

    def apply_sell(self, price: float) -> float:
        return self.apply(Order(code="", action="SELL", shares=0, channel=""), price)


class ZeroSlippage(SlippageModel):
    """零滑点模型 (仅用于测试)"""

    def apply(self, order: Order, price: float) -> float:
        return price

    def apply_buy(self, price: float) -> float:
        return price

    def apply_sell(self, price: float) -> float:
        return price

    def __repr__(self) -> str:
        return "ZeroSlippage()"
