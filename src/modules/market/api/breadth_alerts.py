"""市场广度提醒 CRUD API。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.modules.market import breadth_service as svc
from src.modules.market.breadth_engine import ENGINE
from src.platform.persistence.database import SessionLocal, get_db
from src.platform.persistence.models import BreadthAlertHit, BreadthAlertRule
from src.web.errors import api_error

logger = logging.getLogger(__name__)
router = APIRouter()


class BreadthAlertCreate(BaseModel):
    market: str = "CN"
    name: str = ""
    enabled: bool = True
    condition_type: str = Field(..., description="breadth_up_pct / breadth_up_pct_change")
    operator: str = "<="
    threshold: float
    lookback_minutes: int = 1
    market_hours_mode: str = "trading_only"
    cooldown_minutes: int = 30
    max_triggers_per_day: int = 3
    repeat_mode: str = "repeat"
    expire_at: str | None = None
    notify_channel_ids: list[int] = []


class BreadthAlertUpdate(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    condition_type: str | None = None
    operator: str | None = None
    threshold: float | None = None
    lookback_minutes: int | None = None
    market_hours_mode: str | None = None
    cooldown_minutes: int | None = None
    max_triggers_per_day: int | None = None
    repeat_mode: str | None = None
    expire_at: str | None = None
    notify_channel_ids: list[int] | None = None


class ToggleBody(BaseModel):
    enabled: bool


@router.get("")
def list_rules(db: Session = Depends(get_db)):
    created = svc.ensure_default_rules(db)
    if created:
        db.commit()
    rules = db.query(BreadthAlertRule).order_by(BreadthAlertRule.id.asc()).all()
    return [svc.serialize_rule(r) for r in rules]


@router.post("/sample-once")
def sample_once():
    """手动触发一次采样(测试用,不受交易时段门控)。"""
    try:
        from src.platform.marketdata.marketdata_client import get_market_data

        snapshots = get_market_data().breadth(market="CN")
        if not snapshots:
            raise api_error(502, "未获取到涨跌家数数据")
        with SessionLocal() as session:
            row = svc.upsert_sample(session, market="CN", snapshot=snapshots[0])
            created = svc.ensure_default_rules(session)
            session.commit()
            sample = svc.sample_to_dict(row)
        triggered = ENGINE.eval_alerts()
        return {
            "sampled": True,
            "sample": sample,
            "defaults_created": len(created),
            "triggered": triggered,
        }
    except Exception as e:
        if hasattr(e, "status_code"):
            raise
        logger.exception("手动采样失败")
        raise api_error(500, str(e)) from e


@router.post("")
def create_rule(body: BreadthAlertCreate, db: Session = Depends(get_db)):
    try:
        rule = svc.create_rule(db, body.model_dump())
        db.commit()
        db.refresh(rule)
        return svc.serialize_rule(rule)
    except ValueError as e:
        raise api_error(400, str(e)) from e


@router.patch("/{rule_id}")
def update_rule(rule_id: int, body: BreadthAlertUpdate, db: Session = Depends(get_db)):
    rule = db.get(BreadthAlertRule, rule_id)
    if rule is None:
        raise api_error(404, "规则不存在")
    try:
        payload = {k: v for k, v in body.model_dump().items() if v is not None}
        svc.update_rule(db, rule, payload)
        db.commit()
        db.refresh(rule)
        return svc.serialize_rule(rule)
    except ValueError as e:
        raise api_error(400, str(e)) from e


@router.post("/{rule_id}/toggle")
def toggle_rule(rule_id: int, body: ToggleBody, db: Session = Depends(get_db)):
    rule = db.get(BreadthAlertRule, rule_id)
    if rule is None:
        raise api_error(404, "规则不存在")
    rule.enabled = bool(body.enabled)
    db.commit()
    db.refresh(rule)
    return svc.serialize_rule(rule)


@router.delete("/{rule_id}")
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = db.get(BreadthAlertRule, rule_id)
    if rule is None:
        raise api_error(404, "规则不存在")
    db.delete(rule)
    db.commit()
    return {"ok": True}


@router.get("/{rule_id}/hits")
def list_hits(rule_id: int, limit: int = 20, db: Session = Depends(get_db)):
    rule = db.get(BreadthAlertRule, rule_id)
    if rule is None:
        raise api_error(404, "规则不存在")
    hits = (
        db.query(BreadthAlertHit)
        .filter(BreadthAlertHit.rule_id == rule_id)
        .order_by(BreadthAlertHit.id.desc())
        .limit(max(1, min(limit, 100)))
        .all()
    )
    return [svc.serialize_hit(h) for h in hits]
