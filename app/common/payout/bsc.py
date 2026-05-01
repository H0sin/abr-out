"""Thin wrapper around web3.py for sending USDT-BEP20 from a hot wallet.

The hot wallet's private key is loaded from settings (env). This module is
deliberately kept synchronous internally — web3.py's HTTP provider is sync —
and exposes async methods that off-load the blocking calls to a thread.

Fee model: BEP20 ``transfer`` consumes ~50–60k gas; we budget
``withdrawal_gas_limit`` (default 80k) and multiply by a configurable
``withdrawal_fee_buffer_pct`` to absorb gas-price spikes between the quote
and broadcast. Fees are converted to USD using a cached BNB/USDT spot price.
"""
from __future__ import annotations

import asyncio
import time
from decimal import ROUND_DOWN, Decimal
from typing import Final

import httpx
from eth_account import Account
from web3 import Web3
from web3.exceptions import ContractLogicError
from web3.types import TxReceipt

from app.common.logging import logger
from app.common.settings import get_settings

# Minimal ABI for ERC-20 / BEP-20 ``transfer`` + ``decimals`` + ``balanceOf``.
_USDT_ABI: Final = [
    {
        "constant": False,
        "inputs": [
            {"name": "_to", "type": "address"},
            {"name": "_value", "type": "uint256"},
        ],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    },
]

_BNB_PRICE_TTL_SEC: Final = 60.0
_bnb_price_cache: dict[str, float] = {"price": 0.0, "ts": 0.0}
_bnb_price_lock = asyncio.Lock()


class PayoutConfigError(RuntimeError):
    """Raised when the hot wallet is not configured (missing key/RPC)."""


class PayoutAddressError(ValueError):
    """Raised when a target address fails checksum/format validation."""


