"""自定义异常。"""


class HetuError(Exception):
    """河图基础异常"""


class ConfigError(HetuError):
    """配置错误"""


class DataError(HetuError):
    """数据源错误"""


class BrokerError(HetuError):
    """交易通道错误"""


class RiskError(HetuError):
    """风控拒绝"""


class ExecutionError(HetuError):
    """执行错误"""


class DataSourceError(DataError):
    """数据源错误（含 source 字段）"""

    def __init__(self, message: str, source: str = "") -> None:
        super().__init__(message)
        self.source = source


class RateLimitError(DataError):
    """API 限流错误"""

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


class SchemaValidationError(DataError):
    """Schema 校验错误"""

    def __init__(self, message: str, field: str = "") -> None:
        super().__init__(message)
        self.field = field
