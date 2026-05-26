"""
河图 (HeTu) 持仓追踪器

PortfolioTracker: 多通道持仓聚合、buyable_today 计算、实时估值。
v2.1: take_snapshot() 支持持久化到 analytics.portfolio_snapshots。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

logger = logging.getLogger("hetu.portfolio")


class PortfolioTracker:
    """
    持仓追踪器

    功能:
    - 多通道持仓汇总
    - T+1 buyable_today 标记
    - 实时估值 (mark-to-market)
    - 盈亏计算
    - v2.1: 快照持久化（防崩溃丢失）
    """

    def __init__(self, persist: bool = False, db_session_factory: object = None):
        self._positions: dict[str, dict] = {}  # code -> position info
        self._cash: float = 0.0
        self._total_asset: float = 0.0
        self._snapshots: list[dict] = []
        self._persist = persist
        self._db_factory = db_session_factory

    def update_from_broker(self, positions: list[dict], channel: str) -> None:
        """从 Broker 通道更新持仓数据"""
        for pos in positions:
            code = pos.get("code", "")
            if not code:
                continue

            if code in self._positions:
                # 更新现有持仓
                existing = self._positions[code]
                existing["shares"] = pos.get("shares", existing.get("shares", 0))
                existing["avg_cost"] = pos.get("avg_cost", existing.get("avg_cost", 0))
                existing["current_price"] = pos.get("current_price", existing.get("current_price", 0))
                existing["market_value"] = pos.get("market_value", existing.get("market_value", 0))
            else:
                # 新持仓
                self._positions[code] = {
                    "code": code,
                    "shares": pos.get("shares", 0),
                    "avg_cost": pos.get("avg_cost", 0),
                    "current_price": pos.get("current_price", 0),
                    "market_value": pos.get("market_value", 0),
                    "unrealized_pnl": 0.0,
                    "channel": channel,
                    "entry_date": pos.get("entry_date", str(date.today())),
                    "buyable_today": True,
                }

            # 重新计算持仓盈亏
            p = self._positions[code]
            if p["shares"] > 0 and p["avg_cost"] > 0:
                p["unrealized_pnl"] = (p["current_price"] - p["avg_cost"]) * p["shares"]

        logger.debug("已从 %s 更新 %d 个持仓", channel, len(positions))

    def update_cash(self, cash: float) -> None:
        """更新现金余额"""
        self._cash = cash
        self._recalc_total_asset()

    def update_prices(self, quotes: dict[str, float]) -> None:
        """批量更新持仓价格

        Args:
            quotes: {code: price} 最新报价
        """
        for code, price in quotes.items():
            if code in self._positions:
                self._positions[code]["current_price"] = price
                self._positions[code]["market_value"] = price * self._positions[code]["shares"]
                # 重新计算未实现盈亏
                p = self._positions[code]
                if p["shares"] > 0 and p["avg_cost"] > 0:
                    p["unrealized_pnl"] = (price - p["avg_cost"]) * p["shares"]

        self._recalc_total_asset()

    def get_position(self, code: str) -> dict | None:
        """获取指定代码的持仓信息"""
        return self._positions.get(code)

    def get_all(self) -> list[dict]:
        """获取所有持仓"""
        return list(self._positions.values())

    def get_active_codes(self) -> list[str]:
        """获取所有有持仓的股票代码"""
        return [code for code, p in self._positions.items() if p.get("shares", 0) > 0]

    def get_buyable_codes(self) -> list[str]:
        """获取可交易的股票代码（buyable_today=True）"""
        return [
            code for code, p in self._positions.items()
            if p.get("shares", 0) > 0 and p.get("buyable_today", True)
        ]

    def get_position_pct(self, code: str) -> float:
        """获取单只股票的持仓占比"""
        if code not in self._positions or self._total_asset <= 0:
            return 0.0
        pos = self._positions[code]
        return pos.get("market_value", 0) / self._total_asset

    def get_total_position_pct(self) -> float:
        """获取总持仓占比"""
        if self._total_asset <= 0:
            return 0.0
        market_value = sum(p.get("market_value", 0) for p in self._positions.values())
        return market_value / self._total_asset

    def get_unrealized_pnl(self) -> float:
        """获取总未实现盈亏"""
        return sum(p.get("unrealized_pnl", 0) for p in self._positions.values())

    def take_snapshot(self) -> dict:
        """获取当前组合快照（不持久化）"""
        snapshot = {
            "date": str(date.today()),
            "cash": self._cash,
            "total_asset": self._total_asset,
            "market_value": sum(p.get("market_value", 0) for p in self._positions.values()),
            "position_pct": self.get_total_position_pct(),
            "unrealized_pnl": self.get_unrealized_pnl(),
            "positions": {
                code: {
                    "shares": p.get("shares", 0),
                    "avg_cost": p.get("avg_cost", 0),
                    "current_price": p.get("current_price", 0),
                    "market_value": p.get("market_value", 0),
                }
                for code, p in self._positions.items()
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._snapshots.append(snapshot)
        return snapshot

    async def save_snapshot(self) -> bool:
        """持久化当前快照到 analytics.portfolio_snapshots 表"""
        snapshot = self.take_snapshot()
        if not self._persist or not self._db_factory:
            logger.debug("快照持久化未启用或未配置数据库连接")
            return False

        try:
            from infrastructure.models import PortfolioSnapshot

            async with self._db_factory() as session:
                record = PortfolioSnapshot(
                    calc_date=date.today(),
                    total_asset=snapshot["total_asset"],
                    cash=snapshot["cash"],
                    market_value=snapshot["market_value"],
                    position_pct=snapshot["position_pct"],
                    daily_return=self._calculate_daily_pnl(),
                    unrealized_pnl=snapshot["unrealized_pnl"],
                    positions_detail=snapshot["positions"],
                )
                session.add(record)
                await session.commit()
                logger.info("组合快照已持久化")
                return True
        except Exception as e:
            logger.error("持久化快照失败: %s", e)
            return False

    def _calculate_daily_pnl(self) -> float:
        """计算当日盈亏"""
        if not self._snapshots or len(self._snapshots) < 2:
            return 0.0
        prev = self._snapshots[-2]
        curr = self._snapshots[-1]
        prev_total = prev.get("total_asset", 0)
        curr_total = curr.get("total_asset", 0)
        if prev_total > 0:
            return (curr_total - prev_total) / prev_total
        return 0.0

    def record_buy(self, code: str, price: float, shares: int, entry_date: str | None = None) -> None:
        """记录买入交易"""
        if code not in self._positions:
            self._positions[code] = {
                "code": code,
                "shares": 0,
                "avg_cost": 0,
                "current_price": price,
                "market_value": 0,
                "unrealized_pnl": 0,
                "channel": "unknown",
                "entry_date": entry_date or str(date.today()),
                "buyable_today": True,
            }

        pos = self._positions[code]
        old_shares = pos["shares"]
        old_cost = pos["avg_cost"]
        new_shares = old_shares + shares

        # 计算新的平均成本
        if new_shares > 0:
            pos["avg_cost"] = (old_cost * old_shares + price * shares) / new_shares
        pos["shares"] = new_shares
        pos["current_price"] = price
        pos["market_value"] = price * new_shares
        pos["entry_date"] = entry_date or str(date.today())
        pos["buyable_today"] = False  # T+1
        logger.info("买入 %s: %d股 @%.2f", code, shares, price)

    def record_sell(self, code: str, price: float, shares: int) -> None:
        """记录卖出交易"""
        if code not in self._positions:
            logger.warning("卖出 %s 失败: 无持仓", code)
            return

        pos = self._positions[code]
        if shares > pos["shares"]:
            logger.warning("卖出 %s %d股超过持仓 %d股", code, shares, pos["shares"])
            shares = pos["shares"]

        pos["shares"] -= shares
        pos["current_price"] = price
        pos["market_value"] = price * pos["shares"]

        if pos["shares"] <= 0:
            del self._positions[code]

        logger.info("卖出 %s: %d股 @%.2f", code, shares, price)
        self._recalc_total_asset()

    def reset_daily(self) -> None:
        """日初始化：重置 buyable_today 标记并计算新日初市值"""
        for code in self._positions:
            self._positions[code]["buyable_today"] = True
            p = self._positions[code]
            # 更新日初市值
            p["market_value"] = p["current_price"] * p["shares"]
        self._recalc_total_asset()
        logger.info("日初始化完成")

    def _recalc_total_asset(self) -> None:
        """重新计算总资产"""
        market_value = sum(p.get("market_value", 0) for p in self._positions.values())
        self._total_asset = self._cash + market_value

    @property
    def summary(self) -> dict:
        """组合摘要"""
        return {
            "cash": self._cash,
            "total_asset": self._total_asset,
            "market_value": sum(p.get("market_value", 0) for p in self._positions.values()),
            "position_count": len(self.get_active_codes()),
            "position_pct": self.get_total_position_pct(),
            "unrealized_pnl": self.get_unrealized_pnl(),
        }
