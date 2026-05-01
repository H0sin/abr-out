from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.common.logging import logger, setup_logging
from app.common.settings import get_settings
from app.worker.jobs.aggregate_ping import aggregate_pings_once
from app.worker.jobs.auto_withdraw import auto_withdraw_once
from app.worker.jobs.backup_db import backup_db_once
from app.worker.jobs.broadcast import broadcast_tick
from app.worker.jobs.enforce_balance import enforce_balances_once
from app.worker.jobs.listing_quality_gate import listing_quality_gate_once
from app.worker.jobs.poll_traffic import poll_traffic_once
from app.worker.jobs.process_withdrawals import process_withdrawals_once


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
        listing_quality_gate_once,
        "interval",
        seconds=30,
        id="listing_quality_gate",
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

    scheduler.add_job(
        process_withdrawals_once,
        "interval",
        seconds=30,
        id="process_withdrawals",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        auto_withdraw_once,
        "interval",
        seconds=60,
        id="auto_withdraw",
        max_instances=1,
        coalesce=True,
    )

    if settings.backup_bot_token and settings.backup_interval_hours > 0:
        scheduler.add_job(
            backup_db_once,
            "interval",
            hours=settings.backup_interval_hours,
            id="backup_db",
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.now(timezone.utc),
        )
        logger.info(
            "DB backup job scheduled every {}h", settings.backup_interval_hours
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
