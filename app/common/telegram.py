"""Telegram WebApp (Mini App) initData verification.

Spec: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


def parse_init_data(init_data: str) -> dict[str, str]:
    return dict(parse_qsl(init_data, strict_parsing=True))


def verify_init_data(init_data: str, bot_token: str, max_age_sec: int = 86400) -> dict:
    """
    Validate Telegram WebApp initData and return the parsed payload (with `user`
    decoded as a dict). Raises ValueError on any failure.
    """
    if not init_data:
        raise ValueError("empty initData")

    parsed = parse_init_data(init_data)
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise ValueError("missing hash")

    # Optional freshness check
    auth_date = int(parsed.get("auth_date", "0"))
    if max_age_sec > 0 and auth_date > 0:
        if time.time() - auth_date > max_age_sec:
            raise ValueError("initData expired")

    # data_check_string = "\n"-joined sorted "key=value"
    data_check_string = "\n".join(
        f"{k}={parsed[k]}" for k in sorted(parsed.keys())
    )

    secret_key = hmac.new(
        b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256
    ).digest()
    computed = hmac.new(
        secret_key, data_check_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(computed, received_hash):
        raise ValueError("hash mismatch")

    # Decode user JSON if present
    if "user" in parsed:
        try:
            parsed["user"] = json.loads(parsed["user"])
        except json.JSONDecodeError as e:
            raise ValueError("invalid user json") from e

    return parsed
