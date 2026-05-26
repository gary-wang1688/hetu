"""
河图 (HeTu) 仓位计算器

职责:
- 根据信号、风控审核结果、组合状态计算最终仓位
- 支持按策略类型差异化仓位上限
- T+1 感知: 今日买入的股票不参与卖出计算
- 输出: 调整后股数 + 止损/止盈价格

架构 5.3.1: RiskManager.assess() -> PositionSizer.calculate() -> 下单
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger("hetu.risk.sizer")

# 各策略类型的默认仓位上限
STRATEGY_POSITION_LIMITS: dict[str, float] = {
    "trend": 0.08,
    "momentum": 0.10,
    "reversal": 0.06,
    "value": 0.12,
    "arbitrage": 0.15,
    "event_driven": 0.08,
    "default": 0.05,
}


@dataclass
class PositionSizing:
    """仓位计算结果"""
    adjusted_shares: int = 0
    position_pct: float = 0.0
    risk_amount: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    max_position_pct: float = 0.0
    reason: str = ""


class PositionSizer:
    """
    仓位计算器

    规则链:
    1. 基础仓位 = 总权益 * 策略类型上限 / 入场价
    2. 风险约束 = 总权益 * 单笔最大亏损% / (入场价 - 止损价)
    3. 最终股数 = min(基础仓位, 风险约束, 信号股数)
    4. 100 股取整（A 股最小交易单位）

    出参含止损/止盈，供 SignalPipeline 挂载止损单。
    """

    def __init__(
        self,
        total_equity: float = 0.0,
        max_total_pct: float = 0.20,
        max_single_pct: float = 0.05,
        max_risk_per_trade: float = 0.01,
        stop_loss_pct: float = 0.08,
        take_profit_ratio: float = 1.5,
        strategy_limits: "dict[str, float] | None" = None,
    ):
        self.equity = total_equity
        self.max_total_pct = max_total_pct
        self.max_single_pct = max_single_pct
        self.max_risk_per_trade = max_risk_per_trade
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_ratio = take_profit_ratio
        self._strategy_limits = strategy_limits or STRATEGY_POSITION_LIMITS
        self._current_total_pct: float = 0.0
        self._entry_dates: dict[str, str] = {}

    # ──────────────────────────────────
    # 单笔计算
    # ──────────────────────────────────

    def calculate(
        self,
        price: float,
        signal_shares: int,
        strategy_type: str = "default",
        current_position_pct: float = 0.0,
        entry_date: "str | None" = None,
    ) -> PositionSizing:
        if price <= 0 or self.equity <= 0:
            return PositionSizing(reason="价格或权益无效")

        # 1) 策略仓位上限
        max_pct = self._strategy_limits.get(strategy_type, self.max_single_pct)

        # 2) 基础仓位 = 权益 * 上限 / 价格
        base_shares = int((self.equity * max_pct) / price)

        # 3) 风险约束 = 权益 * 单笔最大风险 / (价格 * 止损比例)
        stop_price = price * (1 - self.stop_loss_pct)
        risk_per_share = price - stop_price
        if risk_per_share <= 0:
            risk_shares = base_shares
        else:
            risk_shares = int((self.equity * self.max_risk_per_trade) / risk_per_share)

        # 4) 凯利公式修正 (Kelly Criterion)
        # f* = (p * b - q) / b  where b = take_profit_ratio, p = 0.5, q = 0.5
        win_prob = 0.5
        loss_prob = 0.5
        b = self.take_profit_ratio
        kelly_f = (win_prob * b - loss_prob) / b if b > 0 else 0.0
        kelly_f = max(0.0, min(kelly_f, max_pct))  # 上限为策略上限
        kelly_shares = int((self.equity * kelly_f) / price) if kelly_f > 0 else base_shares

        # 5) 最终股数 = min(基础, 凯利, 风险, 信号股数)
        final_shares = min(base_shares, kelly_shares, risk_shares, signal_shares)

        # 6) 100 股取整
        final_shares = (final_shares // 100) * 100
        if final_shares < 100:
            return PositionSizing(reason="仓位不足 100 股")

        # 7) 计算占比
        position_pct = (final_shares * price) / self.equity if self.equity > 0 else 0.0
        take_profit_price = price * self.take_profit_ratio

        return PositionSizing(
            adjusted_shares=final_shares,
            position_pct=position_pct,
            risk_amount=final_shares * risk_per_share,
            stop_loss=stop_price,
            take_profit=take_profit_price,
            max_position_pct=max_pct,
            reason="仓位计算完成",
        )

    # ──────────────────────────────────
    # 批量计算
    # ──────────────────────────────────

    def calculate_batch(
        self, signals: list[dict], strategy_type: str = "default"
    ) -> list[PositionSizing]:
        results: list[PositionSizing] = []
        for sig in signals:
            sizing = self.calculate(
                price=sig.get("price", 0.0),
                signal_shares=sig.get("shares", 0),
                strategy_type=strategy_type,
                current_position_pct=0.0,
            )
            results.append(sizing)
        return results

    # ──────────────────────────────────
    # 状态管理
    # ──────────────────────────────────

    def update_equity(self, equity: float) -> None:
        self.equity = equity

    def set_current_positions(
        self, total_pct: float, entry_dates: "dict[str, str] | None" = None
    ) -> None:
        self._current_total_pct = total_pct
        if entry_dates:
            self._entry_dates = entry_dates

    def reset(self) -> None:
        self._current_total_pct = 0.0
        self._entry_dates.clear()
