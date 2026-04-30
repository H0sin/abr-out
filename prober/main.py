"""Iran-side prober.

Every PROBE_INTERVAL_SEC:
  1. GET {API_BASE}/internal/prober/listings  -> [{listing_id, iran_host, port}]
  2. For each target, do a TCP connect timing (handshake-only, no payload).
  3. POST samples back to {API_BASE}/internal/prober/samples.

xray-core is installed in the image but not used yet; it is reserved for a future
mode where we route the probe through a known-good upstream tunnel for L7 checks.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import httpx
from loguru import logger
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProberSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    api_base: str = "http://api:8000"
    api_internal_token: str = "change-me-internal-token"
    probe_interval_sec: int = 60
    probe_timeout_sec: float = 3.0


settings = ProberSettings()


async def tcp_ping(host: str, port: int, timeout: float) -> int | None:
    start = time.perf_counter()
    try:
        fut = asyncio.open_connection(host, port)
        reader, writer = await asyncio.wait_for(fut, timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        return int((time.perf_counter() - start) * 1000)
    except Exception as e:  # noqa: BLE001
        logger.debug("tcp_ping {}:{} failed: {}", host, port, e)
        return None


async def cycle(client: httpx.AsyncClient) -> None:
    headers = {"X-Internal-Token": settings.api_internal_token}
    try:
        r = await client.get(f"{settings.api_base}/internal/prober/listings", headers=headers)
        r.raise_for_status()
        targets = r.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("fetch listings failed: {}", e)
        return

    samples = []
    for t in targets:
        rtt = await tcp_ping(t["iran_host"], int(t["port"]), settings.probe_timeout_sec)
        samples.append(
            {
                "listing_id": t["listing_id"],
                "rtt_ms": rtt,
                "ok": rtt is not None,
                "sampled_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    if not samples:
        return
    try:
        r = await client.post(
            f"{settings.api_base}/internal/prober/samples",
            headers=headers,
            json=samples,
        )
        r.raise_for_status()
        logger.info("posted {} samples", len(samples))
    except Exception as e:  # noqa: BLE001
        logger.warning("post samples failed: {}", e)


async def main() -> None:
    logger.info(
        "Prober starting, api_base={} interval={}s",
        settings.api_base,
        settings.probe_interval_sec,
    )
    async with httpx.AsyncClient(timeout=15.0) as client:
        while True:
            await cycle(client)
            await asyncio.sleep(settings.probe_interval_sec)


if __name__ == "__main__":
    asyncio.run(main())
