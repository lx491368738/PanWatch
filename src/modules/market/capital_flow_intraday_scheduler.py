"""CN 个股主力净流入盘中后台采样调度。

开盘 09:30–10:00 约 15 秒一轮；其余交易时段由 per-symbol 间隔门控到约 60 秒；
午休 / 休市跳过。详情页只读缓存，不负责触发采样。
"""

from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.modules.market import capital_flow_intraday as cf_intra

logger = logging.getLogger(__name__)

# 以最短窗口 tick；单票是否落点由 sample_interval_sec / maybe_append_sample 决定。
TICK_SECONDS = 15


class CapitalFlowIntradayScheduler:
    def __init__(self, timezone: str = "UTC", interval_seconds: int = 60):
        self.scheduler = AsyncIOScheduler(timezone=timezone)
        self.interval_seconds = max(TICK_SECONDS, int(interval_seconds))
        self._running = False

    async def _sample_job(self):
        if self._running:
            logger.debug("[资金流采样] 上轮仍在执行，跳过本轮")
            return
        if not cf_intra.in_cn_trading_session():
            return

        self._running = True
        try:
            result = await asyncio.to_thread(cf_intra.sample_watchlist_once)
            sampled = int(result.get("sampled") or 0)
            checked = int(result.get("checked") or 0)
            level = logging.INFO if sampled else logging.DEBUG
            logger.log(
                level,
                "[资金流采样] 完成: sampled=%s checked=%s cadence=%ss",
                sampled,
                checked,
                cf_intra.sample_interval_sec(),
            )
        except Exception as e:
            logger.exception(f"[资金流采样] 异常: {e}")
        finally:
            self._running = False

    def start(self):
        self.scheduler.add_job(
            self._sample_job,
            "interval",
            seconds=TICK_SECONDS,
            jitter=2,
            id="capital_flow_intraday_sample",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        self.scheduler.start()
        from src.platform.scheduling.scheduler_registry import register

        register("capital_flow_intraday", self.scheduler)
        logger.info(
            "资金流盘中采样调度已启动：开盘半小时 %ss / 其余交易时段 %ss",
            cf_intra.DENSE_INTERVAL_SEC,
            cf_intra.NORMAL_INTERVAL_SEC,
        )

    def shutdown(self):
        try:
            self.scheduler.shutdown(wait=False)
        except Exception:
            pass
        logger.info("资金流盘中采样调度已关闭")
