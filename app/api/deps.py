from __future__ import annotations

import logging

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.common.db.models import User
from app.common.db.session import SessionLocal
from app.common.settings import get_settings
from app.common.telegram import verify_init_data

log = logging.getLogger(__name__)


async def require_internal_token(
    x_internal_token: str | None = Header(default=None),
) -> None:
    expected = get_settings().api_internal_token
    if not x_internal_token or x_internal_token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid internal token"
        )


async def current_user(
    authorization: str | None = Header(default=None),
) -> User:
    """
    Validate `Authorization: tma <initData>` from the Telegram Mini App and
    return (upserting if needed) the corresponding User row. Blocks 403s.
    """
    if not authorization or not authorization.lower().startswith("tma "):
        log.warning("auth failed: missing/invalid Authorization header (header=%r)", authorization)
        raise HTTPException(status_code=401, detail="missing tma auth")

    init_data = authorization[4:].strip()
    settings = get_settings()
    if not settings.bot_token:
        log.error("auth failed: bot_token not configured")
        raise HTTPException(status_code=500, detail="bot not configured")

    try:
        payload = verify_init_data(init_data, settings.bot_token)
    except ValueError as e:
        log.warning(
            "auth failed: %s (initData length=%d, preview=%r)",
            e,
            len(init_data),
            init_data[:80],
        )
        raise HTTPException(status_code=401, detail=f"invalid initData: {e}") from e

    tg_user = payload.get("user")
    if not isinstance(tg_user, dict) or "id" not in tg_user:
        raise HTTPException(status_code=401, detail="no user in initData")

    telegram_id = int(tg_user["id"])
    username = tg_user.get("username")

    async with SessionLocal() as session:
        stmt = (
            pg_insert(User)
            .values(telegram_id=telegram_id, username=username)
            .on_conflict_do_update(
                index_elements=["telegram_id"],
                set_={"username": username},
            )
            .returning(User)
        )
        result = await session.execute(stmt)
        await session.commit()
        user = result.scalar_one()

    if user.is_blocked:
        raise HTTPException(status_code=403, detail="account_blocked")
    return user


async def current_admin(user: User = Depends(current_user)) -> User:
    """Require the caller to be one of the configured admins."""
    if user.telegram_id not in get_settings().admin_ids:
        raise HTTPException(status_code=403, detail="admin only")
    return user

