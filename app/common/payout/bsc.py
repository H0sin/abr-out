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
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import Any, Final

import httpx
from eth_account import Account
from web3 import Web3
from web3.exceptions import ContractLogicError
from web3.types import TxReceipt

# Addresses that must never receive a withdrawal: zero address, the USDT
# contract itself (sending USDT to its own contract burns it on most BEP20
# implementations), and BSC's standard burn address.
_FORBIDDEN_ADDRESSES: Final = {
    "0x0000000000000000000000000000000000000000",
    "0x000000000000000000000000000000000000dEaD".lower(),
}

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


@dataclass(frozen=True)
class SignedTransfer:
    """A signed BEP20 ``transfer`` ready to broadcast. ``tx_hash`` is the
    deterministic keccak256 of the signed envelope; it's known *before* the
    raw tx ever hits the network and is safe to persist as the canonical
    on-chain identifier."""

    tx_hash: str
    raw_tx: bytes
    nonce: int
    gas_price_wei: int


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

    def is_valid_address(self, addr: str) -> str:
        """Return checksummed address or raise :class:`PayoutAddressError`.

        Rejects the zero address, common burn addresses, the USDT contract
        itself, and (when configured) the hot wallet's own address.
        """
        if not isinstance(addr, str) or len(addr) != 42 or not addr.startswith("0x"):
            raise PayoutAddressError("invalid BSC address format")
        try:
            checksum = Web3.to_checksum_address(addr)
        except Exception as e:  # pragma: no cover - defensive
            raise PayoutAddressError(f"invalid BSC address: {e}") from e
        lower = checksum.lower()
        if lower in _FORBIDDEN_ADDRESSES:
            raise PayoutAddressError("forbidden destination address")
        if lower == self._settings.bsc_usdt_contract.lower():
            raise PayoutAddressError("cannot withdraw to the USDT contract address")
        # Self-send to the hot wallet is almost certainly a misconfiguration.
        try:
            hot = self._acct().address.lower()
        except PayoutConfigError:
            hot = ""
        if hot and lower == hot:
            raise PayoutAddressError("cannot withdraw to the hot wallet address")
        return checksum

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

    async def sign_transfer(
        self, to_address: str, amount_usdt: Decimal, gas_price_wei: int | None = None
    ) -> SignedTransfer:
        """Build and sign a USDT BEP-20 transfer **without broadcasting**.

        Returns a :class:`SignedTransfer`. The ``tx_hash`` is final — the
        caller can persist it before the raw tx is sent, eliminating the
        "already-broadcast but lost the response" double-spend window.
        """
        return await asyncio.to_thread(
            self._sign_transfer_sync, to_address, amount_usdt, gas_price_wei
        )

    def _sign_transfer_sync(
        self, to_address: str, amount_usdt: Decimal, gas_price_wei: int | None
    ) -> SignedTransfer:
        w3 = self._w3_inst()
        acct = self._acct()
        usdt = self._usdt()
        decimals = self._usdt_decimals()
        to = Web3.to_checksum_address(to_address)

        scale = Decimal(10**decimals)
        amount_units = int((amount_usdt * scale).to_integral_value(rounding=ROUND_DOWN))
        if amount_units <= 0:
            raise ValueError("amount_usdt must be > 0 after scaling")

        # Use the pending block so concurrent in-flight sends from the same
        # hot wallet don't collide on nonce.
        nonce = w3.eth.get_transaction_count(acct.address, "pending")
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
        # web3.py exposes the hash on the signed envelope; fall back to keccak
        # if the attribute is absent.
        try:
            tx_hash = signed.hash.hex()
        except Exception:  # pragma: no cover - defensive
            tx_hash = Web3.keccak(raw).hex()
        if not tx_hash.startswith("0x"):
            tx_hash = "0x" + tx_hash
        return SignedTransfer(
            tx_hash=tx_hash, raw_tx=bytes(raw), nonce=int(nonce), gas_price_wei=int(gp)
        )

    async def broadcast_raw(self, raw_tx: bytes) -> str:
        """Broadcast a previously-signed raw tx. Returns the tx hash hex.

        ``already known`` and ``nonce too low`` errors are *not* fatal: if a
        previous attempt already injected the same tx into the mempool the
        send is still durable. The caller should always rely on the
        deterministic hash from :meth:`sign_transfer`, not the return value.
        """
        return await asyncio.to_thread(self._broadcast_raw_sync, raw_tx)

    def _broadcast_raw_sync(self, raw_tx: bytes) -> str:
        h = self._w3_inst().eth.send_raw_transaction(raw_tx)
        return h.hex()

    async def get_receipt(self, tx_hash: str) -> TxReceipt | None:
        return await asyncio.to_thread(self._get_receipt_sync, tx_hash)

    def _get_receipt_sync(self, tx_hash: str) -> TxReceipt | None:
        try:
            return self._w3_inst().eth.get_transaction_receipt(tx_hash)
        except Exception:
            return None

    async def get_transaction(self, tx_hash: str) -> dict[str, Any] | None:
        """Return the tx object (mined or pending). ``None`` means the node
        has dropped the tx from its mempool — almost always a permanent loss.
        """
        return await asyncio.to_thread(self._get_transaction_sync, tx_hash)

    def _get_transaction_sync(self, tx_hash: str) -> dict[str, Any] | None:
        try:
            return dict(self._w3_inst().eth.get_transaction(tx_hash))
        except Exception:
            return None

    async def usdt_balance(self) -> Decimal:
        """Hot wallet's USDT balance in human units."""
        return await asyncio.to_thread(self._usdt_balance_sync)

    def _usdt_balance_sync(self) -> Decimal:
        try:
            raw = int(self._usdt().functions.balanceOf(self._acct().address).call())
        except Exception as exc:  # pragma: no cover - network
            logger.warning("[BscPayout] balanceOf failed: {}", exc)
            return Decimal(0)
        return (Decimal(raw) / Decimal(10**self._usdt_decimals())).quantize(
            Decimal("0.00000001"), rounding=ROUND_DOWN
        )

    async def bnb_balance_wei(self) -> int:
        return await asyncio.to_thread(self._bnb_balance_wei_sync)

    def _bnb_balance_wei_sync(self) -> int:
        try:
            return int(self._w3_inst().eth.get_balance(self._acct().address))
        except Exception as exc:  # pragma: no cover - network
            logger.warning("[BscPayout] get_balance failed: {}", exc)
            return 0

    @property
    def hot_wallet_address(self) -> str:
        return self._acct().address

    # ----- read-only history (RPC fallback for admin wallet view) -----

    async def list_recent_token_transfers(
        self, lookback_blocks: int = 100_000, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Return recent USDT transfers touching the hot wallet via ``eth_getLogs``.

        Each item is a dict with keys: ``hash``, ``from``, ``to``, ``amount``
        (Decimal), ``block``, ``timestamp`` (int seconds, best-effort), and
        ``log_index``. Sorted newest-first. Used as a fallback when BscScan
        is unavailable.
        """
        return await asyncio.to_thread(
            self._list_recent_token_transfers_sync, lookback_blocks, limit
        )

    def _list_recent_token_transfers_sync(
        self, lookback_blocks: int, limit: int
    ) -> list[dict[str, Any]]:
        w3 = self._w3_inst()
        try:
            addr = self._acct().address
        except PayoutConfigError:
            return []
        contract_addr = Web3.to_checksum_address(self._settings.bsc_usdt_contract)
        # keccak256("Transfer(address,address,uint256)")
        transfer_topic = (
            "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
        )
        try:
            head = int(w3.eth.block_number)
        except Exception as exc:
            logger.warning("[BscPayout] block_number failed: {}", exc)
            return []
        addr_topic = "0x" + addr.lower().replace("0x", "").rjust(64, "0")
        decimals = self._usdt_decimals()
        scale = Decimal(10**decimals)

        # Public BSC RPCs cap eth_getLogs to a few thousand blocks per call
        # (typically 5k) AND throttle by request rate (-32005 "limit
        # exceeded"). Walk backward in chunks, throttle between calls, and
        # retry with smaller chunks on rate-limit errors.
        CHUNK = 2_000
        MIN_CHUNK = 250
        INTER_CALL_SLEEP = 0.25  # seconds between getLogs calls
        RATE_LIMIT_BACKOFF = 1.5  # seconds when rpc says "limit exceeded"
        budget = max(CHUNK, int(lookback_blocks))
        events: list[dict[str, Any]] = []
        scanned = 0
        cursor = head
        chunk_size = CHUNK

        def _is_rate_limit(err: Exception) -> bool:
            msg = str(err).lower()
            return "limit exceeded" in msg or "-32005" in msg or "rate" in msg

        while cursor > 0 and scanned < budget and len(events) < max(1, int(limit)) * 2:
            chunk_to = cursor
            chunk_from = max(0, cursor - chunk_size + 1)
            rate_limited_this_round = False
            for topics in (
                [transfer_topic, addr_topic, None],
                [transfer_topic, None, addr_topic],
            ):
                # Two attempts: first at current chunk_size, second at half
                # chunk_size if the node complains about size/rate.
                attempts = 0
                cur_from = chunk_from
                cur_to = chunk_to
                while attempts < 2:
                    try:
                        logs = w3.eth.get_logs(
                            {
                                "fromBlock": cur_from,
                                "toBlock": cur_to,
                                "address": contract_addr,
                                "topics": topics,
                            }
                        )
                        break
                    except Exception as exc:
                        if _is_rate_limit(exc):
                            rate_limited_this_round = True
                            time.sleep(RATE_LIMIT_BACKOFF)
                            # On retry, narrow the upper half only.
                            mid = (cur_from + cur_to) // 2
                            cur_from = mid + 1
                            attempts += 1
                            continue
                        logger.warning(
                            "[BscPayout] eth_getLogs {}-{} failed: {}",
                            cur_from,
                            cur_to,
                            exc,
                        )
                        logs = []
                        break
                else:
                    logs = []
                for lg in logs:
                    try:
                        raw_topics = lg["topics"]
                        frm = "0x" + raw_topics[1].hex()[-40:]
                        to = "0x" + raw_topics[2].hex()[-40:]
                        data_hex = lg["data"]
                        if hasattr(data_hex, "hex"):
                            data_hex = data_hex.hex()
                        raw_amount = int(data_hex, 16) if data_hex else 0
                        block_num = int(lg["blockNumber"])
                        events.append(
                            {
                                "hash": lg["transactionHash"].hex()
                                if hasattr(lg["transactionHash"], "hex")
                                else str(lg["transactionHash"]),
                                "from": Web3.to_checksum_address(frm),
                                "to": Web3.to_checksum_address(to),
                                "amount": (Decimal(raw_amount) / scale).quantize(
                                    Decimal("0.00000001"), rounding=ROUND_DOWN
                                ),
                                "block": block_num,
                                "log_index": int(lg.get("logIndex", 0) or 0),
                                "timestamp": None,
                            }
                        )
                    except Exception as exc:  # pragma: no cover - defensive
                        logger.warning("[BscPayout] log parse failed: {}", exc)
                        continue
                # Polite throttle between calls to avoid -32005 spam.
                time.sleep(INTER_CALL_SLEEP)
            scanned += chunk_to - chunk_from + 1
            cursor = chunk_from - 1
            # Adapt chunk size: shrink on rate limit, grow back slowly.
            if rate_limited_this_round:
                chunk_size = max(MIN_CHUNK, chunk_size // 2)
            elif chunk_size < CHUNK:
                chunk_size = min(CHUNK, chunk_size * 2)

        # Dedupe by (hash, log_index) — incoming/outgoing self-transfers
        # would otherwise appear twice.
        seen: set[tuple[str, int]] = set()
        unique: list[dict[str, Any]] = []
        for e in events:
            k = (e["hash"], e["log_index"])
            if k in seen:
                continue
            seen.add(k)
            unique.append(e)
        unique.sort(key=lambda e: (e["block"], e["log_index"]), reverse=True)
        unique = unique[: max(1, int(limit))]

        # Best-effort timestamp lookup for the small page we're returning.
        for e in unique:
            try:
                blk = w3.eth.get_block(e["block"])
                e["timestamp"] = int(blk["timestamp"])
            except Exception:
                e["timestamp"] = None
        return unique


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
