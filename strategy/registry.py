"""策略注册中心。"""
from strategy.single.bollinger import BollingerStrategy
from strategy.single.turtle import TurtleStrategy
from strategy.single.ma_cross import MACrossStrategy
from strategy.single.momentum import MomentumStrategy
from strategy.single.volume_price import VolumePriceStrategy
from strategy.single.dividend import DividendStrategy
from strategy.single.cb_dual_low import CBDualLowStrategy
from strategy.single.pead import PEADStrategy
from strategy.single.limit_up import LimitUpStrategy
from strategy.single.sector_rotation import SectorRotationStrategy

REGISTRY = {
    'bollinger': BollingerStrategy,
    'turtle': TurtleStrategy,
    'ma_cross': MACrossStrategy,
    'momentum': MomentumStrategy,
    'volume_price': VolumePriceStrategy,
    'dividend': DividendStrategy,
    'cb_dual_low': CBDualLowStrategy,
    'pead': PEADStrategy,
    'limit_up': LimitUpStrategy,
    'sector_rotation': SectorRotationStrategy,
}
