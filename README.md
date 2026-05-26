# 河图 (HeTu) v2.3

> 别人在 K 线里看到涨跌，你在数字里看到秩序。

---

## 项目定位

个人 A 股量化交易系统，**模拟交易验证阶段**。全栈可插拔，风险优先。

| 维度 | 现状 |
|---|---|
| Python 文件 | 107 |
| 代码行数 | ~12,000 |
| 测试 | 121 passed / 0 ruff errors |
| 交易通道 | MX 妙想模拟盘 (生产) / QMT (预留) |
| LLM | DeepSeek V4 (OpenAI 兼容) |
| 推送 | 企业微信群机器人 |

---

## 目录结构

```
hetu/
├── main.py                    CLI 入口
├── README.md                  本文档
├── .gitignore
├── conftest.py                pytest 路径注入
│
├── config/
│   └── default.yaml           唯一配置源 (git-ignored, 含全部密钥/阈值/参数)
│
├── core/                10 files  1,003 lines    抽象接口与类型系统
│   ├── types.py                  260 lines  Signal/Order/Position/Bar 等 Pydantic 模型
│   ├── config.py                  42 lines  YAML 配置加载器 cfg.get("a.b.c")
│   ├── scheduler.py             240 lines  SystemScheduler + 信号节点自注册
│   ├── calendar.py               36 lines  A 股交易日历 (2024-2026)
│   ├── interfaces.py             46 lines  Broker / DataProvider ABC 抽象接口
│   ├── event_bus.py             292 lines  事件总线 pub/sub + ACK/重试
│   ├── events.py                 25 lines  Event / EventDelivery 定义
│   ├── errors.py                 49 lines  自定义异常 (ConfigError, BrokerError 等)
│   └── utils.py                  13 lines  normalize_code 代码标准化
│
├── data/                12 files  1,996 lines    数据源与数据管道
│   ├── base.py                      DataProvider 基类 + 重试/限流
│   ├── pipeline.py                 ETL 9 阶段流水线
│   ├── filters.py                  ST/流动性/T+1 过滤器
│   ├── guard.py                    DataGuard 数据质量守卫 (停牌/除权)
│   ├── schema.py                   列名映射 + OHLC 校验
│   └── sources/
│       ├── mx_data.py              MX Data API (自然语言→行情)
│       ├── mx_screening.py         妙想选股器 (自然语言→候选池)
│       ├── mx_search.py            资讯搜索 + 信号上下文检测
│       ├── sector_provider.py      行业分类
│       └── tdx.py              72   mootdx 通达信 TCP 实时行情 (10只/24ms)
│
├── execution/           13 files  2,144 lines    信号→订单→执行链
│   ├── daily_orchestrator.py 242  日频编排器 (主力: PRE_OPEN→OPEN→POST_CLOSE)
│   ├── daily_pipeline.py    378  日频流水线 (4 阶段)
│   ├── pipeline.py          167  信号执行管道 (风控→下单→止损挂载)
│   ├── stop_monitor.py      347  止损监控器 (价格监控→触发信号→回调)
│   ├── order_manager.py     262  订单 CRUD + 状态机 + 持久化
│   ├── limit_guard.py       179  涨跌停应急守卫 (±10%/±20%/±30%)
│   ├── report_types.py       47  StageReport 类型定义
│   ├── order.py              89  订单类型导出
│   └── channels/
│       ├── mx_broker.py     350  MX 妙想模拟盘 (Header apikey, 代码转换)
│       ├── paper_broker.py        纸上模拟
│       └── qmt_broker.py           QMT 通道 (monitor_type="push" 预留)
│
├── strategy/            24 files    735 lines    10 个策略 + 基座
│   ├── base.py                    策略基类 (on_bar / from_config)
│   ├── registry.py               REGISTRY 注册中心 (10 策略)
│   ├── runner.py                  策略执行器
│   ├── advisor.py                 LLM 信号审核 (5 维审核)
│   ├── screening.py               选股筛选器
│   ├── industry.py                行业分类器
│   ├── prerequisite.py            前置条件检查
│   ├── registry.py, runner.py, ...
│   ├── market/regime.py           市场状态 (牛/熊/震荡)
│   ├── factor/evaluator.py        因子评估器
│   └── single/
│       ├── bollinger.py       34  布林带反转 (20/2σ + MA200 过滤)
│       ├── turtle.py          59  海龟突破 (20/10 Donchian + 确认延迟)
│       ├── ma_cross.py        24  均线金叉 (20/60 低换手)
│       ├── momentum.py        21  截面动量 (60日 前20只 + 行业中性)
│       ├── volume_price.py    26  量价共振 (放量 >1.5x + 日成交>5000万)
│       ├── limit_up.py        22  打板 (pullback -5%~-2% + 止损 -4%)
│       ├── dividend.py        16  红利低波 (股息>3% + 低波动)
│       ├── cb_dual_low.py     10  可转债双低 (价格<120 + 溢价<20%)
│       ├── pead.py            10  业绩超预期 (surprise>20% + hold 5日)
│       └── sector_rotation.py 10  行业轮动 (动量权重 40%)
│
├── risk/                 6 files  1,275 lines    风控体系
│   ├── manager.py           333  RiskManager (6 项检查 + 冻结仓位)
│   ├── stop_loss.py         272  StopLossEngine (硬止损/移动止损/时间止损)
│   ├── circuit_breaker.py   243  三级熔断器 (日亏 2%/5%/benchmark)
│   ├── hedge.py             257  空头对冲 (MA200 + ATR 波动率)
│   ├── position_sizer.py    170  仓位计算器 (凯利公式 + 策略差异化上限)
│   └── __init__.py
│
├── notify/               4 files    346 lines    消息推送 + LLM
│   ├── llm_client.py        OpenA 兼容客户端 (ask/ask_json/重试)
│   ├── push.py              MessagePusher (企微 Webhook + 控制台降级)
│   └── throttle.py          推送频率控制
│
├── backtest/             5 files    877 lines    回测引擎
│   ├── engine.py            BacktestEngine (事件驱动 + T+1)
│   ├── metrics.py           PerformanceMetrics (夏普/回撤/Alpha/Beta)
│   ├── slippage.py          SlippageModel (固定 tick/成交量)
│   └── volume_check.py      VolumeChecker (涨停/跌停检测)
│
├── infrastructure/       5 files    896 lines    基础设施
│   ├── db.py                Database (async SQLAlchemy 2.0)
│   ├── models.py            20 张 ORM 表
│   ├── health.py            全系统健康检查
│   └── logging.py           结构化日志 + SensitiveMaskFilter
│
├── analytics/            3 files    481 lines    分析工具
│   ├── correlation.py       CorrelationAnalyzer (Pearson 矩阵)
│   └── overfitting.py       OverfittingDetector (DSR + WRC)
│
├── portfolio/            3 files    423 lines    持仓管理
│   ├── sync.py              PositionSynchronizer (跨通道对账)
│   └── tracker.py           PortfolioTracker (快照持久化)
│
├── scripts/
│   └── portfolio_snapshot.py   盘中持仓快报 (MX+TDX+LLM+企微)
│
└── tests/               14 files  1,816 lines    121 测试用例
    ├── test_tdx.py              TDX 连通性
    └── unit/
        ├── test_mx_broker.py    33 tests (代码转换/价格/金额/订单解析)
        ├── test_event_bus.py    13 tests (发布/订阅/ACK/重试/回放)
        ├── test_pipeline.py     15 tests (风控拒绝/完整买入/止损集成)
        ├── test_stop_loss.py    15 tests (硬止损/移动/时间/T+1)
        ├── test_stop_monitor.py 10 tests (挂载/补偿/回调)
        ├── test_risk_manager.py 10 tests (六项检查/熔断)
        ├── test_order_manager.py 8 tests (CRUD/状态机/撤单)
        └── test_screening.py    16 tests (选股/代码格式/Markdown 解析)
```

