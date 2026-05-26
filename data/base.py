"""
河图 (HeTu) 数据源基类

所有数据源 Provider 继承此类，自动获得容错能力：
- 指数退避重试（最多 3 次）
- 超时控制
- 请求间隔限流
- 健康检查
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import abstractmethod
from collections.abc import Awaitable, Callable

import pandas as pd

from core.interfaces import DataProvider as DataProviderABC
from core.types import Bar, Quote, DataIssue, ComponentHealth, HealthStatus
from core.errors import DataSourceError, RateLimitError

logger = logging.getLogger("hetu.data")


class BaseDataProvider(DataProviderABC):
    """
    数据源基类 — 内置重试/超时/限流

    子类只需实现 _fetch_* 系列方法（返回 pd.DataFrame），
    基类自动处理: 重试、超时、限流、格式转换、健康检查。
    """

    name: str = "base"
    priority: int = 100
    supports_realtime: bool = False

    def __init__(self, max_retries: int = 3, timeout: float = 30.0, min_interval: float = 3.0) -> None:
        self._max_retries = max_retries
        self._timeout = timeout
        self._min_interval = min_interval
        self._last_call: float = 0.0
        self._healthy: bool = True

    async def get_daily_bars(self, codes: list[str], start: str, end: str) -> dict[str, list[Bar]]:
        df = await self._with_retry(lambda s, e: self._fetch_daily_bars(codes, s, e), start, end)
        return self._df_to_bars(df)

    async def get_minute_bars(self, codes: list[str], start: str, end: str) -> dict[str, list[Bar]]:
        df = await self._with_retry(lambda s, e: self._fetch_minute_bars(codes, s, e), start, end)
        return self._df_to_bars(df)

    async def get_realtime_quote(self, codes: list[str]) -> dict[str, Quote]:
        return await self._with_retry(lambda: self._fetch_realtime_quote(codes))

    async def subscribe_realtime(self, codes: list[str], callback: Callable[[Quote], Awaitable[None]]) -> None:
        if not self.supports_realtime:
            logger.warning("数据源 %s 不支持实时推送", self.name)
            return
        await self._subscribe_realtime(codes, callback)

    async def get_stock_list(self) -> list[dict]:
        return await self._with_retry(lambda: self._fetch_stock_list())

    async def get_index_components(self, index: str) -> list[str]:
        return await self._with_retry(lambda: self._fetch_index_components(index))

    async def get_adjust_factor(self, code: str) -> dict[str, float]:
        return await self._with_retry(lambda: self._fetch_adjust_factor(code))

    async def get_unlock_events(self, code: str) -> list[dict]:
        return await self._with_retry(lambda: self._fetch_unlock_events(code))

    async def health_check(self) -> ComponentHealth:
        try:
            await asyncio.wait_for(self._fetch_stock_list(), timeout=10)
            self._healthy = True
            return ComponentHealth(component="data." + self.name, status=HealthStatus.HEALTHY)
        except Exception as e:
            self._healthy = False
            return ComponentHealth(component="data." + self.name, status=HealthStatus.UNHEALTHY, error=str(e))

    async def validate(self) -> list[DataIssue]:
        """子类可覆盖添加数据源特定的校验"""
        return []

    # ---- 子类需实现的 _fetch_* 方法 ----

    @abstractmethod
    async def _fetch_daily_bars(self, codes: list[str], start: str, end: str) -> pd.DataFrame:
        """返回 DataFrame: code, date, open, high, low, close, volume, amount"""
        ...

    @abstractmethod
    async def _fetch_minute_bars(self, codes: list[str], start: str, end: str) -> pd.DataFrame:
        """返回 DataFrame: code, time, open, high, low, close, volume, amount"""
        ...

    @abstractmethod
    async def _fetch_realtime_quote(self, codes: list[str]) -> dict[str, Quote]:
        ...

    @abstractmethod
    async def _fetch_stock_list(self) -> list[dict]:
        ...

    @abstractmethod
    async def _fetch_index_components(self, index: str) -> list[str]:
        ...

    @abstractmethod
    async def _fetch_adjust_factor(self, code: str) -> dict[str, float]:
        ...

    @abstractmethod
    async def _fetch_unlock_events(self, code: str) -> list[dict]:
        ...

    async def _subscribe_realtime(self, codes: list[str], callback: Callable[[Quote], Awaitable[None]]) -> None:
        """子类覆盖以实现实时推送"""
        ...

    # ---- 内部工具方法 ----

    @staticmethod
    def _df_to_bars(df: pd.DataFrame) -> dict[str, list[Bar]]:
        """DataFrame -> {code: [Bar, ...]}"""
        if df.empty:
            return {}

        required = frozenset({"code", "date", "open", "high", "low", "close", "volume"})
        missing = required - set(df.columns)
        if missing:
            raise DataSourceError(f"缺少必须列: {missing}")

        bars: dict[str, list[Bar]] = {}

        from datetime import date as dt_date

        for _, row in df.iterrows():
            code = str(row.get("code"))
            date_str = str(row.get("date"))

            if code not in bars:
                bars[code] = []

            try:
                d = dt_date.fromisoformat(date_str[:10])
            except Exception:
                d = dt_date.today()

            bars[code].append(Bar(
                code=code,
                date=d,
                open=float(row.get("open")),
                high=float(row.get("high")),
                low=float(row.get("low")),
                close=float(row.get("close")),
                volume=int(row.get("volume", 0)),
                amount=float(row.get("amount", 0.0)),
            ))

        return bars

    async def _with_retry(self, fn: Callable, *args, **kwargs) -> any:
        """指数退避重试包装器"""
        last_error = None

        await self._rate_guard()

        for attempt in range(1, self._max_retries + 2):
            try:
                result = await asyncio.wait_for(
                    fn(*args, **kwargs),
                    timeout=self._timeout
                )
                return result
            except asyncio.TimeoutError:
                last_error = DataSourceError(
                    f"数据源 {self.name} 超时 ({self._timeout}s)",
                    source=self.name,
                )
                logger.warning("数据源 %s 超时 (第 %d 次)", self.name, attempt)
            except RateLimitError as e:
                wait = e.details.get("retry_after", 60) if e.details else 60
                logger.warning("数据源 %s 限流，等待 %ds", self.name, wait)
                await asyncio.sleep(wait)
                continue
            except Exception as e:
                last_error = DataSourceError(
                    f"数据源 {self.name}: {e}",
                    source=self.name,
                )
                logger.warning("数据源 %s 失败 (第 %d 次): %s", self.name, attempt, e)

            if attempt <= self._max_retries:
                delay = 2 ** (attempt - 1)
                await asyncio.sleep(delay)
                await self._rate_guard()

        raise last_error or DataSourceError(
            f"数据源 {self.name}: 重试耗尽",
            source=self.name,
        )

    async def _rate_guard(self) -> None:
        """确保请求间隔 >= min_interval"""
        now = time.time()
        elapsed = now - self._last_call

        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)

        self._last_call = time.time()
