"""Read-only Alpaca paper performance snapshot. HTTP is mocked unless paper keys exist."""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from signal_sim import cli
from signal_sim.alpaca_paper import AlpacaPaperClient
from signal_sim.paper import paper_broker_client, paper_host
from signal_sim.performance import (
    default_snapshot_path,
    paper_performance_snapshot,
    write_paper_performance,
)


PAPER_BROKER_HOST = "paper-api." + "alpaca" + ".markets"

_FAKE_KEYS = {
    "ALPACA_PAPER_API_KEY": "paper-key-id",
    "ALPACA_PAPER_API_SECRET": "paper-secret",
}


def _env(name, extra=None):
    values = dict(_FAKE_KEYS)
    if extra:
        values.update(extra)
    return values.get(name)


def _json_response(payload):
    response = mock.MagicMock()
    response.read.return_value = json.dumps(payload).encode("utf-8")
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


def _account():
    return {
        "status": "ACTIVE",
        "currency": "USD",
        "cash": "100000",
        "equity": "100250.5",
        "buying_power": "200000",
        "trading_blocked": False,
        "account_blocked": False,
        "pattern_day_trader": False,
        "shorting_enabled": True,
        "account_number": "PA123HIDE",
        "id": "uuid-hide",
    }


def _clock():
    return {
        "timestamp": "2026-09-04T12:00:00Z",
        "is_open": False,
        "next_open": "2026-09-08T13:30:00Z",
        "next_close": "2026-09-08T20:00:00Z",
    }


class PaperPerformanceUnitTests(unittest.TestCase):
    def test_snapshot_is_get_only_and_strips_secrets(self):
        calls = []

        def urlopen(request, timeout=None):
            calls.append((request.full_url, request.get_method(), timeout))
            url = request.full_url
            self.assertEqual(request.get_header("Apca-api-key-id"), "paper-key-id")
            if url.endswith("/v2/account"):
                return _json_response(_account())
            if url.endswith("/v2/positions"):
                return _json_response(
                    [
                        {
                            "symbol": "SPY",
                            "qty": "1",
                            "side": "long",
                            "avg_entry_price": "40",
                            "current_price": "40.1",
                            "market_value": "40.1",
                            "cost_basis": "40",
                            "unrealized_pl": "0.1",
                            "account_id": "hide-me",
                        }
                    ]
                )
            if url.endswith("/v2/clock"):
                return _json_response(_clock())
            if "/v2/orders?" in url:
                return _json_response(
                    [
                        {
                            "id": "ord-1",
                            "client_order_id": "ps:SPY:buy:q:1",
                            "status": "new",
                            "symbol": "SPY",
                            "qty": "1",
                            "side": "buy",
                            "account_number": "PA123HIDE",
                        }
                    ]
                )
            if "/v2/account/activities/FILL" in url:
                return _json_response(
                    [
                        {
                            "id": "fill-1",
                            "activity_type": "FILL",
                            "transaction_time": "2026-09-04T13:31:00Z",
                            "type": "fill",
                            "price": "40.05",
                            "qty": "1",
                            "side": "buy",
                            "symbol": "SPY",
                            "order_id": "ord-1",
                            "account_number": "PA123HIDE",
                        }
                    ]
                )
            raise AssertionError(url)

        with mock.patch("signal_sim.paper.read_env", side_effect=_env), mock.patch(
            "signal_sim.alpaca_paper.urllib.request.urlopen", side_effect=urlopen
        ):
            client = paper_broker_client(paper_host())
            report = paper_performance_snapshot(client)

        self.assertEqual(report["mode"], "alpaca-paper-performance")
        self.assertTrue(report["ok"])
        self.assertTrue(report["read_only"])
        self.assertFalse(report["submitted"])
        self.assertFalse(report["alpha"])
        self.assertFalse(report["live_money"])
        self.assertEqual(report["label"], "paper")
        self.assertEqual(report["order_post"], "disabled")
        self.assertEqual(report["account"]["equity"], "100250.5")
        self.assertEqual(report["account"]["cash"], "100000")
        self.assertNotIn("account_number", report["account"])
        self.assertEqual(report["positions"]["n"], 1)
        self.assertEqual(report["positions"]["symbols"]["SPY"], "1")
        self.assertEqual(report["n_fills"], 1)
        self.assertEqual(report["fills"][0]["id"], "fill-1")
        self.assertNotIn("account_number", report["fills"][0])
        self.assertEqual(report["summary"]["equity"], "100250.5")
        self.assertIs(report["summary"]["alpha"], False)
        dumped = json.dumps(report)
        self.assertNotIn("paper-secret", dumped)
        self.assertNotIn("PA123HIDE", dumped)
        self.assertNotIn("hide-me", dumped)
        self.assertTrue(all(method == "GET" for _url, method, _timeout in calls))
        self.assertTrue(all(_timeout == 15 for _url, _method, _timeout in calls))
        self.assertTrue(all(PAPER_BROKER_HOST in url for url, _method, _timeout in calls))
        self.assertTrue(any("/v2/account/activities/FILL" in url for url, _method, _timeout in calls))
        self.assertFalse(any(method == "POST" for _url, method, _timeout in calls))

    def test_default_snapshot_path_is_dated_docs_performance(self):
        from datetime import datetime, timezone

        root = Path(tempfile.mkdtemp())
        path = default_snapshot_path(root, datetime(2026, 9, 4, tzinfo=timezone.utc))
        self.assertEqual(path, root / "docs" / "performance" / "2026-09-04.json")

    def test_write_snapshot_is_labeled_paper_not_alpha(self):
        tmp = Path(tempfile.mkdtemp()) / "performance" / "2026-09-04.json"
        report = {
            "mode": "alpaca-paper-performance",
            "label": "paper",
            "alpha": False,
            "account": {"cash": "1", "equity": "1"},
        }
        written = write_paper_performance(report, tmp)
        payload = json.loads(written.read_text(encoding="utf-8"))
        self.assertEqual(payload["label"], "paper")
        self.assertFalse(payload["alpha"])
        self.assertIn("Not alpha", payload["write_note"])
        self.assertEqual(payload["snapshot_path"], str(tmp))


