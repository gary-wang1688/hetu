"""
河图 (HeTu) 结构化日志

双 handler 架构:
  - 控制台: INFO 级别，面向开发
  - 文件: DEBUG 级别，按天轮转

SensitiveMaskFilter: 自动掩码 API Key / 密码 / Token。
"""

from __future__ import annotations

import logging.config
import re
from pathlib import Path


class SensitiveMaskFilter(logging.Filter):
    """敏感信息掩码过滤器"""

    _PATTERNS = [
        (r"sk-[a-zA-Z0-9]{16,64}", "sk-***"),
        (r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", "Bearer ***"),
        (r'password\s*=\s*["\'][^"\']+["\']', 'password="***"'),
        (r"PG_PASSWORD=[^\s]+", "PG_PASSWORD=***"),
        (r"QMT_TRADE_PASSWORD=[^\s]+", "QMT_TRADE_PASSWORD=***"),
        (r"DEEPSEEK_API_KEY=[^\s]+", "DEEPSEEK_API_KEY=***"),
        (r"JWT_SECRET=[^\s]+", "JWT_SECRET=***"),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        """对日志记录应用掩码"""
        msg = record.getMessage()
        for pattern, replacement in self._PATTERNS:
            msg = re.sub(pattern, replacement, msg)
        record.msg = msg
        record.args = ()  # 清除格式化参数，避免重新格式化暴露
        return True


def setup_logging(
    level: str = "INFO",
    log_dir: str = "logs",
    app_name: str = "hetu",
) -> None:
    """初始化日志系统"""

    log_path = Path(log_dir)
    log_path.mkdir(exist_ok=True)

    config = {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "sensitive_mask": {
                "()": "hetu.infrastructure.logging.SensitiveMaskFilter",
            },
        },
        "formatters": {
            "console": {
                "format": "[%(asctime)s] %(levelname)-5s %(name)s | %(message)s",
                "datefmt": "%H:%M:%S",
            },
            "file": {
                "format": "%(asctime)s | %(levelname)-5s | %(name)s:%(lineno)d | %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "level": level,
                "formatter": "console",
                "filters": ["sensitive_mask"],
                "stream": "ext://sys.stdout",
            },
            "file": {
                "class": "logging.handlers.TimedRotatingFileHandler",
                "level": "DEBUG",
                "formatter": "file",
                "filters": ["sensitive_mask"],
                "filename": str(log_path / f"{app_name}.log"),
                "when": "midnight",
                "interval": 1,
                "backupCount": 30,
                "encoding": "utf-8",
            },
        },
        "loggers": {
            "hetu": {
                "handlers": ["console", "file"],
                "level": "DEBUG",
                "propagate": False,
            },
            "sqlalchemy.engine": {
                "handlers": ["file"],
                "level": "WARNING",
                "propagate": False,
            },
        },
        "root": {
            "handlers": ["console", "file"],
            "level": level,
        },
    }

    logging.config.dictConfig(config)
    logger = logging.getLogger(app_name)
    logger.info("日志系统初始化完成 (level=%s, dir=%s)", level, log_dir)
