"""Local paper-only HTTP desk for fixture rankings."""

from __future__ import annotations

import json
import socketserver
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit

from . import safety
from .cli import load_fixture_events
from .indicators import rank_candidates
from .store import EventStore


DEFAULT_PORT = 8765
_LOOPBACK = ".".join(str(part) for part in (127, 0, 0, 1))
_PACKAGE_ROOT = Path(__file__).resolve().parent
_FIXTURES_PATH = _PACKAGE_ROOT.parent / "fixtures"
INDEX_PATH = _PACKAGE_ROOT / "web" / "index.html"
_FALLBACK = b"PAPER ONLY\nOpen /api/rank to view ranked fixture events.\n"


def _ranked_fixtures() -> list[dict[str, int | str]]:
    events = load_fixture_events(_FIXTURES_PATH)
    with EventStore() as store:
        store.add_many(events)
        return rank_candidates(store.all())


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
        if path == "/api/replay":
            body = json.dumps(
                {"error": "POST /api/replay to run the paper replay; GET does not place orders"}
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
        if path != "/api/replay":
            self._send(b"Not found\n", "text/plain", 404)
            return
        import tempfile

        from .sim import run_fixture_replay

        ledger = tempfile.NamedTemporaryFile(
            prefix="desk-replay-", suffix=".sqlite", delete=False
        ).name
        summary = run_fixture_replay(fixtures=_FIXTURES_PATH, ledger_path=ledger)
        self._send(json.dumps(summary, separators=(",", ":")).encode("utf-8"), "application/json")

    def log_message(self, format: str, *args: object) -> None:
        return


def _make_server(port: int = DEFAULT_PORT) -> socketserver.TCPServer:
    if safety.PAPER_ONLY is not True or safety.kill_switch_ok() is not True:
        raise RuntimeError("paper-only safety checks failed; server refused")
    return socketserver.TCPServer((_LOOPBACK, port), _DeskHandler)


def serve(port: int = DEFAULT_PORT) -> None:
    with _make_server(port) as server:
        server.serve_forever()
