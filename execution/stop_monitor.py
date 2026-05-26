"""
河图 (HeTu) 独立止损监控

StopMonitor: 与主执行链解耦的止损监控器。
- Pipeline 调用 ``attach_stop_order()`` 注册止损意图
- StopMonitor 内部使用 ``StopLossEngine`` 进行止损检查
- ``run_loop()`` 实时监控行情，触发时通过回调传回 SELL Signal
- 挂载失败写 compensation_queue → 定时重试
- 启动时从 DB 恢复所有止损

v2.0 改进:
- 集成 StopLossEngine，支持 HARD/TRAIL/TIME 三种止损类型
- attach_stop_order() 接受外部指定止损价
- run_loop() 触发后通过 on_stop_triggered 回调传回 SELL Signal
- 实现 _persist_stop() 和 _persist_compensation()
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Awaitable
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from core.types import Order, Signal
from risk.stop_loss import StopLossEngine

logger = logging.getLogger("hetu.stop_monitor")

StopTriggerCallback = Callable[[list[Signal]], Awaitable[None]]


class StopMonitor:
    """
    独立止损监控器。

    架构 (v2.0):
    - Pipeline 只负责调用 ``attach_stop_order()`` 注册止损意图
    - StopMonitor 负责：监控行情 → 检查止损 → 触发 SELL Signal
    - 三级保障：直接注册 → 补偿队列重试 → DB 持久化 + 启动重建

    Args:
        persist: 是否启用 DB 持久化。
        db_session_factory: 异步数据库 session 工厂。
        on_stop_triggered: 止损触发回调，接收 SELL Signal 列表。
        hard_threshold: 硬止损阈值（默认 -5%）。
        trail_pct: 移动止损回撤比例（默认 -4%）。
    """

    MAX_RETRIES: int = 5
    RETRY_DELAY: int = 60

    def __init__(
        self,
        persist: bool = False,
        db_session_factory: Any | None = None,
        on_stop_triggered: StopTriggerCallback | None = None,
        hard_threshold: float = 0.05,
        trail_pct: float = 0.04,
    ):
        self._persist = persist
        self._db_factory = db_session_factory
        self._on_stop_triggered = on_stop_triggered

        self._engine = StopLossEngine(
            hard_threshold=hard_threshold,
            trail_pct=trail_pct,
        )

        self._stops: dict[str, dict[str, Any]] = {}
        self._compensation_queue: list[dict] = []
        self._attached: int = 0
        self._triggered: int = 0
        self._failed: int = 0
        self._stop_records: dict[str, dict[str, Any]] = {}

    @classmethod
    def from_config(cls, cfg: object, overrides: object = None) -> StopMonitor:
        """从配置创建 StopMonitor。"""
        threshold = getattr(cfg, "stop_loss_hard", 0.05) if cfg else 0.05
        trail = getattr(cfg, "stop_loss_trail", 0.04) if cfg else 0.04
        return cls(hard_threshold=threshold, trail_pct=trail)

    def attach_stop_order(
        self, order: Order, stop_price: float, stop_type: str = "HARD"
    ) -> str:
        """注册止损订单。返回止损 ID。"""
        code = order.code
        stop_id = str(uuid4())[:8]
        try:
            self._stops[code] = {
                "stop_id": stop_id,
                "stop_price": stop_price,
                "stop_type": stop_type,
                "order_id": str(order.order_id),
                "shares": order.shares if hasattr(order, "shares") else 0,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._engine.add_position(code, stop_price, order.shares if hasattr(order, "shares") else 0, stop_price)
            self._attached += 1

            if self._persist:
                self._persist_stop(code, stop_price, stop_type, order)

            logger.info(f"Stop attached: {code} @ {stop_price} (type={stop_type})")
            return stop_id
        except Exception as e:
            logger.error(f"Failed to attach stop for {code}: {e}")
            self.enqueue_compensation(order, stop_price, str(e))
            self._failed += 1
            return ""

    def remove_stop(self, code: str) -> bool:
        """移除指定代码的止损。"""
        if code in self._stops:
            del self._stops[code]
            return True
        return False

    def enqueue_compensation(self, order: Order, stop_price: float, error: str) -> None:
        """将止损挂载失败的订单加入补偿队列。"""
        entry = {
            "code": order.code,
            "stop_price": stop_price,
            "order_id": str(order.order_id),
            "error": error,
            "retry_count": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._compensation_queue.append(entry)

        if self._persist:
            self._persist_compensation(order, stop_price, error)

    def retry_all(self) -> int:
        """重试补偿队列中所有待处理的止损。返回成功数。"""
        success_count = 0
        remaining = []
        for entry in self._compensation_queue:
            if entry["retry_count"] >= self.MAX_RETRIES:
                logger.warning(f"Max retries exceeded for {entry['code']}")
                continue
            try:
                entry["retry_count"] += 1
                code = entry["code"]
                self._stops[code] = {
                    "stop_id": str(uuid4())[:8],
                    "stop_price": entry["stop_price"],
                    "stop_type": "HARD",
                    "order_id": entry["order_id"],
                    "shares": 0,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                self._engine.add_position(code, entry["stop_price"], 0, entry["stop_price"])
                self._attached += 1
                success_count += 1
                logger.info(f"Compensation retry success: {code}")
            except Exception:
                remaining.append(entry)
        self._compensation_queue = remaining
        return success_count

    def rebuild_all(self) -> int:
        """从 DB 恢复所有止损记录。返回恢复数量。"""
        if not self._persist or not self._db_factory:
            return 0
        count = 0
        try:
            # 在同步上下文中尝试执行异步查询
            import asyncio
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            async def _fetch():
                session = self._db_factory()
                try:
                    import sqlalchemy as sa
                    result = await session.execute(
                        sa.text("SELECT code, stop_price, stop_type, shares "
                                "FROM hetu_stop_loss WHERE status = 'active'")
                    )
                    rows = result.fetchall()
                    for row in rows:
                        code, sp, st, sh = row
                        self._stops[code] = {
                            "stop_id": str(uuid4())[:8],
                            "stop_price": sp,
                            "stop_type": st,
                            "shares": sh,
                            "created_at": datetime.now(timezone.utc).isoformat(),
                        }
                        self._engine.add_position(code, 0, sh, sp)
                    return len(rows)
                finally:
                    await session.close()

            count = loop.run_until_complete(_fetch())
        except Exception as e:
            logger.warning(f"Failed to rebuild stops from DB: {e}")

        return count

    async def run_loop(self, quote_provider: Any, interval: float = 1.0) -> None:
        """实时止损监控主循环。"""
        logger.info("StopMonitor run_loop started")
        self.rebuild_all()
        try:
            while True:
                try:
                    if not self._stops:
                        await asyncio.sleep(interval)
                        continue

                    # 获取持仓价格
                    prices: dict[str, float] = {}
                    for code in list(self._stops.keys()):
                        try:
                            q = await quote_provider.get_quote(code)
                            if q:
                                prices[code] = q.last_price
                        except Exception:
                            continue

                    # 检查止损触发
                    triggered = self._generate_sell_signals(prices)
                    if triggered and self._on_stop_triggered:
                        self._triggered += len(triggered)
                        await self._on_stop_triggered(triggered)

                    # 定时重试补偿队列
                    self.retry_all()

                    await asyncio.sleep(interval)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"StopMonitor loop error: {e}")
                    await asyncio.sleep(interval * 5)
        except asyncio.CancelledError:
            logger.info("StopMonitor run_loop cancelled")

    def _generate_sell_signals(self, prices: dict[str, float]) -> list[Signal]:
        """根据当前价格检查止损，生成 SELL 信号。"""
        triggered_codes = self._engine.generate_stop_signals(prices)
        # 清理已触发的止损记录
        sell_signals = []
        for sig in triggered_codes:
            if sig.code in self._stops:
                del self._stops[sig.code]
            sell_signals.append(sig)
        return sell_signals

    def get_stop_levels(self, code: str) -> dict[str, float] | None:
        """获取指定代码的止损价位。"""
        if code in self._stops:
            return {
                "stop_price": self._stops[code]["stop_price"],
                "stop_type": self._stops[code]["stop_type"],
            }
        return None

    def get_engine(self) -> StopLossEngine:
        """获取内部止损引擎。"""
        return self._engine

    def _persist_stop(self, code: str, stop_price: float, stop_type: str, order: Order) -> None:
        """持久化止损记录到数据库。"""
        if not self._db_factory:
            return
        try:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            async def _insert():
                session = self._db_factory()
                try:
                    import sqlalchemy as sa
                    await session.execute(
                        sa.text("INSERT INTO hetu_stop_loss (code, stop_price, stop_type, "
                                "order_id, status, created_at) VALUES "
                                "(:code, :sp, :st, :oid, 'active', :ts)"),
                        {"code": code, "sp": stop_price, "st": stop_type,
                         "oid": str(order.order_id), "ts": datetime.now(timezone.utc)},
                    )
                    await session.commit()
                finally:
                    await session.close()

            loop.run_until_complete(_insert())
        except Exception as e:
            logger.warning(f"Failed to persist stop: {e}")

    def _persist_compensation(self, order: Order, stop_price: float, error: str) -> None:
        """持久化补偿记录到数据库。"""
        if not self._db_factory:
            return
        try:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            async def _insert():
                session = self._db_factory()
                try:
                    import sqlalchemy as sa
                    await session.execute(
                        sa.text("INSERT INTO hetu_stop_compensation (code, stop_price, "
                                "order_id, error, created_at) VALUES "
                                "(:code, :sp, :oid, :err, :ts)"),
                        {"code": order.code, "sp": stop_price,
                         "oid": str(order.order_id), "err": error,
                         "ts": datetime.now(timezone.utc)},
                    )
                    await session.commit()
                finally:
                    await session.close()

            loop.run_until_complete(_insert())
        except Exception as e:
            logger.warning(f"Failed to persist compensation: {e}")

    @property
    def stats(self) -> dict[str, Any]:
        """返回止损监控器统计信息。"""
        return {
            "attached": self._attached,
            "active": len(self._stops),
            "triggered": self._triggered,
            "failed": self._failed,
            "compensation_queue": len(self._compensation_queue),
        }

    def get_stops(self) -> dict[str, dict[str, Any]]:
        """返回当前所有止损记录。"""
        return dict(self._stops)
