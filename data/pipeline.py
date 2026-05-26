"""
河图 (HeTu) DataPipeline — ETL 数据管道

支持多数据源、过滤、校验、复权的完整 ETL 流水线。

管道阶段:
1. 数据获取 — 从 Provider 拉取原始数据
2. Schema 标准化 — 列名映射 + 类型校验
3. 过滤 — 流动性/ST 过滤
4. 质量检查 — DataGuard 守卫
5. 复权 — 前复权/后复权
6. 返回 — 结构化数据 + 问题报告
"""

from __future__ import annotations

import logging

import pandas as pd

from data.base import BaseDataProvider
from data.filters import LiquidityFilter, STFilter
from data.guard import DataGuard
from data.schema import normalize_columns, validate_daily_bars, compute_adjust_factor, compute_returns
from core.types import Bar, DataIssue, IssueSeverity

logger = logging.getLogger("hetu.data.pipeline")


class DataPipeline:
    """ETL 数据管道"""

    def __init__(
        self,
        provider: BaseDataProvider,
        liquidity_filter: LiquidityFilter | None = None,
        data_guard: DataGuard | None = None,
        st_set: set[str] | None = None,
        adjustment_mode: str = "backward",
    ) -> None:
        self.provider = provider
        self.liquidity_filter = liquidity_filter or LiquidityFilter()
        self.data_guard = data_guard or DataGuard()
        self.st_set = st_set or set()
        self.adjustment_mode = adjustment_mode

    async def run(
        self,
        codes: list[str],
        start_date: str,
        end_date: str,
    ) -> tuple[dict[str, list[Bar]], list[DataIssue]]:
        """
        运行完整 ETL 管道

        Args:
            codes: 股票代码列表
            start_date: 开始日期 YYYY-MM-DD
            end_date: 结束日期 YYYY-MM-DD

        Returns:
            (标准化 Bar 字典, 数据问题列表)
        """
        all_issues: list[DataIssue] = []

        # Stage 1: 获取原始数据
        logger.info("Stage 1: 获取 %d 只股票 %s ~ %s 日线数据", len(codes), start_date, end_date)
        bars = await self.provider.get_daily_bars(codes, start_date, end_date)

        if not bars:
            all_issues.append(DataIssue(
                severity=IssueSeverity.FATAL,
                description=f"未获取到任何数据: {len(codes)} 只股票",
                source="DataPipeline",
            ))
            return {}, all_issues

        # Stage 2: Bar -> DataFrame
        df = self._bars_to_dataframe(bars)

        # Stage 3: Schema 标准化
        logger.info("Stage 2: Schema 标准化")
        df = normalize_columns(df)

        # Stage 4: 校验日线数据
        logger.info("Stage 3: 校验日线数据")
        df, schema_issues = validate_daily_bars(df)
        all_issues.extend(schema_issues)

        if df.empty:
            return {}, all_issues

        # Stage 5: ST 过滤
        logger.info("Stage 4: ST 过滤")
        df, _ = STFilter.filter_st(df, name_col="name" if "name" in df.columns else "code")

        # Stage 6: 流动性过滤
        logger.info("Stage 5: 流动性过滤")
        df, _ = self.liquidity_filter.apply(df)

        # Stage 7: DataGuard 质量检查
        logger.info("Stage 6: 数据质量守卫")
        guard_issues = self.data_guard.check(df, self.st_set)
        all_issues.extend(guard_issues)

        # Check for FATAL issues
        fatals = [i for i in all_issues if i.severity == IssueSeverity.FATAL]
        if fatals:
            logger.error("发现 %d 个 FATAL 问题，中断管道", len(fatals))
            return {}, all_issues

        # Stage 8: 复权
        logger.info("Stage 7: 复权 (%s)", self.adjustment_mode)
        df = compute_adjust_factor(df, self.adjustment_mode)

        # Stage 9: 计算收益率
        logger.info("Stage 8: 计算收益率")
        df = compute_returns(df)

        # Stage 10: DataFrame -> Bar
        logger.info("Stage 9: DataFrame -> Bar")
        result = BaseDataProvider._df_to_bars(df)

        logger.info("管道完成: %d 只股票, %d 个问题", len(result), len(all_issues))
        return result, all_issues

    @staticmethod
    def _bars_to_dataframe(bars: dict[str, list[Bar]]) -> pd.DataFrame:
        """将 Bar 字典转换为 DataFrame"""
        rows = []
        for code, bar_list in bars.items():
            for bar in bar_list:
                rows.append({
                    "code": bar.code,
                    "date": bar.date,
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                    "amount": bar.amount,
                })
        return pd.DataFrame(rows)

    def set_st_set(self, st_set: set[str]) -> None:
        """设置 ST 股票集合"""
        self.st_set = st_set