class BscPayoutClient:
    """Singleton-friendly client. Cheap to construct; web3 instance is lazy."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._w3: Web3 | None = None
        self._account = None
        self._contract = None
        self._decimals: int | None = None

    # ----- lazy wiring -----

    def _w3_inst(self) -> Web3:
        if self._w3 is None:
            if not self._settings.bsc_rpc_url:
                raise PayoutConfigError("BSC_RPC_URL is not configured")
            self._w3 = Web3(
                Web3.HTTPProvider(
                    self._settings.bsc_rpc_url,
                    request_kwargs={"timeout": 15},
                )
            )
        return self._w3

    def _acct(self):
        if self._account is None:
            key = self._settings.bsc_hot_wallet_private_key.strip()
            if not key:
                raise PayoutConfigError("BSC_HOT_WALLET_PRIVATE_KEY is not configured")
            if not key.startswith("0x"):
                key = "0x" + key
            self._account = Account.from_key(key)
        return self._account

    def _usdt(self):
        if self._contract is None:
            w3 = self._w3_inst()
            address = Web3.to_checksum_address(self._settings.bsc_usdt_contract)
            self._contract = w3.eth.contract(address=address, abi=_USDT_ABI)
        return self._contract

    def _usdt_decimals(self) -> int:
        if self._decimals is None:
            try:
                self._decimals = int(self._usdt().functions.decimals().call())
            except Exception:
                # USDT-BSC is 18 decimals; fall back if RPC hiccups.
                self._decimals = 18
        return self._decimals

    # ----- public surface -----

    @staticmethod
    def is_valid_address(addr: str) -> str:
        """Return checksummed address or raise :class:`PayoutAddressError`."""
        if not isinstance(addr, str) or len(addr) != 42 or not addr.startswith("0x"):
            raise PayoutAddressError("invalid BSC address format")
        try:
            return Web3.to_checksum_address(addr)
        except Exception as e:  # pragma: no cover - defensive
            raise PayoutAddressError(f"invalid BSC address: {e}") from e

    async def estimate_fee_usd(self) -> tuple[Decimal, int]:
        """Return ``(fee_usd, gas_price_wei)`` using a live ``eth_gasPrice``
        and a cached BNB/USD spot price. Multiplies by the safety buffer
        configured in settings. Falls back to a conservative 5 gwei if RPC fails.
        """
        gas_price_wei = await asyncio.to_thread(self._gas_price_wei)
        gas_units = self._settings.withdrawal_gas_limit
        buffer = self._settings.withdrawal_fee_buffer_pct
        fee_wei = Decimal(gas_price_wei) * Decimal(gas_units) * buffer
        fee_bnb = fee_wei / Decimal(10**18)
        bnb_usd = await _get_bnb_usd_price(self._settings.bnb_price_feed_url)
        fee_usd = (fee_bnb * bnb_usd).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
        return fee_usd, int(gas_price_wei)

    def _gas_price_wei(self) -> int:
        try:
            return int(self._w3_inst().eth.gas_price)
        except Exception as exc:
            logger.warning("[BscPayout] eth_gasPrice failed: {} — falling back to 5 gwei", exc)
            return 5 * 10**9

    async def send_usdt(
        self, to_address: str, amount_usdt: Decimal, gas_price_wei: int | None = None
    ) -> str:
        """Build, sign and broadcast a USDT BEP-20 transfer. Returns the tx hash hex."""
        return await asyncio.to_thread(
            self._send_usdt_sync, to_address, amount_usdt, gas_price_wei
        )

    def _send_usdt_sync(
        self, to_address: str, amount_usdt: Decimal, gas_price_wei: int | None
    ) -> str:
        w3 = self._w3_inst()
        acct = self._acct()
        usdt = self._usdt()
        decimals = self._usdt_decimals()
        to = Web3.to_checksum_address(to_address)

        # Convert USDT decimal to integer base units (18 decimals on BSC).
        scale = Decimal(10**decimals)
        amount_units = int((amount_usdt * scale).to_integral_value(rounding=ROUND_DOWN))
        if amount_units <= 0:
            raise ValueError("amount_usdt must be > 0 after scaling")

        nonce = w3.eth.get_transaction_count(acct.address)
        gp = int(gas_price_wei) if gas_price_wei else self._gas_price_wei()

        try:
            tx = usdt.functions.transfer(to, amount_units).build_transaction(
                {
                    "chainId": int(w3.eth.chain_id),
                    "from": acct.address,
                    "nonce": nonce,
                    "gas": self._settings.withdrawal_gas_limit,
                    "gasPrice": gp,
                }
            )
        except ContractLogicError as e:
            raise RuntimeError(f"transfer build failed: {e}") from e

        signed = acct.sign_transaction(tx)
        raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
        tx_hash = w3.eth.send_raw_transaction(raw)
        return tx_hash.hex()

    async def get_receipt(self, tx_hash: str) -> TxReceipt | None:
        return await asyncio.to_thread(self._get_receipt_sync, tx_hash)

    def _get_receipt_sync(self, tx_hash: str) -> TxReceipt | None:
        try:
            return self._w3_inst().eth.get_transaction_receipt(tx_hash)
        except Exception:
            return None

    @property
    def hot_wallet_address(self) -> str:
        return self._acct().address


async def _get_bnb_usd_price(url: str) -> Decimal:
    """Fetch BNB/USDT spot price from the configured ticker URL. Cached 60s.

    Falls back to a conservative ``300`` if the call fails — the resulting
    fee quote will be slightly low but withdrawals still proceed; the worker
    re-fetches gas at broadcast time.
    """
    now = time.time()
    cached = _bnb_price_cache["price"]
    if cached and now - _bnb_price_cache["ts"] < _BNB_PRICE_TTL_SEC:
        return Decimal(str(cached))

    async with _bnb_price_lock:
        if (
            _bnb_price_cache["price"]
            and time.time() - _bnb_price_cache["ts"] < _BNB_PRICE_TTL_SEC
        ):
            return Decimal(str(_bnb_price_cache["price"]))
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.get(url)
                r.raise_for_status()
                data = r.json()
            price = float(data.get("price") or data.get("lastPrice") or 0.0)
            if price > 0:
                _bnb_price_cache["price"] = price
                _bnb_price_cache["ts"] = time.time()
                return Decimal(str(price))
        except Exception as exc:
            logger.warning("[BscPayout] BNB price fetch failed: {}", exc)
        return Decimal("300")


_default_client: BscPayoutClient | None = None


def get_payout_client() -> BscPayoutClient:
    """Process-wide :class:`BscPayoutClient` singleton."""
    global _default_client
    if _default_client is None:
        _default_client = BscPayoutClient()
    return _default_client
