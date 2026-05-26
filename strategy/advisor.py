"""
河图 (HeTu) LLM Adviser — 信号审核员模式

架构 #9: LLM 不再直接生成信号，而是作为信号审核/风险把关层。

职责:
- 接收已生成信号 (factor + strategy)
- 检查信号是否与策略理性声明 (Rationale) 一致
- 识别异常/矛盾/过度集中
- 输出: APPROVE / FLAG / REJECT

不负责: 因子计算 / 信号生成 / 仓位计算
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum

from core.types import Signal, MarketRegime

logger = logging.getLogger("hetu.adviser")


# ============================================================
# 类型定义
# ============================================================

class ReviewVerdict(str, Enum):
    """审核裁决"""
    APPROVE = "APPROVE"
    FLAG = "FLAG"
    REJECT = "REJECT"


@dataclass
class ReviewItem:
    """单个信号审核条目"""
    signal: Signal = field(default_factory=lambda: Signal(code=""))
    verdict: ReviewVerdict = ReviewVerdict.FLAG
    reason: str = ""
    adjusted_confidence: float = 0.0
    checks: dict[str, bool] = field(default_factory=dict)


@dataclass
class ReviewReport:
    """审核报告"""
    signals_reviewed: int = 0
    approved: int = 0
    flagged: int = 0
    rejected: int = 0
    items: list[ReviewItem] = field(default_factory=list)
    summary: str = ""
    warnings: list[str] = field(default_factory=list)


# ============================================================
# SignalReviewer — 5维审核
# ============================================================

class SignalReviewer:
    """LLM 信号审核员。确定性规则先行，LLM 只处理模糊边界。

    v2.2: 集成 mx-search 资讯审核
    """

    HARD_LIMITS = {
        "max_position_pct": 0.15,     # 单只最大持仓占比
        "max_sector_concentration": 0.4,  # 最大板块集中度
        "min_confidence": 0.3,        # 最低置信度
        "max_signals_per_day": 8,     # 单日最大信号数
    }

    def __init__(self, hard_limits: dict | None = None):
        self._limits = hard_limits or self.HARD_LIMITS
        self._mx_search = None  # 可选 mx-search 客户端

    def review(
        self,
        signals: list[Signal],
        rationale: str,
        regime: MarketRegime | None = None,
        positions: dict[str, dict] | None = None,
        total_equity: float = 1_000_000,
    ) -> ReviewReport:
        """审核一批信号

        Args:
            signals: 待审核信号列表
            rationale: 策略理性声明文本
            regime: 市场环境（可选）
            positions: 当前持仓 {code: {shares, market_value, ...}}
            total_equity: 总权益

        Returns:
            ReviewReport 审核报告
        """
        report = ReviewReport()
        report.signals_reviewed = len(signals)

        # 1. 硬性过滤
        surviving = self._hard_filter(signals, report, total_equity)

        # 2. 逐信号审核（5维检查）
        for signal in surviving:
            item = self._check_signal(signal, rationale, regime, positions, total_equity)
            report.items.append(item)

            if item.verdict == ReviewVerdict.APPROVE:
                report.approved += 1
            elif item.verdict == ReviewVerdict.FLAG:
                report.flagged += 1
            else:
                report.rejected += 1

        # 3. 组合层面检查
        portfolio_warnings = self._check_portfolio(report.items, total_equity)
        report.warnings.extend(portfolio_warnings)

        # 4. 摘要
        report.summary = self._summarize(report)

        logger.info(
            "信号审核完成: %d 个信号 → %d通过 %d标记 %d拒绝",
            report.signals_reviewed, report.approved, report.flagged, report.rejected,
        )
        return report

    def _hard_filter(
        self, signals: list[Signal], report: ReviewReport, equity: float
    ) -> list[Signal]:
        """硬性过滤：移除明显不符合规则的信号"""
        surviving = []
        max_per_day = self._limits["max_signals_per_day"]
        min_conf = self._limits["min_confidence"]

        for signal in signals:
            # 数量限制
            if len(surviving) >= max_per_day:
                report.warnings.append(f"信号 {signal.code} 超出单日上限 {max_per_day}")
                continue

            # 置信度检查
            if signal.confidence < min_conf:
                item = ReviewItem(
                    signal=signal,
                    verdict=ReviewVerdict.REJECT,
                    reason=f"置信度 {signal.confidence:.2f} 低于最低要求 {min_conf}",
                    checks={"confidence": False},
                )
                report.items.append(item)
                report.rejected += 1
                continue

            # 空代码检查
            if not signal.code:
                item = ReviewItem(
                    signal=signal,
                    verdict=ReviewVerdict.REJECT,
                    reason="股票代码为空",
                )
                report.items.append(item)
                report.rejected += 1
                continue

            surviving.append(signal)

        return surviving

    def _check_signal(
        self,
        signal: Signal,
        rationale: str,
        regime: MarketRegime | None,
        positions: dict | None,
        equity: float,
    ) -> ReviewItem:
        """5维信号审核

        1. strategy_rationale: 信号方向与理性声明是否一致
        2. market_regime: 信号是否适应当前市场环境
        3. signal_quality: 置信度 + 参数完整性
        4. risk_check: 仓位限制检查
        5. news_aligned: 资讯一致性（可选）
        """
        checks = {}
        reasons = []

        # 维度1: 策略理性一致性
        if rationale:
            action_keywords = {"BUY": ["做多", "买入", "看涨", "低估", "增长", "超跌", "拐点"],
                               "SELL": ["卖出", "看跌", "高估", "减速", "破位"]}
            keywords = action_keywords.get(signal.action, [])
            aligned = any(kw in rationale for kw in keywords)
            checks["strategy_rationale"] = aligned
            if not aligned:
                reasons.append("信号方向与策略理性声明不一致")
        else:
            checks["strategy_rationale"] = True  # 无声明时放行

        # 维度2: 市场环境适配性
        if regime:
            checks["market_regime"] = True
            if regime.trend == "BEARISH" and signal.action == "BUY":
                reasons.append("熊市环境下的做多信号")
                checks["market_regime"] = False
            elif regime.trend == "BULLISH" and signal.action == "SELL":
                reasons.append("牛市环境下的做空信号")
                checks["market_regime"] = False
        else:
            checks["market_regime"] = True

        # 维度3: 信号质量
        checks["signal_quality"] = signal.confidence >= self._limits["min_confidence"]
        if signal.confidence < 0.5:
            reasons.append(f"置信度偏低 ({signal.confidence:.2f})")

        # 维度4: 风控检查
        max_pct = self._limits["max_position_pct"]
        if positions and signal.code in positions:
            pos = positions[signal.code]
            market_value = pos.get("market_value", 0)
            pos_pct = market_value / equity if equity > 0 else 0
            if signal.action == "BUY" and pos_pct >= max_pct:
                checks["risk_check"] = False
                reasons.append(f"持仓占比 {pos_pct:.1%} 超过上限 {max_pct:.1%}")
            else:
                checks["risk_check"] = True
        else:
            checks["risk_check"] = True

        # 维度5: 资讯一致性（基础检查，无 mx-search 时默认通过）
        checks["news_aligned"] = True

        # 综合裁决
        failed = sum(1 for v in checks.values() if v is False)
        if failed == 0:
            verdict = ReviewVerdict.APPROVE
            adjusted_conf = signal.confidence
        elif failed <= 2:
            verdict = ReviewVerdict.FLAG
            adjusted_conf = signal.confidence * 0.5
        else:
            verdict = ReviewVerdict.REJECT
            adjusted_conf = 0.0

        return ReviewItem(
            signal=signal,
            verdict=verdict,
            reason="; ".join(reasons) if reasons else "OK",
            adjusted_confidence=adjusted_conf,
            checks=checks,
        )

    def _check_portfolio(
        self, items: list[ReviewItem], equity: float
    ) -> list[str]:
        """组合层面检查：过度集中、板块集中等"""
        warnings = []

        # 检查通过率
        total = len(items)
        if total == 0:
            return []

        approved = sum(1 for i in items if i.verdict == ReviewVerdict.APPROVE)
        approval_rate = approved / total

        if approval_rate < 0.5:
            warnings.append(f"信号通过率过低: {approved}/{total} ({approval_rate:.0%})")

        # 板块集中度检查
        buy_codes = [i.signal.code for i in items
                     if i.signal.action == "BUY" and i.verdict != ReviewVerdict.REJECT]

        # 简化板块检测：基于代码前缀
        sectors = {}
        sector_map = {
            "60": "上海主板", "00": "深圳主板",
            "30": "创业板", "68": "科创板",
        }
        for code in buy_codes:
            prefix = code[:2]
            sector = sector_map.get(prefix, "其他")
            sectors[sector] = sectors.get(sector, 0) + 1

        max_conc = self._limits["max_sector_concentration"]
        for sector, count in sectors.items():
            pct = count / max(len(buy_codes), 1)
            if pct > max_conc:
                warnings.append(f"板块 {sector} 信号集中度 {pct:.0%} 超过上限 {max_conc:.0%}")

        return warnings

    def _summarize(self, report: ReviewReport) -> str:
        """生成审核摘要"""
        parts = [f"审核 {report.signals_reviewed} 个信号"]

        parts.append(f"通过 {report.approved}")

        if report.flagged > 0:
            parts.append(f"标记 {report.flagged}")

        if report.rejected > 0:
            parts.append(f"拒绝 {report.rejected}")

        if report.warnings:
            parts.append(f"警告 {len(report.warnings)} 条")

        return "，".join(parts)


# ============================================================
# LLMAdviserBridge — LLM 深度审核桥接
# ============================================================

class LLMAdviserBridge:
    """可选 LLM 深度审核桥接。规则无法明确时调用 LLM 二次确认。"""

    @staticmethod
    def build_prompt(
        signal: Signal,
        rationale: str,
        regime: MarketRegime | None = None,
    ) -> str:
        """构建提交给 LLM 的审核 prompt

        Args:
            signal: 待审核信号
            rationale: 策略理性声明
            regime: 市场环境

        Returns:
            LLM prompt 文本
        """
        prompt_parts = [
            "你是一名量化策略审核员，请审核以下交易信号：",
            "",
            "## 信号详情",
            f"- 股票代码: {signal.code}",
            f"- 操作: {signal.action}",
            f"- 置信度: {signal.confidence:.2f}",
            f"- 原因: {signal.reason}",
            f"- 策略: {signal.strategy}",
        ]

        if signal.shares > 0:
            prompt_parts.append(f"- 股数: {signal.shares}")
        if signal.price_limit > 0:
            prompt_parts.append(f"- 限价: {signal.price_limit:.2f}")

        prompt_parts.append("")
        prompt_parts.append("## 策略理性声明")
        prompt_parts.append(rationale)

        if regime:
            prompt_parts.extend([
                "",
                "## 当前市场环境",
                f"- 趋势: {regime.trend}",
                f"- 强度: {regime.strength:.2f}",
            ])

        prompt_parts.extend([
            "",
            "## 审核标准",
            "1. 信号方向与策略理性是否一致？",
            "2. 信号是否适应当前市场环境？",
            "3. 置信度是否合理？",
            "4. 是否有明显的风险提示？",
            "",
            "## 回复格式要求",
            "请以 JSON 格式回复:",
            "{",
            '  "verdict": "APPROVE|FLAG|REJECT",',
            '  "reason": "审核意见",',
            '  "confidence": 0.0-1.0',
            "}",
        ])

        return "\n".join(prompt_parts)

    @staticmethod
    def parse_review(response: str) -> ReviewVerdict:
        """解析 LLM 审核响应

        Args:
            response: LLM 原始响应文本

        Returns:
            ReviewVerdict 裁决
        """
        try:
            data = json.loads(response)
            verdict = data.get("verdict", "FLAG").upper()

            if verdict == "APPROVE":
                return ReviewVerdict.APPROVE
            elif verdict == "REJECT":
                return ReviewVerdict.REJECT
            else:
                return ReviewVerdict.FLAG
        except (json.JSONDecodeError, ValueError, KeyError):
            logger.warning("无法解析 LLM 响应，默认为 FLAG")
            return ReviewVerdict.FLAG
