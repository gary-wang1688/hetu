"""
单策略模块 — 10 个独立交易策略。

可插拔架构: 每个策略通过 DailyOrchestrator._get_strategy_class(name) 懒加载。
策略实例化时从 YAML strategy.{name}.* 自动加载参数。
"""

__doc__ = '单策略模块 — 10 个独立交易策略。\n\n可插拔架构: 每个策略通过 DailyOrchestrator._get_strategy_class(name) 懒加载。\n策略实例化时从 YAML strategy.{name}.* 自动加载参数。\n'
