"""Periodic Postgres backup job.

Runs ``pg_dump`` against the configured Postgres instance, gzips the output,
and uploads the resulting file to each configured admin chat through a
*separate* Telegram bot (``BACKUP_BOT_TOKEN``). The backup bot must have been
started by each admin (i.e. the admin sent ``/start`` to it at least once),
otherwise Telegram will refuse the upload with ``chat not found``.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import datetime, timezone

import httpx

from app.common.logging import logger
from app.common.settings import get_settings

_UPLOAD_TIMEOUT = httpx.Timeout(120.0, connect=10.0)


async def _pg_dump(out_path: str) -> bool:
    """Run pg_dump | gzip into ``out_path``. Returns True on success."""
    s = get_settings()
    env = os.environ.copy()
    env["PGPASSWORD"] = s.postgres_password
    # Plain SQL dump piped through gzip for compactness.
    cmd = (
        f'pg_dump -h "{s.postgres_host}" -p {s.postgres_port} '
        f'-U "{s.postgres_user}" -d "{s.postgres_db}" '
        f'--no-owner --no-privileges | gzip -9 > "{out_path}"'
    )
    proc = await asyncio.create_subprocess_shell(
        cmd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.error(
            "pg_dump failed (rc={}): {}",
            proc.returncode,
            (stderr or b"").decode(errors="replace")[:1000],
        )
        return False
    return True


async def _send_document(token: str, chat_id: int, path: str, caption: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    try:
        with open(path, "rb") as fh:
            files = {"document": (os.path.basename(path), fh, "application/gzip")}
            data = {"chat_id": str(chat_id), "caption": caption}
            async with httpx.AsyncClient(timeout=_UPLOAD_TIMEOUT) as client:
                r = await client.post(url, data=data, files=files)
        if r.status_code != 200 or not r.json().get("ok"):
            logger.error(
                "Backup upload to {} failed: status={} body={}",
                chat_id,
                r.status_code,
                r.text[:500],
            )
            return False
        return True
    except Exception:
        logger.exception("Backup upload to {} crashed", chat_id)
        return False


async def backup_db_once() -> None:
    """Take one DB snapshot and ship it to every admin chat."""
    s = get_settings()
    token = s.backup_bot_token.strip()
    if not token:
        return
    admin_ids = s.admin_ids
    if not admin_ids:
        logger.warning("Backup job: no ADMIN_TELEGRAM_IDS configured; skipping")
        return

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    fname = f"{s.postgres_db}-{ts}.sql.gz"
    tmpdir = tempfile.mkdtemp(prefix="abrout-backup-")
    out_path = os.path.join(tmpdir, fname)

    try:
        ok = await _pg_dump(out_path)
        if not ok:
            return
        size_mb = os.path.getsize(out_path) / (1024 * 1024)
        caption = (
            f"🗄 پشتیبان دیتابیس\n"
            f"DB: <code>{s.postgres_db}</code>\n"
            f"زمان: {ts}\n"
            f"حجم: {size_mb:.2f} MB"
        )
        for chat_id in admin_ids:
            await _send_document(token, chat_id, out_path, caption)
        logger.info("DB backup delivered to {} admin(s) ({:.2f} MB)", len(admin_ids), size_mb)
    finally:
        try:
            if os.path.exists(out_path):
                os.remove(out_path)
            os.rmdir(tmpdir)
        except OSError:
            pass
