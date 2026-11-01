"""广度提醒分渠道投递(克隆价格提醒 outbox 路径)。"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, or_, update

from src.platform.notifications.notifier import NotifierManager
from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import BreadthAlertDelivery, BreadthAlertHit, NotifyChannel


def now_utc():
    return datetime.now(UTC).replace(tzinfo=None)


def enqueue(db, hit, rule, title, content):
    ids = list(dict.fromkeys(rule.notify_channel_ids or []))
    if not ids:
        ids = [c.id for c in db.query(NotifyChannel).filter_by(enabled=True, is_default=True).all()]
    for channel_id in ids or [None]:
        key = str(channel_id) if channel_id is not None else "unconfigured"
        event_id = f"breadth-alert:{hit.id}:{key}:{uuid4().hex}"
        db.add(
            BreadthAlertDelivery(
                hit_id=hit.id,
                channel_id=channel_id,
                channel_key=key,
                event_id=event_id,
                title=title,
                content=f"{content}\n\nEvent ID: {event_id}",
                status="pending" if channel_id is not None else "blocked",
                error_code="" if channel_id is not None else "channel_missing",
                next_attempt_at=now_utc() if channel_id is not None else None,
            )
        )


def update_hit_receipt(db, hit_id):
    rows = db.query(BreadthAlertDelivery).filter_by(hit_id=hit_id).all()
    hit = db.get(BreadthAlertHit, hit_id)
    if hit is not None and rows:
        hit.notify_success = all(r.status == "delivered" for r in rows)
        hit.notify_error = (
            ""
            if hit.notify_success
            else ",".join(sorted({r.error_code or r.status for r in rows if r.status != "delivered"}))
        )


class BreadthAlertDeliveryWorker:
    def __init__(self, session_factory=None):
        self.session_factory = session_factory or SessionLocal

    def claim(self):
        now = now_utc()
        due = or_(
            and_(
                BreadthAlertDelivery.status.in_(["pending", "retry"]),
                BreadthAlertDelivery.next_attempt_at <= now,
            ),
            and_(
                BreadthAlertDelivery.status == "sending",
                BreadthAlertDelivery.lease_until <= now,
            ),
        )
        with self.session_factory() as db:
            exhausted = (
                db.query(BreadthAlertDelivery)
                .filter(due, BreadthAlertDelivery.attempts >= BreadthAlertDelivery.max_attempts)
                .all()
            )
            for row in exhausted:
                changed = db.execute(
                    update(BreadthAlertDelivery)
                    .where(
                        BreadthAlertDelivery.id == row.id,
                        due,
                        BreadthAlertDelivery.attempts >= BreadthAlertDelivery.max_attempts,
                    )
                    .values(
                        status="failed",
                        error_code="attempts_exhausted",
                        lease_token="",
                        lease_until=None,
                        next_attempt_at=None,
                    )
                ).rowcount
                if changed:
                    db.expire_all()
                    update_hit_receipt(db, row.hit_id)
            db.commit()
            row = (
                db.query(BreadthAlertDelivery)
                .filter(due, BreadthAlertDelivery.attempts < BreadthAlertDelivery.max_attempts)
                .order_by(BreadthAlertDelivery.id)
                .first()
            )
            if row is None:
                return None
            token = uuid4().hex
            changed = db.execute(
                update(BreadthAlertDelivery)
                .where(BreadthAlertDelivery.id == row.id, due)
                .values(
                    status="sending",
                    lease_token=token,
                    lease_until=now + timedelta(seconds=90),
                    attempts=BreadthAlertDelivery.attempts + 1,
                )
            ).rowcount
            if not changed:
                db.rollback()
                return None
            db.commit()
            db.refresh(row)
            channel = db.get(NotifyChannel, row.channel_id) if row.channel_id is not None else None
            return {
                "id": row.id,
                "hit_id": row.hit_id,
                "token": token,
                "title": row.title,
                "content": row.content,
                "channel_type": channel.type if channel else None,
                "config": dict(channel.config or {}) if channel else {},
                "available": bool(channel and channel.enabled and db.get(BreadthAlertHit, row.hit_id)),
            }

    def finish(self, claim, *, ok=False, error=""):
        with self.session_factory() as db:
            row = db.get(BreadthAlertDelivery, claim["id"])
            if row is None or row.status != "sending" or row.lease_token != claim["token"]:
                return
            now = now_utc()
            status = (
                "delivered"
                if ok
                else "blocked"
                if error == "channel_unavailable"
                else "failed"
                if row.attempts >= row.max_attempts
                else "retry"
            )
            next_at = (
                now + timedelta(seconds=min(1800, 30 * 2 ** min(row.attempts - 1, 6)))
                if status == "retry"
                else None
            )
            changed = db.execute(
                update(BreadthAlertDelivery)
                .where(
                    BreadthAlertDelivery.id == row.id,
                    BreadthAlertDelivery.status == "sending",
                    BreadthAlertDelivery.lease_token == claim["token"],
                )
                .values(
                    status=status,
                    error_code=error,
                    delivered_at=now if ok else None,
                    next_attempt_at=next_at,
                    lease_token="",
                    lease_until=None,
                )
            ).rowcount
            if changed:
                db.expire_all()
                update_hit_receipt(db, claim["hit_id"])
            db.commit()

    async def drain(self, limit=5):
        processed = 0
        for _ in range(limit):
            claim = await asyncio.to_thread(self.claim)
            if claim is None:
                break
            if not claim["available"]:
                self.finish(claim, error="channel_unavailable")
            else:
                manager = NotifierManager()
                manager.add_channel(claim["channel_type"], claim["config"])
                try:
                    result = await asyncio.wait_for(
                        manager.notify_with_result(claim["title"], claim["content"]),
                        timeout=45,
                    )
                    self.finish(
                        claim,
                        ok=bool(result.get("success")),
                        error="" if result.get("success") else "channel_send_failed",
                    )
                except TimeoutError:
                    self.finish(claim, error="delivery_timeout")
                except Exception:
                    self.finish(claim, error="channel_send_failed")
            processed += 1
        return {"processed": processed}
