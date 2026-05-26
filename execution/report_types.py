"""
河图 (HeTu) 执行报告类型

StageReport: 单阶段执行报告
DailyReport: 完整交易日报告

原在 daily_orchestrator.py 中，拆分至此以控制文件大小。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StageReport:
    """单阶段执行报告。"""
    stage: str = ""
    success: bool = True
    elapsed_ms: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


@dataclass
class DailyReport:
    """完整交易日报告。"""
    date: str = ""
    account_name: str = ""
    initial_capital: float = 0.0
    total_assets: float = 0.0
    avail_balance: float = 0.0
    total_pos_value: float = 0.0
    daily_pnl: float = 0.0
    total_pnl: float = 0.0
    nav: float = 0.0
    positions: list[dict[str, Any]] = field(default_factory=list)
    signals_generated: int = 0
    signals_approved: int = 0
    orders_submitted: int = 0
    orders_filled: int = 0
    stop_events: int = 0
    news_summary: str = ""
    stages: list[StageReport] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    summary: str = ""
