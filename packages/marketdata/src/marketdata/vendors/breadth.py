"""市场涨跌家数 vendor:东财上证+深成指 f104/f105/f106 + 涨停/跌停池条数。

市场级(symbols 恒空)。口径≈沪深 A(不含北交所);指数家数通常仍含 ST。
"""
from __future__ import annotations

from datetime import date, datetime

from marketdata.http import market_get
from marketdata.symbol import Symbol
from marketdata.types import BreadthSnapshot
from marketdata.vendors.base import BreadthVendor as _BreadthVendorBase

_ULIST_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
_ZT_POOL_URL = "https://push2ex.eastmoney.com/getTopicZTPool"
_DT_POOL_URL = "https://push2ex.eastmoney.com/getTopicDTPool"
_HOST_KEY = "push2.eastmoney.com"
_POOL_HOST_KEY = "push2ex.eastmoney.com"

# 上证指数 + 深证成指(全市场家数口径,非沪深300成分)
_SECIDS = "1.000001,0.399001"
_FIELDS = "f12,f14,f104,f105,f106"

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)
_HEADERS = {"User-Agent": _UA, "Referer": "https://quote.eastmoney.com/"}


def _to_int(value) -> int | None:
    if value is None or value == "-" or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _normalize_diff(data: dict | None) -> list[dict]:
    """东财 diff 可能是 list 或 dict(按 index 为 key)。"""
    if not isinstance(data, dict):
        return []
    payload = data.get("data")
    if not isinstance(payload, dict):
        return []
    diff = payload.get("diff") or payload.get("pool") or []
    if isinstance(diff, dict):
        return [v for v in diff.values() if isinstance(v, dict)]
    if isinstance(diff, list):
        return [v for v in diff if isinstance(v, dict)]
    return []


def _pool_count(data: dict | None) -> int:
    """涨停/跌停池条数。优先 pool/diff 长度,其次 tc/total。"""
    if not isinstance(data, dict):
        return 0
    payload = data.get("data")
    if isinstance(payload, dict):
        for key in ("tc", "total", "totalCount"):
            n = _to_int(payload.get(key))
            if n is not None and n >= 0:
                return n
        pool = payload.get("pool") or payload.get("diff") or []
        if isinstance(pool, dict):
            return len(pool)
        if isinstance(pool, list):
            return len(pool)
    return len(_normalize_diff(data))


def _sum_counts(rows: list[dict]) -> tuple[int, int, int]:
    up = down = flat = 0
    for row in rows:
        u = _to_int(row.get("f104")) or 0
        d = _to_int(row.get("f105")) or 0
        f = _to_int(row.get("f106")) or 0
        up += u
        down += d
        flat += f
    return up, down, flat


class EastmoneyBreadthVendor(_BreadthVendorBase):
    """东财市场涨跌家数:ulist 上证+深成指家数 + 涨停/跌停池。"""

    name = "eastmoney"
    supports_markets = {"CN"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[BreadthSnapshot]:
        market = str((config or {}).get("market") or "CN").upper()
        if market != "CN":
            return []

        ulist = market_get(
            _ULIST_URL,
            host_key=_HOST_KEY,
            params={
                "fltt": 2,
                "secids": _SECIDS,
                "fields": _FIELDS,
            },
            headers=_HEADERS,
            parse="json",
            retries=2,
            timeout=8,
            log_label="涨跌家数",
        )
        rows = _normalize_diff(ulist)
        if not rows:
            return []

        up, down, flat = _sum_counts(rows)
        total = up + down + flat
        if total <= 0:
            return []

        trade_day = date.today().strftime("%Y%m%d")
        limit_up = self._fetch_pool_count(_ZT_POOL_URL, trade_day, "涨停池")
        limit_down = self._fetch_pool_count(_DT_POOL_URL, trade_day, "跌停池")

        up_pct = round(up / total * 100.0, 2) if total else None
        return [
            BreadthSnapshot(
                market="CN",
                up_count=up,
                down_count=down,
                flat_count=flat,
                limit_up_count=limit_up,
                limit_down_count=limit_down,
                total_count=total,
                up_pct=up_pct,
                timestamp=datetime.now(),
                source=self.name,
            )
        ]

    def _fetch_pool_count(self, url: str, trade_day: str, label: str) -> int:
        data = market_get(
            url,
            host_key=_POOL_HOST_KEY,
            params={
                "ut": "7eea3edcaed734bea9cbfc24409ed989",
                "dpt": "wz.ztzt",
                "Pageindex": 0,
                "pagesize": 10000,
                "sort": "fbt:asc",
                "date": trade_day,
            },
            headers=_HEADERS,
            parse="json",
            retries=1,
            timeout=8,
            log_label=label,
        )
        return _pool_count(data)
