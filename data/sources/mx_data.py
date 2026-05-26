"""
河图 (HeTu) 妙想金融数据源

实现 DataProvider 接口，封装 mx-data 的 toolQuery API。
用自然语言查询结构化金融数据，作为 akshare 的补充数据源。

API 端点: POST /api/claw/query
请求参数: {"toolQuery": "<自然语言>"}
返回格式: 日期-行 table（headName + 指标数组）

覆盖方法:
    get_daily_bars()     — 日线行情（自然语言 -> Bar 列表）
    get_stock_list()     — 全 A 股列表
    get_index_components() — 指数成分股
    get_adjust_factor()  — 复权因子
    get_unlock_events()  — 解禁事件
    health_check()       — 连通性验证

未实现（mx-data API 不支持）:
    get_minute_bars()    — 分钟线 -> raise NotImplementedError
    subscribe_realtime() — 实时推流 -> raise NotImplementedError
    get_realtime_quote() — 快照 -> 可选实现
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
from aiohttp import ClientTimeout

from core.interfaces import DataProvider
from core.types import Bar, Quote, DataIssue, ComponentHealth, HealthStatus

logger = logging.getLogger("hetu.data.mx")

MX_DEFAULT_URL = "https://mkapi2.dfcfs.com/finskillshub"
QUERY_ENDPOINT = "/api/claw/query"
REQUEST_TIMEOUT = ClientTimeout(total=60)
MAX_RETRIES = 3
RETRY_DELAY = 1.0

_QUERY_TEMPLATES = {
    "daily_bars": "{code} {start}到{end}每个交易日的开盘价 最高价 最低价 收盘价 成交量 成交额",
    "stock_list": "全部A股列表",
    "index_components": "{index_name}成分股",
    "adjust_factor": "{code}复权因子",
    "unlock_events": "{code}限售解禁事件",
}


class MXDataProvider(DataProvider):
    """妙想金融数据源。

    作为 akshare 的补充，提供基本面多期数据和自然语言查询能力。

    用法:
        provider = MXDataProvider.from_config(cfg)
        bars = await provider.get_daily_bars(["600519.SH"], "2026-01-01", "2026-05-26")
    """

    name: str = "mx_data"
    priority: int = 90
    supports_realtime: bool = False

    def __init__(self, api_key: str = "", api_url: str = "") -> None:
        self._api_key = api_key
        self._api_url = api_url or MX_DEFAULT_URL
        self._session: aiohttp.ClientSession | None = None

    @classmethod
    def from_config(cls, cfg: object, overrides: object = None) -> MXDataProvider:
        """从配置对象创建实例"""
        api_key = getattr(cfg, "mx_api_key", "") if hasattr(cfg, "mx_api_key") else ""
        api_url = getattr(cfg, "mx_api_url", MX_DEFAULT_URL) if hasattr(cfg, "mx_api_url") else MX_DEFAULT_URL
        if overrides and hasattr(overrides, "api_key"):
            api_key = overrides.api_key
        if overrides and hasattr(overrides, "api_url"):
            api_url = overrides.api_url
        return cls(api_key=api_key, api_url=api_url)

    async def get_daily_bars(self, codes: list[str], start: str, end: str) -> dict[str, list[Bar]]:
        """
        获取日线 K 线数据。

        Args:
            codes: 股票代码列表 (如 ["600519.SH", "000001.SZ"])
            start: 开始日期 "YYYY-MM-DD"
            end: 结束日期 "YYYY-MM-DD"

        Returns:
            {code: [Bar, ...]}
        """
        result: dict[str, list[Bar]] = {}
        for code in codes:
            try:
                formatted_code = _format_code(code)
                bars = await self._fetch_daily_bars(formatted_code, start, end)
                result[code] = bars
            except Exception as e:
                logger.warning("获取 %s 日线失败: %s", code, e)
                result[code] = []
        return result

    async def get_minute_bars(self, codes: list[str], start: str, end: str) -> dict[str, list[Bar]]:
        raise NotImplementedError("MX Data API 不支持分钟线")

    async def get_realtime_quote(self, codes: list[str]) -> dict[str, Quote]:
        logger.warning("MX Data API 不支持实时行情")
        return {}

    async def subscribe_realtime(self, codes: list[str], callback) -> None:
        raise NotImplementedError("MX Data API 不支持实时推流")

    async def get_stock_list(self) -> list[dict]:
        """获取全 A 股列表"""
        question = _QUERY_TEMPLATES["stock_list"]
        raw = await self.query_raw(question)
        if not raw:
            return []
        return [
            {"code": _get_field(r, "code", ""), "name": _get_field(r, "name", "")}
            for r in raw
        ]

    async def get_index_components(self, index: str) -> list[str]:
        """获取指数成分股

        Args:
            index: 指数名称 (如 "沪深300", "中证500")
        """
        question = _QUERY_TEMPLATES["index_components"].format(index_name=index)
        raw = await self.query_raw(question)
        if not raw:
            return []
        codes: list[str] = []
        for r in raw:
            code = _get_field(r, "code", "")
            if code:
                codes.append(_format_code(code))
        return codes

    async def get_adjust_factor(self, code: str) -> dict[str, float]:
        """获取复权因子

        Returns:
            {date: factor, ...}
        """
        formatted = _format_code(code)
        question = _QUERY_TEMPLATES["adjust_factor"].format(code=formatted)
        raw = await self.query_raw(question)
        if not raw:
            return {}
        result: dict[str, float] = {}
        for r in raw:
            date = _get_field(r, "date", "")
            factor = _safe_float(_get_field(r, "factor"))
            if date and factor is not None:
                result[date] = factor
        return result

    async def get_unlock_events(self, code: str) -> list[dict]:
        """获取限售解禁事件"""
        formatted = _format_code(code)
        question = _QUERY_TEMPLATES["unlock_events"].format(code=formatted)
        raw = await self.query_raw(question)
        return raw if raw else []

    async def health_check(self) -> ComponentHealth:
        try:
            raw = await self.query_raw("测试连通性")
            if raw is not None:
                return ComponentHealth(component="data.mx_data", status=HealthStatus.HEALTHY)
            return ComponentHealth(component="data.mx_data", status=HealthStatus.DEGRADED, error="空响应")
        except Exception as e:
            return ComponentHealth(component="data.mx_data", status=HealthStatus.UNHEALTHY, error=str(e))

    async def validate(self) -> list[DataIssue]:
        return []

    async def close(self) -> None:
        """关闭 HTTP session"""
        if self._session:
            await self._session.close()
            self._session = None

    async def query_raw(self, question: str) -> list[dict[str, Any]]:
        """
        原生查询接口

        Args:
            question: 自然语言查询

        Returns:
            结构化数据行列表
        """
        response = await self._query(question)
        if not response:
            return []
        return _table_to_rows(response)

    async def _fetch_daily_bars(self, code: str, start: str, end: str) -> list[Bar]:
        """获取单只股票日线 K 线"""
        question = _QUERY_TEMPLATES["daily_bars"].format(code=code, start=start, end=end)
        raw = await self.query_raw(question)
        if not raw:
            return []
        bars: list[Bar] = []
        for row in raw:
            bar = _row_to_bar(row, code)
            if bar:
                bars.append(bar)
        return bars

    async def _query(self, question: str) -> dict | None:
        """发送查询请求到 MX API"""
        url = f"{self._api_url}{QUERY_ENDPOINT}"
        payload = {"toolQuery": question}

        for attempt in range(MAX_RETRIES):
            try:
                session = await self._ensure_session()
                async with session.post(url, json=payload, timeout=REQUEST_TIMEOUT) as resp:
                    if resp.status == 429:
                        retry_after = int(resp.headers.get("Retry-After", 60))
                        logger.warning("MX API 限流，等待 %ds", retry_after)
                        await asyncio.sleep(retry_after)
                        continue
                    resp.raise_for_status()
                    data = await resp.json()
                    return data
            except aiohttp.ClientError as e:
                logger.warning("MX API 请求失败 (第 %d 次): %s", attempt + 1, e)
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_DELAY * (2 ** attempt))
                else:
                    raise

        return None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """确保 session 存在"""
        if self._session is None or self._session.closed:
            headers = {}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"
            self._session = aiohttp.ClientSession(headers=headers)
        return self._session


# ---- 模块级工具函数 ----

UNIT_MULTIPLIERS: dict[str, float] = {
    "亿": 1e8, "亿元": 1e8, "亿股": 1e8,
    "万": 1e4, "万元": 1e4, "万股": 1e4,
    "%": 1.0,
    "元": 1.0, "股": 1.0, "手": 1.0,
    "倍": 1.0, "次": 1.0, "天": 1.0, "点": 1.0,
}


def _table_to_rows(raw: dict) -> list[dict[str, Any]]:
    """将 MX API 的 table 响应转换为行列表
    
    解析 dataList 或 partialResults 中的表格数据。
    """
    outer = raw.get("data", {})
    if not isinstance(outer, dict):
        return []

    # Try dataList first
    data_list = outer.get("dataList", [])
    if data_list:
        return _parse_data_list(data_list)

    # Try partialResults
    partial = outer.get("partialResults", [])
    if partial:
        rows: list[dict[str, Any]] = []
        for part in partial:
            dl = part.get("dataList", [])
            rows.extend(_parse_data_list(dl))
        return rows

    return []


def _parse_data_list(data_list: list) -> list[dict[str, Any]]:
    """解析 dataList 中的表格数据"""
    if not data_list:
        return []

    first = data_list[0]
    head_name = first.get("headName", [])
    indicators = first.get("indicators", [])

    if not head_name or not indicators:
        return []

    rows: list[dict[str, Any]] = []
    for indicator in indicators:
        row: dict[str, Any] = {}
        for i, name in enumerate(head_name):
            if i < len(indicator):
                row[name] = indicator[i]
        rows.append(row)

    return rows


def _clean_value(val: Any) -> str:
    """清理单元格值为字符串"""
    if val is None:
        return ""
    return str(val).strip()


def _safe_float_with_unit(val: Any) -> float:
    """安全转换为 float，支持带单位值如 '1285.88元', '277.1万'"""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)

    s = str(val).strip()
    if not s:
        return 0.0

    # Check for explicit unit multipliers
    for unit, mult in UNIT_MULTIPLIERS.items():
        if s.endswith(unit):
            num_str = s[:-len(unit)].strip()
            try:
                return float(num_str) * mult
            except ValueError:
                return 0.0

    # Plain number or percentage
    s = s.replace(",", "").replace("，", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _row_to_bar(row: dict[str, Any], code: str) -> Bar | None:
    """将 MX API 返回的行数据转换为 Bar 对象"""
    date = _clean_value(_get_field(row, "date", ""))
    if not date:
        return None

    try:
        from datetime import date as dt_date
        d = dt_date.fromisoformat(date[:10])
    except ValueError:
        return None

    return Bar(
        code=code,
        date=d,
        open=_safe_float_with_unit(_get_field(row, "open", 0)),
        high=_safe_float_with_unit(_get_field(row, "high", 0)),
        low=_safe_float_with_unit(_get_field(row, "low", 0)),
        close=_safe_float_with_unit(_get_field(row, "close", 0)),
        volume=_safe_int(_get_field(row, "volume", 0)) or 0,
        amount=_safe_float_with_unit(_get_field(row, "amount", 0)),
    )


def _format_code(code: str) -> str:
    """格式化股票代码，确保带交易所后缀 (.SH/.SZ)"""
    code = code.strip()
    if "." in code:
        return code
    if code.startswith("6") or code.startswith("5"):
        return f"{code}.SH"
    return f"{code}.SZ"


def _safe_float(val: Any) -> float | None:
    """安全转换为 float"""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_int(val: Any) -> int | None:
    """安全转换为 int"""
    if val is None:
        return None
    try:
        return int(float(str(val)))
    except (ValueError, TypeError):
        return None


def _get_field(row: dict, key: str, default: Any = "") -> Any:
    """多键查找（兼容不同字段名）"""
    if key in row:
        return row[key]
    # Try common aliases
    aliases = {
        "code": ["symbol", "stockCode", "股票代码"],
        "name": ["stockName", "股票名称"],
        "date": ["tradeDate", "日期", "time"],
        "open": ["openingPrice", "开盘价", "开盘"],
        "high": ["highestPrice", "最高价", "最高"],
        "low": ["lowestPrice", "最低价", "最低"],
        "close": ["closingPrice", "收盘价", "收盘", "lastPrice"],
        "volume": ["tradeVolume", "成交量", "pvolume"],
        "amount": ["tradeAmount", "成交额"],
        "factor": ["adjustFactor", "复权因子"],
    }
    for alias in aliases.get(key, []):
        if alias in row:
            return row[alias]
    return default
