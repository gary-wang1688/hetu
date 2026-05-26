"""河图 (HeTu) TDX 行情数据源 — 通达信 TCP 协议。基于 mootdx。"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger("hetu.data.tdx")


@dataclass
class TDXQuote:
    code: str
    price: float = 0.0; open: float = 0.0; high: float = 0.0; low: float = 0.0
    pre_close: float = 0.0; volume: int = 0; amount: float = 0.0
    bid1: float = 0.0; ask1: float = 0.0
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)


class TDXProvider:
    def __init__(self):
        self._client = None

    def get_quotes(self, codes: list[str]) -> dict[str, TDXQuote]:
        if not codes: return {}
        try:
            client = self._ensure_client()
            mdx = [c if '.' in c else (c + ('.SH' if c.startswith('6') else '.SZ')) for c in codes]
            df = client.quotes(mdx)
            result = {}
            ts = datetime.now(timezone.utc)
            for _, row in df.iterrows():
                raw = str(row.get('code', ''))
                mkt = int(row.get('market', 0))
                suffix = '.SH' if mkt == 1 else '.SZ'
                code = f'{raw}{suffix}' if not raw.endswith(('.SH', '.SZ')) else raw
                result[code] = TDXQuote(
                    code=code,
                    price=float(row.get('price', 0) or 0),
                    open=float(row.get('open', 0) or 0),
                    high=float(row.get('high', 0) or 0),
                    low=float(row.get('low', 0) or 0),
                    pre_close=float(row.get('last_close', 0) or 0),
                    volume=int(row.get('vol', 0) or 0),
                    amount=float(row.get('amount', 0) or 0),
                    bid1=float(row.get('bid1', 0) or 0),
                    ask1=float(row.get('ask1', 0) or 0),
                    timestamp=ts,
                )
            return result
        except Exception as e:
            logger.warning("TDX: %s", e)
            return {}

    def close(self):
        if self._client:
            try: self._client.close()
            except Exception: pass
            self._client = None

    def _ensure_client(self):
        if self._client is None:
            from mootdx.quotes import Quotes
            self._client = Quotes.factory(market='std')
        return self._client
