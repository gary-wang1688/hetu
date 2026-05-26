"""
河图 (HeTu) 策略相关性矩阵

架构 §5.4.4: 策略组合模式前置条件。
上线前需提供与其他已上线策略的收益率相关性矩阵。
相关系数 > 0.7 需标记为高度相似。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

logger = logging.getLogger("hetu.corr")


@dataclass
class CorrelationReport:
    """策略相关性报告"""
    strategy_name: str = ""
    correlations: dict[str, float] = field(default_factory=dict)
    high_correlation_pairs: list[str] = field(default_factory=list)
    max_correlation: float = 0.0
    max_correlation_with: str = ""
    is_diverse: bool = True


class CorrelationAnalyzer:
    """
    策略相关性分析器

    计算策略两两之间的收益率相关性。
    用于上线审批和组合模式选择。
    """

    HIGH_CORR_THRESHOLD = 0.7

    def __init__(self, threshold: float = 0.7):
        self._threshold = threshold

    def compute_matrix(self, returns: dict[str, pd.Series]) -> pd.DataFrame:
        """计算策略收益率相关性矩阵

        Args:
            returns: {strategy_name: daily_return_series} 日收益率字典

        Returns:
            pd.DataFrame 相关性矩阵
        """
        if not returns:
            return pd.DataFrame()

        # 对齐所有日收益率到同一个索引
        df = pd.DataFrame(returns)

        # 计算 Pearson 相关系数矩阵
        corr_matrix = df.corr(method="pearson")

        logger.debug("计算 %d 个策略的相关性矩阵", len(returns))
        return corr_matrix

    def check_new_strategy(
        self,
        new_name: str,
        new_returns: pd.Series,
        existing_returns: dict[str, pd.Series],
    ) -> CorrelationReport:
        """检查新策略与现有策略的相关性

        Args:
            new_name: 新策略名称
            new_returns: 新策略日收益率序列
            existing_returns: 现有策略日收益率字典

        Returns:
            CorrelationReport 报告
        """
        report = CorrelationReport(strategy_name=new_name)

        if not existing_returns:
            report.is_diverse = True
            report.summary = "无现有策略，自动通过"
            return report

        # 计算与每个现有策略的相关性
        for name, returns in existing_returns.items():
            if name == new_name:
                continue
            # 对齐索引
            common = new_returns.index.intersection(returns.index)
            if len(common) < 10:
                logger.warning("策略 %s 与 %s 共同交易日不足 %d，无法计算相关性", new_name, name, 10)
                continue

            corr = new_returns[common].corr(returns[common])
            report.correlations[name] = corr

            if abs(corr) > report.max_correlation:
                report.max_correlation = abs(corr)
                report.max_correlation_with = name

            if abs(corr) > self._threshold:
                report.high_correlation_pairs.append(f"{new_name} <-> {name}: {corr:.2f}")

        report.is_diverse = len(report.high_correlation_pairs) == 0

        if report.is_diverse:
            logger.info("新策略 %s 低相关性 (max=%.2f)，可上线", new_name, report.max_correlation)
        else:
            logger.warning(
                "新策略 %s 高度相似于: %s",
                new_name,
                ",".join(report.high_correlation_pairs),
            )

        return report

    def find_diverse_subset(
        self,
        returns: dict[str, pd.Series],
        max_corr: float = 0.6,
    ) -> list[str]:
        """从策略池中选择低相关性子集

        Greedy: 逐个加入相关性最低的策略。

        Args:
            returns: {strategy_name: daily_return_series}
            max_corr: 可接受的最大相关性

        Returns:
            低相关性策略名称列表
        """
        matrix = self.compute_matrix(returns)
        if matrix.empty:
            return []

        selected = []
        remaining = list(matrix.columns)

        while remaining:
            if not selected:
                # 选择第一个策略（任意）
                selected.append(remaining[0])
                remaining.pop(0)
                continue

            # 选择与已选策略集合最大相关性最低的策略
            best = None
            best_max_corr = float("inf")

            for name in remaining:
                max_abs_corr = max(abs(matrix.loc[name, s]) for s in selected)
                if max_abs_corr < best_max_corr:
                    best_max_corr = max_abs_corr
                    best = name

            if best is None or best_max_corr > max_corr:
                break

            selected.append(best)
            remaining.remove(best)

        logger.info("低相关性子集: %s (max_corr=%.2f)", selected, max_corr)
        return selected

    def summary(self, returns: dict[str, pd.Series]) -> dict:
        """生成相关性分析摘要"""
        matrix = self.compute_matrix(returns)
        if matrix.empty:
            return {"error": "无有效收益数据"}

        # 平均相关性
        vals = []
        for i in range(len(matrix.columns)):
            for j in range(i + 1, len(matrix.columns)):
                vals.append(abs(matrix.iloc[i, j]))

        return {
            "n_strategies": len(returns),
            "mean_correlation": float(np.mean(vals)) if vals else 0.0,
            "max_correlation": float(np.max(vals)) if vals else 0.0,
            "min_correlation": float(np.min(vals)) if vals else 0.0,
            "high_corr_pairs": sum(1 for v in vals if v > self.HIGH_CORR_THRESHOLD),
        }

    @staticmethod
    def to_entries(
        strategy_a: str,
        strategy_b: str,
        correlation: float,
        date: str,
    ) -> dict:
        """生成数据库存储条目"""
        return {
            "strategy_a": strategy_a,
            "strategy_b": strategy_b,
            "correlation": correlation,
            "calc_date": date,
        }
