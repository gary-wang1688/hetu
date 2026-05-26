"""
河图 (HeTu) DataGuard — 数据质量守卫

三级严重度 + 处置路径:
- WARN   -> 记录但不阻断
- ERROR  -> 剔除该股票/日期，发告警
- FATAL  -> 阻断整个管道，CRITICAL 告警

检查项:
1. 停牌日检测 (ERROR)
2. 除权跳空检测 (WARN/ERROR)
3. 成交量异动 (WARN)
4. ST 状态检查 (WARN)
5. 跨数据源交叉验证
"""

from __future__ import annotations

import logging

import pandas as pd

from core.types import DataIssue, IssueSeverity

logger = logging.getLogger("hetu.guard")


class DataGuard:
    """数据质量守卫"""

    def __init__(
        self,
        split_threshold: float = 0.11,
        limit_threshold: float = 0.095,
        volume_spike_mult: float = 5.0,
    ) -> None:
        self.split_threshold = split_threshold
        self.limit_threshold = limit_threshold
        self.volume_spike_mult = volume_spike_mult

    def check(self, df: pd.DataFrame, st_set: set[str] | None = None) -> list[DataIssue]:
        """
        全量数据质量检查

        Args:
            df: 标准化后的日线 DataFrame (columns: code, date, open, high, low, close, volume, ...)
            st_set: ST 股票代码集合

        Returns:
            DataIssue 列表，按严重度排序 (FATAL > ERROR > WARN)
        """
        if df.empty:
            return []

        issues: list[DataIssue] = []

        issues.extend(self._check_stop_day(df))
        issues.extend(self._check_split_jump(df))
        issues.extend(self._check_limit(df))

        if st_set:
            issues.extend(self._check_st(df, st_set))

        # Sort by severity: FATAL(0) > ERROR(1) > WARN(2)
        severity_order = {IssueSeverity.FATAL: 0, IssueSeverity.ERROR: 1, IssueSeverity.WARN: 2}
        issues.sort(key=lambda i: severity_order.get(i.severity, 99))

        return issues

    def _check_stop_day(self, df: pd.DataFrame) -> list[DataIssue]:
        """
        检测停牌日（v2.1: 升级为 ERROR，停牌日数据直接从候选池剔除）:
        - 成交量为 0
        - 开盘价 = 收盘价 = 最高价 = 最低价 (一字板也可能是停牌)
        """
        issues: list[DataIssue] = []

        zero_vol = df["volume"] <= 0
        for _, row in df[zero_vol].iterrows():
            issues.append(DataIssue(
                severity=IssueSeverity.ERROR,
                code=str(row.get("code")),
                date=str(row.get("date", "")),
                field="volume",
                description="成交量为 0，停牌日",
                source="DataGuard",
            ))

        # Detect flat price (OHLC all equal)
        if all(col in df.columns for col in ("open", "high", "low", "close")):
            flat = (
                (df["open"] == df["close"])
                & (df["high"] == df["low"])
                & (df["open"] == df["high"])
            )
            for _, row in df[flat].iterrows():
                issues.append(DataIssue(
                    severity=IssueSeverity.ERROR,
                    code=str(row.get("code")),
                    date=str(row.get("date", "")),
                    field="ohlc",
                    description="OHLC 四价相等，停牌日或一字板",
                    source="DataGuard",
                ))

        return issues

    def _check_split_jump(self, df: pd.DataFrame) -> list[DataIssue]:
        """检测除权除息造成的价格跳空 (pct_change > split_threshold)"""
        issues: list[DataIssue] = []

        if "close" not in df.columns:
            return issues

        for code, code_df in df.groupby("code"):
            code_df = code_df.sort_values("date")
            if len(code_df) < 2:
                continue
            pct_changes = code_df["close"].pct_change().abs()
            jumps = pct_changes > self.split_threshold
            for idx in jumps[jumps].index:
                row = code_df.loc[idx]
                issues.append(DataIssue(
                    severity=IssueSeverity.ERROR,
                    code=str(row.get("code")),
                    date=str(row.get("date", "")),
                    field="close",
                    description=f"价格跳空 {pct_changes[idx]:.1%}，疑似除权除息",
                    source="DataGuard",
                ))

        return issues

    def _check_limit(self, df: pd.DataFrame) -> list[DataIssue]:
        """检测成交量异常放量"""
        issues: list[DataIssue] = []

        if "volume" not in df.columns:
            return issues

        for code, code_df in df.groupby("code"):
            if len(code_df) < 20:
                continue
            code_df = code_df.sort_values("date")
            avg_vol = code_df["volume"].rolling(20).mean()
            spike = code_df["volume"] > avg_vol * self.volume_spike_mult

            for idx in spike[spike].index:
                row = code_df.loc[idx]
                vol_ratio = code_df.loc[idx, "volume"] / avg_vol[idx]
                issues.append(DataIssue(
                    severity=IssueSeverity.WARN,
                    code=str(row.get("code")),
                    date=str(row.get("date", "")),
                    field="volume",
                    description=f"成交量异动 {vol_ratio:.1f}x 均量",
                    source="DataGuard",
                ))

        return issues

    def _check_st(self, df: pd.DataFrame, st_set: set[str]) -> list[DataIssue]:
        """检查 ST 股票状态"""
        issues: list[DataIssue] = []

        if "code" not in df.columns:
            return issues

        for _, row in df[df["code"].isin(st_set)].iterrows():
            issues.append(DataIssue(
                severity=IssueSeverity.WARN,
                code=str(row.get("code")),
                date=str(row.get("date", "")),
                field="code",
                description="ST 股票（涨跌幅限制不同）",
                source="DataGuard",
            ))

        return issues

    def cross_validate(
        self,
        primary: pd.DataFrame,
        secondary: pd.DataFrame,
        tolerance: float = 0.01,
    ) -> list[DataIssue]:
        """
        跨数据源交叉验证

        Args:
            primary: 主数据源 DataFrame
            secondary: 辅数据源 DataFrame
            tolerance: 允许的偏差阈值 (默认 1%)

        Returns:
            不一致项列表
        """
        issues: list[DataIssue] = []

        if primary.empty or secondary.empty:
            return issues

        merged = pd.merge(
            primary[["code", "date", "close"]],
            secondary[["code", "date", "close"]],
            on=["code", "date"],
            suffixes=("_primary", "_secondary"),
            how="inner",
        )

        if merged.empty:
            return issues

        merged["diff"] = (merged["close_primary"] - merged["close_secondary"]).abs()
        merged["diff_pct"] = merged["diff"] / merged["close_primary"]

        diff_rows = merged[merged["diff_pct"] > tolerance]

        for _, row in diff_rows.iterrows():
            issues.append(DataIssue(
                severity=IssueSeverity.WARN,
                code=str(row.get("code")),
                date=str(row.get("date", "")),
                field="close",
                description=f"跨源收盘价偏差 {row['diff_pct']:.2%}",
                source="DataGuard",
            ))

        return issues
