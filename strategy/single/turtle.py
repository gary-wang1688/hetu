"""海龟突破策略 — Richard Dennis 原版适配 A 股。"""
from core.types import Bar, Signal, Action, StrategyContext
from strategy.base import BaseStrategy, ParamSchema


class TurtleStrategy(BaseStrategy):
    name = "turtle"

    def __init__(self, entry_period: int = 20, exit_period: int = 10, confirm_delay: int = 1, max_consecutive_stops: int = 5):
        self._entry_period = entry_period
        self._exit_period = exit_period
        self._confirm_delay = confirm_delay
        self._max_consecutive_stops = max_consecutive_stops
        self._pending_confirmations: dict[str, tuple[float, int]] = {}
        self._consecutive_stops = 0
        self._paused_until = ""

    async def on_bar(self, bar: Bar, history: list[Bar], ctx: StrategyContext) -> list[Signal]:
        if len(history) < self._entry_period:
            return []

        # Donchian 通道
        entry_high = max(b.high for b in history[-self._entry_period:])
        exit_low = min(b.low for b in history[-self._exit_period:])

        # 确认延迟
        if bar.close > entry_high:
            if self._confirm_delay > 0:
                self._pending_confirmations[bar.code] = (bar.close, 0)
                return []
            return [self._buy_signal(bar, "海龟突破")]

        if bar.close < exit_low:
            return [self._sell_signal(bar, "海龟离场")]

        # 确认后下单
        if bar.code in self._pending_confirmations:
            _, days = self._pending_confirmations[bar.code]
            if days >= self._confirm_delay:
                del self._pending_confirmations[bar.code]
                return [self._buy_signal(bar, "海龟突破(确认)")]
            self._pending_confirmations[bar.code] = (bar.close, days + 1)

        return []

    def _buy_signal(self, bar: Bar, reason: str) -> Signal:
        return Signal(strategy=self.name, code=bar.code, action=Action.BUY, confidence=0.65, reason=reason)

    def _sell_signal(self, bar: Bar, reason: str) -> Signal:
        return Signal(strategy=self.name, code=bar.code, action=Action.SELL, confidence=0.7, reason=reason)

    def get_rationale(self) -> str:
        return "海龟突破: Donchian 通道高点突破买入，低点突破卖出。A股调整: +1日确认延迟过滤假突破。"

    def get_param_schema(self) -> list[ParamSchema]:
        return [
            ParamSchema("entry_period", 20, "int", 10, 30, "入场通道周期"),
            ParamSchema("exit_period", 10, "int", 5, 20, "离场通道周期"),
        ]
