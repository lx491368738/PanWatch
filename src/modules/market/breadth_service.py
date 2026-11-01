"""市场广度采样查询与提醒规则领域服务。"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from src.modules.market.price_alert_service import nonnegative_integer, parse_expire_at, validate_channel_ids
from src.platform.persistence.models import BreadthAlertHit, BreadthAlertRule, MarketBreadthSample
from src.platform.scheduling import trading_calendar as calendar

CN_TZ = ZoneInfo("Asia/Shanghai")

BREADTH_CONDITION_TYPES = {"breadth_up_pct", "breadth_up_pct_change"}
BREADTH_OPERATORS = {">=", "<=", ">", "<"}

# 开盘活跃窗口 09:30–10:00 加密采样；其余交易时段按分钟。
OPEN_RUSH_START = time(9, 30)
OPEN_RUSH_END = time(10, 0)
OPEN_RUSH_INTERVAL_SEC = 15
NORMAL_INTERVAL_SEC = 60


def cn_now() -> datetime:
    return datetime.now(CN_TZ)


def to_cn(dt: datetime) -> datetime:
    return dt.astimezone(CN_TZ) if dt.tzinfo else dt.replace(tzinfo=CN_TZ)


def is_open_rush(dt: datetime | None = None) -> bool:
    """A 股开盘后半小时(含 09:30,不含 10:00)。"""
    local = to_cn(dt or cn_now()).time()
    return OPEN_RUSH_START <= local < OPEN_RUSH_END


def sample_interval_seconds(dt: datetime | None = None) -> int:
    return OPEN_RUSH_INTERVAL_SEC if is_open_rush(dt) else NORMAL_INTERVAL_SEC


def floor_minute(dt: datetime) -> datetime:
    local = to_cn(dt)
    return local.replace(second=0, microsecond=0)


def floor_sample_bucket(dt: datetime | None = None) -> datetime:
    """开盘活跃窗口按 15 秒对齐,其余按整分钟对齐。"""
    local = to_cn(dt or cn_now())
    if is_open_rush(local):
        sec = (local.second // OPEN_RUSH_INTERVAL_SEC) * OPEN_RUSH_INTERVAL_SEC
        return local.replace(second=sec, microsecond=0)
    return local.replace(second=0, microsecond=0)


def sample_to_dict(row: MarketBreadthSample) -> dict[str, Any]:
    return {
        "t": row.sampled_at,
        "up": row.up_count,
        "down": row.down_count,
        "flat": row.flat_count,
        "limit_up": row.limit_up_count,
        "limit_down": row.limit_down_count,
        "total": row.total_count,
        "up_pct": row.up_pct,
    }


def retention_cutoff_date(market: str = "CN", keep_days: int = 20) -> str:
    """最早保留的交易日 YYYY-MM-DD(含);往前数 keep_days 个交易日。"""
    cursor = cn_now().date()
    kept = 0
    # Walk back enough calendar days to cover holidays/weekends.
    for _ in range(keep_days * 3 + 14):
        if calendar.is_trading_day(market, cursor):
            kept += 1
            if kept >= keep_days:
                return cursor.isoformat()
        cursor -= timedelta(days=1)
    return cursor.isoformat()


def upsert_sample(
    db: Session,
    *,
    market: str,
    snapshot,
) -> MarketBreadthSample:
    sampled = floor_sample_bucket(getattr(snapshot, "timestamp", None) or cn_now())
    sampled_at = sampled.isoformat()
    trade_date = sampled.date().isoformat()
    existing = (
        db.query(MarketBreadthSample)
        .filter(
            MarketBreadthSample.market == market,
            MarketBreadthSample.sampled_at == sampled_at,
        )
        .one_or_none()
    )
    if existing is None:
        existing = MarketBreadthSample(market=market, sampled_at=sampled_at, trade_date=trade_date)
        db.add(existing)
    existing.trade_date = trade_date
    existing.up_count = int(snapshot.up_count or 0)
    existing.down_count = int(snapshot.down_count or 0)
    existing.flat_count = int(snapshot.flat_count or 0)
    existing.limit_up_count = int(snapshot.limit_up_count or 0)
    existing.limit_down_count = int(snapshot.limit_down_count or 0)
    existing.total_count = int(snapshot.total_count or 0)
    existing.up_pct = snapshot.up_pct
    existing.source = snapshot.source or "eastmoney"
    db.flush()
    return existing


def trim_old_samples(db: Session, *, market: str = "CN", keep_days: int = 20) -> int:
    cutoff = retention_cutoff_date(market, keep_days)
    deleted = (
        db.query(MarketBreadthSample)
        .filter(
            MarketBreadthSample.market == market,
            MarketBreadthSample.trade_date < cutoff,
        )
        .delete(synchronize_session=False)
    )
    return int(deleted or 0)


def list_samples(
    db: Session,
    *,
    market: str = "CN",
    trade_date: str | None = None,
) -> list[MarketBreadthSample]:
    day = trade_date or cn_now().date().isoformat()
    return (
        db.query(MarketBreadthSample)
        .filter(
            MarketBreadthSample.market == market,
            MarketBreadthSample.trade_date == day,
        )
        .order_by(MarketBreadthSample.sampled_at.asc())
        .all()
    )


def latest_sample(db: Session, *, market: str = "CN") -> MarketBreadthSample | None:
    return (
        db.query(MarketBreadthSample)
        .filter(MarketBreadthSample.market == market)
        .order_by(MarketBreadthSample.sampled_at.desc())
        .first()
    )


def sample_at_or_before(
    db: Session,
    *,
    market: str,
    sampled_at: str,
) -> MarketBreadthSample | None:
    return (
        db.query(MarketBreadthSample)
        .filter(
            MarketBreadthSample.market == market,
            MarketBreadthSample.sampled_at <= sampled_at,
        )
        .order_by(MarketBreadthSample.sampled_at.desc())
        .first()
    )


def validate_breadth_condition(
    *,
    condition_type: str,
    operator: str,
    threshold: Any,
    lookback_minutes: Any = 5,
) -> dict[str, Any]:
    ctype = str(condition_type or "").strip()
    op = str(operator or "").strip()
    if ctype not in BREADTH_CONDITION_TYPES:
        raise ValueError(f"不支持的条件类型: {ctype}")
    if op not in BREADTH_OPERATORS:
        raise ValueError(f"不支持的运算符: {op}")
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(threshold):
        raise ValueError("threshold 必须是有限数字")
    lookback = nonnegative_integer(lookback_minutes if lookback_minutes is not None else 5)
    if ctype == "breadth_up_pct_change" and lookback <= 0:
        raise ValueError("变化提醒的 lookback_minutes 必须为正整数")
    return {
        "condition_type": ctype,
        "operator": op,
        "threshold": float(threshold),
        "lookback_minutes": lookback if ctype == "breadth_up_pct_change" else 5,
    }


def serialize_rule(rule: BreadthAlertRule) -> dict[str, Any]:
    return {
        "id": rule.id,
        "market": rule.market,
        "name": rule.name,
        "enabled": bool(rule.enabled),
        "condition_type": rule.condition_type,
        "operator": rule.operator,
        "threshold": rule.threshold,
        "lookback_minutes": rule.lookback_minutes,
        "market_hours_mode": rule.market_hours_mode,
        "cooldown_minutes": rule.cooldown_minutes,
        "max_triggers_per_day": rule.max_triggers_per_day,
        "repeat_mode": rule.repeat_mode,
        "expire_at": rule.expire_at.replace(tzinfo=UTC).isoformat() if rule.expire_at else None,
        "notify_channel_ids": list(rule.notify_channel_ids or []),
        "last_trigger_at": rule.last_trigger_at.replace(tzinfo=UTC).isoformat() if rule.last_trigger_at else None,
        "trigger_count_today": rule.trigger_count_today,
        "trigger_date": rule.trigger_date,
        "created_at": rule.created_at.replace(tzinfo=UTC).isoformat() if rule.created_at else None,
        "updated_at": rule.updated_at.replace(tzinfo=UTC).isoformat() if rule.updated_at else None,
    }


def create_rule(db: Session, payload: dict[str, Any]) -> BreadthAlertRule:
    cond = validate_breadth_condition(
        condition_type=payload.get("condition_type"),
        operator=payload.get("operator"),
        threshold=payload.get("threshold"),
        lookback_minutes=payload.get("lookback_minutes", 5),
    )
    market = str(payload.get("market") or "CN").upper()
    if market != "CN":
        raise ValueError("P0 仅支持 CN 市场广度提醒")
    mode = str(payload.get("market_hours_mode") or "trading_only")
    if mode not in {"always", "trading_only"}:
        raise ValueError("market_hours_mode 仅支持 always/trading_only")
    repeat = str(payload.get("repeat_mode") or "repeat")
    if repeat not in {"once", "repeat"}:
        raise ValueError("repeat_mode 仅支持 once/repeat")
    rule = BreadthAlertRule(
        market=market,
        name=str(payload.get("name") or "").strip() or _default_name(cond),
        enabled=bool(payload.get("enabled", True)),
        condition_type=cond["condition_type"],
        operator=cond["operator"],
        threshold=cond["threshold"],
        lookback_minutes=cond["lookback_minutes"],
        market_hours_mode=mode,
        cooldown_minutes=nonnegative_integer(payload.get("cooldown_minutes", 30)),
        max_triggers_per_day=nonnegative_integer(payload.get("max_triggers_per_day", 3)),
        repeat_mode=repeat,
        expire_at=parse_expire_at(payload.get("expire_at")),
        notify_channel_ids=validate_channel_ids(db, payload.get("notify_channel_ids")),
    )
    db.add(rule)
    db.flush()
    return rule


def update_rule(db: Session, rule: BreadthAlertRule, payload: dict[str, Any]) -> BreadthAlertRule:
    if "condition_type" in payload or "operator" in payload or "threshold" in payload or "lookback_minutes" in payload:
        cond = validate_breadth_condition(
            condition_type=payload.get("condition_type", rule.condition_type),
            operator=payload.get("operator", rule.operator),
            threshold=payload.get("threshold", rule.threshold),
            lookback_minutes=payload.get("lookback_minutes", rule.lookback_minutes),
        )
        rule.condition_type = cond["condition_type"]
        rule.operator = cond["operator"]
        rule.threshold = cond["threshold"]
        rule.lookback_minutes = cond["lookback_minutes"]
    if "name" in payload:
        rule.name = str(payload.get("name") or "").strip() or rule.name
    if "enabled" in payload:
        rule.enabled = bool(payload["enabled"])
    if "market_hours_mode" in payload:
        mode = str(payload.get("market_hours_mode") or "trading_only")
        if mode not in {"always", "trading_only"}:
            raise ValueError("market_hours_mode 仅支持 always/trading_only")
        rule.market_hours_mode = mode
    if "cooldown_minutes" in payload:
        rule.cooldown_minutes = nonnegative_integer(payload["cooldown_minutes"])
    if "max_triggers_per_day" in payload:
        rule.max_triggers_per_day = nonnegative_integer(payload["max_triggers_per_day"])
    if "repeat_mode" in payload:
        repeat = str(payload.get("repeat_mode") or "repeat")
        if repeat not in {"once", "repeat"}:
            raise ValueError("repeat_mode 仅支持 once/repeat")
        rule.repeat_mode = repeat
    if "expire_at" in payload:
        rule.expire_at = parse_expire_at(payload.get("expire_at"))
    if "notify_channel_ids" in payload:
        rule.notify_channel_ids = validate_channel_ids(db, payload.get("notify_channel_ids"))
    db.flush()
    return rule


def _default_name(cond: dict[str, Any]) -> str:
    if cond["condition_type"] == "breadth_up_pct_change":
        return f"上涨占比{cond['lookback_minutes']}分钟变化 {cond['operator']} {cond['threshold']}pp"
    return f"上涨占比 {cond['operator']} {cond['threshold']}%"


def ensure_default_rules(db: Session) -> list[BreadthAlertRule]:
    """种子默认规则(无渠道也可入库)。调用方负责 commit。

    若已存在旧默认急变规则(5 分钟窗口),就地升级为 1 分钟；返回值非空表示有写入。
    """
    touched: list[BreadthAlertRule] = []
    legacy_names = {
        "急变：5分钟占比降 ≥ 10pp",
        "急变：1分钟占比降 ≥ 10pp",
    }
    legacy = (
        db.query(BreadthAlertRule)
        .filter(
            BreadthAlertRule.condition_type == "breadth_up_pct_change",
            BreadthAlertRule.name.in_(legacy_names),
        )
        .all()
    )
    for rule in legacy:
        rule.name = "急变：1分钟占比降 ≥ 5pp"
        rule.lookback_minutes = 1
        rule.operator = "<="
        rule.threshold = -5.0
        touched.append(rule)

    existing = db.query(BreadthAlertRule).count()
    if existing:
        return touched
    return [
        create_rule(
            db,
            {
                "name": "弱势：上涨占比 < 30%",
                "condition_type": "breadth_up_pct",
                "operator": "<",
                "threshold": 30.0,
                "notify_channel_ids": [],
            },
        ),
        create_rule(
            db,
            {
                "name": "急变：1分钟占比降 ≥ 5pp",
                "condition_type": "breadth_up_pct_change",
                "operator": "<=",
                "threshold": -5.0,
                "lookback_minutes": 1,
                "notify_channel_ids": [],
            },
        ),
    ]


def compare(op: str, left: float, right: float) -> bool:
    if op == ">=":
        return left >= right
    if op == "<=":
        return left <= right
    if op == ">":
        return left > right
    if op == "<":
        return left < right
    return False


def serialize_hit(hit: BreadthAlertHit) -> dict[str, Any]:
    return {
        "id": hit.id,
        "rule_id": hit.rule_id,
        "trigger_time": hit.trigger_time.replace(tzinfo=UTC).isoformat() if hit.trigger_time else None,
        "trigger_bucket": hit.trigger_bucket,
        "trigger_snapshot": hit.trigger_snapshot or {},
        "notify_success": bool(hit.notify_success),
        "notify_error": hit.notify_error or "",
    }
