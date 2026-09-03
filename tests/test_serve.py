import io
import json
import shutil
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from functools import partial
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from signal_sim import cli, safety, serve

FORBIDDEN_BROKER_FRAGMENTS = (
    "alpaca.markets",
    "interactivebrokers",
    "tradier",
    "tradestation",
    ":7496",
    ":4001",
)


class ServeTests(unittest.TestCase):
    def request(self, path):
        with patch.object(serve.safety, "PAPER_ONLY", True), patch.object(
            serve.safety, "kill_switch_ok", return_value=True
        ):
            with serve._make_server(0) as server:
                thread = threading.Thread(target=server.handle_request)
                thread.start()
                host, port = server.server_address
                with urlopen(f"http://{host}:{port}{path}", timeout=2) as response:
                    body = response.read()
                    content_type = response.headers.get_content_type()
                thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        return body, content_type

    def test_api_rank_matches_rank_fixtures(self):
        expected = io.StringIO()
        with redirect_stdout(expected):
            cli.main(["rank", "--fixtures"])

        body, content_type = self.request("/api/rank")

        self.assertEqual(json.loads(body), json.loads(expected.getvalue()))
        self.assertEqual(content_type, "application/json")

    def test_api_rank_is_json_list_with_nvda_from_fixtures(self):
        body, content_type = self.request("/api/rank")

        payload = json.loads(body)
        self.assertEqual(content_type, "application/json")
        self.assertIsInstance(payload, list)
        self.assertIn("NVDA", {row["ticker"] for row in payload})

    def test_get_api_replay_does_not_place_orders(self):
        with patch.object(serve.safety, "PAPER_ONLY", True), patch.object(
            serve.safety, "kill_switch_ok", return_value=True
        ):
            with serve._make_server(0) as server:
                thread = threading.Thread(target=server.handle_request)
                thread.start()
                host, port = server.server_address
                with self.assertRaises(HTTPError) as error:
                    urlopen(f"http://{host}:{port}/api/replay", timeout=2)
                thread.join(timeout=2)
        self.assertEqual(error.exception.code, 405)
        self.assertIn(b"GET does not place orders", error.exception.read())

    def test_api_diagnose_matches_diagnose_fixtures(self):
        expected = io.StringIO()
        with redirect_stdout(expected):
            cli.main(["diagnose", "--fixtures"])

        body, content_type = self.request("/api/diagnose")

        self.assertEqual(json.loads(body), json.loads(expected.getvalue()))
        self.assertEqual(content_type, "application/json")
        payload = json.loads(body)
        self.assertEqual(payload["mode"], "local-paper-diagnose")
        self.assertNotIn("candidates", payload)
        self.assertNotIn("sharpe", json.dumps(payload).lower())

    def test_get_api_path_does_not_place_orders(self):
        with patch.object(serve.safety, "PAPER_ONLY", True), patch.object(
            serve.safety, "kill_switch_ok", return_value=True
        ):
            with serve._make_server(0) as server:
                thread = threading.Thread(target=server.handle_request)
                thread.start()
                host, port = server.server_address
                with self.assertRaises(HTTPError) as error:
                    urlopen(f"http://{host}:{port}/api/path", timeout=2)
                thread.join(timeout=2)
        self.assertEqual(error.exception.code, 405)
        self.assertIn(b"GET does not place orders", error.exception.read())

    def test_post_api_path_runs_three_step_paper_path(self):
        with patch.object(serve.safety, "PAPER_ONLY", True), patch.object(
            serve.safety, "kill_switch_ok", return_value=True
        ):
            with serve._make_server(0) as server:
                thread = threading.Thread(target=server.handle_request)
                thread.start()
                host, port = server.server_address
                request = Request(f"http://{host}:{port}/api/path", data=b"", method="POST")
                with urlopen(request, timeout=5) as response:
                    body = response.read()
                    content_type = response.headers.get_content_type()
                thread.join(timeout=5)
        payload = json.loads(body)
        self.assertEqual(content_type, "application/json")
        self.assertEqual(payload["mode"], "local-paper-path")
        self.assertEqual(len(payload["steps"]), 3)
        self.assertEqual({row["ticker"] for row in payload["steps"][0]["orders"]}, {"NVDA", "XOM", "DIS", "QQQ"})
        self.assertIn({"ticker": "AAPL", "reason": "no_mark"}, payload["steps"][0]["refusals"])
        self.assertEqual({row["ticker"] for row in payload["steps"][2]["positions"]}, {"MSFT", "SPY"})
        self.assertNotIn("sharpe", json.dumps(payload).lower())

    def test_get_api_liquid_does_not_place_orders(self):
        with patch.object(serve.safety, "PAPER_ONLY", True), patch.object(
            serve.safety, "kill_switch_ok", return_value=True
        ):
            with serve._make_server(0) as server:
                thread = threading.Thread(target=server.handle_request)
                thread.start()
                host, port = server.server_address
                with self.assertRaises(HTTPError) as error:
                    urlopen(f"http://{host}:{port}/api/liquid", timeout=2)
                thread.join(timeout=2)
        self.assertEqual(error.exception.code, 405)
        self.assertIn(b"GET does not place orders", error.exception.read())

    def test_post_api_liquid_fills_sector_mark_book(self):
        with patch.object(serve.safety, "PAPER_ONLY", True), patch.object(
            serve.safety, "kill_switch_ok", return_value=True
        ):
            with serve._make_server(0) as server:
                thread = threading.Thread(target=server.handle_request)
                thread.start()
                host, port = server.server_address
                request = Request(f"http://{host}:{port}/api/liquid", data=b"", method="POST")
                with urlopen(request, timeout=5) as response:
                    body = response.read()
                    content_type = response.headers.get_content_type()
                thread.join(timeout=5)
        payload = json.loads(body)
        self.assertEqual(content_type, "application/json")
        self.assertEqual(payload["mode"], "local-paper-replay")
        self.assertEqual(
            {row["ticker"] for row in payload["orders"]},
            {"NVDA", "MSFT", "XLE", "XOM", "DIS", "NFLX", "SPY", "QQQ"},
        )
        self.assertEqual(payload["refusals"], [])
        self.assertNotIn(100.0, [row["fill_px"] for row in payload["orders"]])

    def test_post_api_replay_runs_paper_round_trip(self):
        with patch.object(serve.safety, "PAPER_ONLY", True), patch.object(
            serve.safety, "kill_switch_ok", return_value=True
        ):
            with serve._make_server(0) as server:
                thread = threading.Thread(target=server.handle_request)
                thread.start()
                host, port = server.server_address
                request = Request(f"http://{host}:{port}/api/replay", data=b"", method="POST")
                with urlopen(request, timeout=5) as response:
                    body = response.read()
                    content_type = response.headers.get_content_type()
                thread.join(timeout=5)
        payload = json.loads(body)
        self.assertEqual(content_type, "application/json")
        self.assertEqual(payload["mode"], "local-paper-replay")
        self.assertEqual({row["ticker"] for row in payload["orders"]}, {"NVDA", "XLE"})
        self.assertIn("total_pnl", payload)

    def test_root_serves_browser_file(self):
        body, content_type = self.request("/")

        self.assertEqual(body, serve.INDEX_PATH.read_bytes())
        self.assertEqual(content_type, "text/html")

    def test_root_falls_back_to_paper_only_message(self):
        missing = Path(__file__).with_name("missing-index.html")
        with patch.object(serve, "INDEX_PATH", missing):
            body, content_type = self.request("/")

        self.assertIn(b"PAPER ONLY", body)
        self.assertIn(b"/api/rank", body)
        self.assertEqual(content_type, "text/plain")

    def test_server_refuses_when_paper_only_is_not_true(self):
        with patch.object(serve.safety, "PAPER_ONLY", False), patch.object(
            serve.safety, "kill_switch_ok", return_value=True
        ), patch.object(serve.socketserver, "TCPServer") as server_factory:
            with self.assertRaises(RuntimeError):
                serve._make_server(0)

        server_factory.assert_not_called()

    def test_server_refuses_when_kill_switch_is_not_ok(self):
        with patch.object(serve.safety, "PAPER_ONLY", True), patch.object(
            serve.safety, "kill_switch_ok", return_value=False
        ), patch.object(serve.socketserver, "TCPServer") as server_factory:
            with self.assertRaises(RuntimeError):
                serve._make_server(0)

        server_factory.assert_not_called()

    def test_cli_uses_default_and_override_ports(self):
        with patch("signal_sim.serve.serve") as run_server:
            self.assertEqual(cli.main(["serve"]), 0)
            self.assertEqual(cli.main(["serve", "--port", "9000"]), 0)

        self.assertEqual(
            [call.args for call in run_server.call_args_list],
            [(8765,), (9000,)],
        )


