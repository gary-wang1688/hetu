"""
河图 (HeTu) 股票过滤器

1. 流动性过滤器 — 剔除日成交额/市值/换手率不足的股票
2. ST 过滤器 — 剔除 ST/*ST 股票
3. 交易日过滤器 — T+1 规则感知
"""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger("hetu.filters")


class LiquidityFilter:
    """流动性过滤器"""

    def __init__(
        self,
        min_daily_amount: float = 1e8,
        min_market_cap: float = 50e8,
        min_turnover_rate: float = 0.5,
    ) -> None:
        self.min_daily_amount = min_daily_amount
        self.min_market_cap = min_market_cap
        self.min_turnover_rate = min_turnover_rate

    def apply(
        self,
        df: pd.DataFrame,
        amount_col: str = "avg_amount_20d",
        cap_col: str = "market_cap",
        turnover_col: str = "turnover_rate_5d",
    ) -> tuple[pd.DataFrame, list[str]]:
        """
        过滤不满足流动性要求的股票

        Returns:
            (过滤后的 DataFrame, 被剔除的股票代码列表)
        """
        if df.empty:
            return df, []

        before = len(df)
        removed_codes: set = set()

        # 成交额过滤
        if amount_col in df.columns:
            bad = df[amount_col] < self.min_daily_amount
            removed_codes.update(df.loc[bad, "code"].unique())
            df = df[~df["code"].isin(removed_codes)]
            logger.debug("成交额过滤: 剔除 %d 只", len(removed_codes))

        # 市值过滤
        if cap_col in df.columns:
            bad = df[cap_col] < self.min_market_cap
            df = df[~bad]

        # 换手率过滤
        if turnover_col in df.columns:
            bad = df[turnover_col] < self.min_turnover_rate
            df = df[~bad]

        after = len(df) if "code" in df.columns else 0
        logger.info("流动性过滤: %d -> %d 行 (剔除 %d 只)", before, after, len(removed_codes))

        return df, list(removed_codes)


class STFilter:
    """ST 股票过滤器"""

    ST_KEYWORDS: frozenset[str] = frozenset({"ST", "SST", "*ST", "NST", "PT"})

    @staticmethod
    def is_st(name: str) -> bool:
        """判断股票名称是否为 ST"""
        if not name:
            return False
        normalized = name.upper().replace(" ", "")
        return any(kw in normalized for kw in STFilter.ST_KEYWORDS)

    @classmethod
    def filter_st(cls, df: pd.DataFrame, name_col: str = "name") -> tuple[pd.DataFrame, set[str]]:
        """
        剔除 ST 股票

        Returns:
            (过滤后的 DataFrame, ST 代码集合)
        """
        if df.empty:
            return df, set()

        if name_col not in df.columns:
            logger.warning("无股票名称列 '%s'，跳过 ST 过滤", name_col)
            return df, set()

        df["_is_st"] = df[name_col].apply(cls.is_st)
        st_codes = set(df.loc[df["_is_st"], "code"].unique())
        df = df.drop(columns=["_is_st"])
        df = df[~df["code"].isin(st_codes)]

        st_preview = list(st_codes)[:5]
        logger.info("ST 过滤: 剔除 %d 只 (%s...)", len(st_codes), st_preview)

        return df, st_codes

    @classmethod
    def build_st_set(cls, stock_list: list[dict], name_field: str = "name") -> set[str]:
        """从股票列表构建 ST 代码集合"""
        return {
            s.get("code", "")
            for s in stock_list
            if cls.is_st(s.get(name_field, ""))
        }


class T1Filter:
    """T+1 交易日过滤器 — 标记当日不可卖出的持仓"""

    @staticmethod
    def filter(codes: list[str], positions: list[dict]) -> list[str]:
        """
        过滤掉当日买入不可卖出的持仓

        Args:
            codes: 待过滤的股票代码列表
            positions: 当前持仓列表 (dict 需含 code, buyable_today 字段)

        Returns:
            可卖出的股票代码列表
        """
        t1_set = {
            p["code"]
            for p in positions
            if not p.get("buyable_today", True)
        }
        return [c for c in codes if c not in t1_set]
