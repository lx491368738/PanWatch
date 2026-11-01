"""市场广度采样调度器：交易时段自动采样 + 提醒评估 + 投递。

开盘活跃窗口 09:30–10:00 约 15 秒一点；其余交易时段约 60 秒一点；午休/休市跳过。
"""

from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.modules.market import breadth_service as svc
from src.modules.market.breadth_alert_delivery import BreadthAlertDeliveryWorker
from src.modules.market.breadth_engine import ENGINE
from src.platform.marketdata.models import MARKETS, MarketCode
from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import MarketBreadthSample

logger = logging.getLogger(__name__)

# 调度器以最短窗口 tick；实际是否落点由 cadence 门控。
TICK_SECONDS = 15


class BreadthScheduler:
    def __init__(self, timezone: str = "UTC", interval_seconds: int = 60):
        self.scheduler = AsyncIOScheduler(timezone=timezone)
        # interval_seconds 保留兼容调用方；真正节奏由 open-rush / normal 决定。
        self.interval_seconds = max(TICK_SECONDS, int(interval_seconds))
        self._running = False
        self._delivering = False
        self.delivery_worker = BreadthAlertDeliveryWorker()

    async def _delivery_job(self):
        if self._delivering:
            return
        self._delivering = True
        try:
            await self.delivery_worker.drain()
        except Exception:
            logger.exception("[市场广度] 投递队列处理失败")
        finally:
            self._delivering = False

    def _should_sample_now(self) -> bool:
        if not MARKETS[MarketCode.CN].is_trading_time():
            return False
        bucket = svc.floor_sample_bucket()
        with SessionLocal() as db:
            exists = (
                db.query(MarketBreadthSample.id)
                .filter(
                    MarketBreadthSample.market == "CN",
                    MarketBreadthSample.sampled_at == bucket.isoformat(),
                )
                .first()
            )
            return exists is None

    async def _sample_job(self):
        if self._running:
            logger.debug("[市场广度] 上轮采样仍在执行，跳过本轮")
            return
        if not MARKETS[MarketCode.CN].is_trading_time():
            logger.debug("[市场广度] CN 非交易时段，跳过采样")
            return
        try:
            due = await asyncio.to_thread(self._should_sample_now)
        except Exception as e:
            logger.exception(f"[市场广度] 采样门控异常: {e}")
            return
        if not due:
            return

        self._running = True
        try:
            result = await asyncio.to_thread(ENGINE.sample_once)
            sampled = result.get("sampled")
            triggered = result.get("triggered", 0)
            level = logging.INFO if sampled or triggered else logging.DEBUG
            interval = svc.sample_interval_seconds()
            logger.log(
                level,
                "[市场广度] 采样完成: sampled=%s triggered=%s trimmed=%s cadence=%ss rush=%s",
                sampled,
                triggered,
                result.get("trimmed", 0),
                interval,
                svc.is_open_rush(),
            )
        except Exception as e:
            logger.exception(f"[市场广度] 采样异常: {e}")
        finally:
            self._running = False

    def start(self):
        self.scheduler.add_job(
            self._delivery_job,
            "interval",
            seconds=10,
            id="breadth_alert_delivery",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        self.scheduler.add_job(
            self._sample_job,
            "interval",
            seconds=TICK_SECONDS,
            jitter=3,
            id="breadth_sample",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        self.scheduler.start()
        from src.platform.scheduling.scheduler_registry import register

        register("breadth", self.scheduler)
        logger.info(
            "市场广度调度器已启动：开盘半小时 %ss / 其余交易时段 %ss",
            svc.OPEN_RUSH_INTERVAL_SEC,
            svc.NORMAL_INTERVAL_SEC,
        )

    def shutdown(self):
        try:
            self.scheduler.shutdown(wait=False)
        except Exception:
            pass
        logger.info("市场广度调度器已关闭")
