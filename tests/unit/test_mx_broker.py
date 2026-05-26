"""MXBroker 交易通道单元测试。

重点：代码/价格/金额/状态转换函数的边界条件。
网络请求部分通过 mock aiohttp 测试。
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from core.types import (
    Action, Order, OrderStatus, OrderType,
    HealthStatus,
)
from execution.channels.mx_broker import (
    MXBroker,
    _code_to_mx,
    _code_from_mx,
    _code_to_market,
    _price_to_mx,
    _price_from_mx,
    _money_from_mx,
    _parse_order,
    _parse_position,
    _parse_trade,
    _safe_get,
)


# ===========================================================
# 代码转换
# ===========================================================

class TestCodeConversion:
    def test_hetu_to_mx(self):
        assert _code_to_mx("600519.SH") == "600519"
        assert _code_to_mx("000001.SZ") == "000001"
        assert _code_to_mx("300750.SZ") == "300750"
        assert _code_to_mx("688001.SH") == "688001"
        assert _code_to_mx("600519") == "600519"       # already plain

    def test_mx_to_hetu(self):
        assert _code_from_mx("600519", 1) == "600519.SH"
        assert _code_from_mx("000001", 0) == "000001.SZ"
        assert _code_from_mx("00700", 116) == "00700.HK"

    def test_code_to_market(self):
        assert _code_to_market("600519") == 1    # 沪
        assert _code_to_market("000001") == 0    # 深
        assert _code_to_market("300750") == 0    # 创业板
        assert _code_to_market("920001") == 0    # 北


# ===========================================================
# 价格转换
# ===========================================================

class TestPriceConversion:
    def test_price_to_mx_sh(self):
        """沪市 2 位小数：price * 100"""
        assert _price_to_mx(1780.00, "600519.SH") == 178000
        assert _price_to_mx(12.50, "600519.SH") == 1250
        assert _price_to_mx(0.01, "600519.SH") == 1

    def test_price_to_mx_sz(self):
        """深市 3 位小数：price * 1000"""
        assert _price_to_mx(12.345, "000001.SZ") == 12345
        assert _price_to_mx(300.50, "300750.SZ") == 300500

    def test_price_to_mx_none(self):
        assert _price_to_mx(None, "600519.SH") == 0

    def test_price_from_mx(self):
        assert _price_from_mx(178000, 2) == 1780.00
        assert _price_from_mx(12345, 3) == 12.345
        assert _price_from_mx(0, 2) == 0.0


# ===========================================================
# 金额转换
# ===========================================================

class TestMoneyConversion:
    def test_currency_1000_converts(self):
        """厘 → 元：/1000"""
        assert _money_from_mx(125680500, 1000) == 125680.50
        assert _money_from_mx(0, 1000) == 0.0

    def test_currency_1_passthrough(self):
        """已是元"""
        assert _money_from_mx(500000.00, 1) == 500000.00
        assert _money_from_mx(123.45, 1) == 123.45
        assert _money_from_mx(0, 1) == 0.0

    def test_none_value(self):
        assert _money_from_mx(None, 1000) == 0.0


# ===========================================================
# 订单状态映射
# ===========================================================

class TestParseOrder:
    def test_order_status_mapping(self):
        """MX 状态 1-10 → OrderStatus"""
        base = {
            "id": "ORD001", "secCode": "600519", "secMkt": 1,
            "drt": 1, "price": 178000, "priceDec": 2,
            "count": 100, "time": 1742000120, "type": 1,
        }
        status_expect = {
            1: OrderStatus.CREATED,
            2: OrderStatus.SUBMITTED,
            3: OrderStatus.PARTIAL_FILLED,
            4: OrderStatus.FILLED,
            5: OrderStatus.PARTIAL_FILLED,
            8: OrderStatus.CANCELLED,
            9: OrderStatus.REJECTED,
        }
        for mx_stat, expected in status_expect.items():
            raw = {**base, "status": mx_stat}
            order = _parse_order(raw)
            assert order.status == expected, f"status {mx_stat} → {expected}"

    def test_order_buy_action(self):
        raw = {"id": "B1", "secCode": "600519", "secMkt": 1,
               "drt": 1, "price": 178000, "priceDec": 2,
               "count": 100, "status": 4, "time": 1742000120, "type": 1}
        order = _parse_order(raw)
        assert order.action == Action.BUY
        assert order.code == "600519.SH"

    def test_order_sell_action(self):
        raw = {"id": "S1", "secCode": "000001", "secMkt": 0,
               "drt": 2, "price": 12345, "priceDec": 3,
               "count": 500, "status": 4, "time": 1742000120, "type": 1}
        order = _parse_order(raw)
        assert order.action == Action.SELL
        assert order.code == "000001.SZ"

    def test_order_market_type(self):
        raw = {"id": "M1", "secCode": "600519", "secMkt": 1,
               "drt": 1, "price": 0, "priceDec": 2,
               "count": 100, "status": 2, "time": 1742000120, "type": 5}
        order = _parse_order(raw)
        assert order.order_type == OrderType.MARKET


# ===========================================================
# 持仓解析
# ===========================================================

class TestParsePosition:
    def test_basic(self):
        raw = {
            "secCode": "600519", "secMkt": 1, "secName": "贵州茅台",
            "count": 100, "availCount": 100,
            "value": 178000,           # 厘
            "price": 178000, "priceDec": 2,
            "costPrice": 170000, "costPriceDec": 2,
            "profit": 8000,           # 厘
            "profitPct": 4.7, "posPct": 14.2,
        }
        pos = _parse_position(raw, currency_unit=1000, channel="mx")
        assert pos.code == "600519.SH"
        assert pos.shares == 100
        assert pos.current_price == 1780.00
        assert pos.avg_cost == 1700.00
        assert pos.market_value == 178.00      # 178000 厘 → 178 元
        assert pos.unrealized_pnl == 8.00      # 8000 厘 → 8 元
        assert pos.buyable_today is True

    def test_buyable_false(self):
        raw = {
            "secCode": "000001", "secMkt": 0, "secName": "平安银行",
            "count": 500, "availCount": 0,
            "value": 5000000, "price": 10000, "priceDec": 2,
            "costPrice": 10000, "costPriceDec": 2,
            "profit": 0, "profitPct": 0, "posPct": 5,
        }
        pos = _parse_position(raw, currency_unit=1000, channel="mx")
        assert pos.buyable_today is False


# ===========================================================
# 成交解析
# ===========================================================

class TestParseTrade:
    def test_valid_trade(self):
        raw = {
            "id": "T1", "secCode": "600519", "secMkt": 1,
            "drt": 1, "count": 100, "tradeCount": 100,
            "price": 178000, "priceDec": 2,
            "tradePrice": 178000, "status": 4, "time": 1742000120,
        }
        trade = _parse_trade(raw)
        assert trade is not None
        assert trade.code == "600519.SH"
        assert trade.action == Action.BUY
        assert trade.filled_price == 1780.00
        assert trade.filled_shares == 100

    def test_zero_trade_skipped(self):
        raw = {
            "id": "T2", "secCode": "000001", "secMkt": 0,
            "drt": 2, "count": 500, "tradeCount": 0,
            "price": 12345, "priceDec": 3,
            "tradePrice": 0, "status": 2, "time": 1742000120,
        }
        assert _parse_trade(raw) is None


# ===========================================================
# 工具函数
# ===========================================================

class TestSafeGet:
    def test_deep(self):
        d = {"a": {"b": {"c": 42}}}
        assert _safe_get(d, "a", "b", "c") == 42

    def test_missing(self):
        d = {"a": 1}
        assert _safe_get(d, "a", "b", "c") is None

    def test_default(self):
        d = {}
        assert _safe_get(d, "x", default="fallback") == "fallback"

    def test_non_dict(self):
        assert _safe_get({"a": None}, "a", "b") is None


# ===========================================================
# MXBroker 实例测试（本地逻辑，不联网）
# ===========================================================

class TestMXBrokerLocal:
    @pytest.fixture
    def broker_no_key(self):
        """无 API Key 的 broker，所有方法返回错误而非抛异常。"""
        return MXBroker(api_key="")

    @pytest.fixture
    def order_buy(self):
        return Order(
            order_id=uuid4(),
            channel="mx",
            code="600519.SH",
            action=Action.BUY,
            order_type=OrderType.LIMIT,
            price=1780.00,
            shares=100,
        )

    async def test_health_check_no_key(self, broker_no_key):
        h = await broker_no_key.health_check()
        assert h.status == HealthStatus.UNHEALTHY
        assert "MX_APIKEY" in h.error

    async def test_submit_order_no_key(self, broker_no_key, order_buy):
        r = await broker_no_key.submit_order(order_buy)
        assert r.success is False
        assert "MX_APIKEY" in r.error

    async def test_cancel_order_no_key(self, broker_no_key):
        r = await broker_no_key.cancel_order(uuid4())
        assert r.success is False

    async def test_query_order_no_key(self, broker_no_key):
        assert await broker_no_key.query_order("ORD001") is None

    async def test_get_positions_no_key(self, broker_no_key):
        assert await broker_no_key.get_positions() == []

    async def test_get_trades_no_key(self, broker_no_key):
        assert await broker_no_key.get_trades("2026-01-01", "2026-05-26") == []

    async def test_cancel_all_no_key(self, broker_no_key):
        r = await broker_no_key.cancel_all()
        assert r.success is False
        assert "MX_APIKEY" in r.error

    async def test_get_balance_no_key(self, broker_no_key):
        assert await broker_no_key.get_balance() == {}

    async def test_close(self, broker_no_key):
        await broker_no_key.close()
        # 不应抛异常


# ===========================================================
# YAML 配置驱动 (v2.3: 移除 env var fallback)
# ===========================================================

class TestMXBrokerConfig:
    def test_from_config_api_key(self):
        class MockCfg:
            def get(self, key, default=None):
                if key == "brokers.mx.api_key":
                    return "test-key-from-yaml"
                if key == "brokers.mx.base_url":
                    return "https://custom.example.com"
                return default
        broker = MXBroker.from_config(MockCfg())
        assert broker._api_key == "test-key-from-yaml"
        assert broker._api_url == "https://custom.example.com"

    def test_from_config_default_url(self):
        class MockCfg:
            def get(self, key, default=None):
                return default
        broker = MXBroker.from_config(MockCfg())
        assert "mkapi2.dfcfs.com" in broker._api_url
