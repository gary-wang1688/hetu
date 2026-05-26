"""布林带反转策略。"""
from core.types import Bar, Signal, Action, StrategyContext
from strategy.base import BaseStrategy, ParamSchema


class BollingerStrategy(BaseStrategy):
    name = "bollinger"

    def __init__(self, period: int = 20, std_mult: float = 2.0, ma_filter_period: int = 200):
        self._period = period
        self._std_mult = std_mult
        self._ma_filter_period = ma_filter_period

    async def on_bar(self, bar: Bar, history: list[Bar], ctx: StrategyContext) -> list[Signal]:
        if len(history) < max(self._period, self._ma_filter_period):
            return []
        closes = [b.close for b in history[-self._period:]]
        avg = sum(closes) / self._period
        variance = sum((c - avg) ** 2 for c in closes) / self._period
        lower = avg - self._std_mult * (variance ** 0.5)
        if self._ma_filter_period > 0 and len(history) >= self._ma_filter_period:
            ma200 = sum(b.close for b in history[-self._ma_filter_period:]) / self._ma_filter_period
            if bar.close < ma200:
                return []
        if bar.close <= lower:
            return [Signal(strategy=self.name, code=bar.code, action=Action.BUY, confidence=0.6,
                          reason=f"布林带下轨反弹 close={bar.close:.2f} lower={lower:.2f}")]
        return []

    def get_rationale(self) -> str:
        return "布林带: 价格触及下轨反弹买入。MA200 过滤熊市。"

    def get_param_schema(self) -> list[ParamSchema]:
        return [ParamSchema("period", 20, "int", 10, 30), ParamSchema("std_mult", 2.0, "float", 1.5, 3.0)]
