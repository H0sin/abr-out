from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/topup/manual")
async def manual_topup() -> dict[str, str]:
    # TODO: admin auth + insert topup wallet_transaction.
    return {"status": "todo"}
