from __future__ import annotations

from fastapi import Header, HTTPException, status

from app.common.settings import get_settings


async def require_internal_token(
    x_internal_token: str | None = Header(default=None),
) -> None:
    expected = get_settings().api_internal_token
    if not x_internal_token or x_internal_token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid internal token"
        )
