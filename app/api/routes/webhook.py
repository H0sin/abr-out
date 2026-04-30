from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/webhook", tags=["webhook"])


@router.post("/nowpayments")
async def nowpayments_ipn() -> dict[str, str]:
    # TODO: HMAC verify, upsert payment_intent, insert wallet_transaction.
    return {"status": "todo"}
