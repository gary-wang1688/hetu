"""
河图 (HeTu) 妙想 (MX) 交易通道

对接妙想模拟盘 API (mx_moni)。

架构: MXBroker 实现 Broker 接口，负责 hetu 内部数据模型 <=> MX API JSON 双向转换。

代码转换:   "600519.SH" <=> "600519" + secMkt=1
价格编码:   1780.00 元 -> price * 10^decimal_places -> 整数
金额解码:   MX 响应 -> 根据 currencyUnit /1000 -> 元
状态映射:   MX status 1-10 -> OrderStatus enum
订单追踪:   本地 _orders dict + _channel_map -> 快速 lookup

环境变量:
    MX_APIKEY  妙想 API 密钥
    MX_API_URL 妙想 API 地址
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone
from uuid import UUID

import aiohttp
from aiohttp import ClientTimeout

from core.interfaces import Broker
from core.types import (
    Action, Order, OrderStatus, OrderType, Position, Trade,
    ChannelResult, ComponentHealth, HealthStatus,
)

logger = logging.getLogger("hetu.channel.mx")

MX_DEFAULT_URL = "https://mkapi2.dfcfs.com/finskillshub"
REQUEST_TIMEOUT = ClientTimeout(total=30)
MAX_RETRIES = 3
RETRY_DELAY = 1.0

_MX_STATUS_MAP: dict[int, OrderStatus] = {
    1: OrderStatus.CREATED, 2: OrderStatus.SUBMITTED,
    3: OrderStatus.PARTIAL_FILLED, 4: OrderStatus.FILLED,
    5: OrderStatus.PARTIAL_FILLED, 6: OrderStatus.SUBMITTED,
    7: OrderStatus.CANCELLED, 8: OrderStatus.CANCELLED,
    9: OrderStatus.REJECTED, 10: OrderStatus.REJECTED,
}

_MX_DRT_MAP: dict[int, Action] = {1: Action.BUY, 2: Action.SELL}


class MXBroker(Broker):
    name = "mx_broker"

    def __init__(self, api_key: str = "", api_url: str = ""):
        self._api_key = api_key
        self._api_url = api_url.rstrip("/") or MX_DEFAULT_URL
        self._session: aiohttp.ClientSession | None = None
        self._orders: dict[UUID, Order] = {}
        self._channel_map: dict[str, UUID] = {}
        self._last_error: str = ""

        if not self._api_key:
            self._last_error = "MX_APIKEY 未配置"
            logger.warning("MXBroker: MX_APIKEY 未配置")

    @classmethod
    def from_config(cls, cfg: object) -> MXBroker:
        return cls(
            api_key=str(cfg.get("brokers.mx.api_key", "")),
            api_url=str(cfg.get("brokers.mx.base_url", "")),
        )

    # ----------------------------------------------------------------
    # Broker 接口
    # ----------------------------------------------------------------

    async def submit_order(self, order: Order) -> ChannelResult:
        if not self._api_key:
            return ChannelResult(success=False, error="MX_APIKEY 未配置")
        mx_code = _code_to_mx(order.code)
        sec_mkt = _code_to_market(order.code)
        mx_price = _price_to_mx(order.price, order.code) if order.price else 0

        payload = {
            "moneyUnit": 1,
            "secCode": mx_code,
            "secMkt": sec_mkt,
            "price": mx_price,
            "count": order.shares,
            "type": 1,  # 限价单
            "drt": 1 if order.action == Action.BUY else 2,
        }
        logger.info("MX下单: %s %s x%s @%.2f (整数%d)",
                     order.action.value, order.code, order.shares, order.price or 0, mx_price)

        data = await self._post("/api/claw/mockTrading/trade", payload)
        if data is None:
            return ChannelResult(success=False, error="网络请求失败")

        code = str(data.get("code", ""))
        if code != "0":
            msg = data.get("message", "未知错误")
            return ChannelResult(success=False, error=msg)

        channel_id = str(data.get("data", {}).get("orderId", ""))
        order.channel_order_id = channel_id
        order.status = OrderStatus.SUBMITTED
        self._orders[order.order_id] = order
        if channel_id:
            self._channel_map[channel_id] = order.order_id
        return ChannelResult(success=True, data={"channel_order_id": channel_id})

    async def cancel_order(self, order_id: str | UUID) -> ChannelResult:
        if isinstance(order_id, UUID):
            order = self._orders.get(order_id)
            channel_id = order.channel_order_id if order else ""
        else:
            channel_id = str(order_id)
            order_id = self._channel_map.get(channel_id)

        if not channel_id:
            return ChannelResult(success=False, error="订单不存在")

        data = await self._post("/api/claw/mockTrading/cancel", {"orderId": channel_id, "type": "cancel"})
        if data is None:
            return ChannelResult(success=False, error="网络请求失败")

        code = str(data.get("code", ""))
        if code != "0":
            return ChannelResult(success=False, error=data.get("message", "未知错误"))

        if isinstance(order_id, UUID) and order_id in self._orders:
            self._orders[order_id].status = OrderStatus.CANCELLED
        return ChannelResult(success=True)

    async def cancel_all(self) -> ChannelResult:
        if not self._api_key:
            return ChannelResult(success=False, error="MX_APIKEY 未配置")
        data = await self._post("/api/claw/mockTrading/cancel", {"type": "all"})
        if data is None:
            return ChannelResult(success=False, error="网络请求失败")
        for o in self._orders.values():
            o.status = OrderStatus.CANCELLED
        return ChannelResult(success=True)

    async def get_balance(self) -> dict:
        if not self._api_key:
            return {}
        data = await self._post("/api/claw/mockTrading/balance", {"moneyUnit": 1})
        if data is None:
            return {}

        code = str(data.get("code", ""))
        if code != "0":
            logger.warning("MX 余额查询失败: %s", data.get("message", ""))
            return {}

        inner = data.get("data", {}) if isinstance(data.get("data"), dict) else {}
        cu = inner.get("currencyUnit", 1000)
        return {
            "account_id": str(inner.get("accID", "")),
            "account_name": str(inner.get("accName", "")),
            "total_assets": _money_from_mx(inner.get("totalAssets", 0), cu),
            "avail_balance": _money_from_mx(inner.get("availBalance", 0), cu),
            "frozen_money": _money_from_mx(inner.get("frozenMoney", 0), cu),
            "total_pos_value": _money_from_mx(inner.get("totalPosValue", 0), cu),
            "total_pos_pct": float(inner.get("totalPosPct", 0)),
            "nav": float(inner.get("nav", 0)),
            "init_money": _money_from_mx(inner.get("initMoney", 0), cu),
            "opr_days": int(inner.get("oprDays", 0)),
        }

    async def get_positions(self) -> list[Position]:
        if not self._api_key:
            return []
        data = await self._post("/api/claw/mockTrading/positions", {"moneyUnit": 1})
        if data is None:
            return []

        code = str(data.get("code", ""))
        if code != "0":
            logger.warning("MX 持仓查询失败: %s", data.get("message", ""))
            return []

        pos_data = data.get("data", {}) if isinstance(data.get("data"), dict) else {}
        pos_list = pos_data.get("posList") or []
        cu = pos_data.get("currencyUnit", 1)
        return [_parse_position(p, cu) for p in (pos_list or [])]

    async def get_trades(self, start_date="", end_date="") -> list[Trade]:
        if not self._api_key:
            return []
        data = await self._post("/api/claw/mockTrading/orders", {"moneyUnit": 1, "type": "done"})
        if data is None:
            return []
        orders_raw = _safe_get(data, "data", "orders") or []
        trades = []
        for raw in (orders_raw or []):
            if int(raw.get("status", 0)) in (4,):  # FILLED
                r = _parse_trade_raw(raw)
                if r:
                    trades.append(r)
        return trades

    async def query_order(self, order_id: str | UUID) -> Order | None:
        if isinstance(order_id, UUID):
            return self._orders.get(order_id)
        local_id = self._channel_map.get(str(order_id))
        if local_id:
            return self._orders.get(local_id)

        data = await self._post("/api/claw/mockTrading/orders", {"moneyUnit": 1, "type": "all"})
        if data is None:
            return None
        orders_raw = _safe_get(data, "data", "orders") or []
        for raw in (orders_raw or []):
            if str(raw.get("id", "")) == str(order_id):
                return _parse_order_raw(raw)
        return None

    async def health_check(self) -> ComponentHealth:
        if not self._api_key:
            return ComponentHealth(name=self.name, status=HealthStatus.UNHEALTHY,
                                    message="MX_APIKEY 未配置", error="MX_APIKEY 未配置")
        try:
            bal = await self.get_balance()
            if bal and bal.get("account_name"):
                return ComponentHealth(
                    name=self.name, status=HealthStatus.HEALTHY,
                    message=f"{bal['account_name']} 总资产 ¥{bal['total_assets']:,.2f}",
                )
            return ComponentHealth(name=self.name, status=HealthStatus.DEGRADED, message="余额查询失败")
        except Exception as e:
            return ComponentHealth(name=self.name, status=HealthStatus.UNHEALTHY, message=str(e))

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    # ----------------------------------------------------------------
    # 内部
    # ----------------------------------------------------------------

    async def _post(self, endpoint: str, payload: dict) -> dict | None:
        url = f"{self._api_url}{endpoint}"
        headers = {"apikey": self._api_key}
        session = await self._ensure_session()

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with session.post(url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT) as resp:
                    body = await resp.json()
                    code = str(body.get("code", ""))
                    if code == "113":
                        self._last_error = body.get("message", "调用次数超限")
                        logger.warning("MX 超限 (attempt %d)", attempt)
                        if attempt < MAX_RETRIES:
                            await asyncio.sleep(RETRY_DELAY * (2 ** (attempt - 1)))
                        continue
                    if code in ("0", "200"):
                        return body
                    self._last_error = body.get("message", "")
                    logger.warning("MX API 返回错误: %s", body.get("message", ""))
                    return body
            except Exception as e:
                logger.warning("MX HTTP 错误 (attempt %d): %s", attempt, e)
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAY)
        return None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=REQUEST_TIMEOUT)
        return self._session


# ================================================================
# 数据转换函数
# ================================================================

def _code_to_mx(hetu_code: str) -> str:
    """河图代码 -> MX代码。600519.SH -> 600519"""
    code = hetu_code.strip().upper()
    if "." in code:
        return code.split(".")[0]
    return code


def _code_from_mx(mx_code: str, sec_mkt: int) -> str:
    """MX代码 -> 河图代码。600519, 1 -> 600519.SH"""
    if sec_mkt in (1, 0, 116):
        suffix_map = {1: ".SH", 0: ".SZ", 116: ".HK"}
        return f"{mx_code}{suffix_map.get(sec_mkt, '.SZ')}"
    if sec_mkt == 2:
        return f"{mx_code}.HK"
    return f"{mx_code}.SZ"


def _code_to_market(hetu_code: str) -> int:
    """河图代码 -> secMkt。600519 -> 1(沪), 000001 -> 0(深), 北交 -> 0"""
    code = hetu_code.strip().upper()
    if code.startswith("6"):
        return 1
    return 0


def _price_to_mx(price: float | None, hetu_code: str) -> int:
    """价格 -> MX 整数编码。沪市/北交 price*100, 深市 price*1000"""
    if price is None:
        return 0
    code = hetu_code.strip().upper()
    if ".SH" in code or code.startswith(("6", "5", "9", "4", "8")):
        return int(price * 100)
    return int(price * 1000)


def _price_from_mx(raw_price: int | float, price_dec: int) -> float:
    """MX 整数价格 -> float。price_dec 是小数位数"""
    return float(raw_price) / (10 ** price_dec)


def _money_from_mx(raw: int | float | None, currency_unit: int) -> float:
    """MX 金额 -> 元。currencyUnit 1000=厘, 1=元"""
    if raw is None:
        return 0.0
    return float(raw) / currency_unit


def _safe_get(d: dict, *keys: str, default=None):
    for k in keys:
        if isinstance(d, dict):
            d = d.get(k)
        else:
            return default
    return d if d is not None else default


def _parse_position(raw: dict, currency_unit: int, channel: str = "mx") -> Position:
    mx_code = str(raw.get("secCode", ""))
    sec_mkt = raw.get("secMkt", 0)
    price_dec = raw.get("priceDec", 2)
    cost_dec = raw.get("costPriceDec", 2)
    current_price = _price_from_mx(raw.get("price", 0), price_dec)
    shares = raw.get("count", 0)
    market_value = _money_from_mx(raw.get("value", 0), currency_unit)

    return Position(
        channel="mx",
        code=_code_from_mx(mx_code, sec_mkt),
        name=raw.get("secName", ""),
        shares=shares,
        avg_cost=_price_from_mx(raw.get("costPrice", 0), cost_dec),
        current_price=current_price,
        market_value=market_value,
        unrealized_pnl=_money_from_mx(raw.get("profit", 0), currency_unit),
        entry_date=date.today(),
        buyable_today=raw.get("availCount", 0) > 0,
    )


def _parse_order_raw(raw: dict) -> Order:
    mx_code = str(raw.get("secCode", ""))
    sec_mkt = raw.get("secMkt", 0)
    mx_status = raw.get("status", 0)
    price_dec = raw.get("priceDec", 2)
    drt = raw.get("drt", 0)
    order_type = OrderType.LIMIT if raw.get("type", 1) != 5 else OrderType.MARKET
    channel_id = str(raw.get("id", ""))

    return Order(
        channel="mx",
        code=_code_from_mx(mx_code, sec_mkt),
        action=_MX_DRT_MAP.get(drt, Action.BUY),
        order_type=order_type,
        price=_price_from_mx(raw.get("price", 0), price_dec),
        shares=raw.get("count", 0),
        status=_MX_STATUS_MAP.get(mx_status, OrderStatus.CREATED),
        channel_order_id=channel_id,
        created_at=datetime.fromtimestamp(raw.get("time", 0), tz=timezone.utc),
    )


def _parse_trade_raw(raw: dict) -> Trade | None:
    mx_code = str(raw.get("secCode", ""))
    sec_mkt = raw.get("secMkt", 0)
    price_dec = raw.get("priceDec", 2)
    trade_price = _price_from_mx(raw.get("tradePrice", 0), price_dec)
    filled_shares = raw.get("tradeCount", 0)
    if filled_shares <= 0:
        return None
    drt = raw.get("drt", 0)

    return Trade(
        order_id=UUID(int=0),
        channel="mx",
        code=_code_from_mx(mx_code, sec_mkt),
        action=_MX_DRT_MAP.get(drt, Action.BUY),
        filled_price=trade_price,
        filled_shares=filled_shares,
        trade_time=datetime.fromtimestamp(raw.get("time", 0), tz=timezone.utc),
    )

_parse_order = _parse_order_raw  # compatibility alias for tests
_parse_trade = _parse_trade_raw
_parse_position = _parse_position
