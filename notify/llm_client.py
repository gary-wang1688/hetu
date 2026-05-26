"""
河图 (HeTu) LLM 客户端 — OpenAI 兼容接口

支持所有 OpenAI Chat Completions 兼容的模型:
    DeepSeek, OpenAI, MiniMax, 通义千问, 智谱 GLM 等。

用法:
    client = LLMClient.from_config(cfg)
    answer = await client.ask("分析今日交易表现...")
    data = await client.ask_json("返回持仓的 JSON 分析", schema={...})
"""

from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp

logger = logging.getLogger("hetu.llm")

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"
MAX_RETRIES = 2
RETRY_DELAY = 1.0


class LLMClient:
    """OpenAI 兼容 LLM 客户端。

    自动处理重试、超时、错误码。
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        max_tokens: int = 2000,
        temperature: float = 0.3,
    ):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._session: aiohttp.ClientSession | None = None

    # ----------------------------------------------------------------
    # 工厂方法
    # ----------------------------------------------------------------

    @classmethod
    def from_config(cls, cfg: object) -> LLMClient:
        """从 YAML 配置创建实例。

        config/default.yaml:
            llm:
              provider: deepseek
              model: deepseek-chat
              api_key: "sk-xxx"
              base_url: "https://api.deepseek.com/v1"    # 可选
              max_tokens_per_call: 4000                    # 可选
        """
        return cls(
            api_key=str(cfg.get("llm.api_key", "")),
            base_url=str(cfg.get("llm.base_url", DEFAULT_BASE_URL)),
            model=str(cfg.get("llm.model", DEFAULT_MODEL)),
            max_tokens=int(cfg.get("llm.max_tokens_per_call", 2000)),
        )

    # ----------------------------------------------------------------
    # 核心接口
    # ----------------------------------------------------------------

    async def ask(self, prompt: str, **kwargs) -> str:
        """发送单轮对话，返回文本。"""
        result = await self._chat([{"role": "user", "content": prompt}], **kwargs)
        return result

    async def ask_json(
        self,
        prompt: str,
        *,
        schema: dict | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """发送单轮对话，返回 JSON 对象。

        Args:
            prompt: 用户提示
            schema: 可选的 JSON Schema 用于结构化输出
        """
        messages = [{"role": "user", "content": prompt}]
        if schema:
            # OpenAI-compatible structured output
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": schema},
            }
        else:
            # 普通方式：要求返回 JSON
            messages.append({
                "role": "system",
                "content": "你必须只返回有效的 JSON 对象，不要包含任何 Markdown 代码块或其他文本。",
            })

        raw = await self._chat(messages, **kwargs)
        # 尝试清理可能包裹的 Markdown
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("\n```", 1)[0]
        return json.loads(raw)

    async def _chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        """底层 OpenAI Chat Completions 调用。"""
        url = f"{self._base_url}/chat/completions"
        payload = {
            "model": model or self._model,
            "messages": messages,
            "max_tokens": max_tokens or self._max_tokens,
            "temperature": temperature or self._temperature,
        }

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        session = await self._ensure_session()
        for attempt in range(MAX_RETRIES + 1):
            try:
                async with session.post(url, json=payload, headers=headers) as resp:
                    data = await resp.json()
                    if resp.status != 200:
                        err = data.get("error", {}).get("message", str(data))
                        if resp.status == 429 and attempt < MAX_RETRIES:
                            logger.warning("LLM 限流 (attempt %d)", attempt + 1)
                            import asyncio
                            await asyncio.sleep(RETRY_DELAY * (2 ** attempt))
                            continue
                        raise RuntimeError(f"LLM API 错误 {resp.status}: {err}")
                    return data["choices"][0]["message"]["content"]
            except (aiohttp.ClientError, TimeoutError) as e:
                if attempt < MAX_RETRIES:
                    logger.warning("LLM 网络错误 (attempt %d): %s", attempt + 1, e)
                    import asyncio
                    await asyncio.sleep(RETRY_DELAY)
                else:
                    raise

        raise RuntimeError("LLM 调用失败，已达最大重试次数")

    # ----------------------------------------------------------------
    # 生命周期
    # ----------------------------------------------------------------

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session
