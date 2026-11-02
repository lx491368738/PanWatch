"""Intraday 主力净流入采样：进程内当日短缓存，交易时段后台常驻记录。

无分钟资金流序列时，复用日级 CapitalFlow.main_net_inflow 快照按时段表拼点：
09:30–10:00 ≈15s；其余交易时段 ≈60s；午休与非交易时段不采。
覆盖关注 ∪ 持仓中的 A 股；详情弹窗只读已采曲线。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, time
from zoneinfo import ZoneInfo

from src.platform.marketdata.collectors.capital_flow_collector import CapitalFlowCollector
from src.platform.marketdata.models import MarketCode
from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import Stock
from src.platform.scheduling import trading_calendar

CN_TZ = ZoneInfo("Asia/Shanghai")
OPEN_DENSE_END = time(10, 0)
DENSE_INTERVAL_SEC = 15
NORMAL_INTERVAL_SEC = 60
# 连续相同净值点数达到该阈值则视为停滞
STAGNANT_MIN_POINTS = 3
# 采样专用短 TTL：须短于采样间隔，又避免同轮/紧邻 tick 重复打源站
SAMPLE_FETCH_TTL_SEC = 12.0
# 单轮最多拉几只，避免自选一多打爆东财（其余留给下一 15s tick 轮转）
MAX_SAMPLES_PER_TICK = 6

_LOCK = threading.Lock()
_SERIES: dict[str, SymbolSeries] = {}
_RR_CURSOR = 0


@dataclass
class FlowPoint:
    t: str  # ISO8601 with offset
    main_net_inflow: float
    delta: float | None = None


@dataclass
class SymbolSeries:
    symbol: str
    trade_date: str
    points: list[FlowPoint] = field(default_factory=list)


def _cn_now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(CN_TZ)
    if now.tzinfo is None:
        return now.replace(tzinfo=CN_TZ)
    return now.astimezone(CN_TZ)


def trade_date_str(now: datetime | None = None) -> str:
    return _cn_now(now).date().isoformat()


def sample_interval_sec(now: datetime | None = None) -> int:
    """当前应使用的采样间隔；非交易时段返回 NORMAL（调用方不应采样）。"""
    dt = _cn_now(now)
    t = dt.time()
    if time(9, 30) <= t < OPEN_DENSE_END:
        return DENSE_INTERVAL_SEC
    return NORMAL_INTERVAL_SEC


def in_cn_trading_session(now: datetime | None = None) -> bool:
    """是否处于 A 股连续竞价时段（含午休外）。

    日历已知时走 ``market_status``；未知时回退到工作日 + 固定时段窗，
    避免 HOLIDAYS 未预热导致盘中永远不采样。
    """
    dt = _cn_now(now)
    status = trading_calendar.market_status(MarketCode.CN, dt)
    if status == "trading":
        return True
    if status in ("closed", "pre_market", "after_hours", "break"):
        return False
    if dt.weekday() >= 5:
        return False
    t = dt.time()
    return (time(9, 30) <= t <= time(11, 30)) or (time(13, 0) <= t <= time(15, 0))


def is_watchlist_or_holding_cn(db, symbol: str) -> bool:
    """关注 ∪ 持仓：自选表即关注；持仓经 FK 依附 Stock，故存在 CN Stock 行即可。"""
    sym = str(symbol or "").strip()
    if not sym:
        return False
    row = (
        db.query(Stock.id)
        .filter(Stock.symbol == sym, Stock.market == MarketCode.CN.value)
        .first()
    )
    return row is not None


def list_cn_watchlist_symbols(db=None) -> list[str]:
    """全部 CN 自选代码（含持仓标的）。"""
    owns_session = db is None
    session = db if db is not None else SessionLocal()
    try:
        rows = (
            session.query(Stock.symbol)
            .filter(Stock.market == MarketCode.CN.value)
            .order_by(Stock.sort_order.asc(), Stock.id.asc())
            .all()
        )
        out: list[str] = []
        seen: set[str] = set()
        for (symbol,) in rows:
            sym = str(symbol or "").strip()
            if not sym or sym in seen:
                continue
            seen.add(sym)
            out.append(sym)
        return out
    finally:
        if owns_session:
            session.close()


def _symbols_due(symbols: list[str], now: datetime) -> list[str]:
    """仅返回已到采样间隔的标的，避免无意义打源。"""
    interval = sample_interval_sec(now)
    due: list[str] = []
    for symbol in symbols:
        points = get_series(symbol, now=now).points
        elapsed = _seconds_since_last(points, now)
        if elapsed is None or elapsed + 1e-6 >= interval:
            due.append(symbol)
    return due


def sample_watchlist_once(*, now: datetime | None = None, fetch_flow=None) -> dict:
    """对关注∪持仓中的 CN 标的按间隔尝试追加采样点（轮转限流）。"""
    global _RR_CURSOR
    dt = _cn_now(now)
    if not in_cn_trading_session(dt):
        return {"sampled": 0, "checked": 0, "due": 0, "skipped_session": True}

    symbols = list_cn_watchlist_symbols()
    due = _symbols_due(symbols, dt)
    if not due:
        return {"sampled": 0, "checked": len(symbols), "due": 0, "skipped_session": False}

    # 轮转：每 tick 最多采 MAX_SAMPLES_PER_TICK 只，把压力摊到多个 15s 窗口
    start = _RR_CURSOR % len(due)
    ordered = due[start:] + due[:start]
    batch = ordered[:MAX_SAMPLES_PER_TICK]
    _RR_CURSOR = (start + len(batch)) % max(len(due), 1)

    sampled = 0
    try:
        from marketdata.http import fetch_source
    except Exception:  # pragma: no cover
        fetch_source = None

    def _run():
        nonlocal sampled
        for symbol in batch:
            before = len(get_series(symbol, now=dt).points)
            series = maybe_append_sample(symbol, now=dt, fetch_flow=fetch_flow)
            if len(series.points) > before:
                sampled += 1

    if fetch_source is not None:
        with fetch_source("capital_flow_intraday"):
            _run()
    else:
        _run()

    return {
        "sampled": sampled,
        "checked": len(symbols),
        "due": len(due),
        "batch": len(batch),
        "skipped_session": False,
    }


def _series_key(symbol: str) -> str:
    return str(symbol).strip()


def get_series(symbol: str, *, now: datetime | None = None) -> SymbolSeries:
    """读取当日序列；跨日则清空该标的旧点。"""
    key = _series_key(symbol)
    today = trade_date_str(now)
    with _LOCK:
        series = _SERIES.get(key)
        if series is None or series.trade_date != today:
            series = SymbolSeries(symbol=key, trade_date=today, points=[])
            _SERIES[key] = series
        return SymbolSeries(
            symbol=series.symbol,
            trade_date=series.trade_date,
            points=list(series.points),
        )


def clear_series(symbol: str | None = None) -> None:
    """测试/日切辅助。"""
    with _LOCK:
        if symbol is None:
            _SERIES.clear()
        else:
            _SERIES.pop(_series_key(symbol), None)


def _recompute_deltas(points: list[FlowPoint]) -> list[FlowPoint]:
    out: list[FlowPoint] = []
    prev: float | None = None
    for p in points:
        delta = None if prev is None else p.main_net_inflow - prev
        out.append(FlowPoint(t=p.t, main_net_inflow=p.main_net_inflow, delta=delta))
        prev = p.main_net_inflow
    return out


def is_stagnant(points: list[FlowPoint]) -> bool:
    if len(points) < STAGNANT_MIN_POINTS:
        return False
    tail = points[-STAGNANT_MIN_POINTS:]
    first = tail[0].main_net_inflow
    return all(abs(p.main_net_inflow - first) < 1e-9 for p in tail)


def _seconds_since_last(points: list[FlowPoint], now: datetime) -> float | None:
    if not points:
        return None
    last_t = datetime.fromisoformat(points[-1].t)
    if last_t.tzinfo is None:
        last_t = last_t.replace(tzinfo=CN_TZ)
    return max(0.0, (now - last_t.astimezone(CN_TZ)).total_seconds())


def maybe_append_sample(
    symbol: str,
    *,
    now: datetime | None = None,
    fetch_flow=None,
) -> SymbolSeries:
    """若在交易时段且距上次采样已达间隔，则拉一次短 TTL 资金流并追加点。"""
    key = _series_key(symbol)
    dt = _cn_now(now)
    today = trade_date_str(dt)

    with _LOCK:
        series = _SERIES.get(key)
        if series is None or series.trade_date != today:
            series = SymbolSeries(symbol=key, trade_date=today, points=[])
            _SERIES[key] = series

        if not in_cn_trading_session(dt):
            return SymbolSeries(symbol=series.symbol, trade_date=series.trade_date, points=list(series.points))

        interval = sample_interval_sec(dt)
        elapsed = _seconds_since_last(series.points, dt)
        if elapsed is not None and elapsed + 1e-6 < interval:
            return SymbolSeries(symbol=series.symbol, trade_date=series.trade_date, points=list(series.points))

    # 网络 IO 放锁外；TTL 略短于当前间隔，避免 15s 密集窗被长缓存挡住
    interval = sample_interval_sec(dt)
    fetch_ttl = min(SAMPLE_FETCH_TTL_SEC, max(3.0, float(interval) - 3.0))
    if fetch_flow is None:
        collector = CapitalFlowCollector(MarketCode.CN)
        flow = collector.get_capital_flow(key, ttl_sec=fetch_ttl)
    else:
        flow = fetch_flow(key)

    if flow is None or flow.main_net_inflow is None:
        return get_series(key, now=dt)

    point = FlowPoint(
        t=dt.isoformat(timespec="seconds"),
        main_net_inflow=float(flow.main_net_inflow),
        delta=None,
    )

    with _LOCK:
        series = _SERIES.get(key)
        if series is None or series.trade_date != today:
            series = SymbolSeries(symbol=key, trade_date=today, points=[])
            _SERIES[key] = series
        # 双重检查：并发下可能已有更新点
        elapsed = _seconds_since_last(series.points, dt)
        interval = sample_interval_sec(dt)
        if elapsed is not None and elapsed + 1e-6 < interval:
            return SymbolSeries(symbol=series.symbol, trade_date=series.trade_date, points=list(series.points))
        series.points.append(point)
        series.points = _recompute_deltas(series.points)
        return SymbolSeries(symbol=series.symbol, trade_date=series.trade_date, points=list(series.points))


def build_payload(
    symbol: str,
    *,
    eligible: bool,
    series: SymbolSeries,
    sampled: bool,
    now: datetime | None = None,
) -> dict:
    dt = _cn_now(now)
    points = [
        {
            "t": p.t,
            "main_net_inflow": p.main_net_inflow,
            "delta": p.delta,
        }
        for p in series.points
    ]
    latest = points[-1] if points else None
    return {
        "symbol": symbol,
        "market": MarketCode.CN.value,
        "eligible": eligible,
        "trade_date": series.trade_date,
        "in_session": in_cn_trading_session(dt),
        "interval_sec": sample_interval_sec(dt),
        "sampled": sampled,
        "stagnant": is_stagnant(series.points),
        "point_count": len(points),
        "latest": latest,
        "points": points,
    }
