"""选股筛选器。"""
import logging
logger = logging.getLogger('hetu.strategy.screening')
class Screener:
    def __init__(self, cfg=None): self._cfg = cfg
    async def screen(self, condition, market='A'): return []
