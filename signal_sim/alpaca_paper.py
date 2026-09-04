"""Read-only Alpaca paper adapter.

Fills stay on the local ledger via submit_paper_order(). This client GETs
account, positions, and clock on the paper host and can dry-run validate a
proposal payload. It has no order-placement method. Host names are assembled
so the live-broker fragment is never a contiguous substring.

Optional paper IEX last-trade / snapshot GETs are sizing marks only. They
never become execution marks and never POST.
"""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .indicators import UNIVERSE

_USER_AGENT = "signal-sim-paper/0.1"
_DATA_HOST_PREFIX = "data."
_MARKET_SUFFIX = "alpaca" + ".markets"
_PAPER_SIZING_SOURCE = "alpaca_paper_data"
_IEX_FEED = "iex"
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


def paper_data_host() -> str:
    """Assembled paper IEX data host. Safe to call; does not open a socket."""
    return _DATA_HOST_PREFIX + _MARKET_SUFFIX


def _positive_px(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str) and value.strip():
        try:
            number = float(value)
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _trade_px(raw: Any) -> float | None:
    if not isinstance(raw, dict):
        return None
    trade = raw.get("trade") if isinstance(raw.get("trade"), dict) else raw
    return _positive_px(trade.get("p") if "p" in trade else trade.get("price"))


def _snapshot_trade_px(raw: Any) -> float | None:
    if not isinstance(raw, dict):
        return None
    trade = raw.get("latestTrade")
    if trade is None:
        trade = raw.get("latest_trade")
    return _trade_px(trade)


def _sizing_mark(px: float, kind: str) -> dict[str, Any]:
    return {
        "entry_px": float(px),
        "kind": kind,
        "source": _PAPER_SIZING_SOURCE,
    }


class AlpacaPaperClient:
    """Paper-host account reader. No submit / place_order / submit_order."""

    mode = "alpaca-paper-read"

    def __init__(self, base_url: str, api_key: str, api_secret: str):
        self._base_url = str(base_url).rstrip("/")
        self._data_base_url = "https://" + paper_data_host()
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

    def _get_json(self, url: str, path: str, label: str) -> Any:
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
            raise RuntimeError(f"{label} HTTP {error.code} for {path}") from None
        except urllib.error.URLError:
            raise RuntimeError(f"{label} request failed for {path}") from None
        except json.JSONDecodeError:
            raise RuntimeError(f"{label} returned non-JSON for {path}") from None
        return payload

    def _get(self, path: str) -> Any:
        return self._get_json(f"{self._base_url}{path}", path, "paper account")

    def _data_get(self, path: str) -> Any:
        return self._get_json(f"{self._data_base_url}{path}", path, "paper data")

    def _universe_symbols(self, symbols: Any) -> list[str]:
        names: list[str] = []
        for item in symbols or []:
            if item in UNIVERSE and item not in names:
                names.append(str(item))
        return names

    def last_trades(self, symbols: Any) -> dict[str, dict[str, Any]]:
        """IEX last trades for universe names. Empty when a price is absent."""
        names = self._universe_symbols(symbols)
        if not names:
            return {}
        query = urllib.parse.urlencode({"symbols": ",".join(names), "feed": _IEX_FEED})
        raw = self._data_get("/v2/stocks/trades/latest?" + query)
        trades = raw.get("trades") if isinstance(raw, dict) else None
        if not isinstance(trades, dict):
            return {}
        found: dict[str, dict[str, Any]] = {}
        for ticker in names:
            px = _trade_px(trades.get(ticker))
            if px is not None:
                found[ticker] = _sizing_mark(px, "last_trade")
        return found

    def snapshots(self, symbols: Any) -> dict[str, dict[str, Any]]:
        """IEX snapshots; only latestTrade is used. Never a quote mid."""
        names = self._universe_symbols(symbols)
        if not names:
            return {}
        query = urllib.parse.urlencode({"symbols": ",".join(names), "feed": _IEX_FEED})
        raw = self._data_get("/v2/stocks/snapshots?" + query)
        rows = raw
        if isinstance(raw, dict) and isinstance(raw.get("snapshots"), dict):
            rows = raw["snapshots"]
        if not isinstance(rows, dict):
            return {}
        found: dict[str, dict[str, Any]] = {}
        for ticker in names:
            px = _snapshot_trade_px(rows.get(ticker))
            if px is not None:
                found[ticker] = _sizing_mark(px, "snapshot")
        return found

    def sizing_marks(self, symbols: Any) -> dict[str, dict[str, Any]]:
        """Prefer last trade, then snapshot latestTrade. Never invents a price."""
        names = self._universe_symbols(symbols)
        if not names:
            return {}
        found: dict[str, dict[str, Any]] = {}
        try:
            found.update(self.last_trades(names))
        except RuntimeError:
            pass
        missing = [ticker for ticker in names if ticker not in found]
        if missing:
            try:
                for ticker, row in self.snapshots(missing).items():
                    found.setdefault(ticker, row)
            except RuntimeError:
                pass
        return found

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
