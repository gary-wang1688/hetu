"""
河图 (HeTu) SignalPipeline — 带补偿事务的核心执行链

架构 §5.1: 这是系统心脏。每个信号过此管道：
  信号 → 风控审核 → 仓位计算 → 预检 → DB写入 → Broker提交 → 止损注册

关键设计:
- Phase 1 (纯计算/读): 可安全重试，无副作用
- Phase 2 (副作用): 补偿事务 — 止损挂载失败不阻断订单
- stop_loss_pending: 止损挂载失败的兜底队列
- correlation_id: 同一信号衍生的订单+止损共享此 ID

v2.0 改进:
- 新增 stop_monitor 参数，Pipeline 不再自己挂载止损
- 止损注册委托给 StopMonitor.attach_stop_order()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import uuid4

from core.types import Signal, Order, OrderType, OrderStatus, StrategyContext, AssessmentResult
from core.events import Event
from core.event_bus import EventBus
from risk.manager import RiskManager
from risk.position_sizer import PositionSizing

if TYPE_CHECKING:
    from execution.stop_monitor import StopMonitor

logger = logging.getLogger("hetu.pipeline")


@dataclass
class PipelineResult:
    """单信号执行结果。"""
    success: bool = False
    signal: Signal | None = None
    orders: list = field(default_factory=list)
    sizing: PositionSizing | None = None
    risk_assessment: AssessmentResult | None = None
    error: str | None = None
    correlation_id: str = ""
    stop_loss_pending: bool = False


class SignalPipeline:
    """
    信号 → 订单的完整执行链。

    Args:
        risk_manager: 风控审核器。
        order_manager: 订单管理器。
        event_bus: 事件总线。
        stop_monitor: 止损监控器（v2.0，可选）。
        stop_loss_pending_list: 已有待修复的止损列表（可选）。
    """

    def __init__(
        self,
        risk_manager: RiskManager,
        event_bus: EventBus,
        stop_monitor: StopMonitor | None = None,
        stop_loss_pending_list: list[dict] | None = None,
    ):
        self.risk = risk_manager
        self.bus = event_bus
        self.orders: list = []
        self.stop_monitor = stop_monitor
        self._stop_loss_pending: list[dict] = stop_loss_pending_list or []

    async def process(
        self, signal: Signal, total_equity: float, strategy_type: str = "default"
    ) -> PipelineResult:
        result = PipelineResult(
            success=False,
            signal=signal,
            correlation_id=str(uuid4()),
        )

        try:
            # Phase 1: 风控审核
            ctx = StrategyContext(total_asset=total_equity)
            assessment = self.risk.assess(signal, ctx)
            result.risk_assessment = assessment
            if not assessment.approved:
                result.error = f"风险审核未通过: {assessment.reason}"
                return result

            # Phase 2: 仓位计算
            sizing = self.risk.calculate_position(signal, total_equity, strategy_type)
            result.sizing = sizing

            # Phase 3: 构建 Order
            order = Order(
                signal_id=signal.signal_id,
                channel=getattr(signal, "channel", "mx"),
                code=signal.code,
                action=signal.action,
                order_type=OrderType.LIMIT,
                price=signal.price_limit if signal.price_limit > 0 else None,
                shares=sizing.adjusted_shares if hasattr(sizing, "adjusted_shares") else signal.shares,
                status=OrderStatus.CREATED,
                strategy_name=strategy_type,
                correlation_id=result.correlation_id,
            )
            result.orders.append(order)
            self.orders.append(order)

            # Phase 4: 发布事件
            event = Event(
                event_type="ORDER_CREATED",
                payload={"order_id": str(order.order_id), "code": order.code},
            )
            await self.bus.publish(event)

            # Phase 5: 止损注册（v2.0 委托给 StopMonitor）
            if self.stop_monitor and signal.stop_loss:
                self.stop_monitor.attach_stop_order(
                    order=order,
                    stop_price=signal.stop_loss,
                )

            result.success = True

        except Exception as e:
            logger.error(f"Pipeline process error for {signal.code}: {e}")
            result.error = str(e)

            # 止损兜底队列
            if self.stop_monitor and signal.stop_loss:
                self._stop_loss_pending.append({
                    "code": signal.code,
                    "stop_loss": signal.stop_loss,
                })
                result.stop_loss_pending = True

        return result

    def _attach_stop_loss(self, order: Order, sizing: PositionSizing) -> None:
        if not self.stop_monitor:
            return
        stop_price = getattr(order, "stop_loss", None)
        if stop_price is None:
            return
        try:
            self.stop_monitor.attach_stop_order(order=order, stop_price=stop_price)
        except Exception as e:
            logger.warning(f"Stop loss attach failed for {order.code}: {e}")
            self._stop_loss_pending.append({
                "code": order.code,
                "order_id": str(order.order_id),
                "stop_price": stop_price,
                "error": str(e),
            })

    def get_pending_stops(self) -> list[dict]:
        return list(self._stop_loss_pending)

    def repair_pending_stops(self, repaired_order_ids: list[str]) -> None:
        self._stop_loss_pending = [
            s for s in self._stop_loss_pending
            if s.get("order_id") not in repaired_order_ids
        ]
