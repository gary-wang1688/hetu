"""StopLossEngine 单元测试。"""
from __future__ import annotations

from datetime import date

import pytest

from core.types import Bar, Action
from risk.stop_loss import StopLossEngine, StopAction


@pytest.fixture
def engine() -> StopLossEngine:
    return StopLossEngine(hard_threshold=0.05, trail_pct=0.04)


@pytest.fixture
def position_entry():
    """(code, entry_price, shares, entry_date)"""
    return ("000001.SZ", 50.0, 1000, "2024-01-15")


def _make_bar(
    code: str = "000001.SZ",
    price: float = 50.0,
    high: float | None = None,
) -> Bar:
    """创建测试用 Bar。"""
    if high is None:
        high = price + 0.5
    return Bar(
        code=code,
        date=date(2024, 1, 20),
        open=price - 0.1,
        high=high,
        low=price - 0.3,
        close=price,
        volume=10000000,
        amount=500000000.0,
    )


# ============================================================
# 硬止损 (Hard Stop)
# ============================================================

class TestHardStop:
    def test_hard_stop_triggered(self, engine: StopLossEngine) -> None:
        """价格跌破硬止损价应触发。"""
        engine.add_position("000001.SZ", entry_price=50.0)
        # hard_stop = 50 * (1 - 0.05) = 47.5
        bar = _make_bar(price=47.0)
        result = engine.check("000001.SZ", bar, today="2024-01-16")
        assert result == StopAction.HARD_STOP

    def test_hard_stop_not_triggered(self, engine: StopLossEngine) -> None:
        """价格高于硬止损价不应触发。"""
        engine.add_position("000001.SZ", entry_price=50.0)
        bar = _make_bar(price=52.0)
        result = engine.check("000001.SZ", bar, today="2024-01-16")
        assert result == StopAction.NONE

    def test_hard_stop_boundary(self, engine: StopLossEngine) -> None:
        """价格等于硬止损价应触发。"""
        engine.add_position("000001.SZ", entry_price=50.0)
        # hard_stop = 47.5
        bar = _make_bar(price=47.5)
        result = engine.check("000001.SZ", bar, today="2024-01-16")
        assert result == StopAction.HARD_STOP


# ============================================================
# 移动止损 (Trail Stop)
# ============================================================

class TestTrailStop:
    def test_trail_stop_triggered(self, engine: StopLossEngine) -> None:
        """价格从高位回撤超过阈值应触发移动止损。"""
        engine.add_position("000001.SZ", entry_price=50.0)
        # 先涨到 60，更新最高价，trail_stop = 60 * (1 - 0.04) = 57.6
        bar_high = _make_bar(price=60.0, high=60.0)
        engine.check("000001.SZ", bar_high, today="2024-01-16")
        # 然后跌到 57
        bar_low = _make_bar(price=57.0, high=57.0)
        result = engine.check("000001.SZ", bar_low, today="2024-01-17")
        assert result == StopAction.TRAIL_STOP

    def test_trail_stop_not_triggered(self, engine: StopLossEngine) -> None:
        """价格在移动止损价上方不应触发。"""
        engine.add_position("000001.SZ", entry_price=50.0)
        bar = _make_bar(price=55.0, high=55.0)
        result = engine.check("000001.SZ", bar, today="2024-01-16")
        assert result == StopAction.NONE


# ============================================================
# 时间止损 (Time Stop)
# ============================================================

class TestTimeStop:
    def test_time_stop_loss(self, engine: StopLossEngine) -> None:
        """持有超过最大天数应触发时间止损。"""
        from datetime import date, timedelta
        old_date = (date.today() - timedelta(days=30)).isoformat()
        engine.add_position("000001.SZ", entry_price=50.0, entry_date=old_date)
        bar = _make_bar(price=51.0)
        # days_held >= 20 (max_hold_days)
        result = engine.check("000001.SZ", bar)
        assert result == StopAction.ALERT

    def test_time_stop_profit_no_trigger(self, engine: StopLossEngine) -> None:
        """盈利但持有时间未到的持仓不触发时间止损。"""
        from datetime import date, timedelta
        recent_date = (date.today() - timedelta(days=2)).isoformat()
        engine.add_position("000001.SZ", entry_price=50.0, entry_date=recent_date)
        bar = _make_bar(price=55.0)
        result = engine.check("000001.SZ", bar)
        assert result == StopAction.NONE


