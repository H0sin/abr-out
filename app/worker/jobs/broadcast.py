"""Broadcast worker: drains queued/running broadcasts respecting Telegram rate limits."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from sqlalchemy import select

from app.api.routes.admin import Audience, _audience_filters  # reuse filter logic
from app.common.db.models import Broadcast, BroadcastStatus, User
from app.common.db.session import SessionLocal
from app.common.logging import logger
from app.common.telegram_bot import send_message

# Telegram limit: ~30 msgs/sec global. Stay safely below.
_MSGS_PER_SECOND = 25


async def broadcast_tick() -> None:
    """Pick the next queued/running broadcast and process it to completion."""
    async with SessionLocal() as session:
        bc = (
            await session.execute(
                select(Broadcast)
                .where(
                    Broadcast.status.in_(
                        [BroadcastStatus.queued, BroadcastStatus.running]
                    )
                )
                .order_by(Broadcast.created_at.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if bc is None:
            return
        bc.status = BroadcastStatus.running
        bc_id = bc.id
        text = bc.text
        try:
            audience_data = json.loads(bc.audience)
        except Exception:
            audience_data = {"kind": "all"}
        await session.commit()

    audience = Audience(**audience_data)

    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(User.telegram_id).where(*_audience_filters(audience))
            )
        ).scalars().all()

    sent = 0
    failed = 0
    batch_start = asyncio.get_event_loop().time()
    batch_count = 0

    for uid in rows:
        try:
            resp = await send_message(uid, text)
            if resp and resp.get("ok"):
                sent += 1
            else:
                failed += 1
        except Exception:
            failed += 1
            logger.exception("broadcast {} send failed for {}", bc_id, uid)

        batch_count += 1
        if batch_count >= _MSGS_PER_SECOND:
            elapsed = asyncio.get_event_loop().time() - batch_start
            if elapsed < 1.0:
                await asyncio.sleep(1.0 - elapsed)
            batch_start = asyncio.get_event_loop().time()
            batch_count = 0

        # Periodically flush counters so the UI can show progress.
        if (sent + failed) % 50 == 0:
            await _update_progress(bc_id, sent, failed)

    await _finalize(bc_id, sent, failed)


async def _update_progress(bc_id: int, sent: int, failed: int) -> None:
    async with SessionLocal() as session:
        bc = await session.get(Broadcast, bc_id)
        if bc is None:
            return
        bc.sent = sent
        bc.failed = failed
        await session.commit()


async def _finalize(bc_id: int, sent: int, failed: int) -> None:
    async with SessionLocal() as session:
        bc = await session.get(Broadcast, bc_id)
        if bc is None:
            return
        bc.sent = sent
        bc.failed = failed
        bc.status = BroadcastStatus.done
        bc.finished_at = datetime.now(timezone.utc)
        await session.commit()
    logger.info("broadcast {} finished: sent={} failed={}", bc_id, sent, failed)
