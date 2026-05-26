"""
河图 (HeTu) 数据库连接池

使用 asyncpg + SQLAlchemy 2.0 async 引擎。
连接池配置: 2~10 连接，个人系统足够。
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

logger = logging.getLogger("hetu.db")


class Database:
    """PostgreSQL 连接管理器"""

    def __init__(self, dsn: str, min_conn: int = 2, max_conn: int = 10):
        self.dsn = dsn
        self._min_conn = min_conn
        self._max_conn = max_conn
        self._engine: AsyncEngine | None = None
        self._sessionmaker: async_sessionmaker | None = None

    async def connect(self) -> None:
        """建立数据库连接并初始化 session factory"""
        logger.info("正在连接数据库: %s", self.dsn)
        self._engine = create_async_engine(
            self.dsn,
            pool_size=self._min_conn,
            max_overflow=self._max_conn - self._min_conn,
            pool_pre_ping=True,
            echo=False,
        )
        self._sessionmaker = async_sessionmaker(
            self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        # 验证连接
        async with self._engine.begin() as conn:
            await conn.execute(SQL("SELECT 1"))
        logger.info("数据库连接成功")

    async def disconnect(self) -> None:
        """断开数据库连接"""
        if self._engine:
            logger.info("正在断开数据库连接")
            await self._engine.dispose()
            self._engine = None
            self._sessionmaker = None
            logger.info("数据库连接已断开")

    def session(self) -> AsyncSession:
        """获取一个新的数据库 session"""
        if not self._sessionmaker:
            raise RuntimeError("数据库未连接，请先调用 connect()")
        return self._sessionmaker()

    @property
    def engine(self) -> AsyncEngine:
        """获取 SQLAlchemy 引擎"""
        if not self._engine:
            raise RuntimeError("数据库未连接，请先调用 connect()")
        return self._engine

    async def health_check(self) -> tuple[bool, str]:
        """数据库健康检查"""
        try:
            async with self._engine.begin() as conn:
                await conn.execute(SQL("SELECT 1"))
            return True, "OK"
        except Exception as e:
            return False, str(e)

    @staticmethod
    def build_dsn(
        host: str = "localhost",
        port: int = 5432,
        database: str = "hetu",
        user: str = "hetu",
        password: str = "",
    ) -> str:
        """构建 PostgreSQL 连接字符串"""
        if password:
            return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{database}"
        return f"postgresql+asyncpg://{user}@{host}:{port}/{database}"


# 全局数据库实例
_db: Optional[Database] = None


def get_db() -> Database:
    """获取全局数据库实例（必须已初始化）"""
    if _db is None:
        raise RuntimeError("Database 未初始化，请先调用 init_db()")
    return _db


async def init_db(dsn: str | None = None, **kwargs) -> Database:
    """初始化全局数据库实例"""
    global _db
    if dsn is None:
        dsn = Database.build_dsn(**kwargs)

    _db = Database(dsn)
    await _db.connect()
    return _db


async def close_db() -> None:
    """关闭全局数据库连接"""
    global _db
    if _db:
        await _db.disconnect()
        _db = None

# 添加 SQL 文本支持
from sqlalchemy import text as SQL
