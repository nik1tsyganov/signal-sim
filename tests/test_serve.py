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

    def test_api_marks_matches_marks_fixtures(self):
        expected = io.StringIO()
        with redirect_stdout(expected):
            cli.main(["marks", "--fixtures"])

        body, content_type = self.request("/api/marks")

        self.assertEqual(json.loads(body), json.loads(expected.getvalue()))
        self.assertEqual(content_type, "application/json")
        payload = json.loads(body)
        self.assertEqual(payload["mode"], "local-paper-marks")
        self.assertEqual(
            set(payload["default_fillable"]),
            {"NVDA", "MSFT", "XLE", "XOM", "DIS", "NFLX", "SPY", "QQQ"},
        )
        self.assertEqual(set(payload["default_fillable"]), set(payload["liquid_fillable"]))
        self.assertEqual(set(payload["two_name_fillable"]), {"NVDA", "XLE"})
        self.assertIn("MSFT", payload["liquid_fillable"])
        self.assertIn("AAPL", payload["no_mark_default"])
        self.assertIn("AAPL", payload["no_mark_liquid"])
        self.assertIn("AAPL", payload["universe"])
        self.assertIn("NVDA", payload["universe"])
        self.assertEqual(len(payload["universe"]), 15)
        self.assertTrue(set(payload["default_fillable"]).isdisjoint(payload["no_mark_default"]))
        self.assertEqual(set(payload["no_print"]), {"AMZN", "GOOGL", "META"})
        self.assertNotIn("AAPL", payload["no_print"])
        self.assertIn("no checked-in", payload["no_print_reason"].lower())
        self.assertNotIn("sharpe", json.dumps(payload).lower())

    def test_api_diagnose_matches_diagnose_fixtures(self):
        expected = io.StringIO()
        with redirect_stdout(expected):
            cli.main(["diagnose", "--fixtures"])

        body, content_type = self.request("/api/diagnose")

        self.assertEqual(json.loads(body), json.loads(expected.getvalue()))
        self.assertEqual(content_type, "application/json")
        payload = json.loads(body)
        self.assertEqual(payload["mode"], "local-paper-diagnose")
        self.assertEqual(payload["cut"], "decision_at")
        self.assertEqual(payload["when"], payload["decision_at"])
        self.assertGreaterEqual(payload["stats"]["n_events_after_decision"], 1)
        self.assertIn("decision_at", payload["note"])
        self.assertNotIn("candidates", payload)
        self.assertNotIn("sharpe", json.dumps(payload).lower())

    def test_api_drift_matches_drift_fixtures(self):
        expected = io.StringIO()
        with redirect_stdout(expected):
            cli.main(["drift", "--fixtures"])

        body, content_type = self.request("/api/drift")

        self.assertEqual(json.loads(body), json.loads(expected.getvalue()))
        self.assertEqual(content_type, "application/json")
        payload = json.loads(body)
        self.assertEqual(payload["mode"], "local-paper-drift")
        nvda = next(row for row in payload["targets"] if row["ticker"] == "NVDA")
        self.assertEqual(nvda["insider_confirm"], 1)
        self.assertEqual(nvda["congress_confirm"], 1)
        self.assertEqual(nvda["insider_lag_hours"], 2.75)
        self.assertEqual(nvda["congress_lag_hours"], 16.75)
        self.assertEqual(nvda["gov_confirm"], 1)
        self.assertNotIn("sharpe", json.dumps(payload).lower())

    def test_api_walkforward_matches_walkforward_fixtures(self):
        expected = io.StringIO()
        with redirect_stdout(expected):
            cli.main(["walkforward", "--fixtures"])

        body, content_type = self.request("/api/walkforward")

        self.assertEqual(json.loads(body), json.loads(expected.getvalue()))
        self.assertEqual(content_type, "application/json")
        payload = json.loads(body)
        self.assertEqual(payload["mode"], "local-paper-walkforward")
        self.assertEqual(payload["n_folds"], 2)
        self.assertEqual(payload["folds"][0]["comparisons"]["no_news"]["total_pnl"], 0)
        self.assertNotIn("sharpe", json.dumps(payload).lower())

    def test_api_shadow_matches_shadow_fixtures_without_writing(self):
        expected = io.StringIO()
        with redirect_stdout(expected):
            cli.main(["shadow", "--fixtures"])
        cli_payload = json.loads(expected.getvalue())
        cli_payload.pop("report_path", None)

        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        with patch("signal_sim.shadow.artifacts_dir", return_value=folder):
            body, content_type = self.request("/api/shadow")

        payload = json.loads(body)
        self.assertEqual(content_type, "application/json")
        self.assertNotIn("report_path", payload)
        self.assertEqual(payload, cli_payload)
        self.assertEqual(payload["mode"], "local-paper-shadow")
        self.assertEqual(payload["walkforward"]["n_folds"], 2)
        self.assertEqual(payload["walkforward"]["folds"][0]["comparisons"]["no_news"]["total_pnl"], 0)
        self.assertEqual(list(folder.iterdir()), [])
        self.assertNotIn("sharpe", json.dumps(payload).lower())
        self.assertNotIn("best_fold", json.dumps(payload).lower())

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
        self.assertEqual(len(payload["position_history"]), 3)
        self.assertIn("XOM", payload["position_history"][0]["held"])
        self.assertNotIn("XOM", payload["position_history"][1]["held"])
        self.assertEqual(payload["position_history"][2]["held"], ["MSFT", "SPY"])
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
        self.assertEqual(
            {row["ticker"] for row in payload["refusals"]},
            {"AAPL", "CMCSA", "CVX", "XLK"},
        )
        self.assertTrue(all(row["reason"] == "no_mark" for row in payload["refusals"]))
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
        self.assertEqual(
            {row["ticker"] for row in payload["orders"]},
            {"NVDA", "MSFT", "XLE", "XOM", "DIS", "NFLX", "SPY", "QQQ"},
        )
        self.assertIn("total_pnl", payload)

    def test_root_serves_browser_file(self):
        body, content_type = self.request("/")

        self.assertEqual(body, serve.INDEX_PATH.read_bytes())
        self.assertEqual(content_type, "text/html")
        self.assertIn(b"/api/liquid", body)
        self.assertIn(b"/api/path", body)
        self.assertIn(b"/api/diagnose", body)
        self.assertIn(b"/api/marks", body)
        self.assertIn(b"/api/drift", body)
        self.assertIn(b"/api/walkforward", body)
        self.assertIn(b"/api/shadow", body)
        self.assertIn(b"Drift targets", body)
        self.assertIn(b"Walk-forward", body)
        self.assertIn(b"Shadow-paper", body)
        self.assertIn(b"insider_confirm", body)
        self.assertIn(b"insider_lag_hours", body)
        self.assertIn(b"gov_confirm", body)
        self.assertIn(b"filing_lags", body)
        self.assertIn(b"intel_brief", body)
        self.assertIn(b"trendradar", body)
        self.assertIn(b"intensity", body)
        self.assertIn(b"no_mark", body)
        self.assertIn(b"data.steps", body)
        self.assertIn(b"not_in_rank_cut", body)
        self.assertIn(b"no_print", body)
        self.assertIn(b"equity_curve", body)
        self.assertIn(b"position_history", body)
        self.assertIn(b"n_events_after_decision", body)
        self.assertIn(b"renderJson", body)
        self.assertNotIn(b"${escapeHtml(JSON.stringify({", body)
        self.assertNotIn(b"sharpe", body.lower())

    def test_root_falls_back_to_paper_only_message(self):
        missing = Path(__file__).with_name("missing-index.html")
        with patch.object(serve, "INDEX_PATH", missing):
            body, content_type = self.request("/")

        self.assertIn(b"PAPER ONLY", body)
        self.assertIn(b"/api/rank", body)
        self.assertIn(b"/api/diagnose", body)
        self.assertIn(b"/api/marks", body)
        self.assertIn(b"/api/drift", body)
        self.assertIn(b"/api/walkforward", body)
        self.assertIn(b"/api/shadow", body)
        self.assertIn(b"/api/replay", body)
        self.assertIn(b"/api/liquid", body)
        self.assertIn(b"/api/path", body)
        self.assertIn(b"405", body)
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
