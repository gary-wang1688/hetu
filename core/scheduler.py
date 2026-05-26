"""
河图 (HeTu) 系统调度器

职责:
- 定义全链路时间节点的调度顺序
- 链式依赖: 上游失败 → 下游自动降级
- 交易日历感知: 非交易日不执行
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, time, datetime
from enum import Enum
from typing import Callable, Any

logger = logging.getLogger("hetu.scheduler")


# ============================================================
# 调度阶段
# ============================================================

class PipelinePhase(str, Enum):
    PRE_OPEN = "PRE_OPEN"
    OPEN = "OPEN"
    MORNING = "MORNING"
    MIDDAY = "MIDDAY"
    AFTERNOON = "AFTERNOON"
    POST_CLOSE = "POST_CLOSE"


class FailurePolicy(str, Enum):
    RETRY = "RETRY"
    SKIP = "SKIP"
    HALT = "HALT"
    DEGRADE = "DEGRADE"


@dataclass
class TaskSpec:
    name: str
    phase: PipelinePhase
    time: time
    handler: Callable | None = None
    depends_on: list[str] = field(default_factory=list)
    retry_count: int = 2
    retry_delay: float = 5.0
    failure_policy: FailurePolicy = FailurePolicy.SKIP
    timeout_seconds: float = 300.0
    enabled: bool = True


@dataclass
class TaskResult:
    task_name: str
    success: bool
    data: Any = None
    error: str | None = None
    duration_seconds: float = 0.0
    retries_used: int = 0
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class ScheduleResult:
    phase: PipelinePhase
    date: date
    results: dict[str, TaskResult] = field(default_factory=dict)
    halted: bool = False
    halt_reason: str = ""


# ============================================================
# 默认调度表
# ============================================================

DEFAULT_SCHEDULE: list[TaskSpec] = [
    TaskSpec(name="sector_filter",     phase=PipelinePhase.PRE_OPEN,  time=time(8, 0)),
    TaskSpec(name="lhb_analysis",      phase=PipelinePhase.PRE_OPEN,  time=time(8, 30), depends_on=["sector_filter"]),
    TaskSpec(name="deep_research",     phase=PipelinePhase.PRE_OPEN,  time=time(9, 10), depends_on=["sector_filter"]),
    TaskSpec(name="open_auction",      phase=PipelinePhase.OPEN,      time=time(9, 25), depends_on=["deep_research"], failure_policy=FailurePolicy.HALT),
    TaskSpec(name="execute_entry",     phase=PipelinePhase.OPEN,      time=time(9, 35), depends_on=["open_auction"], failure_policy=FailurePolicy.RETRY),
    TaskSpec(name="position_monitor",  phase=PipelinePhase.MORNING,   time=time(10, 0)),
    TaskSpec(name="capital_flow",      phase=PipelinePhase.AFTERNOON, time=time(13, 5)),
    TaskSpec(name="afternoon_scan",    phase=PipelinePhase.AFTERNOON, time=time(14, 20)),
    TaskSpec(name="cancel_stale",      phase=PipelinePhase.AFTERNOON, time=time(14, 58), depends_on=["position_monitor"], failure_policy=FailurePolicy.RETRY),
    TaskSpec(name="limit_up_review",   phase=PipelinePhase.POST_CLOSE,time=time(15, 10)),
    TaskSpec(name="post_market_report",phase=PipelinePhase.POST_CLOSE,time=time(16, 10), depends_on=["limit_up_review"]),
    TaskSpec(name="stock_selection",   phase=PipelinePhase.POST_CLOSE,time=time(17, 0)),
    TaskSpec(name="daily_summary",     phase=PipelinePhase.POST_CLOSE,time=time(20, 0)),
]


def _time_to_phase(hour: int, minute: int) -> PipelinePhase:
    t = (hour, minute)
    if t < (9, 25):
        return PipelinePhase.PRE_OPEN
    elif t < (9, 35):
        return PipelinePhase.OPEN
    elif t < (11, 30):
        return PipelinePhase.MORNING
    elif t < (13, 0):
        return PipelinePhase.MIDDAY
    elif t < (15, 0):
        return PipelinePhase.AFTERNOON
    else:
        return PipelinePhase.POST_CLOSE


# ============================================================
# 调度引擎
# ============================================================

class SystemScheduler:
    def __init__(self, calendar=None):
        self._tasks: dict[str, TaskSpec] = {}
        self._calendar = calendar
        self._last_results: dict[str, TaskResult] = {}

    def register(self, task: TaskSpec) -> None:
        self._tasks[task.name] = task
        logger.debug("注册任务: %s @ %s", task.name, task.time)

    def register_tasks(self, tasks: list[TaskSpec]) -> None:
        for t in tasks:
            self.register(t)

    async def run_phase(self, phase: PipelinePhase) -> ScheduleResult:
        today = date.today()
        if self._calendar and not self._calendar(today):
            logger.info("非交易日 %s, 跳过", today)
            return ScheduleResult(phase=phase, date=today)

        phase_tasks = [t for t in self._tasks.values() if t.phase == phase and t.enabled]
        if not phase_tasks:
            return ScheduleResult(phase=phase, date=today)

        result = ScheduleResult(phase=phase, date=today)
        done: set[str] = set()

        while len(done) < len(phase_tasks):
            ready = [t for t in phase_tasks if t.name not in done and all(d in done for d in t.depends_on)]
            if not ready:
                break

            tasks = [self._execute_task(t) for t in ready]
            task_results = await asyncio.gather(*tasks, return_exceptions=True)

            for task, tr in zip(ready, task_results):
                if isinstance(tr, Exception):
                    tr = TaskResult(task_name=task.name, success=False, error=str(tr))
                result.results[task.name] = tr
                self._last_results[task.name] = tr
                done.add(task.name)

                if not tr.success and task.failure_policy == FailurePolicy.HALT:
                    result.halted = True
                    result.halt_reason = f"{task.name}: {tr.error}"
                    return result

        return result

    async def run_all(self) -> dict[PipelinePhase, ScheduleResult]:
        results = {}
        for phase in PipelinePhase:
            if phase == PipelinePhase.MIDDAY:
                continue
            results[phase] = await self.run_phase(phase)
            if results[phase].halted:
                break
        return results

    async def _execute_task(self, task: TaskSpec) -> TaskResult:
        start = datetime.now()
        retries = 0

        while True:
            try:
                if task.handler is None:
                    result = None
                else:
                    result = await asyncio.wait_for(
                        task.handler(self._last_results),
                        timeout=task.timeout_seconds,
                    )
                duration = (datetime.now() - start).total_seconds()
                return TaskResult(task_name=task.name, success=True, data=result, duration_seconds=duration, retries_used=retries)
            except asyncio.TimeoutError:
                if retries < task.retry_count:
                    retries += 1
                    await asyncio.sleep(task.retry_delay)
                else:
                    return TaskResult(task_name=task.name, success=False, error="超时", retries_used=retries)
            except Exception as e:
                if retries < task.retry_count:
                    retries += 1
                    await asyncio.sleep(task.retry_delay)
                else:
                    return TaskResult(task_name=task.name, success=False, error=str(e), retries_used=retries)

    def list_tasks(self, phase: PipelinePhase | None = None) -> list[TaskSpec]:
        if phase:
            return [t for t in self._tasks.values() if t.phase == phase]
        return list(self._tasks.values())

    @classmethod
    def from_config(cls, cfg: object, calendar=None) -> SystemScheduler:
        scheduler = cls(calendar=calendar)
        scheduler.register_tasks(DEFAULT_SCHEDULE)

        strategy_names = [
            "bollinger", "volume_price", "turtle", "ma_cross",
            "momentum", "limit_up", "pead", "cb_dual_low",
            "dividend", "sector_rotation",
        ]

        for name in strategy_names:
            windows = cfg.get(f"strategy.{name}.signal_windows", None)
            if not windows:
                continue
            for win_str in windows:
                try:
                    h, m = map(int, win_str.split(":"))
                    phase = _time_to_phase(h, m)
                    task = TaskSpec(
                        name=f"signal_node_{name}_{h:02d}{m:02d}",
                        phase=phase,
                        time=time(h, m),
                        depends_on=[],
                        failure_policy=FailurePolicy.SKIP,
                        timeout_seconds=180,
                    )
                    scheduler.register(task)
                except (ValueError, TypeError):
                    logger.warning("无效 signal_window: %s.%s", name, win_str)

        return scheduler
