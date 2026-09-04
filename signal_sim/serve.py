"""Local paper-only HTTP desk for fixture rankings."""

from __future__ import annotations

import json
import socketserver
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit

from . import safety
from .cli import rank_fixture_events
from .fixture_load import load_fixture_events
from .diagnose import fixture_diagnostics


DEFAULT_PORT = 8765
_LOOPBACK = ".".join(str(part) for part in (127, 0, 0, 1))
_PACKAGE_ROOT = Path(__file__).resolve().parent
_FIXTURES_PATH = _PACKAGE_ROOT.parent / "fixtures"
INDEX_PATH = _PACKAGE_ROOT / "web" / "index.html"
_FALLBACK = (
    b"PAPER ONLY\n"
    b"GET /api/rank ranked fixture events at decision_at\n"
    b"GET /api/diagnose Hawkes and clusters; not a ranking input\n"
    b"GET /api/marks who can fill vs no_mark\n"
    b"POST /api/replay default NVDA/XLE book\n"
    b"POST /api/liquid eight-name sector book\n"
    b"POST /api/path three-step path\n"
    b"GET on replay/liquid/path returns 405 and does not place orders\n"
)


def _ranked_fixtures() -> list[dict[str, int | str]]:
    return rank_fixture_events(_FIXTURES_PATH)


class _DeskHandler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/api/rank":
            body = json.dumps(_ranked_fixtures(), separators=(",", ":")).encode("utf-8")
            self._send(body, "application/json")
            return
        if path == "/api/diagnose":
            events = load_fixture_events(_FIXTURES_PATH)
            body = json.dumps(fixture_diagnostics(events), separators=(",", ":")).encode("utf-8")
            self._send(body, "application/json")
            return
        if path == "/api/marks":
            from .sim import fixture_mark_map

            body = json.dumps(fixture_mark_map(), separators=(",", ":")).encode("utf-8")
            self._send(body, "application/json")
            return
        if path == "/api/replay":
            body = json.dumps(
                {"error": "POST /api/replay to run the paper replay; GET does not place orders"}
            ).encode("utf-8")
            self._send(body, "application/json", 405)
            return
        if path == "/api/path":
            body = json.dumps(
                {"error": "POST /api/path to run the paper path; GET does not place orders"}
            ).encode("utf-8")
            self._send(body, "application/json", 405)
            return
        if path == "/api/liquid":
            body = json.dumps(
                {"error": "POST /api/liquid to run the sector mark book; GET does not place orders"}
            ).encode("utf-8")
            self._send(body, "application/json", 405)
            return
        if path == "/":
            if INDEX_PATH.is_file():
                self._send(INDEX_PATH.read_bytes(), "text/html")
            else:
                self._send(_FALLBACK, "text/plain")
            return
        self._send(b"Not found\n", "text/plain", 404)

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        import tempfile

        if path == "/api/replay":
            from .sim import run_fixture_replay

            ledger = tempfile.NamedTemporaryFile(
                prefix="desk-replay-", suffix=".sqlite", delete=False
            ).name
            summary = run_fixture_replay(fixtures=_FIXTURES_PATH, ledger_path=ledger)
            self._send(json.dumps(summary, separators=(",", ":")).encode("utf-8"), "application/json")
            return
        if path == "/api/path":
            from .sim import run_fixture_path

            ledger = tempfile.NamedTemporaryFile(
                prefix="desk-path-", suffix=".sqlite", delete=False
            ).name
            summary = run_fixture_path(fixtures=_FIXTURES_PATH, ledger_path=ledger)
            self._send(json.dumps(summary, separators=(",", ":")).encode("utf-8"), "application/json")
            return
        if path == "/api/liquid":
            from .sim import run_fixture_replay

            ledger = tempfile.NamedTemporaryFile(
                prefix="desk-liquid-", suffix=".sqlite", delete=False
            ).name
            summary = run_fixture_replay(
                fixtures=_FIXTURES_PATH,
                ledger_path=ledger,
                mark_book_path=_FIXTURES_PATH / "marks" / "liquid.json",
            )
            self._send(json.dumps(summary, separators=(",", ":")).encode("utf-8"), "application/json")
            return
        self._send(b"Not found\n", "text/plain", 404)

    def log_message(self, format: str, *args: object) -> None:
        return


def _make_server(port: int = DEFAULT_PORT) -> socketserver.TCPServer:
    if safety.PAPER_ONLY is not True or safety.kill_switch_ok() is not True:
        raise RuntimeError("paper-only safety checks failed; server refused")
    return socketserver.TCPServer((_LOOPBACK, port), _DeskHandler)


def serve(port: int = DEFAULT_PORT) -> None:
    with _make_server(port) as server:
        server.serve_forever()
