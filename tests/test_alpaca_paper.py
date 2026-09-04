"""Read-only Alpaca paper client. HTTP is mocked unless paper keys exist."""

import io
import json
import os
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from signal_sim import cli
from signal_sim.alpaca_paper import AlpacaPaperClient
from signal_sim.paper import (
    LiveEndpointError,
    OrderRefused,
    PaperSubmitRefused,
    missing_paper_keys,
    paper_broker_client,
    paper_host,
    paper_submit_enabled,
    require_paper_submit,
    resolve_paper_base_url,
    submit_paper_order,
)


PAPER_BROKER_HOST = "paper-api." + "alpaca" + ".markets"
LIVE_BROKER_HOST = "api." + "alpaca" + ".markets"

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


class AlpacaPaperClientUnitTests(unittest.TestCase):
    def test_missing_keys_raise_and_never_open_a_socket(self):
        with mock.patch("signal_sim.paper.read_env", return_value=None), mock.patch(
            "socket.create_connection"
        ) as connect, mock.patch(
            "signal_sim.alpaca_paper.urllib.request.urlopen"
        ) as urlopen:
            with self.assertRaises(NotImplementedError) as error:
                paper_broker_client(PAPER_BROKER_HOST, 443)
            self.assertIn("no verified key", str(error.exception))
            connect.assert_not_called()
            urlopen.assert_not_called()

    def test_keys_construct_read_client_without_opening_a_socket(self):
        with mock.patch("signal_sim.paper.read_env", side_effect=_env), mock.patch(
            "signal_sim.alpaca_paper.urllib.request.urlopen"
        ) as urlopen:
            client = paper_broker_client(PAPER_BROKER_HOST)
        self.assertIsInstance(client, AlpacaPaperClient)
        self.assertEqual(client.mode, "alpaca-paper-read")
        self.assertEqual(client._base_url, "https://" + PAPER_BROKER_HOST)
        urlopen.assert_not_called()
        for name in ("submit", "submit_order", "place_order", "submit_paper_order"):
            self.assertFalse(hasattr(client, name), name)
        self.assertNotIn(_FAKE_KEYS["ALPACA_PAPER_API_SECRET"], repr(client))

    def test_live_base_url_raises_before_http(self):
        def env(name):
            return _env(
                name,
                {"ALPACA_PAPER_API_BASE_URL": "https://" + LIVE_BROKER_HOST},
            )

        with mock.patch("signal_sim.paper.read_env", side_effect=env), mock.patch(
            "signal_sim.alpaca_paper.urllib.request.urlopen"
        ) as urlopen:
            with self.assertRaises(LiveEndpointError):
                paper_broker_client(PAPER_BROKER_HOST)
            urlopen.assert_not_called()

    def test_http_base_url_is_refused(self):
        def env(name):
            return _env(
                name,
                {"ALPACA_PAPER_API_BASE_URL": "http://" + PAPER_BROKER_HOST},
            )

        with mock.patch("signal_sim.paper.read_env", side_effect=env):
            with self.assertRaises(ValueError) as error:
                resolve_paper_base_url()
        self.assertIn("https", str(error.exception))

    def test_embedded_credentials_are_refused(self):
        def env(name):
            return _env(
                name,
                {
                    "ALPACA_PAPER_API_BASE_URL": (
                        "https://user:pass@" + PAPER_BROKER_HOST
                    )
                },
            )

        with mock.patch("signal_sim.paper.read_env", side_effect=env):
            with self.assertRaises(ValueError) as error:
                resolve_paper_base_url()
        self.assertIn("credential", str(error.exception))

    def test_read_smoke_uses_get_only_and_strips_account_fields(self):
        calls = []

        def urlopen(request, timeout=None):
            calls.append((request.full_url, request.get_method(), timeout))
            url = request.full_url
            self.assertEqual(request.get_header("Apca-api-key-id"), "paper-key-id")
            self.assertEqual(request.get_header("Apca-api-secret-key"), "paper-secret")
            if url.endswith("/v2/account"):
                return _json_response(
                    {
                        "status": "ACTIVE",
                        "currency": "USD",
                        "cash": "100000",
                        "equity": "100000",
                        "buying_power": "200000",
                        "trading_blocked": False,
                        "account_blocked": False,
                        "pattern_day_trader": False,
                        "shorting_enabled": True,
                        "account_number": "PA123HIDE",
                        "id": "uuid-hide",
                    }
                )
            if url.endswith("/v2/positions"):
                return _json_response(
                    [{"symbol": "NVDA", "qty": "2", "side": "long", "market_value": "1"}]
                )
            if url.endswith("/v2/clock"):
                return _json_response(
                    {
                        "timestamp": "2026-09-04T12:00:00Z",
                        "is_open": False,
                        "next_open": "2026-09-08T13:30:00Z",
                        "next_close": "2026-09-08T20:00:00Z",
                    }
                )
            raise AssertionError(url)

        with mock.patch("signal_sim.paper.read_env", side_effect=_env), mock.patch(
            "signal_sim.alpaca_paper.urllib.request.urlopen", side_effect=urlopen
        ):
            client = paper_broker_client(paper_host())
            report = client.read_smoke(
                {"ticker": "NVDA", "side": "buy", "idempotency_key": "dry-1"}
            )

        self.assertTrue(report["ok"])
        self.assertEqual(report["mode"], "alpaca-paper-read")
        self.assertEqual(report["order_post"], "disabled")
        self.assertEqual(report["account"]["status"], "ACTIVE")
        self.assertNotIn("account_number", report["account"])
        self.assertNotIn("id", report["account"])
        self.assertEqual(report["positions"]["n"], 1)
        self.assertEqual(report["positions"]["symbols"]["NVDA"], "2")
        self.assertFalse(report["clock"]["is_open"])
        self.assertTrue(report["dry_run"]["ok"])
        self.assertFalse(report["dry_run"]["submitted"])
        dumped = json.dumps(report)
        self.assertNotIn("paper-secret", dumped)
        self.assertNotIn("PA123HIDE", dumped)
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(method == "GET" for _url, method, _timeout in calls))
        self.assertTrue(all(timeout == 15 for _url, _method, timeout in calls))
        self.assertTrue(all(PAPER_BROKER_HOST in url for url, _method, _timeout in calls))

    def test_dry_run_refuses_unknown_ticker_and_does_not_post(self):
        client = AlpacaPaperClient(
            base_url="https://" + PAPER_BROKER_HOST,
            api_key="paper-key-id",
            api_secret="paper-secret",
        )
        with mock.patch("signal_sim.alpaca_paper.urllib.request.urlopen") as urlopen:
            result = client.validate_order_payload(
                {"ticker": "TSLA", "side": "buy", "idempotency_key": "x"}
            )
        self.assertFalse(result["ok"])
        self.assertFalse(result["submitted"])
        urlopen.assert_not_called()

    def test_submit_flag_does_not_add_an_order_method(self):
        def env(name):
            if name == "SIGNAL_SIM_ALPACA_PAPER_SUBMIT":
                return "1"
            return _env(name)

        with mock.patch("signal_sim.paper.read_env", side_effect=env), mock.patch(
            "signal_sim.runtime_env.read_env", side_effect=env
        ):
            self.assertTrue(paper_submit_enabled())
            client = paper_broker_client(PAPER_BROKER_HOST)
        self.assertFalse(hasattr(client, "submit"))
        self.assertFalse(hasattr(client, "submit_order"))
        self.assertEqual(client.mode, "alpaca-paper-read")

    def test_last_trade_and_snapshot_are_get_only_and_never_invent(self):
        calls = []

        def urlopen(request, timeout=None):
            calls.append((request.full_url, request.get_method()))
            url = request.full_url
            self.assertIn("feed=iex", url)
            if "/v2/stocks/trades/latest" in url:
                return _json_response(
                    {
                        "trades": {
                            "AAPL": {"p": 220.5, "t": "2026-09-04T16:00:00Z"},
                            "XLK": {"p": 0},
                            "CMCSA": {"price": "not-a-number"},
                        }
                    }
                )
            if "/v2/stocks/snapshots" in url:
                return _json_response(
                    {
                        "XLK": {"latestTrade": {"p": 41.25}},
                        "CMCSA": {"latestQuote": {"ap": 32.0, "bp": 31.5}},
                        "CVX": {"dailyBar": {"c": 150.0}},
                    }
                )
            raise AssertionError(url)

        with mock.patch("signal_sim.paper.read_env", side_effect=_env), mock.patch(
            "signal_sim.alpaca_paper.urllib.request.urlopen", side_effect=urlopen
        ):
            client = paper_broker_client(paper_host())
            marks = client.sizing_marks(["AAPL", "XLK", "CMCSA", "CVX", "TSLA"])

        self.assertEqual(marks["AAPL"]["entry_px"], 220.5)
        self.assertEqual(marks["AAPL"]["kind"], "last_trade")
        self.assertEqual(marks["XLK"]["entry_px"], 41.25)
        self.assertEqual(marks["XLK"]["kind"], "snapshot")
        self.assertNotIn("CMCSA", marks)
        self.assertNotIn("CVX", marks)
        self.assertNotIn("TSLA", marks)
        self.assertTrue(all(method == "GET" for _url, method in calls))
        self.assertTrue(all("/v2/orders" not in url for url, _method in calls))
        self.assertTrue(all(( "data." + "alpaca" + ".markets") in url for url, _method in calls))
        from signal_sim.paper import execution_mark_failure

        self.assertEqual(
            execution_mark_failure(marks["AAPL"]["kind"], marks["AAPL"]["source"]),
            "execution mark must be fixture_mark",
        )

    def test_empty_last_trades_do_not_open_a_socket(self):
        with mock.patch("signal_sim.paper.read_env", side_effect=_env), mock.patch(
            "signal_sim.alpaca_paper.urllib.request.urlopen"
        ) as urlopen:
            client = paper_broker_client(PAPER_BROKER_HOST)
            self.assertEqual(client.sizing_marks([]), {})
            self.assertEqual(client.last_trades(["TSLA"]), {})
        urlopen.assert_not_called()

    def test_fixture_mark_gate_is_unchanged_when_paper_client_exists(self):
        import os
        import tempfile

        tmp = tempfile.mkdtemp()
        with mock.patch("signal_sim.paper.read_env", side_effect=_env):
            client = paper_broker_client(PAPER_BROKER_HOST)
        self.assertEqual(client.mode, "alpaca-paper-read")
        with self.assertRaisesRegex(OrderRefused, "fixture_mark"):
            submit_paper_order(
                {
                    "ticker": "NVDA",
                    "side": "buy",
                    "size_frac": 0.1,
                    "event_ids": ["paper-client-mark"],
                    "decision_at": "2026-09-02T10:15:00Z",
                    "idempotency_key": "paper-client-mark",
                },
                ledger_path=os.path.join(tmp, "ledger.sqlite"),
                mark_px=178.5,
                audit_path=os.path.join(tmp, "audit.jsonl"),
                kill_root=tmp,
                mark_kind="vendor",
            )


