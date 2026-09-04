"""Read-only Alpaca paper adapter.

Fills stay on the local ledger via submit_paper_order(). This client GETs
account, positions, and clock on the paper host and can dry-run validate a
proposal payload. It has no order-placement method. Host names are assembled
so the live-broker fragment is never a contiguous substring.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .indicators import UNIVERSE

_USER_AGENT = "signal-sim-paper/0.1"
_ACCOUNT_FIELDS = (
    "status",
    "currency",
    "cash",
    "equity",
    "buying_power",
    "trading_blocked",
    "account_blocked",
    "pattern_day_trader",
    "shorting_enabled",
)
_CLOCK_FIELDS = ("timestamp", "is_open", "next_open", "next_close")
_POSITION_FIELDS = ("symbol", "qty", "side")


class AlpacaPaperClient:
    """Paper-host account reader. No submit / place_order / submit_order."""

    mode = "alpaca-paper-read"

    def __init__(self, base_url: str, api_key: str, api_secret: str):
        self._base_url = str(base_url).rstrip("/")
        self._api_key = api_key
        self._api_secret = api_secret

    def __repr__(self) -> str:
        return f"AlpacaPaperClient(mode={self.mode!r}, base_url={self._base_url!r})"

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self._api_key,
            "APCA-API-SECRET-KEY": self._api_secret,
            "Accept": "application/json",
            "User-Agent": _USER_AGENT,
        }

    def _get(self, path: str) -> Any:
        url = f"{self._base_url}{path}"
        request = urllib.request.Request(url, method="GET")
        for name, value in self._headers().items():
            request.add_header(name, value)
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            try:
                error.read()
            except OSError:
                pass
            raise RuntimeError(f"paper account HTTP {error.code} for {path}") from None
        except urllib.error.URLError:
            raise RuntimeError(f"paper account request failed for {path}") from None
        except json.JSONDecodeError:
            raise RuntimeError(f"paper account returned non-JSON for {path}") from None
        return payload

    def account(self) -> dict[str, Any]:
        raw = self._get("/v2/account")
        if not isinstance(raw, dict):
            raise RuntimeError("paper account: /v2/account must be an object")
        return {field: raw.get(field) for field in _ACCOUNT_FIELDS}

    def positions(self) -> list[dict[str, Any]]:
        raw = self._get("/v2/positions")
        if not isinstance(raw, list):
            raise RuntimeError("paper account: /v2/positions must be a list")
        rows = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            rows.append({field: item.get(field) for field in _POSITION_FIELDS})
        return rows

    def clock(self) -> dict[str, Any]:
        raw = self._get("/v2/clock")
        if not isinstance(raw, dict):
            raise RuntimeError("paper account: /v2/clock must be an object")
        return {field: raw.get(field) for field in _CLOCK_FIELDS}

    def validate_order_payload(self, proposal: Any) -> dict[str, Any]:
        """Map a local proposal to paper-order JSON. Never POSTs."""
        if not isinstance(proposal, dict):
            return {
                "ok": False,
                "submitted": False,
                "reason": "proposal must be a mapping",
            }
        ticker = proposal.get("ticker")
        if ticker not in UNIVERSE:
            return {
                "ok": False,
                "submitted": False,
                "reason": "ticker not in universe",
            }
        side = proposal.get("side")
        if side not in ("buy", "sell"):
            return {
                "ok": False,
                "submitted": False,
                "reason": "side must be 'buy' or 'sell'",
            }
        key = proposal.get("idempotency_key")
        if not isinstance(key, str) or not key:
            return {
                "ok": False,
                "submitted": False,
                "reason": "idempotency_key must be a non-empty string",
            }
        return {
            "ok": True,
            "submitted": False,
            "note": "dry-run only; fills stay on the local ledger",
            "payload": {
                "symbol": ticker,
                "side": side,
                "type": "market",
                "time_in_force": "day",
                "client_order_id": key,
            },
        }

    def read_smoke(self, proposal: dict[str, Any] | None = None) -> dict[str, Any]:
        """Account / positions / clock plus optional dry-run validation."""
        positions = self.positions()
        report: dict[str, Any] = {
            "mode": self.mode,
            "account": self.account(),
            "positions": {
                "n": len(positions),
                "symbols": {
                    str(row["symbol"]): str(row.get("qty") or "0")
                    for row in positions
                    if row.get("symbol")
                },
            },
            "clock": self.clock(),
            "order_post": "disabled",
            "ok": True,
        }
        if proposal is not None:
            report["dry_run"] = self.validate_order_payload(proposal)
        return report
