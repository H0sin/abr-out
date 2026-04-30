from __future__ import annotations

import asyncio

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.common.logging import logger, setup_logging
from app.common.settings import get_settings
from app.worker.jobs.aggregate_ping import aggregate_pings_once
from app.worker.jobs.broadcast import broadcast_tick
from app.worker.jobs.enforce_balance import enforce_balances_once
from app.worker.jobs.poll_traffic import poll_traffic_once


async def main() -> None:
    setup_logging()
    settings = get_settings()
    scheduler = AsyncIOScheduler(timezone="UTC")

    scheduler.add_job(
        poll_traffic_once,
        "interval",
        seconds=settings.traffic_poll_interval_sec,
        id="poll_traffic",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        enforce_balances_once,
        "interval",
        seconds=max(settings.traffic_poll_interval_sec, 60),
        id="enforce_balance",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        aggregate_pings_once,
        "interval",
        minutes=5,
        id="aggregate_ping",
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        broadcast_tick,
        "interval",
        seconds=5,
        id="broadcast",
        max_instances=1,
        coalesce=True,
    )

    scheduler.start()
    logger.info("Worker scheduler started")
    try:
        # park forever
        await asyncio.Event().wait()
    finally:
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    asyncio.run(main())