class PaperAccountCliTests(unittest.TestCase):
    def test_missing_keys_exit_2(self):
        error = io.StringIO()
        with mock.patch("signal_sim.paper.read_env", return_value=None), redirect_stdout(
            io.StringIO()
        ), redirect_stderr(error):
            code = cli.main(["paper-account"])
        self.assertEqual(code, 2)
        text = error.getvalue()
        self.assertIn("ALPACA_PAPER_API_KEY", text)
        self.assertIn("ALPACA_PAPER_API_SECRET", text)
        self.assertNotIn("paper-secret", text)

    def test_read_smoke_prints_sanitized_json(self):
        def urlopen(request, timeout=None):
            if request.full_url.endswith("/v2/account"):
                return _json_response({"status": "ACTIVE", "cash": "1", "account_number": "NO"})
            if request.full_url.endswith("/v2/positions"):
                return _json_response([])
            if request.full_url.endswith("/v2/clock"):
                return _json_response({"is_open": True, "timestamp": "2026-09-04T12:00:00Z"})
            raise AssertionError(request.full_url)

        printed = io.StringIO()
        error = io.StringIO()
        with mock.patch("signal_sim.paper.read_env", side_effect=_env), mock.patch(
            "signal_sim.alpaca_paper.urllib.request.urlopen", side_effect=urlopen
        ), redirect_stdout(printed), redirect_stderr(error):
            code = cli.main(["paper-account", "--dry-run"])
        self.assertEqual(code, 0)
        payload = json.loads(printed.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["order_post"], "disabled")
        self.assertFalse(payload["dry_run"]["submitted"])
        self.assertNotIn("NO", printed.getvalue())
        self.assertNotIn("paper-secret", printed.getvalue() + error.getvalue())


