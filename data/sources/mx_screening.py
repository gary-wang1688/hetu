"""
河图 (HeTu) 妙想智能选股器

封装 mx-xuangu 的 stock-screen API，将自然语言选股条件转换为结构化股票列表。

API 端点: POST /api/claw/stock-screen
请求参数: {"keyword": "<自然语言选股条件>"}
返回: dataList (结构化) 或 partialResults (Markdown 回退)

用法:
    screener = MXScreener.from_config(cfg)
    codes = await screener.screen("市盈率<20 涨幅>2% 非ST A股")
    top10 = await screener.screen_top("沪深300成分股 股息率>3%", n=10)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
from aiohttp import ClientTimeout

logger = logging.getLogger("hetu.screening")

MX_DEFAULT_URL = "https://mkapi2.dfcfs.com/finskillshub"
SCREEN_ENDPOINT = "/api/claw/stock-screen"
REQUEST_TIMEOUT = ClientTimeout(total=30)
MAX_RETRIES = 3
RETRY_DELAY = 1.0

_FIELD_CODE = "SECURITY_CODE"
_FIELD_NAME = "SECURITY_SHORT_NAME"
_FIELD_PRICE = "NEWEST_PRICE"
_FIELD_CHG = "CHG"
_FIELD_PCHG = "PCHG"


class MXScreener:
    """妙想智能选股器"""

    def __init__(self, api_key: str = "", api_url: str = "") -> None:
        self._api_key = api_key
        self._api_url = api_url or MX_DEFAULT_URL
        self._session: aiohttp.ClientSession | None = None

    @classmethod
    def from_config(cls, cfg: object) -> MXScreener:
        """从配置对象创建实例"""
        api_key = getattr(cfg, "mx_api_key", "") if hasattr(cfg, "mx_api_key") else ""
        api_url = getattr(cfg, "mx_api_url", MX_DEFAULT_URL) if hasattr(cfg, "mx_api_url") else MX_DEFAULT_URL
        return cls(api_key=api_key, api_url=api_url)

    async def screen(self, condition: str) -> list[dict[str, Any]]:
        """
        自然语言选股

        Args:
            condition: 选股条件 (如 "市盈率<20 市盈率>0 非ST A股")

        Returns:
            股票列表，每只股票包含 code, name, price, pct_change 等字段
        """
        response = await self._query(condition)
        if not response:
            return []
        return self._parse_response(response)

    async def screen_top(self, condition: str, n: int = 10) -> list[str]:
        """
        选股并返回 Top N 代码

        Args:
            condition: 选股条件
            n: 返回数量

        Returns:
            股票代码列表
        """
        results = await self.screen(condition)
        return [
            self._format_code(r.get(_FIELD_CODE, ""))
            for r in results[:n]
            if r.get(_FIELD_CODE)
        ]

    @staticmethod
    def _format_code(code: str) -> str:
        """格式化股票代码"""
        code = code.strip()
        if "." in code:
            return code
        if code.startswith("6") or code.startswith("5"):
            return f"{code}.SH"
        return f"{code}.SZ"

    def _parse_response(self, response: dict) -> list[dict[str, Any]]:
        """解析选股 API 响应"""
        outer = response.get("data", {})
        if not isinstance(outer, dict):
            return []

        # Try dataList (structured)
        data_list = outer.get("dataList", [])
        if data_list:
            return self._parse_data_list(data_list)

        # Try partialResults (markdown fallback)
        partial = outer.get("partialResults", [])
        if partial:
            return self._parse_partial(partial)

        return []

    def _parse_data_list(self, data_list: list) -> list[dict[str, Any]]:
        """解析结构化 dataList"""
        if not data_list:
            return []

        results: list[dict[str, Any]] = []
        for item in data_list:
            if not isinstance(item, dict):
                continue
            head_name = item.get("headName", [])
            indicators = item.get("indicators", [])
            if not head_name or not indicators:
                continue

            for indicator in indicators:
                row: dict[str, Any] = {}
                for i, name in enumerate(head_name):
                    if i < len(indicator):
                        row[name] = indicator[i]
                results.append(row)

        return results

    def _parse_partial(self, partial: list) -> list[dict[str, Any]]:
        """解析 partialResults (Markdown 格式)"""
        results: list[dict[str, Any]] = []
        for part in partial:
            if isinstance(part, dict):
                data_list = part.get("dataList", [])
                results.extend(self._parse_data_list(data_list))
        return results

    async def _query(self, keyword: str) -> dict | None:
        """发送选股请求"""
        url = f"{self._api_url}{SCREEN_ENDPOINT}"
        payload = {"keyword": keyword}

        for attempt in range(MAX_RETRIES):
            try:
                session = await self._ensure_session()
                async with session.post(url, json=payload, timeout=REQUEST_TIMEOUT) as resp:
                    if resp.status == 429:
                        retry_after = int(resp.headers.get("Retry-After", 60))
                        logger.warning("选股 API 限流，等待 %ds", retry_after)
                        await asyncio.sleep(retry_after)
                        continue
                    resp.raise_for_status()
                    data = await resp.json()
                    return data
            except aiohttp.ClientError as e:
                logger.warning("选股 API 请求失败 (第 %d 次): %s", attempt + 1, e)
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_DELAY * (2 ** attempt))

        return None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """确保 session 存在"""
        if self._session is None or self._session.closed:
            headers = {}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"
            self._session = aiohttp.ClientSession(headers=headers)
        return self._session

    async def close(self) -> None:
        """关闭 HTTP session"""
        if self._session:
            await self._session.close()
            self._session = None

    async def screen_codes(self, condition: str) -> list[str]:
        """选股返回代码列表。"""
        return await self.screen_top(condition, n=200)


# ============================================================
# 内部工具函数 (供测试)
# ============================================================

def _build_column_map(cols: list[dict]) -> dict[str, str]:
    m = {}
    for col in cols:
        key = col.get("key") or col.get("field", "")
        title = col.get("title", "")
        if key and title:
            m[key] = title
    return m


def _parse_stock_row(row: dict, col_map: dict) -> dict | None:
    code = row.get("SECURITY_CODE")
    if not code:
        return None
    return {
        "code": _format_stock_code(code),
        "name": row.get("SECURITY_SHORT_NAME", ""),
        "price": float(row.get("NEWEST_PRICE", 0) or 0),
        "chg_pct": float(row.get("PCHG", 0) or 0),
        "chg_val": float(row.get("CHG", 0) or 0),
    }




def _parse_markdown_table(md: str) -> list[dict]:
    if not md or "|" not in md:
        return []
    lines = md.strip().split("\n")
    if len(lines) < 3:
        return []
    headers = [h.strip() for h in lines[0].split("|") if h.strip()]
    results = []
    for line in lines[2:]:
        cols = [c.strip() for c in line.split("|") if c.strip()]
        if len(cols) < len(headers):
            continue
        row = {}
        for i, h in enumerate(headers):
            row[h] = cols[i] if i < len(cols) else ""
        code = row.get("代码", "")
        chg_str = row.get("涨跌幅", "0%")
        pct = _parse_pct(chg_str)
        results.append({
            "code": _format_stock_code(code),
            "name": row.get("名称", ""),
            "chg_pct": pct,
        })
    return results


def _format_stock_code(code: str) -> str:
    code = code.strip()
    if "." in code:
        return code
    if code.startswith(("6", "5", "9")):
        return f"{code}.SH"
    return f"{code}.SZ"


def _parse_pct(val: str | float | None) -> float:
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace("+", "").replace("%", "")
    try:
        return float(s)
    except ValueError:
        return 0.0
