"""
河图 (HeTu) 统一数据 Schema

功能:
1. 标准化多源列名映射
2. 日线数据校验与清洗
3. 复权因子计算（前复权/后复权）
4. 收益率计算
"""

from __future__ import annotations

import logging

import pandas as pd

from core.types import DataIssue, IssueSeverity
from core.errors import SchemaValidationError

logger = logging.getLogger("hetu.schema")

# 多数据源列名 -> 统一列名
COLUMN_MAP: dict[str, str] = {
    # 中文列名
    "日期": "date",
    "股票代码": "code",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
    "成交额": "amount",
    "换手率": "turnover",
    "涨跌幅": "pct_change",
    # 其他数据源
    "time": "date",
    "stime": "date",
    "lastPrice": "close",
    "lastClose": "pre_close",
    "pvolume": "volume",
    "amount": "amount",  # 语义一致性警告: 源列名与目标相同
    "symbol": "code",
    # 快捷列名
    "date": "date",
    "close": "close",
    "volume": "volume",
    "amount": "amount",
}

# 日线数据必须包含的列
DAILY_REQUIRED: frozenset = frozenset({"date", "close", "volume", "amount"})

# 日线数据完整列集
DAILY_ALL: frozenset = DAILY_REQUIRED | frozenset({"code", "open", "high", "low"})


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """标准化列名（v2.1: 新增冲突检测）。

    注意: 当源列名与目标列名相同（如 amount->amount）时，
    不会触发重命名，但数据源的值语义可能与预期不同。
    建议数据源层在调用前做语义校验。
    """
    rename: dict[str, str] = {}
    for col in df.columns:
        if col in COLUMN_MAP:
            target = COLUMN_MAP[col]
            if col != target:
                rename[col] = target
            else:
                # Same name, log warning about potential semantic mismatch
                logger.debug("列名无变化: %s (源语义与目标相同？)", col)

    if rename:
        df = df.rename(columns=rename)
        logger.debug("标准化列名: %d 列", len(rename))

    return df


def validate_daily_bars(df: pd.DataFrame) -> tuple[pd.DataFrame, list[DataIssue]]:
    """
    校验日线数据，返回 (清洗后的DataFrame, 问题列表)。

    检查项:
    - 必须列存在
    - 价格 > 0
    - 无 NaN 在关键列
    - high >= low, high >= open, high >= close, low <= open, low <= close
    - volume >= 0
    - date 格式化与去重
    """
    issues: list[DataIssue] = []

    # 先标准化列名
    df = normalize_columns(df)

    if df.empty:
        return df, issues

    # 检查必须列
    missing = [col for col in DAILY_REQUIRED if col not in df.columns]
    if missing:
        raise SchemaValidationError(
            f"缺少必须列: {', '.join(missing)}",
            field=",".join(missing),
        )

    # 清理 code 列
    if "code" in df.columns:
        df["code"] = df["code"].astype(str).str.strip()

    # 检查必需列 NaN + 清理
    for col in ("open", "high", "low", "close", "volume"):
        if col not in df.columns:
            continue
        nan_count = df[col].isna().sum()
        if nan_count > 0:
            issues.append(DataIssue(
                severity=IssueSeverity.WARN,
                code="*",
                date="*",
                field=col,
                description=f"{col} 有 {nan_count} 个空值",
                source="schema",
            ))
            df = df.dropna(subset=[col])

    # 检查价格正数
    for col in ("open", "high", "low", "close"):
        if col not in df.columns:
            continue
        neg_count = (df[col] <= 0).sum()
        if neg_count > 0:
            issues.append(DataIssue(
                severity=IssueSeverity.WARN,
                code="*",
                date="*",
                field=col,
                description=f"{col} 有 {neg_count} 个非正数",
                source="schema",
            ))
            df = df[df[col] > 0]

    # 修正 high < max(open, close)
    if "high" in df.columns and "open" in df.columns and "close" in df.columns:
        bad_high = df["high"] < df[["open", "close"]].max(axis=1)
        if bad_high.any():
            issues.append(DataIssue(
                severity=IssueSeverity.WARN,
                code="*",
                date="*",
                field="high",
                description=f"high < max(open, close) 共 {bad_high.sum()} 行，已修正",
                source="schema",
            ))
            df.loc[bad_high, "high"] = df.loc[bad_high, ["open", "close"]].max(axis=1)

    # 修正 low > min(open, close)
    if "low" in df.columns and "open" in df.columns and "close" in df.columns:
        bad_low = df["low"] > df[["open", "close"]].min(axis=1)
        if bad_low.any():
            issues.append(DataIssue(
                severity=IssueSeverity.WARN,
                code="*",
                date="*",
                field="low",
                description=f"low > min(open, close) 共 {bad_low.sum()} 行，已修正",
                source="schema",
            ))
            df.loc[bad_low, "low"] = df.loc[bad_low, ["open", "close"]].min(axis=1)

    # 修正负成交量
    if "volume" in df.columns:
        bad_vol = df["volume"] < 0
        if bad_vol.any():
            issues.append(DataIssue(
                severity=IssueSeverity.WARN,
                code="*",
                date="*",
                field="volume",
                description=f"负成交量 {bad_vol.sum()} 行，已置 0",
                source="schema",
            ))
            df.loc[bad_vol, "volume"] = 0

    # 日期格式化
    if "date" in df.columns:
        try:
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        except Exception as e:
            issues.append(DataIssue(
                severity=IssueSeverity.ERROR,
                code="*",
                date="*",
                field="date",
                description=f"日期解析失败: {e}",
                source="schema",
            ))

    # 去重
    if "code" in df.columns and "date" in df.columns:
        before = len(df)
        df = df.drop_duplicates(subset=["code", "date"])
        after = len(df)
        if before != after:
            logger.info("去重: %d -> %d 行", before, after)

    return df, issues


def compute_adjust_factor(
    df: pd.DataFrame,
    adjustment_mode: str = "backward",
) -> pd.DataFrame:
    """
    计算复权因子

    Args:
        df: 含 code, date, close, adj_factor 列（原始前收）
        adjustment_mode: "backward" (后复权) | "forward" (前复权)

    Returns:
        添加 adj_close 列的 DataFrame
    """
    if df.empty:
        return df

    if "adj_factor" not in df.columns:
        df["adj_factor"] = 1.0

    result = df.copy()
    result = result.sort_values("date")

    if adjustment_mode == "backward":
        # 后复权: 从最新日往前乘
        for code in result["code"].unique():
            mask = result["code"] == code
            code_df = result.loc[mask].sort_values("date")
            if len(code_df) < 2:
                continue
            latest_adj = code_df["adj_factor"].iloc[-1]
            if not latest_adj or latest_adj <= 0:
                continue
            result.loc[mask, "adj_factor"] = code_df["adj_factor"] / latest_adj
    else:
        # 前复权: 从最早日往后乘
        for code in result["code"].unique():
            mask = result["code"] == code
            code_df = result.loc[mask].sort_values("date")
            if len(code_df) < 2:
                continue
            first_adj = code_df["adj_factor"].iloc[0]
            if not first_adj or first_adj <= 0:
                continue
            result.loc[mask, "adj_factor"] = code_df["adj_factor"] / first_adj

    result["adj_close"] = result["close"] // result["adj_factor"].fillna(1.0)

    return result


def compute_returns(df: pd.DataFrame) -> pd.DataFrame:
    """计算日收益率（按 code 分组）"""
    if df.empty:
        return df

    result = df.copy()
    result["return_1d"] = result.groupby("code")["close"].pct_change()
    result["return_1d"] = result["return_1d"].fillna(0.0)

    return result
