"""
河图 (HeTu) 多通道持仓对账

Sync: 多 Broker 通道持仓汇总 + 不一致检测 + 严重告警
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from portfolio.tracker import PortfolioTracker

logger = logging.getLogger("hetu.sync")


@dataclass
class ReconciliationResult:
    """对账结果"""
    matched: bool = True
    mismatches: list[dict] = field(default_factory=list)
    severe: bool = False
    summary: str = ""


class PositionSynchronizer:
    """
    多通道持仓同步器

    功能:
    - 从多个 Broker 拉取持仓
    - 跨通道对比检测不一致
    - 严重不一致 -> 进入 safe_mode
    """

    DIFF_TOLERANCE = 5  # 股数差异容忍度

    def __init__(self, tracker: PortfolioTracker):
        self._tracker = tracker
        self._channel_positions: dict[str, list[dict]] = {}

    async def sync_from_broker(self, channel: str) -> list[dict]:
        """从指定 Broker 通道拉取并更新持仓

        Args:
            channel: Broker 通道名称（如 'mx', 'qmt', 'paper'）

        Returns:
            从该通道获取的持仓列表
        """
        positions = []
        try:
            # 尝试通过 tracker 已有的 broker 连接获取持仓
            broker = getattr(self._tracker, f"_{channel}_broker", None)
            if broker and hasattr(broker, "get_positions"):
                raw = await broker.get_positions()
                positions = [
                    {
                        "code": p.code,
                        "shares": p.shares,
                        "avg_cost": p.avg_cost,
                        "current_price": p.current_price,
                        "market_value": p.market_value,
                        "channel": channel,
                    }
                    for p in raw
                ]
            else:
                logger.warning("通道 %s 未配置 Broker，无法拉取持仓", channel)
        except Exception as e:
            logger.error("同步通道 %s 持仓失败: %s", channel, e)

        self._channel_positions[channel] = positions
        # 同时更新 tracker
        self._tracker.update_from_broker(positions, channel)
        return positions

    def reconcile(self) -> ReconciliationResult:
        """跨通道对账

        Returns:
            ReconciliationResult 包含对账结果
        """
        result = ReconciliationResult()

        channels = list(self._channel_positions.keys())
        if len(channels) < 2:
            result.summary = "只有单一通道，无需对账"
            return result

        for i in range(len(channels)):
            for j in range(i + 1, len(channels)):
                ch_a = channels[i]
                ch_b = channels[j]
                pos_a = {p["code"]: p for p in self._channel_positions.get(ch_a, [])}
                pos_b = {p["code"]: p for p in self._channel_positions.get(ch_b, [])}

                all_codes = set(pos_a.keys()) | set(pos_b.keys())
                for code in all_codes:
                    a = pos_a.get(code)
                    b = pos_b.get(code)

                    if a is None and b is not None:
                        result.matched = False
                        result.mismatches.append({
                            "code": code,
                            "type": "missing_in",
                            "channel": ch_a,
                            "detail": f"{code} 在 {ch_b} 存在但在 {ch_a} 缺失",
                        })
                    elif b is None and a is not None:
                        result.matched = False
                        result.mismatches.append({
                            "code": code,
                            "type": "missing_in",
                            "channel": ch_b,
                            "detail": f"{code} 在 {ch_a} 存在但在 {ch_b} 缺失",
                        })
                    elif a and b and abs(a.get("shares", 0) - b.get("shares", 0)) > self.DIFF_TOLERANCE:
                        result.matched = False
                        result.mismatches.append({
                            "code": code,
                            "type": "share_diff",
                            "channel_a": ch_a,
                            "channel_b": ch_b,
                            "shares_a": a.get("shares", 0),
                            "shares_b": b.get("shares", 0),
                            "detail": f"{code}: {ch_a}={a.get('shares')}股, {ch_b}={b.get('shares')}股",
                        })

        # 估值差异 > 5% 视为严重
        value_diffs = [m for m in result.mismatches if m.get("type") == "share_diff"]
        for diff in value_diffs:
            if abs(diff["shares_a"] - diff["shares_b"]) / max(abs(diff["shares_a"]), 1) > 0.5:
                result.severe = True
                break

        if result.mismatches:
            result.summary = f"发现 {len(result.mismatches)} 处不一致"
            if result.severe:
                result.summary += "（严重：持仓差异超 50%）"
        else:
            result.summary = "所有通道持仓一致"

        return result

    def get_channels(self) -> list[str]:
        """获取已同步的通道列表"""
        return list(self._channel_positions.keys())

    def get_channel_positions(self, channel: str) -> list[dict]:
        """获取指定通道的持仓数据"""
        return self._channel_positions.get(channel, [])
