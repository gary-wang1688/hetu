"""均线交叉策略。"""
from core.types import Bar, Signal, Action, StrategyContext
from strategy.base import BaseStrategy, ParamSchema


class MACrossStrategy(BaseStrategy):
    name = "ma_cross"

    def __init__(self, fast: int = 20, slow: int = 60, adx_threshold: int = 20):
        self._fast = fast; self._slow = slow; self._adx = adx_threshold

    async def on_bar(self, bar: Bar, history: list[Bar], ctx: StrategyContext) -> list[Signal]:
        if len(history) < self._slow: return []
        fast_ma = sum(b.close for b in history[-self._fast:]) / self._fast
        slow_ma = sum(b.close for b in history[-self._slow:]) / self._slow
        prev_fast = sum(b.close for b in history[-self._fast-1:-1]) / self._fast
        prev_slow = sum(b.close for b in history[-self._slow-1:-1]) / self._slow
        if prev_fast <= prev_slow and fast_ma > slow_ma:
            return [Signal(strategy=self.name, code=bar.code, action=Action.BUY, confidence=0.5,
                          reason=f"金叉 fast={fast_ma:.2f} slow={slow_ma:.2f}")]
        return []

    def get_rationale(self) -> str: return "均线交叉"
    def get_param_schema(self) -> list[ParamSchema]: return [ParamSchema("fast", 20, "int")]
