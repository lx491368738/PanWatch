"""当日主力净流入采样：时段间隔、拼点、停滞判定。"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from src.modules.market import capital_flow_intraday as cf


CN = ZoneInfo("Asia/Shanghai")


@pytest.fixture(autouse=True)
def _clean_series():
    cf.clear_series()
    yield
    cf.clear_series()


def test_sample_interval_dense_then_normal():
    open_dense = datetime(2026, 11, 2, 9, 45, tzinfo=CN)  # Mon
    after_dense = datetime(2026, 11, 2, 10, 5, tzinfo=CN)
    afternoon = datetime(2026, 11, 2, 14, 0, tzinfo=CN)
    assert cf.sample_interval_sec(open_dense) == 15
    assert cf.sample_interval_sec(after_dense) == 60
    assert cf.sample_interval_sec(afternoon) == 60


def test_session_windows_weekday_fallback(monkeypatch):
    monkeypatch.setattr(
        cf.trading_calendar,
        "market_status",
        lambda *a, **k: "unknown",
    )
    assert cf.in_cn_trading_session(datetime(2026, 11, 2, 9, 31, tzinfo=CN))
    assert not cf.in_cn_trading_session(datetime(2026, 11, 2, 12, 0, tzinfo=CN))  # lunch
    assert cf.in_cn_trading_session(datetime(2026, 11, 2, 13, 30, tzinfo=CN))
    assert not cf.in_cn_trading_session(datetime(2026, 11, 2, 15, 30, tzinfo=CN))
    assert not cf.in_cn_trading_session(datetime(2026, 11, 1, 10, 0, tzinfo=CN))  # Sun


def test_append_respects_interval_and_builds_delta(monkeypatch):
    monkeypatch.setattr(cf, "in_cn_trading_session", lambda now=None: True)
    monkeypatch.setattr(cf, "sample_interval_sec", lambda now=None: 60)

    values = iter([100.0, 150.0, 150.0])

    def fetch(_symbol):
        return SimpleNamespace(main_net_inflow=next(values))

    t0 = datetime(2026, 11, 2, 10, 0, tzinfo=CN)
    s1 = cf.maybe_append_sample("600519", now=t0, fetch_flow=fetch)
    assert len(s1.points) == 1
    assert s1.points[0].delta is None

    # too soon — no new point
    s2 = cf.maybe_append_sample(
        "600519", now=datetime(2026, 11, 2, 10, 0, 30, tzinfo=CN), fetch_flow=fetch
    )
    assert len(s2.points) == 1

    s3 = cf.maybe_append_sample(
        "600519", now=datetime(2026, 11, 2, 10, 1, 0, tzinfo=CN), fetch_flow=fetch
    )
    assert len(s3.points) == 2
    assert s3.points[1].delta == pytest.approx(50.0)

    s4 = cf.maybe_append_sample(
        "600519", now=datetime(2026, 11, 2, 10, 2, 0, tzinfo=CN), fetch_flow=fetch
    )
    assert len(s4.points) == 3
    assert s4.points[2].delta == pytest.approx(0.0)
    assert cf.is_stagnant(s4.points) is False  # values 100,150,150 — last 3 not identical

    # force three identical
    cf.clear_series("600519")
    flat = iter([10.0, 10.0, 10.0])

    def fetch_flat(_s):
        return SimpleNamespace(main_net_inflow=next(flat))

    for i in range(3):
        cf.maybe_append_sample(
            "600519",
            now=datetime(2026, 11, 2, 10, i, tzinfo=CN),
            fetch_flow=fetch_flat,
        )
    series = cf.get_series("600519", now=datetime(2026, 11, 2, 10, 5, tzinfo=CN))
    assert cf.is_stagnant(series.points)


def test_no_sample_outside_session(monkeypatch):
    monkeypatch.setattr(cf, "in_cn_trading_session", lambda now=None: False)
    called = {"n": 0}

    def fetch(_s):
        called["n"] += 1
        return SimpleNamespace(main_net_inflow=1.0)

    series = cf.maybe_append_sample(
        "600519", now=datetime(2026, 11, 2, 16, 0, tzinfo=CN), fetch_flow=fetch
    )
    assert series.points == []
    assert called["n"] == 0


def test_day_rollover_clears_points(monkeypatch):
    monkeypatch.setattr(cf, "in_cn_trading_session", lambda now=None: True)
    monkeypatch.setattr(cf, "sample_interval_sec", lambda now=None: 1)

    cf.maybe_append_sample(
        "600519",
        now=datetime(2026, 11, 2, 10, 0, tzinfo=CN),
        fetch_flow=lambda _s: SimpleNamespace(main_net_inflow=1.0),
    )
    assert len(cf.get_series("600519", now=datetime(2026, 11, 2, 10, 0, tzinfo=CN)).points) == 1

    next_day = cf.get_series("600519", now=datetime(2026, 11, 3, 10, 0, tzinfo=CN))
    assert next_day.trade_date == "2026-11-03"
    assert next_day.points == []


def test_sample_watchlist_once_covers_all_symbols(monkeypatch):
    monkeypatch.setattr(cf, "in_cn_trading_session", lambda now=None: True)
    monkeypatch.setattr(cf, "sample_interval_sec", lambda now=None: 1)
    monkeypatch.setattr(cf, "list_cn_watchlist_symbols", lambda db=None: ["600519", "000001"])
    monkeypatch.setattr(cf, "MAX_SAMPLES_PER_TICK", 10)
    values = {"600519": 10.0, "000001": 20.0}

    result = cf.sample_watchlist_once(
        now=datetime(2026, 11, 2, 10, 0, tzinfo=CN),
        fetch_flow=lambda sym: SimpleNamespace(main_net_inflow=values[sym]),
    )
    assert result["skipped_session"] is False
    assert result["checked"] == 2
    assert result["sampled"] == 2
    assert len(cf.get_series("600519").points) == 1
    assert len(cf.get_series("000001").points) == 1


def test_sample_watchlist_once_rotates_batch(monkeypatch):
    monkeypatch.setattr(cf, "in_cn_trading_session", lambda now=None: True)
    monkeypatch.setattr(cf, "sample_interval_sec", lambda now=None: 1)
    monkeypatch.setattr(cf, "list_cn_watchlist_symbols", lambda db=None: [f"s{i}" for i in range(5)])
    monkeypatch.setattr(cf, "MAX_SAMPLES_PER_TICK", 2)
    cf._RR_CURSOR = 0
    fetched: list[str] = []

    def fetch(sym):
        fetched.append(sym)
        return SimpleNamespace(main_net_inflow=1.0)

    t0 = datetime(2026, 11, 2, 10, 0, tzinfo=CN)
    r1 = cf.sample_watchlist_once(now=t0, fetch_flow=fetch)
    assert r1["batch"] == 2
    assert fetched == ["s0", "s1"]
    r2 = cf.sample_watchlist_once(now=datetime(2026, 11, 2, 10, 0, 30, tzinfo=CN), fetch_flow=fetch)
    # s0/s1 尚未到下一间隔；due 只剩 s2..，轮转继续
    assert r2["sampled"] >= 1
    assert "s2" in fetched


def test_sample_watchlist_once_skips_outside_session(monkeypatch):
    monkeypatch.setattr(cf, "in_cn_trading_session", lambda now=None: False)
    monkeypatch.setattr(
        cf,
        "list_cn_watchlist_symbols",
        lambda db=None: (_ for _ in ()).throw(AssertionError("should not list")),
    )
    result = cf.sample_watchlist_once(now=datetime(2026, 11, 2, 16, 0, tzinfo=CN))
    assert result["skipped_session"] is True
    assert result["sampled"] == 0