class PaperPerformanceCliTests(unittest.TestCase):
    def test_missing_keys_exit_2(self):
        error = io.StringIO()
        with mock.patch("signal_sim.paper.read_env", return_value=None), redirect_stdout(
            io.StringIO()
        ), redirect_stderr(error):
            code = cli.main(["paper-performance"])
        self.assertEqual(code, 2)
        text = error.getvalue()
        self.assertIn("ALPACA_PAPER_API_KEY", text)
        self.assertIn("ALPACA_PAPER_API_SECRET", text)
        self.assertNotIn("paper-secret", text)

    def test_cli_is_read_only_even_when_flag_one(self):
        calls = []

        def env(name):
            return _env(name, {"SIGNAL_SIM_ALPACA_PAPER_SUBMIT": "1"})

        def urlopen(request, timeout=None):
            calls.append((request.full_url, request.get_method()))
            url = request.full_url
            if url.endswith("/v2/account"):
                return _json_response(_account())
            if url.endswith("/v2/positions"):
                return _json_response([])
            if url.endswith("/v2/clock"):
                return _json_response(_clock())
            if "/v2/orders?" in url:
                return _json_response([])
            if "/v2/account/activities/FILL" in url:
                return _json_response([])
            raise AssertionError(url)

        printed = io.StringIO()
        error = io.StringIO()
        tmp = Path(tempfile.mkdtemp()) / "snap.json"
        with mock.patch("signal_sim.paper.read_env", side_effect=env), mock.patch(
            "signal_sim.runtime_env.read_env", side_effect=env
        ), mock.patch(
            "signal_sim.alpaca_paper.urllib.request.urlopen", side_effect=urlopen
        ), redirect_stdout(printed), redirect_stderr(error):
            code = cli.main(["paper-snapshot", "--write", "--out", str(tmp)])
        self.assertEqual(code, 0)
        payload = json.loads(printed.getvalue())
        self.assertEqual(payload["mode"], "alpaca-paper-performance")
        self.assertTrue(payload["read_only"])
        self.assertFalse(payload["submitted"])
        self.assertEqual(payload["order_post"], "disabled")
        self.assertEqual(payload["submit_flag"], "1")
        self.assertFalse(payload["alpha"])
        self.assertTrue(tmp.is_file())
        disk = json.loads(tmp.read_text(encoding="utf-8"))
        self.assertEqual(disk["label"], "paper")
        self.assertIn("unused for paper-performance", error.getvalue())
        self.assertIn("paper, not alpha", error.getvalue())
        dumped = printed.getvalue() + error.getvalue() + tmp.read_text(encoding="utf-8")
        self.assertNotIn("paper-secret", dumped)
        self.assertNotIn("PA123HIDE", dumped)
        self.assertTrue(all(method == "GET" for _url, method in calls))
        self.assertFalse(any(url.endswith("/v2/orders") and method == "POST" for url, method in calls))


def _paper_keys_present():
    return bool(os.environ.get("ALPACA_PAPER_API_KEY", "").strip()) and bool(
        os.environ.get("ALPACA_PAPER_API_SECRET", "").strip()
    )


@unittest.skipUnless(_paper_keys_present(), "ALPACA_PAPER_API_KEY/SECRET not set")
class PaperPerformanceIntegrationTests(unittest.TestCase):
    def test_live_paper_snapshot_is_get_only(self):
        client = paper_broker_client(paper_host())
        self.assertIsInstance(client, AlpacaPaperClient)
        report = paper_performance_snapshot(client)
        self.assertEqual(report["mode"], "alpaca-paper-performance")
        self.assertTrue(report["ok"])
        self.assertTrue(report["read_only"])
        self.assertFalse(report["submitted"])
        self.assertIn("cash", report["account"])
        self.assertIn("equity", report["account"])
        self.assertIn("n", report["positions"])
        dumped = json.dumps(report)
        self.assertNotIn(os.environ["ALPACA_PAPER_API_KEY"], dumped)
        self.assertNotIn(os.environ["ALPACA_PAPER_API_SECRET"], dumped)
        self.assertNotIn("account_number", dumped)


if __name__ == "__main__":
    unittest.main()
