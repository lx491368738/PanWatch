"""HTTP gate for CN capital-flow intraday series."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.market import capital_flow_intraday as cf
from src.modules.market.api import capital_flow as api


def test_ineligible_symbol_returns_empty(monkeypatch):
    app = FastAPI()
    app.include_router(api.router, prefix="/api/capital-flow")

    def fake_db():
        yield SimpleNamespace()

    app.dependency_overrides[api.get_db] = fake_db
    monkeypatch.setattr(cf, "is_watchlist_or_holding_cn", lambda db, symbol: False)
    monkeypatch.setattr(cf, "maybe_append_sample", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not sample")))

    client = TestClient(app)
    resp = client.get("/api/capital-flow/600519/intraday", params={"sample": "true"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["eligible"] is False
    assert body["points"] == []
    assert body["reason"] == "not_watchlist_or_holding"


def test_eligible_can_sample(monkeypatch):
    cf.clear_series()
    app = FastAPI()
    app.include_router(api.router, prefix="/api/capital-flow")

    def fake_db():
        yield SimpleNamespace()

    app.dependency_overrides[api.get_db] = fake_db
    monkeypatch.setattr(cf, "is_watchlist_or_holding_cn", lambda db, symbol: True)
    monkeypatch.setattr(cf, "in_cn_trading_session", lambda now=None: True)
    monkeypatch.setattr(cf, "sample_interval_sec", lambda now=None: 1)

    empty = cf.SymbolSeries(symbol="600519", trade_date="2026-11-02", points=[])
    series = cf.SymbolSeries(
        symbol="600519",
        trade_date="2026-11-02",
        points=[cf.FlowPoint(t="2026-11-02T10:00:00+08:00", main_net_inflow=1.0, delta=None)],
    )
    monkeypatch.setattr(cf, "get_series", lambda symbol, now=None: empty)
    monkeypatch.setattr(cf, "maybe_append_sample", lambda symbol, **k: series)

    client = TestClient(app)
    resp = client.get("/api/capital-flow/600519/intraday", params={"sample": "true"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["eligible"] is True
    assert body["point_count"] == 1
    assert body["sampled"] is True
    cf.clear_series()
