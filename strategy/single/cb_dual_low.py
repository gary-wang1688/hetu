"""可转债双低策略。"""
from strategy.base import BaseStrategy

class CBDualLowStrategy(BaseStrategy):
    name = "cb_dual_low"
    def __init__(self, max_price=120, max_premium=0.20, top_n=10):
        self._max_price = max_price; self._max_premium = max_premium; self._top_n = top_n
    async def on_bar(self, bar, history, ctx): return []
    def get_rationale(self): return "可转债双低: 低价+低溢价率。"
    def get_param_schema(self): return []
