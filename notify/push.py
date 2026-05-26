"""
河图 (HeTu) 消息推送 — 企业微信 + 控制台

双模式:
    方式一: 群机器人 Webhook（简单，URL 自带 key）
    方式二: 企业应用（CorpID + Secret，可推给任意成员）

用法:
    pusher = MessagePusher.from_config(cfg)
    await pusher.push("日报内容")
    await pusher.push_markdown("## 标题\n正文")
    await pusher.push_alert("⚠ 止损触发")
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import aiohttp

logger = logging.getLogger("hetu.notify")

WECOM_API_BASE = "https://qyapi.weixin.qq.com/cgi-bin"


class MessagePusher:
    """消息推送器 — 企微 Webhook + 控制台降级。"""

    def __init__(
        self,
        webhook_url: str = "",
        throttle_interval: int = 60,
        max_per_window: int = 10,
    ):
        self._webhook_url = webhook_url
        self._throttle_interval = throttle_interval
        self._max_per_window = max_per_window
        self._send_count = 0
        self._last_send = datetime.min
        self._session: aiohttp.ClientSession | None = None

    # ----------------------------------------------------------------
    # 工厂方法
    # ----------------------------------------------------------------

    @classmethod
    def from_config(cls, cfg: object) -> MessagePusher:
        """从 YAML 创建实例。

        config/default.yaml:
            notifications:
              throttle_window_s: 60
              channels:
                wecom:
                  webhook_url: "https://qyapi.weixin.qq.com/..."
        """
        return cls(
            webhook_url=str(cfg.get("notifications.channels.wecom.webhook_url", "")),
            throttle_interval=int(cfg.get("notifications.throttle_window_s", 60)),
        )

    # ----------------------------------------------------------------
    # 公开接口
    # ----------------------------------------------------------------

    async def push(self, message: str) -> bool:
        """推送纯文本消息。"""
        return await self._try_send("text", {"content": message})

    async def push_markdown(self, content: str) -> bool:
        """推送 Markdown 格式消息。"""
        return await self._try_send("markdown", {"content": content})

    async def push_alert(self, message: str) -> bool:
        """推送告警消息（文本 + @all 可选）。"""
        return await self.push(f"⚠ {message}")

    async def push_report(self, title: str, body: str) -> bool:
        """推送报告（标题 + 正文用 Markdown）。"""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        markdown = f"## {title}\n> {ts}\n\n{body}"
        return await self.push_markdown(markdown)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    # ----------------------------------------------------------------
    # 内部
    # ----------------------------------------------------------------

    async def _try_send(self, msg_type: str, content: dict) -> bool:
        if self._throttle_check():
            logger.warning("推送频率超限，跳过")
            return False

        # 1. 企微 Webhook
        if self._webhook_url:
            try:
                ok = await self._send_webhook(msg_type, content)
                if ok:
                    self._after_send()
                    return True
            except Exception as e:
                logger.warning("企微推送失败: %s", e)

        # 2. 控制台降级
        self._print_console(msg_type, content)
        return True

    async def _send_webhook(self, msg_type: str, content: dict) -> bool:
        payload: dict[str, Any] = {"msgtype": msg_type}
        payload[msg_type] = content

        session = await self._ensure_session()
        async with session.post(self._webhook_url, json=payload) as resp:
            data = await resp.json()
            if data.get("errcode") == 0:
                return True
            logger.warning("企微错误: errcode=%s errmsg=%s",
                          data.get("errcode"), data.get("errmsg"))
            return False

    def _print_console(self, msg_type: str, content: dict) -> None:
        text = content.get("content", str(content))
        logger.info("📤 [控制台推送] %s", text[:200])

    def _throttle_check(self) -> bool:
        now = datetime.now()
        if (now - self._last_send).total_seconds() > self._throttle_interval:
            self._send_count = 0
        return self._send_count >= self._max_per_window

    def _after_send(self) -> None:
        self._send_count += 1
        self._last_send = datetime.now()

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session
