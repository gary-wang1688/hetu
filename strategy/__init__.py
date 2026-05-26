"""
河图 (HeTu) 策略层

├── single/     10 个独立策略 → 通过 DailyOrchestrator._get_strategy_class() 懒加载
├── base.py     策略基类 + 参数 schema
├── runner.py   批量执行策略 on_bar()
├── advisor.py  LLM 信号审核员（含 mx-search 资讯增强）
├── registry.py 策略注册中心（已实现，待集成到 orchestrator）
├── prerequisite.py 策略前置条件检查（已实现，待集成）
└── industry.py  市场板块分类器（代码前缀 → 板块标签）

数据源客户端已移至 data/sources/:
    from data.sources.mx_screening import MXScreener
    from data.sources.mx_search import MXSearchClient

集成路径（v2.4 目标）:
    DailyOrchestrator 当前使用硬编码 _STRATEGY_CLASSES 字典选中策略，
    未来应通过 StrategyRegistry 统一注册+发现，并用 PrerequisiteChecker
    在策略执行前校验参数完整性和信号合理性。
"""

__doc__ = '\n河图 (HeTu) 策略层\n\n├── single/     10 个独立策略 → 通过 DailyOrchestrator._get_strategy_class() 懒加载\n├── base.py     策略基类 + 参数 schema\n├── runner.py   批量执行策略 on_bar()\n├── advisor.py  LLM 信号审核员（含 mx-search 资讯增强）\n├── registry.py 策略注册中心（已实现，待集成到 orchestrator）\n├── prerequisite.py 策略前置条件检查（已实现，待集成）\n└── industry.py  市场板块分类器（代码前缀 → 板块标签）\n\n数据源客户端已移至 data/sources/:\n    from data.sources.mx_screening import MXScreener\n    from data.sources.mx_search import MXSearchClient\n\n集成路径（v2.4 目标）:\n    DailyOrchestrator 当前使用硬编码 _STRATEGY_CLASSES 字典选中策略，\n    未来应通过 StrategyRegistry 统一注册+发现，并用 PrerequisiteChecker\n    在策略执行前校验参数完整性和信号合理性。\n'
