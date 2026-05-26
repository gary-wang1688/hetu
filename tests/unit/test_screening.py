"""MXScreener 单元测试。"""

from __future__ import annotations

import pytest

from data.sources.mx_screening import (
    MXScreener,
    _build_column_map,
    _parse_stock_row,
    _parse_markdown_table,
    _format_stock_code,
    _parse_pct,
)


# ============================================================
# 代码格式化
# ============================================================

class TestFormatStockCode:
    def test_shanghai(self):
        assert _format_stock_code("600519") == "600519.SH"
        assert _format_stock_code("601318") == "601318.SH"

    def test_shenzhen(self):
        assert _format_stock_code("000001") == "000001.SZ"
        assert _format_stock_code("300750") == "300750.SZ"

    def test_already_formatted(self):
        assert _format_stock_code("600519.SH") == "600519.SH"

    def test_beijing(self):
        assert _format_stock_code("920001") == "920001.SH"


# ============================================================
# 列映射
# ============================================================

class TestBuildColumnMap:
    def test_basic(self):
        cols = [
            {"key": "SECURITY_CODE", "title": "代码"},
            {"key": "NEWEST_PRICE", "title": "最新价(元)"},
        ]
        m = _build_column_map(cols)
        assert m["SECURITY_CODE"] == "代码"
        assert m["NEWEST_PRICE"] == "最新价(元)"

    def test_field_fallback(self):
        cols = [{"field": "CHG", "title": "涨跌额"}]
        m = _build_column_map(cols)
        assert m["CHG"] == "涨跌额"


# ============================================================
# 单行解析
# ============================================================

class TestParseStockRow:
    def test_basic(self):
        row = {
            "SECURITY_CODE": "600519",
            "SECURITY_SHORT_NAME": "贵州茅台",
            "NEWEST_PRICE": "1780.00",
            "PCHG": "1.23",
            "CHG": "21.89",
        }
        col_map = {"SECURITY_CODE": "代码", "SECURITY_SHORT_NAME": "名称",
                    "NEWEST_PRICE": "最新价", "PCHG": "涨跌幅", "CHG": "涨跌额"}
        s = _parse_stock_row(row, col_map)
        assert s["code"] == "600519.SH"
        assert s["name"] == "贵州茅台"
        assert s["price"] == 1780.0
        assert s["chg_pct"] == 1.23
        assert s["chg_val"] == 21.89

    def test_missing_code(self):
        assert _parse_stock_row({}, {}) is None


# ============================================================
# Markdown 表格解析
# ============================================================

class TestParseMarkdownTable:
    def test_basic(self):
        md = """| 代码 | 名称 | 最新价 | 涨跌幅 |
|------|------|--------|--------|
|600519|贵州茅台| 1780.0 | +1.23% |
|000001|平安银行| 12.50 | -0.80% |"""
        stocks = _parse_markdown_table(md)
        assert len(stocks) == 2
        assert stocks[0]["code"] == "600519.SH"
        assert stocks[0]["name"] == "贵州茅台"
        assert stocks[0]["chg_pct"] == 1.23
        assert stocks[1]["code"] == "000001.SZ"
        assert stocks[1]["chg_pct"] == -0.80

    def test_empty(self):
        assert _parse_markdown_table("") == []
        assert _parse_markdown_table("no table here") == []


# ============================================================
# 百分比解析
# ============================================================

class TestParsePct:
    def test_string(self):
        assert _parse_pct("1.23%") == 1.23
        assert _parse_pct("+1.23%") == 1.23
        assert _parse_pct("-0.80%") == -0.80

    def test_numeric(self):
        assert _parse_pct(1.23) == 1.23
        assert _parse_pct(0) == 0.0

    def test_none(self):
        assert _parse_pct(None) == 0.0


# ============================================================
# 无 Key 降级
# ============================================================

class TestMXScreenerNoKey:
    @pytest.fixture
    def screener(self):
        return MXScreener(api_key="")

    async def test_screen_no_key(self, screener):
        assert await screener.screen("市盈率<20") == []

    async def test_screen_codes_no_key(self, screener):
        assert await screener.screen_codes("市盈率<20") == []

    async def test_screen_top_no_key(self, screener):
        assert await screener.screen_top("市盈率<20", 5) == []
