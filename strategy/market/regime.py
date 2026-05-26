"""市场状态识别。"""
from dataclasses import dataclass
@dataclass
class MarketRegime:
    trend: str = 'RANGING'
    strength: float = 0.5
