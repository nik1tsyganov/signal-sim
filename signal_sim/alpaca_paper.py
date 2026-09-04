"""Alpaca paper adapter: reads plus a gated paper-host POST.

Fills on the local ledger still go through submit_paper_order(). This client
GETs account, positions, clock, and orders on the paper host. Remote POSTs
to /v2/orders require require_paper_submit() rails (flag=1, paper host,
keys, explicit CLI). Host names are assembled so the live-broker fragment
is never a contiguous substring.

Optional paper IEX last-trade / snapshot GETs are sizing marks only. They
never become execution marks.
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
_POSITION_FIELDS = (
    "symbol",
    "qty",
    "side",
    "avg_entry_price",
    "current_price",
    "market_value",
    "cost_basis",
    "unrealized_pl",
)
_ORDER_FIELDS = (
    "id",
    "client_order_id",
    "status",
    "symbol",
    "qty",
    "notional",
    "filled_qty",
    "side",
    "type",
    "time_in_force",
    "submitted_at",
    "filled_at",
)
_FILL_FIELDS = (
    "id",
    "activity_type",
    "transaction_time",
    "type",
    "price",
    "qty",
    "side",
    "symbol",
    "order_id",
    "cum_qty",
)
_PAPER_TRADING_PREFIX = "paper-api."


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


def paper_trading_host() -> str:
    """Assembled paper trading host. Safe to call; does not open a socket."""
    return _PAPER_TRADING_PREFIX + _MARKET_SUFFIX


def sanitize_order(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    return {field: raw.get(field) for field in _ORDER_FIELDS}


def sanitize_fill(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    return {field: raw.get(field) for field in _FILL_FIELDS}


def _positive_size(value: Any) -> float | None:
    number = _positive_px(value)
    return number


def _size_text(value: Any) -> str | None:
    number = _positive_size(value)
    if number is None:
        return None
    if abs(number - round(number)) < 1e-9:
        return str(int(round(number)))
    return f"{number:.6f}".rstrip("0").rstrip(".")


class AlpacaPaperClient:
    """Paper-host reader plus a gated post_paper_order. No submit / place_order."""

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

    def _request_json(
        self,
        url: str,
        path: str,
        label: str,
        *,
        method: str = "GET",
        body: Any | None = None,
    ) -> Any:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url, data=data, method=method)
        for name, value in self._headers().items():
            request.add_header(name, value)
        if body is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            code = error.code
            try:
                error.read()
            except OSError:
                pass
            raise RuntimeError(f"{label} HTTP {code} for {path}") from None
        except urllib.error.URLError:
            raise RuntimeError(f"{label} request failed for {path}") from None
        except json.JSONDecodeError:
            raise RuntimeError(f"{label} returned non-JSON for {path}") from None
        return payload

    def _get_json(self, url: str, path: str, label: str) -> Any:
        return self._request_json(url, path, label, method="GET")

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

    def _paper_host_ok(self) -> bool:
        host = (urllib.parse.urlsplit(self._base_url).hostname or "").lower()
        return host == paper_trading_host()

    def order_payload(self, proposal: Any) -> dict[str, Any]:
        """Map a proposal or ticket to paper-order JSON. Never POSTs."""
        if not isinstance(proposal, dict):
            return {
                "ok": False,
                "submitted": False,
                "reason": "proposal must be a mapping",
            }
        ticker = proposal.get("symbol") or proposal.get("ticker")
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
        existing = proposal.get("payload") if isinstance(proposal.get("payload"), dict) else {}
        key = (
            proposal.get("client_order_id")
            or existing.get("client_order_id")
            or proposal.get("idempotency_key")
        )
        if not isinstance(key, str) or not key:
            return {
                "ok": False,
                "submitted": False,
                "reason": "idempotency_key must be a non-empty string",
            }
        if len(key) > 48:
            return {
                "ok": False,
                "submitted": False,
                "reason": "client_order_id must be at most 48 characters",
            }
        qty_text = _size_text(proposal.get("qty") if proposal.get("qty") is not None else existing.get("qty"))
        notional_text = _size_text(
            proposal.get("notional") if proposal.get("notional") is not None else existing.get("notional")
        )
        if qty_text is None and notional_text is None:
            return {
                "ok": False,
                "submitted": False,
                "reason": "qty or notional must be a positive finite number",
            }
        if qty_text is not None and notional_text is not None:
            return {
                "ok": False,
                "submitted": False,
                "reason": "use qty or notional, not both",
            }
        payload = {
            "symbol": str(ticker),
            "side": side,
            "type": "market",
            "time_in_force": "day",
            "client_order_id": key,
        }
        if qty_text is not None:
            payload["qty"] = qty_text
        else:
            payload["notional"] = notional_text
        return {
            "ok": True,
            "submitted": False,
            "payload": payload,
        }

    def validate_order_payload(self, proposal: Any) -> dict[str, Any]:
        """Map a local proposal to paper-order JSON. Never POSTs."""
        if not isinstance(proposal, dict):
            return {
                "ok": False,
                "submitted": False,
                "reason": "proposal must be a mapping",
            }
        ticker = proposal.get("ticker") or proposal.get("symbol")
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
        key = proposal.get("idempotency_key") or proposal.get("client_order_id")
        if not isinstance(key, str) or not key:
            return {
                "ok": False,
                "submitted": False,
                "reason": "idempotency_key must be a non-empty string",
            }
        result = {
            "ok": True,
            "submitted": False,
            "note": "dry-run only; fills stay on the local ledger unless post_paper_order rails pass",
            "payload": {
                "symbol": ticker,
                "side": side,
                "type": "market",
                "time_in_force": "day",
                "client_order_id": key,
            },
        }
        sized = self.order_payload({**proposal, "ticker": ticker, "idempotency_key": key})
        if sized.get("ok") is True:
            result["payload"] = sized["payload"]
        return result

    def order_by_client_id(self, client_order_id: str) -> dict[str, Any] | None:
        query = urllib.parse.urlencode({"client_order_id": client_order_id})
        path = "/v2/orders:by_client_order_id?" + query
        try:
            raw = self._get(path)
        except RuntimeError as error:
            if "HTTP 404" in str(error):
                return None
            raise
        if not isinstance(raw, dict):
            return None
        return sanitize_order(raw)

    def orders(self, *, status: str = "all", limit: int = 10) -> list[dict[str, Any]]:
        query = urllib.parse.urlencode({"status": status, "limit": str(limit), "direction": "desc"})
        raw = self._get("/v2/orders?" + query)
        if not isinstance(raw, list):
            return []
        return [sanitize_order(item) for item in raw if isinstance(item, dict)]

    def fills(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """GET paper fill activities. Never POSTs. Empty when the host returns none."""
        query = urllib.parse.urlencode({"page_size": str(limit), "direction": "desc"})
        path = "/v2/account/activities/FILL?" + query
        try:
            raw = self._get(path)
        except RuntimeError as error:
            if "HTTP 404" not in str(error):
                raise
            fallback = "/v2/account/activities?" + urllib.parse.urlencode(
                {"activity_types": "FILL", "page_size": str(limit), "direction": "desc"}
            )
            raw = self._get(fallback)
        rows = raw
        if isinstance(raw, dict):
            if isinstance(raw.get("activities"), list):
                rows = raw["activities"]
            else:
                return []
        if not isinstance(rows, list):
            return []
        return [sanitize_fill(item) for item in rows if isinstance(item, dict)]

    def post_paper_order(self, proposal: Any, *, explicit: bool) -> dict[str, Any]:
        """POST /v2/orders on the paper host only after the hard rails pass."""
        # Circular: paper.py constructs this client; the gate lives there.
        from .paper import require_paper_submit

        require_paper_submit(explicit=explicit)
        if not self._paper_host_ok():
            raise ValueError(f"paper broker host refused: {self._base_url!r}")
        built = self.order_payload(proposal)
        if built.get("ok") is not True:
            raise ValueError(built.get("reason") or "paper order payload refused")
        payload = built["payload"]
        existing = self.order_by_client_id(payload["client_order_id"])
        if existing and existing.get("id"):
            return {
                **existing,
                "submitted": True,
                "duplicate": True,
            }
        raw = self._request_json(
            f"{self._base_url}/v2/orders",
            "/v2/orders",
            "paper order",
            method="POST",
            body=payload,
        )
        if not isinstance(raw, dict):
            raise RuntimeError("paper order: /v2/orders must be an object")
        return {
            **sanitize_order(raw),
            "submitted": True,
            "duplicate": False,
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
