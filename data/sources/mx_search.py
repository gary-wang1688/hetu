"""
河图 (HeTu) 妙想资讯搜索客户端

封装 mx-search 的 news-search API，为信号审核提供外部资讯上下文。

API 端点: POST /api/claw/news-search
请求参数: {"query": "<自然语言查询>"}
返回: 研报/新闻/公告列表（含标题、正文、来源、日期）

用法:
    searcher = MXSearchClient.from_config(cfg)
    news = await searcher.search("贵州茅台最新研报")
    events = await searcher.check_signal_context("600519.SH", "BUY")
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import aiohttp
from aiohttp import ClientTimeout

from core.types import Action

logger = logging.getLogger("hetu.search")

MX_DEFAULT_URL = "https://mkapi2.dfcfs.com/finskillshub"
SEARCH_ENDPOINT = "/api/claw/news-search"
REQUEST_TIMEOUT = ClientTimeout(total=30)
MAX_RETRIES = 3
RETRY_DELAY = 1.0

CATEGORY_MAP = {
    "研报": "research",
    "新闻": "news",
    "公告": "announcement",
    "互动易": "interaction",
    "财报": "financial",
}


@dataclass
class NewsItem:
    """资讯条目"""
    title: str = ""
    content: str = ""
    source: str = ""
    date: str = ""
    url: str = ""
    category: str = ""
    relevance_score: float = 0.0


class MXSearchClient:
    """妙想资讯搜索客户端"""

    def __init__(self, api_key: str = "", api_url: str = "") -> None:
        self._api_key = api_key
        self._api_url = api_url or MX_DEFAULT_URL
        self._session: aiohttp.ClientSession | None = None

    @classmethod
    def from_config(cls, cfg: object) -> MXSearchClient:
        """从配置对象创建实例"""
        api_key = getattr(cfg, "mx_api_key", "") if hasattr(cfg, "mx_api_key") else ""
        api_url = getattr(cfg, "mx_api_url", MX_DEFAULT_URL) if hasattr(cfg, "mx_api_url") else MX_DEFAULT_URL
        return cls(api_key=api_key, api_url=api_url)

    async def search(self, query: str, category: str = "", limit: int = 20) -> list[NewsItem]:
        """
        资讯搜索

        Args:
            query: 自然语言查询
            category: 类别过滤 (研报/新闻/公告，空字符串表示全部)
            limit: 返回条数上限

        Returns:
            NewsItem 列表
        """
        response = await self._query(query)
        if not response:
            return []

        outer = response.get("data", {})
        if not isinstance(outer, dict):
            return []

        items = self._parse_response(outer)
        if category:
            items = [i for i in items if i.category == category or category in i.category]
        return items[:limit]

    async def check_signal_context(
        self,
        code: str,
        action: Action,
    ) -> list[NewsItem]:
        """
        查询信号上下文 — 搜索与当前信号相关的最新资讯

        Args:
            code: 股票代码
            action: 交易动作 (BUY/SELL)

        Returns:
            相关资讯列表
        """
        name = self._format_code(code)
        direction = "利好" if action == Action.BUY else "利空"
        query = f"{name} 最新{direction}消息 新闻 公告 研报"
        return await self.search(query)

    async def search_news(self, query: str, limit: int = 20) -> list[NewsItem]:
        """搜索新闻"""
        return await self.search(query, category="新闻", limit=limit)

    async def search_reports(self, query: str, limit: int = 20) -> list[NewsItem]:
        """搜索研报"""
        return await self.search(query, category="研报", limit=limit)

    async def search_announcements(self, query: str, limit: int = 20) -> list[NewsItem]:
        """搜索公告"""
        return await self.search(query, category="公告", limit=limit)

    def _parse_response(self, outer: dict) -> list[NewsItem]:
        """解析搜索 API 响应"""
        items: list[NewsItem] = []

        data_list = outer.get("dataList", [])
        for dl in data_list:
            if not isinstance(dl, dict):
                continue
            head_name = dl.get("headName", [])
            indicators = dl.get("indicators", [])
            if not head_name or not indicators:
                continue

            for indicator in indicators:
                row = self._row_to_dict(head_name, indicator)
                items.append(NewsItem(
                    title=row.get("title", row.get("head", "")),
                    content=row.get("content", row.get("body", "")),
                    source=row.get("source", row.get("infoSource", "")),
                    date=row.get("date", row.get("reportDate", row.get("declarationDate", ""))),
                    url=row.get("url", ""),
                    category=CATEGORY_MAP.get(row.get("category", row.get("type", "")), "news"),
                ))

        # Partial results
        partial = outer.get("partialResults", [])
        for part in partial:
            if isinstance(part, dict):
                dl = part.get("dataList", [])
                for item in dl:
                    if isinstance(item, dict):
                        hn = item.get("headName", [])
                        inds = item.get("indicators", [])
                        for indicator in inds:
                            row = self._row_to_dict(hn, indicator)
                            items.append(NewsItem(
                                title=row.get("title", row.get("head", "")),
                                content=row.get("content", row.get("body", "")),
                                source=row.get("source", ""),
                                date=row.get("date", ""),
                                url=row.get("url", ""),
                                category=CATEGORY_MAP.get(row.get("category", "news"), "news"),
                            ))

        return items

    @staticmethod
    def _row_to_dict(head_name: list, indicator: list) -> dict[str, str]:
        """将 headName + indicator 转换为字典"""
        row: dict[str, str] = {}
        for i, name in enumerate(head_name):
            if i < len(indicator):
                row[name] = str(indicator[i]) if indicator[i] is not None else ""
        return row

    @staticmethod
    def _format_code(code: str) -> str:
        """格式化股票代码"""
        code = code.strip()
        if "." in code:
            name = code.split(".")[0]
            suffix = code.split(".")[1]
            market_name = "上交所" if suffix == "SH" else "深交所"
            return f"{name}({market_name})"
        return code

    async def _query(self, query: str) -> dict | None:
        """发送搜索请求"""
        url = f"{self._api_url}{SEARCH_ENDPOINT}"
        payload = {"query": query}

        for attempt in range(MAX_RETRIES):
            try:
                session = await self._ensure_session()
                async with session.post(url, json=payload, timeout=REQUEST_TIMEOUT) as resp:
                    if resp.status == 429:
                        retry_after = int(resp.headers.get("Retry-After", 60))
                        logger.warning("搜索 API 限流，等待 %ds", retry_after)
                        await asyncio.sleep(retry_after)
                        continue
                    resp.raise_for_status()
                    data = await resp.json()
                    return data
            except aiohttp.ClientError as e:
                logger.warning("搜索 API 请求失败 (第 %d 次): %s", attempt + 1, e)
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
