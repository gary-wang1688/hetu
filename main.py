#!/usr/bin/env python3
"""
河图 (HeTu) CLI 入口

Agent 可调用入口: python main.py --strategy turtle

用法:
    # 完整流水线（仅查看，不下单）
    python main.py

    # 指定策略 + 允许下单
    python main.py --strategy limit_up --live

    # 只跑盘前选股+信号
    python main.py --stage pre_open

所有 API Key 从 .env 文件和系统环境变量读取。
"""

from dotenv import load_dotenv
load_dotenv()

    # 盘后复盘 + 自动发帖
    python main.py --stage post_close --auto-post

    # 查看所有可用策略
    python main.py --list-strategies
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中（允许从任意位置执行）
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# noqa: E402 — sys.path 必须在 imports 之前修改
from core.config import Config  # noqa: E402
from execution.daily_orchestrator import DailyPipeline  # noqa: E402


def setup_logging(level: str = "INFO") -> None:
    """配置日志输出。"""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


async def run(args: argparse.Namespace) -> int:
    """执行流水线，返回退出码。"""
    setup_logging(args.log_level)
    logger = logging.getLogger("hetu.main")

    # 1. 加载配置
    config_path = args.config or "config/default.yaml"
    if not Path(config_path).exists():
        logger.error("配置文件不存在: %s", config_path)
        logger.error("请复制 config/default.yaml.example 并填入 API Key")
        return 1

    cfg = Config(config_path)
    logger.info("配置加载: %s", config_path)

    # 2. 检查 API Key
    api_key = cfg.get("brokers.mx.api_key", "")
    if not api_key:
        logger.error("MX_APIKEY 未配置，无法对接妙想模拟盘")
        logger.error("请在 config/default.yaml brokers.mx.api_key 中填入")
        return 1

    # 3. 列出策略
    if args.list_strategies:
        pipeline = DailyPipeline.from_config(cfg)
        strategies = pipeline.list_strategies()
        print(f"可用策略 ({len(strategies)} 个):")
        for s in strategies:
            cond = cfg.get(f"strategy.{s}.screen_condition", "(无选股条件)")
            print(f"  {s:18s}  {cond}")
        return 0

    # 4. 单阶段执行
    if args.stage:
        pipeline = DailyPipeline.from_config(cfg)
        stage_map = {
            "pre_open": pipeline.run_pre_open,
            "open": lambda: None,  # OPEN 需要信号输入，单独阶段不适用
            "intraday": pipeline.run_intraday,
            "post_close": pipeline.run_post_close,
        }
        handler = stage_map.get(args.stage)
        if handler is None:
            logger.error("无效阶段: %s，可选: %s", args.stage, list(stage_map))
            return 1

        logger.info("单阶段执行: %s", args.stage)
        if args.stage == "pre_open":
            result = await handler(strategy=args.strategy)
        elif args.stage == "post_close":
            result = await handler(auto_post=args.auto_post)
        else:
            result = await handler()
        _print_stage_result(result)
        return 0

    # 6. 盘中监控模式
    if args.mode == "monitor":
        from core.scheduler import SystemScheduler
        scheduler = SystemScheduler.from_config(cfg)
        pipeline = DailyPipeline.from_config(cfg)

        logger.info("=== 盘中监控启动 (strategy=%s) ===", args.strategy or "全部")
        await pipeline._run_monitor_loop(scheduler, strategy=args.strategy or "")

        # 收盘后自动复盘
        post = await pipeline.run_post_close(auto_post=args.auto_post)
        _print_stage_result(post)
        return 0

    # 5. 完整流水线
    pipeline = DailyPipeline.from_config(cfg)
    logger.info("启动完整流水线: strategy=%s live=%s auto_post=%s",
                 args.strategy or "全部", args.live, args.auto_post)

    report = await pipeline.run_full(
        strategy=args.strategy or "",
        auto_post=args.auto_post,
        live=args.live,
    )

    # 输出报告
    _print_report(report)

    if not args.live:
        print("\n⚠  dry-run 模式，未实际提交订单。加 --live 启用真实下单。")

    return 0


# ===================================================================
# 输出格式化
# ===================================================================

def _print_report(report) -> None:
    """格式化输出交易日报告。"""
    print("=" * 60)
    print(f"  河图日报 — {report.date}")
    print("=" * 60)
    print(f"  账户:     {report.account_name}")
    print(f"  总资产:   ¥{report.total_assets:,.2f}")
    print(f"  净值:     {report.nav:.4f}")
    print(f"  日盈亏:   {report.daily_pnl:+,.2f}")
    print(f"  累计盈亏: {report.total_pnl:+,.2f}")
    print(f"  信号:     生成 {report.signals_generated}  通过 {report.signals_approved}")
    print(f"  订单:     提交 {report.orders_submitted}  成交 {report.orders_filled}")
    print(f"  止损事件: {report.stop_events}")

    if report.stages:
        print()
        for s in report.stages:
            icon = "✅" if s.success else "❌"
            print(f"  {icon} {s.stage:12s}  {s.elapsed_ms:6.0f}ms")

    if report.summary:
        print()
        print(report.summary)

    if report.warnings:
        print("\n⚠ 警告:")
        for w in report.warnings:
            print(f"  - {w}")


def _print_stage_result(stage) -> None:
    """输出单阶段结果。"""
    if stage is None:
        print("该阶段不支持独立执行")
        return
    icon = "✅" if stage.success else "❌"
    print(f"{icon} {stage.stage}: {stage.elapsed_ms:.0f}ms")
    if stage.details:
        for k, v in stage.details.items():
            if isinstance(v, dict):
                print(f"  {k}: {v}")
            elif isinstance(v, list) and len(v) <= 10:
                print(f"  {k}: {v}")
            else:
                print(f"  {k}: {v}")
    if stage.errors:
        for e in stage.errors:
            print(f"  ❌ {e}")


# ===================================================================
# CLI
# ===================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="河图 (HeTu) A 股量化交易系统 — 妙想模拟盘版",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py                              # 完整流水线 dry-run
  python main.py --strategy turtle             # 单策略运行
  python main.py --strategy limit_up --live    # 打板策略 + 真实下单
  python main.py --stage pre_open              # 只看盘前选股+信号
  python main.py --stage post_close            # 只看盘后复盘
  python main.py --list-strategies             # 列出所有策略
""",
    )
    parser.add_argument(
        "--config", default="config/default.yaml",
        help="配置文件路径 (默认: config/default.yaml)"
    )
    parser.add_argument(
        "--strategy", default="",
        help="指定策略名 (如 turtle/limit_up/dividend)，空=全部"
    )
    parser.add_argument(
        "--stage", default="",
        choices=["pre_open", "open", "intraday", "post_close"],
        help="只执行指定阶段 (默认: 全链路)"
    )
    parser.add_argument(
        "--mode", default="",
        choices=["monitor"],
        help="盘中监控模式 (按策略 signal_windows 触发)"
    )
    parser.add_argument(
        "--live", action="store_true",
        help="启用真实下单（默认 dry-run，不提交订单）"
    )
    parser.add_argument(
        "--auto-post", action="store_true",
        help="盘后自动发帖到妙想社区"
    )
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别 (默认: INFO)"
    )
    parser.add_argument(
        "--list-strategies", action="store_true",
        help="列出所有可用策略"
    )

    args = parser.parse_args()

    try:
        exit_code = asyncio.run(run(args))
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n中断")
        sys.exit(130)
    except Exception as e:
        logging.getLogger("hetu.main").exception("执行异常: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
