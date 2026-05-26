"""业绩超预期策略。"""
from strategy.base import BaseStrategy

class PEADStrategy(BaseStrategy):
    name = "pead"
    def __init__(self, min_surprise=0.20, hold_days=5):
        self._min_surprise = min_surprise; self._hold_days = hold_days
    async def on_bar(self, bar, history, ctx): return []
    def get_rationale(self): return "业绩超预期: 盈余公告后漂移。"
    def get_param_schema(self): return []
