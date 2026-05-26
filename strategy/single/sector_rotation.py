"""行业轮动策略。"""
from strategy.base import BaseStrategy

class SectorRotationStrategy(BaseStrategy):
    name = "sector_rotation"
    def __init__(self, momentum_weight=0.4, top_n=3):
        self._momentum_weight = momentum_weight; self._top_n = top_n
    async def on_bar(self, bar, history, ctx): return []
    def get_rationale(self): return "行业轮动: 动量+估值+资金流。"
    def get_param_schema(self): return []
