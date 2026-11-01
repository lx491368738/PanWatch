"""市场涨跌家数(东财) vendor + client 方法测试。离线 monkeypatch market_get。"""
from __future__ import annotations

import marketdata.vendors.breadth as br
from marketdata.client import MarketData
from marketdata.defaults import StaticConfigProvider
from marketdata.ports import SourceConfig
from marketdata.types import BreadthSnapshot


def _ulist_payload(sh_up=1200, sh_down=800, sh_flat=50, sz_up=1100, sz_down=900, sz_flat=40) -> dict:
    return {
        "data": {
            "diff": [
                {"f12": "000001", "f14": "上证指数", "f104": sh_up, "f105": sh_down, "f106": sh_flat},
                {"f12": "399001", "f14": "深证成指", "f104": sz_up, "f105": sz_down, "f106": sz_flat},
            ]
        }
    }


def _pool_payload(n: int) -> dict:
    return {"data": {"pool": [{"code": f"{i:06d}"} for i in range(n)], "tc": n}}


class TestHelpers:
    def test_normalize_diff_list_and_dict(self):
        assert len(br._normalize_diff(_ulist_payload())) == 2
        as_dict = {"data": {"diff": {"0": {"f104": 1}, "1": {"f104": 2}}}}
        assert len(br._normalize_diff(as_dict)) == 2
        assert br._normalize_diff(None) == []

    def test_pool_count_prefers_tc(self):
        assert br._pool_count({"data": {"tc": 42, "pool": [1, 2]}}) == 42
        assert br._pool_count({"data": {"pool": [{}, {}, {}]}}) == 3
        assert br._pool_count(None) == 0

    def test_sum_counts(self):
        rows = br._normalize_diff(_ulist_payload(10, 20, 1, 30, 40, 2))
        assert br._sum_counts(rows) == (40, 60, 3)


class TestEastmoneyBreadthVendor:
    def test_sums_sh_sz_and_pools(self, monkeypatch):
        calls = {"n": 0}

        def fake_get(url, **kwargs):
            calls["n"] += 1
            if "ulist" in url:
                return _ulist_payload(1000, 500, 10, 800, 600, 20)
            if "ZTPool" in url or "zt" in url.lower():
                return _pool_payload(55)
            if "DTPool" in url or "dt" in url.lower():
                return _pool_payload(12)
            return None

        monkeypatch.setattr(br, "market_get", fake_get)
        out = br.EastmoneyBreadthVendor().fetch([], {})
        assert len(out) == 1 and isinstance(out[0], BreadthSnapshot)
        item = out[0]
        assert item.up_count == 1800
        assert item.down_count == 1100
        assert item.flat_count == 30
        assert item.total_count == 2930
        assert item.limit_up_count == 55
        assert item.limit_down_count == 12
        assert item.up_pct == round(1800 / 2930 * 100, 2)
        assert item.source == "eastmoney"
        assert calls["n"] == 3

    def test_empty_ulist_returns_empty(self, monkeypatch):
        monkeypatch.setattr(br, "market_get", lambda *a, **k: None)
        assert br.EastmoneyBreadthVendor().fetch([], {}) == []

    def test_non_cn_market_returns_empty(self, monkeypatch):
        monkeypatch.setattr(br, "market_get", lambda *a, **k: _ulist_payload())
        assert br.EastmoneyBreadthVendor().fetch([], {"market": "US"}) == []


class TestMarketDataBreadth:
    def test_breadth_via_engine(self, monkeypatch):
        def fake_get(url, **kwargs):
            if "ulist" in url:
                return _ulist_payload(100, 50, 5, 80, 40, 5)
            return _pool_payload(3)

        monkeypatch.setattr(br, "market_get", fake_get)
        md = MarketData(
            config=StaticConfigProvider(
                {"breadth": [SourceConfig(vendor="eastmoney", priority=0)]}
            )
        )
        out = md.breadth()
        assert len(out) == 1
        assert out[0].up_count == 180
        assert out[0].total_count == 280

    def test_breadth_no_sources_returns_empty(self):
        md = MarketData(config=StaticConfigProvider({}))
        assert md.breadth() == []
