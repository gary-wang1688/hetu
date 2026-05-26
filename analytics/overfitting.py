"""
河图 (HeTu) 过拟合检测 -- 统计算法

架构 §5.4.6: 用确定性算法替代 LLM 猜测。
- DSR: Deflated Sharpe Ratio (Lopez de Prado)
- WRC: White's Reality Check
- 参数扰动: 参数敏感度分析
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

logger = logging.getLogger("hetu.overfit")


@dataclass
class OverfitReport:
    """过拟合检测报告"""
    dsr: float = 0.0
    dsr_significant: bool = False
    wrc_pvalue: float = 1.0
    wrc_significant: bool = False
    param_sensitivity: float = 0.0
    param_sensitive: bool = True
    oos_is_ratio: float = 0.0
    is_overfit: bool = True
    warnings: list[str] = field(default_factory=list)


class OverfittingDetector:
    """
    过拟合检测器

    用法:
        detector = OverfittingDetector()
        report = detector.detect(
            returns=pd.Series(...),          # 策略日收益序列
            benchmark_returns=pd.Series(...), # 基准日收益序列
            n_trials=100,                     # 蒙特卡洛次数
        )
    """

    def __init__(
        self,
        dsr_threshold: float = 0.95,
        wrc_threshold: float = 0.05,
        param_sensitivity_threshold: float = 0.3,
        oos_is_drop_threshold: float = 0.3,
    ):
        self.dsr_threshold = dsr_threshold
        self.wrc_threshold = wrc_threshold
        self.param_sensitivity_threshold = param_sensitivity_threshold
        self.oos_is_drop_threshold = oos_is_drop_threshold

    def compute_dsr(
        self,
        returns: pd.Series,
        benchmark: pd.Series | None = None,
        n_trials: int = 100,
    ) -> float:
        """计算 Deflated Sharpe Ratio

        DSR = Prob(真实 Sharpe > 最大伪 Sharpe Ratio)

        Args:
            returns: 策略日收益率
            benchmark: 基准日收益率（可选，用于超额收益）
            n_trials: 蒙特卡洛试验次数

        Returns:
            DSR 概率值 [0, 1]
        """
        if len(returns) < 20:
            return 0.0

        # 计算策略年化 Sharpe
        if benchmark is not None:
            excess = returns - benchmark
        else:
            excess = returns
        observed_sr = self._annualized_sharpe(excess)

        # 蒙特卡洛模拟：生成随机策略的最大 Sharpe 分布
        n = len(excess)
        random_sharpes = []

        for _ in range(n_trials):
            # 生成随机收益（零 mean 假设）
            random_returns = np.random.normal(0, np.std(excess), n)
            random_returns = pd.Series(random_returns, index=excess.index)
            random_sharpes.append(self._annualized_sharpe(random_returns))

        # DSR: observed SR 在随机分布中的分位数
        random_sharpes = np.array(random_sharpes)
        dsr = np.mean(observed_sr > random_sharpes)

        logger.debug("DSR=%.4f (observed_SR=%.4f)", dsr, observed_sr)
        return dsr

    def compute_wrc(
        self,
        returns: pd.Series,
        benchmark_returns: pd.Series,
    ) -> float:
        """White's Reality Check 检验

        H0: 策略不优于基准
        Bootstrap 方法计算 p-value

        Args:
            returns: 策略日收益率
            benchmark_returns: 基准日收益率

        Returns:
            p-value
        """
        if len(returns) < 20:
            return 1.0

        # 计算策略相对于基准的表现差异
        diff = returns - benchmark_returns
        mean_diff = diff.mean()

        # Bootstrap 计算 H0 分布
        n_bootstrap = 1000
        boot_means = []
        n = len(diff)

        for _ in range(n_bootstrap):
            boot_sample = np.random.choice(diff.values, size=n, replace=True)
            boot_means.append(np.mean(boot_sample))

        boot_means = np.array(boot_means)
        # 单边检验: 策略显著优于基准
        pvalue = np.mean(boot_means <= 0)  # bootstrap 下概率

        logger.debug("WRC p-value=%.4f", pvalue)
        return pvalue

    def compute_param_sensitivity(
        self,
        returns_by_param: dict[str, pd.Series],
        base_param: str,
    ) -> float:
        """计算参数敏感度

        Args:
            returns_by_param: {param_value: return_series} 不同参数下的收益
            base_param: 基准参数

        Returns:
            敏感度分数 [0, 1]，越高越敏感（越可能过拟合）
        """
        if base_param not in returns_by_param or len(returns_by_param) < 2:
            return 0.0

        base_returns = returns_by_param[base_param]
        base_sharpe = self._annualized_sharpe(base_returns)

        sharpes = {}
        for param, rets in returns_by_param.items():
            sharpes[param] = self._annualized_sharpe(rets)

        # 参数敏感度 = Sharpe 的变异系数
        sharpe_values = list(sharpes.values())
        mean_sr = np.mean(sharpe_values)
        std_sr = np.std(sharpe_values)

        if mean_sr > 0:
            sensitivity = std_sr / abs(mean_sr)
        else:
            sensitivity = 1.0

        sensitivity = min(sensitivity, 1.0)

        logger.debug(
            "参数敏感度=%.4f (base_SR=%.4f, mean_SR=%.4f, std_SR=%.4f)",
            sensitivity, base_sharpe, mean_sr, std_sr,
        )
        return sensitivity

    def detect(
        self,
        returns: pd.Series,
        benchmark_returns: pd.Series | None = None,
        n_trials: int = 100,
        oos_returns: pd.Series | None = None,
        param_returns: dict[str, pd.Series] | None = None,
        base_param: str = "default",
    ) -> OverfitReport:
        """综合过拟合检测

        Args:
            returns: 策略日收益率（通常是样本内）
            benchmark_returns: 基准日收益率
            n_trials: 蒙特卡洛试验次数
            oos_returns: 样本外收益率（用于计算 IS/OOS 衰减比）
            param_returns: 不同参数下的收益率
            base_param: 基准参数名

        Returns:
            OverfitReport 综合检测报告
        """
        report = OverfitReport()
        warnings = []

        # 1. DSR 检验
        report.dsr = self.compute_dsr(returns, benchmark_returns, n_trials)
        report.dsr_significant = report.dsr > self.dsr_threshold
        if not report.dsr_significant:
            warnings.append(f"DSR={report.dsr:.3f} 不显著，可接受")

        # 2. WRC 检验
        if benchmark_returns is not None:
            report.wrc_pvalue = self.compute_wrc(returns, benchmark_returns)
            report.wrc_significant = report.wrc_pvalue < self.wrc_threshold
            if report.wrc_significant:
                warnings.append(f"WRC p-value={report.wrc_pvalue:.3f} 显著，策略有收益")
            else:
                warnings.append(f"WRC p-value={report.wrc_pvalue:.3f} 不显著")

        # 3. IS/OOS 衰减比
        if oos_returns is not None and len(oos_returns) > 5:
            is_sharpe = self._annualized_sharpe(returns)
            oos_sharpe = self._annualized_sharpe(oos_returns)
            if is_sharpe > 0:
                report.oos_is_ratio = oos_sharpe / is_sharpe
            else:
                report.oos_is_ratio = 0.0

            if report.oos_is_ratio < self.oos_is_drop_threshold:
                warnings.append(f"OOS/IS={report.oos_is_ratio:.2f} 严重衰减，疑似过拟合")

        # 4. 参数敏感度
        if param_returns is not None:
            report.param_sensitivity = self.compute_param_sensitivity(param_returns, base_param)
            report.param_sensitive = report.param_sensitivity > self.param_sensitivity_threshold
            if report.param_sensitive:
                warnings.append(f"参数敏感度={report.param_sensitivity:.3f} 过高，疑似过拟合")

        # 综合判断
        report.is_overfit = (
            (not report.dsr_significant)
            or report.param_sensitive
            or (oos_returns is not None and report.oos_is_ratio < self.oos_is_drop_threshold)
        )

        report.warnings = warnings

        if report.is_overfit:
            logger.warning("策略检测到过拟合风险: %s", "; ".join(warnings))
        else:
            logger.info("策略通过过拟合检测")

        return report

    @staticmethod
    def _annualized_sharpe(returns: pd.Series, rf: float = 0.02) -> float:
        """年化 Sharpe Ratio 计算

        Args:
            returns: 日收益率序列
            rf: 无风险利率（年化）

        Returns:
            年化 Sharpe Ratio
        """
        if len(returns) < 5:
            return 0.0
        excess = returns - rf / 252  # 日化无风险利率
        if excess.std() == 0:
            return 0.0
        return float(excess.mean() / excess.std() * np.sqrt(252))
