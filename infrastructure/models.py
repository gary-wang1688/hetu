"""
河图 (HeTu) SQLAlchemy 模型

20 张表的 ORM 映射。使用 declarative base，支持 alembic 自动迁移。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, Float,
    ForeignKey, Integer, JSON, String, Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# ============================================================
# Signal — 策略信号表
# ============================================================

class Signal(Base):
    """策略信号表 — signals"""
    __tablename__ = "signals"
    __table_args__ = (
        UniqueConstraint("strategy", "code", "bar_time", "action", name="uq_signal"),
        CheckConstraint("action IN ('BUY','SELL')", name="ck_signal_action"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_signal_confidence"),
        {"schema": "trading"},
    )

    signal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    action: Mapped[str] = mapped_column(String(4), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    param_set: Mapped[str | None] = mapped_column(String(64))
    price_limit: Mapped[float | None] = mapped_column(Float)
    shares: Mapped[int] = mapped_column(Integer, nullable=False)
    bar_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    params_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    expire_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ============================================================
# Order — 订单表
# ============================================================

class Order(Base):
    """订单表 — orders"""
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("action IN ('BUY','SELL')", name="ck_order_action"),
        {"schema": "trading"},
    )

    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    signal_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("trading.signals.signal_id"))
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    action: Mapped[str] = mapped_column(String(4), nullable=False)
    order_type: Mapped[str] = mapped_column(String(8), nullable=False)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    shares: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="CREATED")
    channel_order_id: Mapped[str | None] = mapped_column(String(64))
    correlation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), default=uuid.uuid4)
    strategy_name: Mapped[str | None] = mapped_column(String(64))
    error_msg: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ============================================================
# Trade — 成交表
# ============================================================

class Trade(Base):
    """成交表 — trades"""
    __tablename__ = "trades"
    __table_args__ = (
        CheckConstraint("action IN ('BUY','SELL')", name="ck_trade_action"),
        {"schema": "trading"},
    )

    trade_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("trading.orders.order_id"), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    action: Mapped[str] = mapped_column(String(4), nullable=False)
    filled_price: Mapped[float] = mapped_column(Float, nullable=False)
    filled_shares: Mapped[int] = mapped_column(Integer, nullable=False)
    commission: Mapped[float] = mapped_column(Float, default=0.0)
    stamp_tax: Mapped[float] = mapped_column(Float, default=0.0)
    slippage_pct: Mapped[float] = mapped_column(Float, default=0.0)
    trade_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ============================================================
# Position — 持仓表
# ============================================================

class Position(Base):
    """持仓表 — positions"""
    __tablename__ = "positions"
    __table_args__ = (
        {"schema": "trading"},
    )

    position_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    shares: Mapped[int] = mapped_column(Integer, nullable=False)
    avg_cost: Mapped[float] = mapped_column(Float, nullable=False)
    current_price: Mapped[float] = mapped_column(Float, default=0.0)
    market_value: Mapped[float] = mapped_column(Float, default=0.0)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    stop_loss: Mapped[float | None] = mapped_column(Float)
    take_profit: Mapped[float | None] = mapped_column(Float)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    strategy_name: Mapped[str | None] = mapped_column(String(64))
    buyable_today: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ============================================================
# StopLossPending — 止损挂起表
# ============================================================

class StopLossPending(Base):
    """止损挂起表 — stop_loss_pending"""
    __tablename__ = "stop_loss_pending"
    __table_args__ = (
        {"schema": "trading"},
    )

    pending_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("trading.orders.order_id"), nullable=False)
    stop_price: Mapped[float] = mapped_column(Float, nullable=False)
    stop_type: Mapped[str] = mapped_column(String(16), nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ============================================================
# SystemEvent — 系统事件表
# ============================================================

class SystemEvent(Base):
    """系统事件表 — system_events"""
    __tablename__ = "system_events"
    __table_args__ = (
        {"schema": "trading"},
    )

    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), default="INFO")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ============================================================
# EventDelivery — 事件投递记录
# ============================================================

class EventDelivery(Base):
    """事件投递记录 — event_deliveries"""
    __tablename__ = "event_deliveries"
    __table_args__ = (
        {"schema": "trading"},
    )

    delivery_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("trading.system_events.event_id"), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="PENDING")
    error_msg: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ============================================================
# Job — 任务表
# ============================================================

class Job(Base):
    """定时任务表 — jobs"""
    __tablename__ = "jobs"
    __table_args__ = (
        {"schema": "trading"},
    )

    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    cron: Mapped[str] = mapped_column(String(64))
    handler: Mapped[str] = mapped_column(String(128), nullable=False)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ============================================================
# JobRun — 任务执行记录
# ============================================================

class JobRun(Base):
    """任务执行记录 — job_runs"""
    __tablename__ = "job_runs"
    __table_args__ = (
        {"schema": "trading"},
    )

    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("trading.jobs.job_id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="RUNNING")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)


# ============================================================
# ConfigChange — 配置变更审计
# ============================================================

class ConfigChange(Base):
    """配置变更审计 — config_changes"""
    __tablename__ = "config_changes"
    __table_args__ = (
        {"schema": "trading"},
    )

    change_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    config_key: Mapped[str] = mapped_column(String(128), nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str] = mapped_column(Text, nullable=False)
    changed_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ============================================================
# Notification — 通知记录
# ============================================================

class Notification(Base):
    """通知记录 — notifications"""
    __tablename__ = "notifications"
    __table_args__ = (
        {"schema": "trading"},
    )

    notification_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel: Mapped[str] = mapped_column(String(16), default="wechat")
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    level: Mapped[str] = mapped_column(String(16), default="INFO")
    status: Mapped[str] = mapped_column(String(16), default="PENDING")
    error_msg: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ============================================================
# StrategyDefinition — 策略定义
# ============================================================

class StrategyDefinition(Base):
    """策略定义表 — strategy_definitions"""
    __tablename__ = "strategy_definitions"
    __table_args__ = (
        {"schema": "trading"},
    )

    definition_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    version: Mapped[str] = mapped_column(String(16), default="0.1.0")
    lifecycle: Mapped[str] = mapped_column(String(16), default="DRAFT")
    frequency: Mapped[str] = mapped_column(String(16), default="daily")
    description: Mapped[str | None] = mapped_column(Text)
    rationale: Mapped[dict] = mapped_column(JSON, default=dict)
    param_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ============================================================
# FactorIC — 因子 IC 分析
# ============================================================

class FactorIC(Base):
    """因子 IC 分析 — factor_ic"""
    __tablename__ = "factor_ic"
    __table_args__ = (
        {"schema": "analytics"},
    )

    ic_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    factor_name: Mapped[str] = mapped_column(String(64), nullable=False)
    ic_value: Mapped[float] = mapped_column(Float, nullable=False)
    rank_ic: Mapped[float] = mapped_column(Float)
    ir: Mapped[float] = mapped_column(Float)
    universe: Mapped[str | None] = mapped_column(String(32))
    calc_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ============================================================
# StrategyCorrelation — 策略相关性
# ============================================================

class StrategyCorrelation(Base):
    """策略相关性 — strategy_correlations"""
    __tablename__ = "strategy_correlations"
    __table_args__ = (
        UniqueConstraint("strategy_a", "strategy_b", "calc_date", name="uq_correlation_pair"),
        {"schema": "analytics"},
    )

    correlation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_a: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_b: Mapped[str] = mapped_column(String(64), nullable=False)
    correlation: Mapped[float] = mapped_column(Float, nullable=False)
    window_days: Mapped[int] = mapped_column(Integer, default=60)
    calc_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ============================================================
# StrategyPerformanceRecord — 策略绩效
# ============================================================

class StrategyPerformanceRecord(Base):
    """策略绩效 — strategy_performance"""
    __tablename__ = "strategy_performance"
    __table_args__ = (
        UniqueConstraint("strategy_name", "calc_date", name="uq_perf_date"),
        {"schema": "analytics"},
    )

    perf_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_name: Mapped[str] = mapped_column(String(64), nullable=False)
    daily_return: Mapped[float] = mapped_column(Float, default=0.0)
    sharpe: Mapped[float] = mapped_column(Float, default=0.0)
    max_drawdown: Mapped[float] = mapped_column(Float, default=0.0)
    win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    profit_factor: Mapped[float] = mapped_column(Float, default=0.0)
    calc_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ============================================================
# PortfolioSnapshot — 组合快照
# ============================================================

class PortfolioSnapshot(Base):
    """组合快照 — portfolio_snapshots"""
    __tablename__ = "portfolio_snapshots"
    __table_args__ = (
        {"schema": "analytics"},
    )

    snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    calc_date: Mapped[date] = mapped_column(Date, nullable=False)
    total_asset: Mapped[float] = mapped_column(Float, nullable=False)
    cash: Mapped[float] = mapped_column(Float, default=0.0)
    market_value: Mapped[float] = mapped_column(Float, default=0.0)
    position_pct: Mapped[float] = mapped_column(Float, default=0.0)
    daily_return: Mapped[float] = mapped_column(Float, default=0.0)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    positions_detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ============================================================
# LLMAnalysis — LLM 分析记录
# ============================================================

class LLMAnalysis(Base):
    """LLM 分析 — llm_analyses"""
    __tablename__ = "llm_analyses"
    __table_args__ = (
        {"schema": "analytics"},
    )

    analysis_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    analysis_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt: Mapped[str] = mapped_column(Text)
    response: Mapped[str] = mapped_column(Text)
    verdict: Mapped[str | None] = mapped_column(String(16))
    model: Mapped[str | None] = mapped_column(String(32))
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ============================================================
# User — 用户表
# ============================================================

class User(Base):
    """用户表 — users"""
    __tablename__ = "users"
    __table_args__ = (
        {"schema": "trading"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(String(16), default="viewer")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ============================================================
# Session — 会话表
# ============================================================

class Session(Base):
    """会话表 — sessions"""
    __tablename__ = "sessions"
    __table_args__ = (
        {"schema": "trading"},
    )

    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("trading.users.user_id"), nullable=False)
    token: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ============================================================
# DailyBar — 日线行情
# ============================================================

class DailyBar(Base):
    """日线行情 — daily_bars"""
    __tablename__ = "daily_bars"
    __table_args__ = (
        UniqueConstraint("code", "date", name="uq_bar_code_date"),
        {"schema": "market"},
    )

    bar_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
