"""
河图 (HeTu) 全系统健康检查

HealthChecker: 定期检查所有组件健康状态，汇总到 REST endpoint。
"""

from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum
from typing import Any

from core.types import ComponentHealth, HealthStatus

logger = logging.getLogger("hetu.health")


class SystemHealth(Enum):
    """系统整体健康状态"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class HealthChecker:
    """
    全系统健康检查器

    检查项:
    - 数据库连接
    - 数据源可用性
    - Broker 连接
    - 风控/止损/持仓服务
    - 调度器状态
    """

    def __init__(self):
        self._components: dict[str, Any] = {}
        self._last_check: float = 0.0
        self._last_full_check: float = 0.0
        self._check_interval: float = 60.0  # seconds

    def register(self, name: str, component: Any) -> None:
        """注册一个需要健康检查的组件"""
        self._components[name] = component
        logger.info("已注册健康检查组件: %s", name)

    def unregister(self, name: str) -> None:
        """取消组件注册"""
        self._components.pop(name, None)
        logger.info("已取消健康检查组件: %s", name)

    async def check_all(self) -> dict[str, ComponentHealth]:
        """检查所有注册组件的健康状态"""
        results: dict[str, ComponentHealth] = {}
        tasks = []

        for name, component in self._components.items():
            tasks.append(self._check_one(name, component))

        check_results = await asyncio.gather(*tasks, return_exceptions=True)

        for name, result in zip(self._components.keys(), check_results):
            if isinstance(result, Exception):
                results[name] = ComponentHealth(
                    name=name,
                    status=HealthStatus.UNHEALTHY,
                    message=str(result),
                )
            else:
                results[name] = result

        self._last_check = time.time()
        self._last_full_check = time.time()
        logger.debug("健康检查完成，共 %d 个组件", len(results))
        return results

    async def _check_one(self, name: str, component: Any) -> ComponentHealth:
        """检查单个组件"""
        try:
            if hasattr(component, "health_check"):
                if asyncio.iscoroutinefunction(component.health_check):
                    result = await asyncio.wait_for(
                        component.health_check(), timeout=10.0
                    )
                else:
                    result = component.health_check()
            else:
                return ComponentHealth(
                    name=name,
                    status=HealthStatus.HEALTHY,
                    message="无健康检查方法",
                )

            if isinstance(result, ComponentHealth):
                return result
            elif isinstance(result, tuple) and len(result) == 2:
                ok, msg = result
                return ComponentHealth(
                    name=name,
                    status=HealthStatus.HEALTHY if ok else HealthStatus.UNHEALTHY,
                    message=str(msg),
                )
            else:
                return ComponentHealth(
                    name=name,
                    status=HealthStatus.HEALTHY if result else HealthStatus.UNHEALTHY,
                    message=str(result),
                )
        except asyncio.TimeoutError:
            return ComponentHealth(
                name=name,
                status=HealthStatus.UNHEALTHY,
                message="健康检查超时",
            )
        except Exception as e:
            return ComponentHealth(
                name=name,
                status=HealthStatus.UNHEALTHY,
                message=str(e),
            )

    def get_system_health(self) -> tuple[SystemHealth, str]:
        """获取整体系统健康状态"""
        results = self.get_last_results()
        if not results:
            return SystemHealth.UNKNOWN, "尚未进行健康检查"

        unhealthy = 0
        for r in results.values():
            if r.status == HealthStatus.UNHEALTHY:
                unhealthy += 1

        if unhealthy == 0:
            return SystemHealth.HEALTHY, "所有组件健康"
        elif unhealthy < len(results):
            return SystemHealth.DEGRADED, f"{unhealthy}/{len(results)} 个组件异常"
        else:
            return SystemHealth.UNHEALTHY, "所有组件不健康"

    async def quick_check(self, component_names: list[str]) -> dict[str, ComponentHealth]:
        """快速检查指定组件"""
        results = {}
        for name in component_names:
            if name in self._components:
                results[name] = await self._check_one(name, self._components[name])
            else:
                results[name] = ComponentHealth(
                    name=name,
                    status=HealthStatus.UNHEALTHY,
                    message="组件未注册",
                )
        return results

    def get_last_results(self) -> dict[str, ComponentHealth]:
        """获取最近一次检查结果（从缓存）"""
        # 返回缓存的最后一次结果
        # 此方法需要配合 check_all 后存储结果
        return getattr(self, "_last_results", {})

    def get_component_status(self, name: str) -> ComponentHealth | None:
        """获取单个组件状态"""
        results = self.get_last_results()
        return results.get(name)

    @property
    def last_check_time(self) -> float:
        """最后一次检查时间"""
        return self._last_check

    @property
    def component_count(self) -> int:
        """注册组件总数"""
        return len(self._components)

    @property
    def healthy_count(self) -> int:
        """健康组件数"""
        results = self.get_last_results()
        return sum(1 for r in results.values() if r.status == HealthStatus.HEALTHY)

    @property
    def unhealthy_count(self) -> int:
        """不健康组件数"""
        results = self.get_last_results()
        return sum(1 for r in results.values() if r.status == HealthStatus.UNHEALTHY)
