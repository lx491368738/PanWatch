"""采样市场涨跌家数并评估广度提醒。"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from src.modules.market import breadth_service as svc
from src.modules.market.breadth_alert_delivery import enqueue
from src.modules.notifications import sources as notification_sources
from src.platform.marketdata.marketdata_client import get_market_data
from src.platform.marketdata.models import MARKETS, MarketCode
from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import BreadthAlertHit, BreadthAlertRule, MarketBreadthSample

logger = logging.getLogger(__name__)


class BreadthEngine:
    def sample_once(self) -> dict:
        if not MARKETS[MarketCode.CN].is_trading_time():
            return {"skipped": "not_trading", "sampled": False}

        snapshots = get_market_data().breadth(market="CN")
        if not snapshots:
            return {"sampled": False, "error": "empty_breadth"}

        snap = snapshots[0]
        with SessionLocal() as db:
            row = svc.upsert_sample(db, market="CN", snapshot=snap)
            trimmed = svc.trim_old_samples(db, market="CN", keep_days=20)
            svc.ensure_default_rules(db)
            db.commit()
            sample = svc.sample_to_dict(row)
            sample_id = row.id
        triggered = self.eval_alerts()
        return {
            "sampled": True,
            "sample_id": sample_id,
            "sample": sample,
            "trimmed": trimmed,
            "triggered": triggered.get("triggered", 0),
        }

    def eval_alerts(self, *, dry_run: bool = False) -> dict:
        with SessionLocal() as db:
            rules = (
                db.query(BreadthAlertRule)
                .filter(BreadthAlertRule.enabled == True)  # noqa: E712
                .order_by(BreadthAlertRule.id.asc())
                .all()
            )
            current = svc.latest_sample(db, market="CN")
            if current is None or current.up_pct is None:
                return {"triggered": 0, "skipped": len(rules), "reason": "no_sample"}

            triggered = 0
            skipped = 0
            now = datetime.now(UTC).replace(tzinfo=None)
            today = svc.cn_now().date().isoformat()
            bucket = svc.floor_minute(svc.cn_now()).strftime("%Y%m%d%H%M")

            for rule in rules:
                if rule.expire_at and rule.expire_at <= now:
                    skipped += 1
                    continue
                if rule.market_hours_mode == "trading_only" and not MARKETS[MarketCode.CN].is_trading_time():
                    skipped += 1
                    continue
                if rule.trigger_date != today:
                    rule.trigger_date = today
                    rule.trigger_count_today = 0
                if rule.max_triggers_per_day and rule.trigger_count_today >= rule.max_triggers_per_day:
                    skipped += 1
                    continue
                if rule.last_trigger_at and rule.cooldown_minutes:
                    if now - rule.last_trigger_at < timedelta(minutes=rule.cooldown_minutes):
                        skipped += 1
                        continue
                if rule.repeat_mode == "once" and rule.last_trigger_at is not None:
                    skipped += 1
                    continue

                value, prior = self._rule_value(db, rule, current)
                if value is None:
                    skipped += 1
                    continue
                if not svc.compare(rule.operator, value, float(rule.threshold)):
                    skipped += 1
                    continue

                snapshot = {
                    "current": svc.sample_to_dict(current),
                    "value": value,
                    "prior_up_pct": prior,
                    "condition_type": rule.condition_type,
                    "operator": rule.operator,
                    "threshold": rule.threshold,
                    "lookback_minutes": rule.lookback_minutes,
                }
                if dry_run:
                    triggered += 1
                    continue

                existing = (
                    db.query(BreadthAlertHit)
                    .filter(
                        BreadthAlertHit.rule_id == rule.id,
                        BreadthAlertHit.trigger_bucket == bucket,
                    )
                    .one_or_none()
                )
                if existing is not None:
                    skipped += 1
                    continue

                hit = BreadthAlertHit(
                    rule_id=rule.id,
                    trigger_time=now,
                    trigger_bucket=bucket,
                    trigger_snapshot=snapshot,
                )
                db.add(hit)
                db.flush()
                rule.last_trigger_at = now
                rule.trigger_count_today = int(rule.trigger_count_today or 0) + 1
                title, content = self._payload(rule, snapshot)
                notification_sources.breadth_hit(db, hit, rule)
                enqueue(db, hit, rule, title, content)
                triggered += 1

            if not dry_run:
                db.commit()
            return {"triggered": triggered, "skipped": skipped, "total_rules": len(rules)}

    def _rule_value(
        self, db, rule: BreadthAlertRule, current: MarketBreadthSample
    ) -> tuple[float | None, float | None]:
        if rule.condition_type == "breadth_up_pct":
            return (float(current.up_pct) if current.up_pct is not None else None), None
        if rule.condition_type == "breadth_up_pct_change":
            lookback = max(1, int(rule.lookback_minutes or 5))
            target = svc.floor_minute(svc.cn_now()) - timedelta(minutes=lookback)
            prior = svc.sample_at_or_before(db, market=rule.market, sampled_at=target.isoformat())
            if prior is None or prior.up_pct is None or current.up_pct is None:
                return None, None
            return float(current.up_pct) - float(prior.up_pct), float(prior.up_pct)
        return None, None

    def _payload(self, rule: BreadthAlertRule, snapshot: dict) -> tuple[str, str]:
        cur = snapshot.get("current") or {}
        title = rule.name or "市场广度提醒"
        if rule.condition_type == "breadth_up_pct_change":
            content = (
                f"上涨占比 {cur.get('up_pct')}%"
                f"（{rule.lookback_minutes} 分钟变化 {snapshot.get('value')}pp，"
                f"阈值 {rule.operator} {rule.threshold}pp）\n"
                f"涨/跌/平 {cur.get('up')}/{cur.get('down')}/{cur.get('flat')}，"
                f"涨停/跌停 {cur.get('limit_up')}/{cur.get('limit_down')}"
            )
        else:
            content = (
                f"上涨占比 {cur.get('up_pct')}%（阈值 {rule.operator} {rule.threshold}%）\n"
                f"涨/跌/平 {cur.get('up')}/{cur.get('down')}/{cur.get('flat')}，"
                f"涨停/跌停 {cur.get('limit_up')}/{cur.get('limit_down')}"
            )
        return title, content


ENGINE = BreadthEngine()
