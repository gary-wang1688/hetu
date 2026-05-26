"""
河图 (HeTu) 核心类型定义

所有领域对象的数据类定义，是整个系统的类型基础。
遵循 pydantic 模型，支持序列化和验证。
"""

from __future__ import annotations

from datetime import date, datetime, timezone, timedelta
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


# ============================================================
# 枚举
# ============================================================

class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(str, Enum):
    CREATED = "CREATED"
    SUBMITTED = "SUBMITTED"
    PARTIAL = "PARTIAL"
    PARTIAL_FILLED = "PARTIAL_FILLED"  # compatibility alias
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class StopType(str, Enum):
    HARD = "HARD"
    TRAILING = "TRAILING"
    TIME = "TIME"


class HealthStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"


class EventType(str, Enum):
    """事件类型枚举。"""
    SIGNAL_RECEIVED = "SIGNAL_RECEIVED"
    SIGNAL_EXECUTED = "SIGNAL_EXECUTED"
    ORDER_CREATED = "ORDER_CREATED"
    ORDER_FILLED = "ORDER_FILLED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    ORDER_REJECTED = "ORDER_REJECTED"
    STOP_LOSS_TRIGGERED = "STOP_LOSS_TRIGGERED"
    RISK_REJECTED = "RISK_REJECTED"
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"


class EventPriority(int, Enum):
    """事件优先级。"""
    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


class FailPolicy(str, Enum):
    """失败策略。"""
    SKIP = "SKIP"
    RETRY = "RETRY"
    RAISE = "RAISE"


class DelivStatus(str, Enum):
    """交付状态。"""
    PENDING = "PENDING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    ACKED = "ACKED"


# ============================================================
# 行情
# ============================================================

class Bar(BaseModel):
    code: str
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    amount: float = 0.0


class Quote(BaseModel):
    code: str
    last_price: float
    bid1: float = 0.0
    ask1: float = 0.0
    volume: int = 0
    amount: float = 0.0
    pre_close: float = 0.0
    timestamp: datetime = Field(default_factory=datetime.now)


# ============================================================
# 信号 & 订单 & 持仓
# ============================================================

class MarketRegime(BaseModel):
    trend: str = "RANGING"      # BULLISH / BEARISH / RANGING
    strength: float = 0.5


class StrategyContext(BaseModel):
    current_date: date = Field(default_factory=date.today)
    today: str = ""
    total_asset: float = 0.0
    total_position_pct: float = 0.0
    market_regime: MarketRegime | None = None


class Signal(BaseModel):
    signal_id: UUID = Field(default_factory=uuid4)
    strategy: str = ""
    code: str
    action: Action = Action.BUY
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reason: str = ""
    price_limit: float = 0.0
    shares: int = 0
    stop_loss: float | None = None
    take_profit: float | None = None
    created_at: datetime = Field(default_factory=datetime.now)


class Order(BaseModel):
    order_id: UUID = Field(default_factory=uuid4)
    signal_id: UUID | None = None
    channel: str = Field(..., description="执行通道: qmt | mx")
    code: str
    action: Action
    order_type: OrderType = OrderType.LIMIT
    price: float | None = Field(None, ge=0)
    shares: int = Field(gt=0)
    status: OrderStatus = OrderStatus.CREATED
    strategy_name: str = ""
    channel_order_id: str | None = None
    correlation_id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    error_msg: str | None = None


class Trade(BaseModel):
    trade_id: UUID = Field(default_factory=uuid4)
    order_id: UUID
    channel: str
    code: str
    action: Action
    filled_price: float
    filled_shares: int = Field(gt=0)
    commission: float = 0.0
    stamp_tax: float = 0.0
    slippage_pct: float = 0.0
    trade_time: datetime = Field(default_factory=datetime.now)


class Position(BaseModel):
    position_id: UUID = Field(default_factory=uuid4)
    channel: str
    code: str
    name: str = ""
    shares: int
    avg_cost: float
    current_price: float = 0.0
    market_value: float = 0.0
    unrealized_pnl: float = 0.0
    stop_loss: float | None = None
    take_profit: float | None = None
    entry_date: date
    strategy_name: str = ""
    buyable_today: bool = True
    updated_at: datetime = Field(default_factory=datetime.now)


# ============================================================
# 结果类型
# ============================================================

class AssessmentResult(BaseModel):
    approved: bool = False
    reason: str = ""
    adjusted_shares: int = 0
    adjusted_price: float = 0.0
    stop_loss: float | None = None


class ChannelResult(BaseModel):
    success: bool = False
    error: str = ""
    data: dict = Field(default_factory=dict)
    elapsed_ms: float = 0.0


class ComponentHealth(BaseModel):
    name: str
    status: HealthStatus = HealthStatus.HEALTHY
    message: str = ""
    error: str = ""


# ============================================================
# 风控 & 止损
# ============================================================

class StopLossPending(BaseModel):
    order_id: UUID
    stop_price: float
    stop_type: StopType
    retry_count: int = 0
    last_error: str | None = None
    created_at: datetime = Field(default_factory=datetime.now)


class FrozenSlot(BaseModel):
    frozen_id: UUID = Field(default_factory=uuid4)
    signal_id: UUID | None = None
    code: str
    frozen_amount: float = 0.0
    expires_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc) + timedelta(minutes=5))


# ============================================================
# 数据质量
# ============================================================

class IssueSeverity(str, Enum):
    FATAL = "FATAL"
    ERROR = "ERROR"
    WARN = "WARN"


class DataIssue(BaseModel):
    severity: IssueSeverity = IssueSeverity.WARN
    code: str = ""
    date: str = ""
    field: str = ""
    description: str = ""
    source: str = ""