class PaperOnlyDeskTests(unittest.TestCase):
    def test_server_binds_loopback_only_under_real_safety_rails(self):
        # No safety mocks: the real PAPER_ONLY constant and the real repo-root
        # kill-switch check must permit a loopback bind.
        with serve._make_server(0) as server:
            host = server.server_address[0]

        self.assertEqual(host, "127.0.0.1")
        self.assertNotEqual(host, "0.0.0.0")

    def test_serve_module_names_no_live_broker_host_or_wildcard_bind(self):
        source = Path(serve.__file__).read_text(encoding="utf-8-sig").lower()

        for fragment in FORBIDDEN_BROKER_FRAGMENTS:
            self.assertNotIn(fragment, source, f"{fragment!r} in serve.py")
        self.assertNotIn("0.0.0.0", source)

    def test_server_refuses_via_real_kill_switch_in_temp_root(self):
        # Real kill_switch_ok logic against a temp root that holds a KILL
        # file; the repo-root KILL file is never created or deleted.
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        Path(tmp, safety.KILL_FILE).write_text("stop", encoding="utf-8")

        with patch.object(
            serve.safety, "kill_switch_ok", partial(safety.kill_switch_ok, tmp)
        ), patch.object(serve.socketserver, "TCPServer") as server_factory:
            with self.assertRaises(RuntimeError):
                serve._make_server(0)

        server_factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