---

## 进度 (v2.3)

| 模块 | 状态 | 说明 |
|---|---|---|
| 数据源 (MX+akshare+TDX) | ✅ | 三层 fallback，TDX 实时价 24ms |
| 10 策略引擎 | ✅ | 全部实现 BaseStrategy.on_bar() |
| 风控审核 | ✅ | 6 项检查 + 熔断 + 对冲 |
| MX 模拟盘 | ✅ | 下单/撤单/持仓/余额 (Header apikey) |
| 盘中监控 | ✅ | 6 时间节点 × 策略窗口 |
| LLM 分析 | ✅ | DeepSeek V4, OpenAI 兼容接口 |
| 企微推送 | ✅ | Webhook + push/push_markdown/push_report |
| 日频流水线 | ✅ | PRE_OPEN → OPEN → INTRADAY → POST_CLOSE |
| 持仓快报脚本 | ✅ | MX 查询 → TDX 实时价 → LLM 总结 → 企微推送 |
| 测试 | ✅ | 121 passed / 0 ruff errors |
|---|---|---|
| QMT 实盘 | 🟡 | 接口预留 monitor_type="push" |
| 组合策略 | ❌ | combo_weights 配置未消费 |
| Web UI | ❌ | 未开始 |

---

## 快速开始

```bash
# 日频流水线
python main.py --strategy turtle

# 实盘下单
python main.py --strategy limit_up --live

# 盘中持续监控
python main.py --mode monitor --strategy limit_up

# 持仓快报 (MX + DeepSeek + 企微)
python scripts/portfolio_snapshot.py

# 测试
pytest tests/ -q    # 121 cases
```
