"""
河图 (HeTu) YAML 配置加载器

用法:
    cfg = Config("config/default.yaml")
    key = cfg.get("brokers.mx.api_key")
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class Config:
    def __init__(self, path: str = "config/default.yaml"):
        self._path = Path(path)
        self._data: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            raise FileNotFoundError(f"配置文件不存在: {self._path}")
        with open(self._path, encoding="utf-8") as f:
            self._data = yaml.safe_load(f) or {}

    def get(self, key: str, default: Any = None) -> Any:
        parts = key.split(".")
        node = self._data
        for p in parts:
            if isinstance(node, dict):
                node = node.get(p)
                if node is None:
                    return default
            else:
                return default
        return node

    def all(self) -> dict:
        return dict(self._data)
