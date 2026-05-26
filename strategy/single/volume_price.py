"""量价共振策略。"""
from core.types import Bar, Signal, Action, StrategyContext
from strategy.base import BaseStrategy, ParamSchema


class VolumePriceStrategy(BaseStrategy):
    name = "volume_price"

    def __init__(self, vol_period: int = 20, vol_mult: float = 1.5, min_daily_amount: float = 50_000_000):
        self._vol_period = vol_period
        self._vol_mult = vol_mult
        self._min_daily_amount = min_daily_amount

    async def on_bar(self, bar: Bar, history: list[Bar], ctx: StrategyContext) -> list[Signal]:
        if len(history) < self._vol_period:
            return []
        if bar.amount < self._min_daily_amount:
            return []
        avg_vol = sum(b.volume for b in history[-self._vol_period:]) / self._vol_period
        if bar.volume > avg_vol * self._vol_mult and bar.close > history[-2].close:
            return [Signal(strategy=self.name, code=bar.code, action=Action.BUY, confidence=0.55,
                          reason=f"量价共振 vol={bar.volume} 倍率={bar.volume/avg_vol:.1f}x")]
        return []

    def get_rationale(self) -> str: return "量价共振: 放量上涨确认趋势。"
    def get_param_schema(self) -> list[ParamSchema]: return [ParamSchema("vol_mult", 1.5, "float", 1.2, 3.0)]