class AlpacaPaperSubmitGateTests(unittest.TestCase):
    def test_flag_zero_never_posts(self):
        calls = []

        def env(name):
            return _env(name, {"SIGNAL_SIM_ALPACA_PAPER_SUBMIT": "0"})

        with mock.patch("signal_sim.paper.read_env", side_effect=env), mock.patch(
            "signal_sim.runtime_env.read_env", side_effect=env
        ), mock.patch(
            "signal_sim.alpaca_paper.urllib.request.urlopen",
            side_effect=lambda *a, **k: calls.append(a) or _json_response({}),
        ) as urlopen:
            with self.assertRaises(PaperSubmitRefused) as error:
                require_paper_submit(explicit=True)
            client = paper_broker_client(PAPER_BROKER_HOST)
            with self.assertRaises(PaperSubmitRefused):
                client.post_paper_order(
                    {
                        "symbol": "SPY",
                        "qty": "1",
                        "side": "buy",
                        "type": "market",
                        "time_in_force": "day",
                        "client_order_id": "flag-zero",
                    },
                    explicit=True,
                )
        self.assertIn("SIGNAL_SIM_ALPACA_PAPER_SUBMIT", str(error.exception))
        urlopen.assert_not_called()
        self.assertEqual(calls, [])

    def test_missing_explicit_cli_never_posts(self):
        def env(name):
            return _env(name, {"SIGNAL_SIM_ALPACA_PAPER_SUBMIT": "1"})

        with mock.patch("signal_sim.paper.read_env", side_effect=env), mock.patch(
            "signal_sim.runtime_env.read_env", side_effect=env
        ), mock.patch(
            "signal_sim.alpaca_paper.urllib.request.urlopen"
        ) as urlopen:
            with self.assertRaises(PaperSubmitRefused) as error:
                require_paper_submit(explicit=False)
            client = paper_broker_client(PAPER_BROKER_HOST)
            with self.assertRaises(PaperSubmitRefused):
                client.post_paper_order(
                    {
                        "symbol": "SPY",
                        "qty": "1",
                        "side": "buy",
                        "type": "market",
                        "time_in_force": "day",
                        "client_order_id": "no-cli",
                    },
                    explicit=False,
                )
        self.assertIn("submit-paper", str(error.exception))
        urlopen.assert_not_called()

    def test_live_host_is_refused_before_http(self):
        def env(name):
            return _env(
                name,
                {
                    "SIGNAL_SIM_ALPACA_PAPER_SUBMIT": "1",
                    "ALPACA_PAPER_API_BASE_URL": "https://" + LIVE_BROKER_HOST,
                },
            )

        with mock.patch("signal_sim.paper.read_env", side_effect=env), mock.patch(
            "signal_sim.runtime_env.read_env", side_effect=env
        ), mock.patch(
            "signal_sim.alpaca_paper.urllib.request.urlopen"
        ) as urlopen:
            with self.assertRaises(LiveEndpointError):
                require_paper_submit(explicit=True)
        urlopen.assert_not_called()

    def test_non_paper_url_is_refused_before_http(self):
        def env(name):
            return _env(
                name,
                {
                    "SIGNAL_SIM_ALPACA_PAPER_SUBMIT": "1",
                    "ALPACA_PAPER_API_BASE_URL": "https://example.invalid",
                },
            )

        with mock.patch("signal_sim.paper.read_env", side_effect=env), mock.patch(
            "signal_sim.runtime_env.read_env", side_effect=env
        ), mock.patch(
            "signal_sim.alpaca_paper.urllib.request.urlopen"
        ) as urlopen:
            with self.assertRaises(ValueError) as error:
                require_paper_submit(explicit=True)
        self.assertIn("paper broker host refused", str(error.exception))
        urlopen.assert_not_called()

    def test_flag_one_explicit_posts_and_logs_order_id(self):
        calls = []

        def env(name):
            return _env(name, {"SIGNAL_SIM_ALPACA_PAPER_SUBMIT": "1"})

        def urlopen(request, timeout=None):
            calls.append((request.full_url, request.get_method(), request.data))
            url = request.full_url
            if "orders:by_client_order_id" in url:
                raise urllib.error.HTTPError(url, 404, "not found", hdrs=None, fp=io.BytesIO(b""))
            if request.get_method() == "POST" and url.endswith("/v2/orders"):
                body = json.loads(request.data.decode("utf-8"))
                self.assertEqual(body["symbol"], "SPY")
                self.assertEqual(body["qty"], "1")
                self.assertEqual(body["side"], "buy")
                self.assertEqual(body["client_order_id"], "paper-spy-1")
                self.assertEqual(request.get_header("Apca-api-key-id"), "paper-key-id")
                return _json_response(
                    {
                        "id": "ord-spy-1",
                        "client_order_id": "paper-spy-1",
                        "status": "accepted",
                        "symbol": "SPY",
                        "qty": "1",
                        "side": "buy",
                        "filled_qty": "0",
                        "account_number": "PA123HIDE",
                    }
                )
            raise AssertionError((url, request.get_method()))

        with mock.patch("signal_sim.paper.read_env", side_effect=env), mock.patch(
            "signal_sim.runtime_env.read_env", side_effect=env
        ), mock.patch(
            "signal_sim.alpaca_paper.urllib.request.urlopen", side_effect=urlopen
        ):
            require_paper_submit(explicit=True)
            client = paper_broker_client(PAPER_BROKER_HOST)
            result = client.post_paper_order(
                {
                    "symbol": "SPY",
                    "qty": "1",
                    "side": "buy",
                    "type": "market",
                    "time_in_force": "day",
                    "client_order_id": "paper-spy-1",
                },
                explicit=True,
            )
        self.assertEqual(result["id"], "ord-spy-1")
        self.assertEqual(result["status"], "accepted")
        self.assertTrue(result["submitted"])
        self.assertFalse(result.get("duplicate"))
        dumped = json.dumps(result)
        self.assertNotIn("paper-secret", dumped)
        self.assertNotIn("PA123HIDE", dumped)
        self.assertTrue(any(method == "POST" and url.endswith("/v2/orders") for url, method, _data in calls))
        self.assertTrue(all(PAPER_BROKER_HOST in url for url, _method, _data in calls))
        self.assertFalse(any(("://" + LIVE_BROKER_HOST) in url for url, _method, _data in calls))

    def test_http_error_includes_sanitized_broker_message(self):
        def env(name):
            return _env(name, {"SIGNAL_SIM_ALPACA_PAPER_SUBMIT": "1"})

        def urlopen(request, timeout=None):
            if "orders:by_client_order_id" in request.full_url:
                raise urllib.error.HTTPError(
                    request.full_url, 404, "not found", hdrs=None, fp=io.BytesIO(b"")
                )
            if request.get_method() == "POST" and request.full_url.endswith("/v2/orders"):
                payload = json.dumps({"message": "fractional orders not supported", "secret": "hide"}).encode()
                raise urllib.error.HTTPError(
                    request.full_url, 403, "forbidden", hdrs=None, fp=io.BytesIO(payload)
                )
            raise AssertionError(request.full_url)

        with mock.patch("signal_sim.paper.read_env", side_effect=env), mock.patch(
            "signal_sim.runtime_env.read_env", side_effect=env
        ), mock.patch(
            "signal_sim.alpaca_paper.urllib.request.urlopen", side_effect=urlopen
        ):
            client = paper_broker_client(PAPER_BROKER_HOST)
            with self.assertRaises(RuntimeError) as error:
                client.post_paper_order(
                    {
                        "symbol": "XLK",
                        "qty": "1",
                        "side": "buy",
                        "type": "market",
                        "time_in_force": "day",
                        "client_order_id": "xlk-403",
                    },
                    explicit=True,
                )
        text = str(error.exception)
        self.assertIn("HTTP 403", text)
        self.assertIn("fractional orders not supported", text)
        self.assertNotIn("hide", text)
        self.assertNotIn("paper-secret", text)

    def test_duplicate_client_order_id_does_not_post_again(self):
        calls = []

        def env(name):
            return _env(name, {"SIGNAL_SIM_ALPACA_PAPER_SUBMIT": "1"})

        def urlopen(request, timeout=None):
            calls.append((request.full_url, request.get_method()))
            if "orders:by_client_order_id" in request.full_url:
                return _json_response(
                    {
                        "id": "ord-existing",
                        "client_order_id": "dup-1",
                        "status": "filled",
                        "symbol": "QQQ",
                        "qty": "1",
                        "side": "buy",
                    }
                )
            raise AssertionError("duplicate path must not POST")

        with mock.patch("signal_sim.paper.read_env", side_effect=env), mock.patch(
            "signal_sim.runtime_env.read_env", side_effect=env
        ), mock.patch(
            "signal_sim.alpaca_paper.urllib.request.urlopen", side_effect=urlopen
        ):
            client = paper_broker_client(PAPER_BROKER_HOST)
            result = client.post_paper_order(
                {
                    "symbol": "QQQ",
                    "qty": "1",
                    "side": "buy",
                    "type": "market",
                    "time_in_force": "day",
                    "client_order_id": "dup-1",
                },
                explicit=True,
            )
        self.assertEqual(result["id"], "ord-existing")
        self.assertTrue(result["duplicate"])
        self.assertTrue(result["submitted"])
        self.assertTrue(all(method == "GET" for _url, method in calls))
        self.assertFalse(any("/v2/orders" in url and method == "POST" for url, method in calls))

    def test_paper_submit_cli_flag_zero_never_posts(self):
        calls = []

        def env(name):
            return _env(name, {"SIGNAL_SIM_ALPACA_PAPER_SUBMIT": "0"})

        printed = io.StringIO()
        error = io.StringIO()
        with mock.patch("signal_sim.paper.read_env", side_effect=env), mock.patch(
            "signal_sim.runtime_env.read_env", side_effect=env
        ), mock.patch(
            "signal_sim.alpaca_paper.urllib.request.urlopen",
            side_effect=lambda request, timeout=None: calls.append(request) or _json_response({}),
        ) as urlopen, redirect_stdout(printed), redirect_stderr(error):
            code = cli.main(["paper-submit", "--symbol", "SPY", "--qty", "1"])
        self.assertEqual(code, 2)
        self.assertIn("SIGNAL_SIM_ALPACA_PAPER_SUBMIT", error.getvalue())
        self.assertNotIn("paper-secret", printed.getvalue() + error.getvalue())
        urlopen.assert_not_called()
        self.assertEqual(calls, [])

    def test_paper_submit_cli_mocked_post_when_flag_one(self):
        calls = []

        def env(name):
            return _env(name, {"SIGNAL_SIM_ALPACA_PAPER_SUBMIT": "1"})

        def urlopen(request, timeout=None):
            calls.append((request.full_url, request.get_method()))
            url = request.full_url
            if url.endswith("/v2/account"):
                return _json_response({"status": "ACTIVE", "cash": "100000", "equity": "100000"})
            if url.endswith("/v2/positions"):
                return _json_response([])
            if url.endswith("/v2/clock"):
                return _json_response({"is_open": False, "timestamp": "2026-09-04T12:00:00Z"})
            if "orders:by_client_order_id" in url:
                raise urllib.error.HTTPError(url, 404, "not found", hdrs=None, fp=io.BytesIO(b""))
            if request.get_method() == "POST" and url.endswith("/v2/orders"):
                return _json_response(
                    {
                        "id": "ord-cli-1",
                        "client_order_id": json.loads(request.data.decode("utf-8"))["client_order_id"],
                        "status": "accepted",
                        "symbol": "SPY",
                        "qty": "1",
                        "side": "buy",
                    }
                )
            if url.endswith("/v2/orders") or "/v2/orders?" in url:
                return _json_response(
                    [{"id": "ord-cli-1", "status": "accepted", "symbol": "SPY", "qty": "1"}]
                )
            raise AssertionError(url)

        printed = io.StringIO()
        error = io.StringIO()
        with mock.patch("signal_sim.paper.read_env", side_effect=env), mock.patch(
            "signal_sim.runtime_env.read_env", side_effect=env
        ), mock.patch(
            "signal_sim.alpaca_paper.urllib.request.urlopen", side_effect=urlopen
        ), redirect_stdout(printed), redirect_stderr(error):
            code = cli.main(["paper-submit", "--symbol", "SPY", "--qty", "1"])
        self.assertEqual(code, 0)
        payload = json.loads(printed.getvalue())
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["submitted"])
        self.assertEqual(payload["order"]["id"], "ord-cli-1")
        self.assertEqual(payload["order"]["status"], "accepted")
        dumped = printed.getvalue() + error.getvalue()
        self.assertNotIn("paper-secret", dumped)
        self.assertTrue(any(method == "POST" and url.endswith("/v2/orders") for url, method in calls))
        self.assertTrue(all(PAPER_BROKER_HOST in url for url, _method in calls))


def _paper_keys_present():
    return bool(os.environ.get("ALPACA_PAPER_API_KEY", "").strip()) and bool(
        os.environ.get("ALPACA_PAPER_API_SECRET", "").strip()
    )


@unittest.skipUnless(_paper_keys_present(), "ALPACA_PAPER_API_KEY/SECRET not set")
class AlpacaPaperIntegrationTests(unittest.TestCase):
    def test_read_smoke_against_paper_host(self):
        client = paper_broker_client(paper_host())
        report = client.read_smoke()
        self.assertEqual(report["mode"], "alpaca-paper-read")
        self.assertTrue(report["ok"])
        self.assertIn("status", report["account"])
        self.assertIn("n", report["positions"])
        self.assertIn("is_open", report["clock"])
        self.assertEqual(report["order_post"], "disabled")
        dumped = json.dumps(report)
        self.assertNotIn(os.environ["ALPACA_PAPER_API_KEY"], dumped)
        self.assertNotIn(os.environ["ALPACA_PAPER_API_SECRET"], dumped)
        self.assertFalse(missing_paper_keys())


if __name__ == "__main__":
    unittest.main()
