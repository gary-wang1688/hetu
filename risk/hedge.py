"""
河图 (HeTu) 空头对冲顾问

HedgeAdvisor: 基于基准指数的择时对冲决策。
只在极端环境介入，正常市不干预——见路不走。

v2.2: 策略扩展调研后新增——15% 概率的外部冲击需要保险。

触发逻辑:
- 沪深300 < MA200 -> 轻度对冲 30%
- 沪深300 < MA200 且波动率飙升 -> 中度对冲 60%
- 基准指数单日跌 >3% 或 CircuitBreaker RED -> 完全对冲 100%
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

import numpy as np

logger = logging.getLogger("hetu.hedge")


class HedgeLevel(str, Enum):
    """对冲级别"""
    NONE = "NONE"
    LIGHT = "LIGHT"
    MODERATE = "MODERATE"
    FULL = "FULL"


@dataclass
class HedgeSignal:
    """对冲建议"""
    level: HedgeLevel = HedgeLevel.NONE
    hedge_ratio: float = 0.0
    contracts: int = 0
    reason: str = ""
    index_level: float = 0.0
    ma200: float = 0.0
    volatility_triggered: bool = False
    force_liquidate: bool = False


class HedgeAdvisor:
    """
    空头对冲顾问。

    原则: 正常市场不干预，只在极端环境介入。
    ——叶子农《天幕红尘》"见路不走"

    用法:
        advisor = HedgeAdvisor()
        signal = advisor.evaluate(index_bars, portfolio_asset, red_circuit=False)
    """

    def __init__(
        self,
        ma_period: int = 200,
        hedge_light: float = 0.30,
        hedge_moderate: float = 0.60,
        hedge_full: float = 1.0,
        vol_multiplier: float = 2.0,
        benchmark_drop_threshold: float = 0.03,
        contract_multiplier: int = 300,
    ):
        self.ma_period = ma_period
        self.hedge_light = hedge_light
        self.hedge_moderate = hedge_moderate
        self.hedge_full = hedge_full
        self.vol_multiplier = vol_multiplier
        self.benchmark_drop_threshold = benchmark_drop_threshold
        self.contract_multiplier = contract_multiplier
        self._last_signal: HedgeSignal | None = None

    @classmethod
    def from_config(
        cls, cfg: object, overrides: object = None
    ) -> "HedgeAdvisor":
        params = {
            "ma_period": int(cfg.get("hedge.ma_period", 200)),
            "hedge_light": float(cfg.get("hedge.light_ratio", 0.30)),
            "hedge_moderate": float(cfg.get("hedge.moderate_ratio", 0.60)),
            "hedge_full": float(cfg.get("hedge.full_ratio", 1.0)),
            "vol_multiplier": float(cfg.get("hedge.vol_multiplier", 2.0)),
            "benchmark_drop_threshold": float(cfg.get("hedge.benchmark_threshold", 0.03)),
            "contract_multiplier": int(cfg.get("hedge.contract_multiplier", 300)),
        }
        if overrides is not None:
            for k, v in overrides.__dict__.items():
                if not k.startswith("_") and k in params:
                    params[k] = v
        return cls(**params)

    # ──────────────────────────────────
    # 核心评估
    # ──────────────────────────────────

    def evaluate(
        self,
        index_bars: list[dict],
        portfolio_asset: float,
        circuit_breaker_red: bool = False,
        benchmark_drop: float = 0.0,
    ) -> HedgeSignal:
        if len(index_bars) < self.ma_period:
            logger.debug("数据不足: %d bars < %d MA 周期", len(index_bars), self.ma_period)
            return HedgeSignal(reason=f"数据不足 (需要 {self.ma_period} 根K线)")

        # 1) 计算 MA200
        closes = [bar.get("close", 0.0) for bar in index_bars[-self.ma_period:]]
        ma200 = float(np.mean(closes)) if closes else 0.0
        current = closes[-1] if closes else 0.0

        # 2) 波动率检查 (ATR 飙升)
        vol_triggered = self._check_volatility(index_bars)

        # 3) 决策
        level, ratio, reason = self._decide(
            current=current,
            ma200=ma200,
            vol_triggered=vol_triggered,
            circuit_red=circuit_breaker_red,
            benchmark_drop=benchmark_drop,
        )

        # 4) 计算合约数
        contracts = self._calc_contracts(
            ratio=ratio, asset=portfolio_asset, index_level=current
        )

        signal = HedgeSignal(
            level=level,
            hedge_ratio=ratio,
            contracts=contracts,
            reason=reason,
            index_level=current,
            ma200=ma200,
            volatility_triggered=vol_triggered,
            force_liquidate=(level == HedgeLevel.FULL),
        )
        self._last_signal = signal
        logger.info(
            "对冲评估: %s (ratio=%.0f%%, contracts=%d) | idx=%.0f ma200=%.0f vol=%s cb_red=%s",
            level.value,
            ratio * 100,
            contracts,
            current,
            ma200,
            vol_triggered,
            circuit_breaker_red,
        )
        return signal

    # ──────────────────────────────────
    # 决策逻辑
    # ──────────────────────────────────

    def _decide(
        self,
        current: float,
        ma200: float,
        vol_triggered: bool,
        circuit_red: bool,
        benchmark_drop: float,
    ) -> tuple[HedgeLevel, float, str]:
        # 完全对冲: 基准暴跌 或 熔断 RED
        if circuit_red or abs(benchmark_drop) >= self.benchmark_drop_threshold:
            reason = "熔断RED" if circuit_red else f"基准暴跌 {abs(benchmark_drop):.2%}"
            return HedgeLevel.FULL, self.hedge_full, reason

        # 指数的 MA200 以下
        if current < ma200:
            if vol_triggered:
                return HedgeLevel.MODERATE, self.hedge_moderate, f"指数低于MA200 ({current:.0f} < {ma200:.0f}) + 波动率飙升"
            return HedgeLevel.LIGHT, self.hedge_light, f"指数低于MA200 ({current:.0f} < {ma200:.0f})"

        # 正常市场
        return HedgeLevel.NONE, 0.0, "正常市场，不干预"

    # ──────────────────────────────────
    # 波动率检查 (ATR 飙升)
    # ──────────────────────────────────

    def _check_volatility(
        self, bars: list[dict], window: int = 20
    ) -> bool:
        if len(bars) < window + 10:
            return False

        recent = bars[-window:]
        highs = np.array([b.get("high", b.get("close", 0)) for b in recent])
        lows = np.array([b.get("low", b.get("close", 0)) for b in recent])
        closes_prev = np.array([bars[-(window + 1) + i].get("close", 0) for i in range(window)])

        # True Range
        tr = np.maximum(
            highs - lows,
            np.maximum(
                np.abs(highs - closes_prev),
                np.abs(lows - closes_prev),
            ),
        )
        atr_current = float(np.mean(tr))

        # 历史 ATR
        historical = bars[-window * 2: -window]
        hist_highs = np.array([b.get("high", b.get("close", 0)) for b in historical])
        hist_lows = np.array([b.get("low", b.get("close", 0)) for b in historical])
        hist_closes_prev = np.array([bars[-(window * 2) + i].get("close", 0) for i in range(window)])
        hist_tr = np.maximum(
            hist_highs - hist_lows,
            np.maximum(
                np.abs(hist_highs - hist_closes_prev),
                np.abs(hist_lows - hist_closes_prev),
            ),
        )
        atr_historical = float(np.mean(hist_tr))

        if atr_historical <= 0:
            return False

        # 当前 ATR 是否是历史的 vol_multiplier 倍
        return atr_current >= atr_historical * self.vol_multiplier

    # ──────────────────────────────────
    # 合约数计算
    # ──────────────────────────────────

    def _calc_contracts(
        self, ratio: float, asset: float, index_level: float
    ) -> int:
        if ratio <= 0 or index_level <= 0:
            return 0
        # 合约价值 = 指数点位 * 合约乘数
        contract_value = index_level * self.contract_multiplier
        if contract_value <= 0:
            return 0
        # 需要对冲的金额 / 每张合约价值
        hedge_amount = asset * ratio
        contracts = int(hedge_amount / contract_value)
        return max(0, contracts)

    # ──────────────────────────────────
    # 属性
    # ──────────────────────────────────

    @property
    def last_signal(self) -> "HedgeSignal | None":
        return self._last_signal

    @property
    def is_hedging(self) -> bool:
        if self._last_signal is None:
            return False
        return self._last_signal.level != HedgeLevel.NONE
