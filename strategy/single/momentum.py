"""截面动量策略。"""
from core.types import Bar, Signal, Action, StrategyContext
from strategy.base import BaseStrategy, ParamSchema


class MomentumStrategy(BaseStrategy):
    name = "momentum"

    def __init__(self, lookback: int = 60, top_n: int = 20, max_industry_ratio: float = 0.33):
        self._lookback = lookback; self._top_n = top_n; self._max_industry_ratio = max_industry_ratio

    async def on_bar(self, bar: Bar, history: list[Bar], ctx: StrategyContext) -> list[Signal]:
        if len(history) < self._lookback: return []
        ret = (bar.close - history[-self._lookback].close) / history[-self._lookback].close
        if ret > 0:
            return [Signal(strategy=self.name, code=bar.code, action=Action.BUY,
                          confidence=min(0.3 + ret, 0.9), reason=f"动量 {ret*100:.1f}%")]
        return []

    def get_rationale(self) -> str: return "截面动量: 做多近期涨幅最高的前N只。"
    def get_param_schema(self) -> list[ParamSchema]: return [ParamSchema("top_n", 20, "int")]
