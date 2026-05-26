"""红利低波策略。"""
from core.types import Bar, Signal, StrategyContext
from strategy.base import BaseStrategy, ParamSchema


class DividendStrategy(BaseStrategy):
    name = "dividend"

    def __init__(self, min_dividend_yield: float = 0.03, top_n: int = 15, vol_percentile: float = 0.3):
        self._min_yield = min_dividend_yield; self._top_n = top_n; self._vol_pct = vol_percentile

    async def on_bar(self, bar: Bar, history: list[Bar], ctx: StrategyContext) -> list[Signal]:
        return []

    def get_rationale(self) -> str: return "红利低波: 高股息+低波动选股。日频调仓。"
    def get_param_schema(self) -> list[ParamSchema]: return []
