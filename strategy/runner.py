"""策略执行器。"""
import logging
logger = logging.getLogger('hetu.strategy.runner')

class StrategyRunner:
    def __init__(self):
        self._strategies = {}
    def register(self, name, cls): self._strategies[name] = cls
    def get(self, name): return self._strategies.get(name)
    def list_all(self): return list(self._strategies.keys())
