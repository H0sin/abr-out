"""Minimal async client for 3x-ui HTTP API.

This is a thin skeleton — endpoints are filled in lazily as features need them.
3x-ui exposes endpoints under a configurable web base path with cookie-based auth.
For MVP we assume the default base path is `/` (no panel base path). If the panel
is configured with a base path, prepend it to `XUI_BASE_URL` in the env.

Refs (3x-ui project):
  POST /login                                 -> sets `3x-ui` session cookie
  GET  /panel/api/inbounds/list               -> list all inbounds
  POST /panel/api/inbounds/add                -> add inbound (json body)
  POST /panel/api/inbounds/del/{id}           -> delete inbound
  POST /panel/api/inbounds/addClient          -> add client to inbound
  POST /panel/api/inbounds/{id}/delClient/{uuid}  -> delete client
  POST /panel/api/inbounds/updateClient/{uuid}    -> update client (enable/disable etc.)
  GET  /panel/api/inbounds/getClientTrafficsById/{id}
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx
from loguru import logger

from app.common.settings import get_settings


class XuiError(RuntimeError):
    pass


@dataclass(slots=True)
class ClientTraffic:
    email: str
    up: int
    down: int

    @property
    def total(self) -> int:
        return self.up + self.down


@dataclass(slots=True)
class InboundSnapshot:
    """Per-inbound traffic snapshot: panel-level up/down plus per-client stats.

    All byte counters are cumulative on the panel since the last reset.
    """

    inbound_id: int
    up: int
    down: int
    enable: bool
    clients: list[ClientTraffic]

    @property
    def total(self) -> int:
        return self.up + self.down


class XuiClient:
    def __init__(
        self,
        base_url: str | None = None,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        s = get_settings()
        self._base_url = (base_url or s.xui_base_url).rstrip("/")
        self._username = username or s.xui_username
        self._password = password or s.xui_password
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=15.0)
        self._logged_in = False

    async def __aenter__(self) -> "XuiClient":
        await self.login()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    # --- auth ---

    async def login(self) -> None:
        logger.info(
            "[xui] login -> base_url={} username={}",
            self._base_url,
            self._username,
        )
        try:
            resp = await self._client.post(
                "/login",
                data={"username": self._username, "password": self._password},
            )
        except httpx.HTTPError as e:
            logger.error(
                "[xui] login transport error base_url={} err={!r}",
                self._base_url,
                e,
            )
            raise
        logger.info(
            "[xui] login response status={} body={}",
            resp.status_code,
            (resp.text or "")[:500],
        )
        resp.raise_for_status()
        body = resp.json()
        if not body.get("success"):
            raise XuiError(f"3x-ui login failed: {body}")
        self._logged_in = True
        logger.debug("[xui] login ok")

    async def _request(self, method: str, path: str, **kw: Any) -> dict[str, Any]:
        if not self._logged_in:
            await self.login()
        json_body = kw.get("json")
        logger.info(
            "[xui] -> {} {} json={}",
            method,
            path,
            json.dumps(json_body, ensure_ascii=False)[:1000] if json_body is not None else None,
        )
        try:
            resp = await self._client.request(method, path, **kw)
        except httpx.HTTPError as e:
            logger.error("[xui] {} {} transport error: {!r}", method, path, e)
            raise
        if resp.status_code in (401, 403):
            logger.warning(
                "[xui] {} {} -> {} (re-login & retry)",
                method,
                path,
                resp.status_code,
            )
            await self.login()
            resp = await self._client.request(method, path, **kw)
        logger.info(
            "[xui] <- {} {} status={} body={}",
            method,
            path,
            resp.status_code,
            (resp.text or "")[:1000],
        )
        resp.raise_for_status()
        body = resp.json()
        if not body.get("success", False):
            raise XuiError(f"3x-ui {method} {path} failed: {body}")
        return body

    # --- inbounds ---

    async def list_inbounds(self) -> list[dict[str, Any]]:
        body = await self._request("GET", "/panel/api/inbounds/list")
        return body.get("obj") or []

    async def add_vless_tcp_inbound(
        self,
        port: int,
        remark: str,
        external_host: str | None = None,
        external_port: int | None = None,
    ) -> dict[str, Any]:
        """Create a VLESS TCP plain inbound. Returns the created inbound dict.

        If ``external_host`` is provided, the inbound's streamSettings will
        include an ``externalProxy`` entry pointing at ``external_host:external_port``
        (defaulting ``external_port`` to ``port``). 3x-ui uses this to build
        subscription links that route via the seller's Iran-side proxy.
        """
        logger.info(
            "[xui] add_vless_tcp_inbound port={} remark={!r} external_host={} external_port={}",
            port,
            remark,
            external_host,
            external_port,
        )
        settings_obj = {
            "clients": [],
            "decryption": "none",
            "fallbacks": [],
        }
        stream_settings: dict[str, Any] = {
            "network": "tcp",
            "security": "none",
            "tcpSettings": {"header": {"type": "none"}},
        }
        if external_host:
            stream_settings["externalProxy"] = [
                {
                    "forceTls": "same",
                    "dest": external_host,
                    "port": int(external_port or port),
                    "remark": "",
                }
            ]
        payload = {
            "up": 0,
            "down": 0,
            "total": 0,
            "remark": remark,
            "enable": True,
            "expiryTime": 0,
            "listen": "",
            "port": port,
            "protocol": "vless",
            "settings": json.dumps(settings_obj),
            "streamSettings": json.dumps(stream_settings),
            "sniffing": json.dumps({"enabled": True, "destOverride": ["http", "tls"]}),
        }
        body = await self._request("POST", "/panel/api/inbounds/add", json=payload)
        return body.get("obj") or {}

    async def delete_inbound(self, inbound_id: int) -> None:
        await self._request("POST", f"/panel/api/inbounds/del/{inbound_id}")

    # --- clients ---

    async def add_client(
        self,
        inbound_id: int,
        client_uuid: uuid.UUID,
        email: str,
        total_gb: int = 0,  # 0 = unlimited
        expiry_ms: int = 0,  # 0 = never
        enable: bool = True,
    ) -> None:
        client_obj = {
            "id": str(client_uuid),
            "email": email,
            "limitIp": 0,
            "totalGB": total_gb,
            "expiryTime": expiry_ms,
            "enable": enable,
            "tgId": "",
            "subId": "",
            "flow": "",
        }
        payload = {
            "id": inbound_id,
            "settings": json.dumps({"clients": [client_obj]}),
        }
        await self._request("POST", "/panel/api/inbounds/addClient", json=payload)

    async def update_client_enabled(
        self, inbound_id: int, client_uuid: uuid.UUID, email: str, enable: bool
    ) -> None:
        client_obj = {
            "id": str(client_uuid),
            "email": email,
            "limitIp": 0,
            "totalGB": 0,
            "expiryTime": 0,
            "enable": enable,
            "tgId": "",
            "subId": "",
            "flow": "",
        }
        payload = {
            "id": inbound_id,
            "settings": json.dumps({"clients": [client_obj]}),
        }
        await self._request(
            "POST",
            f"/panel/api/inbounds/updateClient/{client_uuid}",
            json=payload,
        )

    async def delete_client(self, inbound_id: int, client_uuid: uuid.UUID) -> None:
        await self._request(
            "POST", f"/panel/api/inbounds/{inbound_id}/delClient/{client_uuid}"
        )

    async def get_client_traffics(self, inbound_id: int) -> list[ClientTraffic]:
        """Return per-client up/down byte counters for a given inbound."""
        snap = await self.get_inbound_snapshot(inbound_id)
        return snap.clients

    async def get_inbound_snapshot(self, inbound_id: int) -> InboundSnapshot:
        """One round-trip: panel-level up/down + ``clientStats[]`` for an inbound.

        Used by the traffic poller to bill the seller (outbound totals) and
        the buyers (per-client totals) from the same point-in-time read.
        """
        inbound = await self._request(
            "GET", f"/panel/api/inbounds/get/{inbound_id}"
        )
        obj = inbound.get("obj") or {}
        clients = [
            ClientTraffic(
                email=t.get("email", ""),
                up=int(t.get("up", 0)),
                down=int(t.get("down", 0)),
            )
            for t in (obj.get("clientStats") or [])
        ]
        return InboundSnapshot(
            inbound_id=int(obj.get("id", inbound_id)),
            up=int(obj.get("up", 0)),
            down=int(obj.get("down", 0)),
            enable=bool(obj.get("enable", True)),
            clients=clients,
        )

    async def reset_inbound_clients_traffic(self, inbound_id: int) -> None:
        """Reset all client traffic counters for one inbound to zero on the panel.

        Endpoint: ``POST /panel/api/inbounds/resetAllClientTraffics/{inbound_id}``.
        Note: 3x-ui does not expose a per-inbound reset for the inbound's own
        ``up``/``down`` totals — the poller tracks those via diff instead.
        """
        await self._request(
            "POST",
            f"/panel/api/inbounds/resetAllClientTraffics/{inbound_id}",
        )


def build_vless_link(
    *, host: str, port: int, client_uuid: uuid.UUID, remark: str
) -> str:
    """Build a basic vless://... TCP plain link (no flow, no security)."""
    from urllib.parse import quote

    return (
        f"vless://{client_uuid}@{host}:{port}"
        f"?type=tcp&security=none#{quote(remark)}"
    )


def gb_from_bytes(b: int) -> Decimal:
    return Decimal(b) / Decimal(1024**3)
