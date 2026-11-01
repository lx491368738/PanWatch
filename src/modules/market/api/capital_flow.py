"""CN 个股当日主力净流入采样曲线 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.modules.market import capital_flow_intraday as cf_intra
from src.platform.persistence.database import get_db
from src.web.errors import api_error

router = APIRouter()


@router.get("/{symbol}/intraday")
def get_capital_flow_intraday(
    symbol: str,
    sample: bool = Query(
        False,
        description="为 true 且处于交易时段时尝试追加一个采样点；收盘后只读缓存",
    ),
    db: Session = Depends(get_db),
):
    sym = str(symbol or "").strip()
    if not sym:
        raise api_error(400, "symbol_required", "股票代码不能为空")

    eligible = cf_intra.is_watchlist_or_holding_cn(db, sym)
    if not eligible:
        # 临查且不在关注∪持仓：不采样、不展示（约定 Q25）
        return {
            "symbol": sym,
            "market": "CN",
            "eligible": False,
            "trade_date": cf_intra.trade_date_str(),
            "in_session": cf_intra.in_cn_trading_session(),
            "interval_sec": cf_intra.sample_interval_sec(),
            "sampled": False,
            "stagnant": False,
            "point_count": 0,
            "latest": None,
            "points": [],
            "reason": "not_watchlist_or_holding",
        }

    sampled = False
    if sample and cf_intra.in_cn_trading_session():
        before = len(cf_intra.get_series(sym).points)
        series = cf_intra.maybe_append_sample(sym)
        sampled = len(series.points) > before
    else:
        series = cf_intra.get_series(sym)

    return cf_intra.build_payload(sym, eligible=True, series=series, sampled=sampled)
