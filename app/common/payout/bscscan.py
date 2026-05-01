"""Read-only client for the BscScan public API.

Used by the admin "withdrawal wallet" page to fetch the hot wallet's on-chain
transaction history (native BNB + BEP20 USDT transfers). Falls back to RPC
log scanning at the call site when the API is unavailable / unkeyed.

The free tier permits 5 req/sec. We cache short-window results in process
memory so multiple admins refreshing simultaneously don't burn quota.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Final

import httpx

from app.common.logging import logger
from app.common.settings import get_settings

_CACHE_TTL_SEC: Final = 15.0


class BscScanError(RuntimeError):
    """Raised when BscScan responds with an error or is unreachable."""


@dataclass(frozen=True)
class _CacheEntry:
    ts: float
    data: list[dict[str, Any]]


class BscScanClient:
    """Thin async wrapper around the BscScan public API."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._cache: dict[str, _CacheEntry] = {}
        self._lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return bool(self._settings.bscscan_api_key.strip())

    async def list_native_txs(
        self, address: str, page: int = 1, offset: int = 25
    ) -> list[dict[str, Any]]:
        """Return native BNB transactions (in/out) for ``address``."""
        return await self._call(
            {
                "module": "account",
                "action": "txlist",
                "address": address,
                "page": page,
                "offset": offset,
                "sort": "desc",
            }
        )

    async def list_token_txs(
        self,
        address: str,
        contract: str,
        page: int = 1,
        offset: int = 25,
    ) -> list[dict[str, Any]]:
        """Return BEP20 token transfers for ``address`` filtered to ``contract``."""
        return await self._call(
            {
                "module": "account",
                "action": "tokentx",
                "address": address,
                "contractaddress": contract,
                "page": page,
                "offset": offset,
                "sort": "desc",
            }
        )

    # ----- internals -----

    async def _call(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        if not self.configured:
            raise BscScanError("BSCSCAN_API_KEY is not configured")
        key = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        now = time.time()
        cached = self._cache.get(key)
        if cached and now - cached.ts < _CACHE_TTL_SEC:
            return cached.data

        async with self._lock:
            cached = self._cache.get(key)
            if cached and time.time() - cached.ts < _CACHE_TTL_SEC:
                return cached.data
            # The legacy api.bscscan.com V1 endpoint is deprecated. We always
            # talk to Etherscan's unified V2 API and pin chainid=56 (BSC).
            full = {
                **params,
                "chainid": 56,
                "apikey": self._settings.bscscan_api_key.strip(),
            }
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    r = await client.get(self._settings.bscscan_base_url, params=full)
                    r.raise_for_status()
                    body = r.json()
            except Exception as exc:
                logger.warning("[BscScan] HTTP error: {}", exc)
                raise BscScanError(str(exc)) from exc

            status = str(body.get("status", "0"))
            result = body.get("result")
            # status="0" + message="No transactions found" is a valid empty result.
            if status != "1":
                msg = str(body.get("message", ""))
                if isinstance(result, list):
                    self._cache[key] = _CacheEntry(time.time(), [])
                    return []
                if "no transactions" in msg.lower():
                    self._cache[key] = _CacheEntry(time.time(), [])
                    return []
                raise BscScanError(f"BscScan: {msg or 'unknown error'} ({result!r})")
            data = list(result) if isinstance(result, list) else []
            self._cache[key] = _CacheEntry(time.time(), data)
            return data


_default_client: BscScanClient | None = None


def get_bscscan_client() -> BscScanClient:
    global _default_client
    if _default_client is None:
        _default_client = BscScanClient()
    return _default_client