# ============================================================
# T+1 保护
# ============================================================

class TestT1Protection:
    def test_t1_no_stop_signal(self, engine: StopLossEngine) -> None:
        """今日买入的持仓不生成止损信号。"""
        from datetime import date
        today_str = date.today().isoformat()
        engine.add_position("000001.SZ", entry_price=50.0, entry_date=today_str)
        bar = _make_bar(price=45.0)  # 应该触发硬止损
        result = engine.check("000001.SZ", bar, today=today_str)
        assert result == StopAction.NONE


# ============================================================
# 信号生成
# ============================================================

class TestSignalGeneration:
    def test_generate_stop_signal(self, engine: StopLossEngine) -> None:
        """跌破止损价应生成卖出信号。"""
        engine.add_position("000001.SZ", entry_price=50.0, entry_date="2024-01-15")
        bar = _make_bar(price=47.0)
        signals = engine.generate_stop_signals(
            {"000001.SZ": bar}, today="2024-01-16"
        )
        assert len(signals) == 1
        assert signals[0].code == "000001.SZ"
        assert signals[0].action == Action.SELL

    def test_no_signal_when_none(self, engine: StopLossEngine) -> None:
        """空持仓不生成信号。"""
        signals = engine.generate_stop_signals({})
        assert signals == []


# ============================================================
# 持仓管理
# ============================================================

class TestPositionManagement:
    def test_add_remove_position(self, engine: StopLossEngine) -> None:
        """添加后删除持仓。"""
        engine.add_position("000001.SZ", entry_price=50.0)
        assert len(engine) == 1
        engine.remove_position("000001.SZ")
        assert len(engine) == 0

    def test_update_highest_only_increases(self, engine: StopLossEngine) -> None:
        """最高价仅向上更新。"""
        engine.add_position("000001.SZ", entry_price=50.0)
        engine.update_highest("000001.SZ", 55.0)
        engine.update_highest("000001.SZ", 52.0)  # 不应降低
        positions = engine.get_all_positions()
        assert positions["000001.SZ"]["highest_price"] == 55.0

    def test_check_all_batch(self, engine: StopLossEngine) -> None:
        """批量检查多只持仓。"""
        from datetime import date, timedelta
        recent = (date.today() - timedelta(days=2)).isoformat()
        engine.add_position("000001.SZ", entry_price=50.0, entry_date=recent)
        engine.add_position("000002.SZ", entry_price=30.0, entry_date=recent)
        bars = {
            "000001.SZ": _make_bar(code="000001.SZ", price=47.0),
            "000002.SZ": _make_bar(code="000002.SZ", price=32.0),
        }
        results = engine.check_all(bars)
        assert results["000001.SZ"] == StopAction.HARD_STOP
        assert results["000002.SZ"] == StopAction.NONE

    def test_custom_stop_price_stored(self, engine: StopLossEngine) -> None:
        """自定义止损价应被存储。"""
        engine.add_position(
            "000001.SZ", entry_price=50.0, stop_price=48.0
        )
        levels = engine.get_stop_levels("000001.SZ")
        assert levels is not None
        assert levels["hard_stop"] == 48.0

    def test_custom_stop_price_triggers(self, engine: StopLossEngine) -> None:
        """自定义止损价触发。"""
        engine.add_position(
            "000001.SZ",
            entry_price=50.0,
            stop_price=48.0,
            entry_date="2024-01-15",
        )
        bar = _make_bar(price=47.5)
        result = engine.check("000001.SZ", bar, today="2024-01-16")
        assert result == StopAction.HARD_STOP
