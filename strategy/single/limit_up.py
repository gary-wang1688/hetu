"""涨停板策略。"""
from core.types import Bar, Signal, Action, StrategyContext
from strategy.base import BaseStrategy, ParamSchema


class LimitUpStrategy(BaseStrategy):
    name = "limit_up"

    def __init__(self, pullback_min: float = -0.05, pullback_max: float = -0.02, stop_loss: float = -0.04):
        self._pullback_min = pullback_min; self._pullback_max = pullback_max; self._stop_loss = stop_loss

    async def on_bar(self, bar: Bar, history: list[Bar], ctx: StrategyContext) -> list[Signal]:
        if len(history) < 2: return []
        prev_close = history[-2].close
        chg = (bar.close - prev_close) / prev_close
        if chg >= 0.098:
            return [Signal(strategy=self.name, code=bar.code, action=Action.BUY, confidence=0.55,
                          reason=f"涨停 {chg*100:.1f}%", stop_loss=bar.close * (1 + self._stop_loss))]
        return []

    def get_rationale(self) -> str: return "打板: 今日涨停 → 次日竞价打板。"
    def get_param_schema(self) -> list[ParamSchema]: return [ParamSchema("stop_loss", -0.04, "float")]
