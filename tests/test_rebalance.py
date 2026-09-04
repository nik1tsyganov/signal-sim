"""Paper rebalance dry-run and optional local-ledger apply. HTTP is mocked unless paper keys exist."""

import io
import json
import os
import sqlite3
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from signal_sim import cli
from signal_sim.drift import fixture_drift_book
from signal_sim.paper import paper_broker_client, paper_host
from signal_sim.events import Event
from signal_sim.rebalance import (
    APPLY_GATE,
    PAPER_MARK_SKIP,
    SIGNAL_DRIFT,
    allocation_base,
    apply_local_rebalance,
    local_apply_failure,
    paper_held,
    plan_rebalance_tickets,
    proposed_rebalance,
    resolve_sizing_marks,
    submit_paper_rebalance,
)
from signal_sim.sim import load_mark_book
from signal_sim.sizer import size_targets


REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "fixtures"
LIQUID_FILLS = {"NVDA", "MSFT", "XLE", "XOM", "DIS", "NFLX", "SPY", "QQQ"}
NO_MARK_PRINTED = {"AAPL", "CMCSA", "CVX", "XLK"}
PAPER_BROKER_HOST = "paper-api." + "alpaca" + ".markets"
PAPER_DATA_HOST = "data." + "alpaca" + ".markets"

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


def _empty_account(**overrides):
    row = {
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
    row.update(overrides)
    return row


def _clock():
    return {
        "timestamp": "2026-09-04T12:00:00Z",
        "is_open": False,
        "next_open": "2026-09-08T13:30:00Z",
        "next_close": "2026-09-08T20:00:00Z",
    }


def _paper_urlopen(calls, *, trades=None, snapshots=None):
    def urlopen(request, timeout=None):
        calls.append((request.full_url, request.get_method()))
        url = request.full_url
        if url.endswith("/v2/account"):
            return _json_response(_empty_account())
        if url.endswith("/v2/positions"):
            return _json_response([])
        if url.endswith("/v2/clock"):
            return _json_response(_clock())
        if "/v2/stocks/trades/latest" in url:
            return _json_response({"trades": trades or {}})
        if "/v2/stocks/snapshots" in url:
            return _json_response(snapshots if snapshots is not None else {})
        raise AssertionError(url)

    return urlopen


class _FakeMarkClient:
    def __init__(self, paper_marks=None, positions=None):
        self.paper_marks = paper_marks or {}
        self._positions = positions or []

    def account(self):
        return _empty_account()

    def positions(self):
        return list(self._positions)

    def clock(self):
        return _clock()

    def sizing_marks(self, symbols):
        return {ticker: dict(row) for ticker, row in self.paper_marks.items() if ticker in set(symbols)}


def _live_event(ticker="NVDA", event_id="live-nvda"):
    return Event.from_dict(
        {
            "id": event_id,
            "source": "quiver",
            "kind": "news",
            "ticker": ticker,
            "entities": [ticker],
            "headline": "SECRET HEADLINE about a person",
            "url": "https://example.invalid/pii",
            "occurred_at": "2026-09-04T16:00:00Z",
            "filed_at": None,
            "observed_at": "2026-09-04T16:00:00Z",
            "confidence": 1.0,
            "raw_ref": "raw-pii-ref",
        }
    )


def _sized_drift_targets(mark_book=None):
    book = mark_book if mark_book is not None else load_mark_book()
    drift = fixture_drift_book(FIXTURES)
    horizon_hours = (book["exit_at"] - book["decision_at"]).total_seconds() / 3600.0
    fillable = [row for row in drift["targets"] if row["ticker"] in book["marks"]]
    targets, skipped = size_targets(
        fillable,
        size_frac=float(book["size_frac"]),
        horizon_hours=horizon_hours,
        max_gross_frac=float(book["max_gross_frac"]),
        max_name_frac=float(book["max_name_frac"]),
    )
    return book, targets, skipped


class RebalancePlannerTests(unittest.TestCase):
    def test_empty_paper_opens_fillable_drift_targets(self):
        book, expected, _size_skips = _sized_drift_targets()
        report = proposed_rebalance(
            fixtures=FIXTURES,
            account=_empty_account(),
            positions=[],
            clock=_clock(),
        )
        self.assertEqual(report["mode"], "paper-rebalance-dry-run")
        self.assertEqual(report["signal"], SIGNAL_DRIFT)
        self.assertTrue(report["ok"])
        self.assertFalse(report["submitted"])
        self.assertFalse(report["local_applied"])
        self.assertEqual(report["order_post"], "disabled")
        self.assertEqual(report["apply_gate"], APPLY_GATE)
        self.assertIn("not alpha", report["note"].lower())
        self.assertEqual(report["decision_at"], "2026-09-02T10:15:00Z")
        self.assertTrue(report["printed_at"].endswith("Z"))
        self.assertEqual(report["clock"]["timestamp"], "2026-09-04T12:00:00Z")
        self.assertNotIn("account_number", report["account"])
        self.assertNotIn("id", report["account"])
        tickets = {row["symbol"]: row for row in report["tickets"]}
        self.assertEqual(set(tickets), {row["ticker"] for row in expected})
        self.assertEqual(set(tickets), LIQUID_FILLS)
        for row in expected:
            ticket = tickets[row["ticker"]]
            self.assertEqual(ticket["side"], "buy")
            self.assertEqual(ticket["action"], "open")
            self.assertFalse(ticket["submitted"])
            self.assertAlmostEqual(ticket["size_frac"], float(row["target_frac"]))
            self.assertAlmostEqual(
                ticket["qty"],
                100000.0 * float(row["target_frac"]) / book["marks"][row["ticker"]]["entry_px"],
            )
            self.assertAlmostEqual(
                ticket["notional"],
                abs(ticket["qty"]) * book["marks"][row["ticker"]]["entry_px"],
            )
            self.assertIn("cluster-drift-stub", ticket["rationale"])
            self.assertEqual(ticket["payload"]["type"], "market")
        skip_reasons = {row["ticker"]: row["reason"] for row in report["skipped"]}
        for ticker in NO_MARK_PRINTED:
            self.assertEqual(skip_reasons[ticker], "no_mark")

    def test_held_leftover_without_target_is_a_close(self):
        book = load_mark_book()
        report = proposed_rebalance(
            fixtures=FIXTURES,
            account=_empty_account(),
            positions=[{"symbol": "SPY", "qty": "10", "side": "long"}],
            clock=_clock(),
        )
        spy = next(row for row in report["tickets"] if row["symbol"] == "SPY")
        expected_target = next(
            row for row in report["targets"] if row["ticker"] == "SPY"
        )
        target_shares = 100000.0 * float(expected_target["target_frac"]) / book["marks"]["SPY"]["entry_px"]
        self.assertEqual(spy["side"], "buy" if target_shares > 10 else "sell")
        self.assertEqual(spy["action"], "adjust")
        tickets, skipped = plan_rebalance_tickets(
            targets=[],
            marks=book["marks"],
            held={"SPY": {"shares": 10.0, "side": "long"}},
            cash=100000.0,
            allocation=100000.0,
            cost_bps=0.0,
            signal=SIGNAL_DRIFT,
            decision_at="2026-09-02T10:15:00Z",
        )
        self.assertEqual(skipped, [])
        self.assertEqual(len(tickets), 1)
        self.assertEqual(tickets[0]["symbol"], "SPY")
        self.assertEqual(tickets[0]["side"], "sell")
        self.assertEqual(tickets[0]["action"], "close")
        self.assertAlmostEqual(tickets[0]["qty"], -10.0)
        self.assertIn("close leftover", tickets[0]["rationale"])
        self.assertFalse(tickets[0]["submitted"])

    def test_held_unmarked_name_is_skipped_without_a_price(self):
        report = proposed_rebalance(
            fixtures=FIXTURES,
            account=_empty_account(),
            positions=[
                {"symbol": "AAPL", "qty": "3", "side": "long"},
                {"symbol": "TSLA", "qty": "1", "side": "long"},
            ],
            clock=_clock(),
        )
        reasons = {row["ticker"]: row["reason"] for row in report["skipped"]}
        self.assertEqual(reasons["AAPL"], "held_no_mark")
        self.assertEqual(reasons["TSLA"], "held_no_mark")
        self.assertFalse(any(row["symbol"] == "AAPL" for row in report["tickets"]))
        self.assertFalse(any(row["symbol"] == "TSLA" for row in report["tickets"]))

    def test_on_target_holding_emits_no_ticket(self):
        book, expected, _skipped = _sized_drift_targets()
        nvda = next(row for row in expected if row["ticker"] == "NVDA")
        qty = 100000.0 * float(nvda["target_frac"]) / book["marks"]["NVDA"]["entry_px"]
        tickets, skipped = plan_rebalance_tickets(
            targets=[nvda],
            marks=book["marks"],
            held={"NVDA": {"shares": qty, "side": "long"}},
            cash=100000.0,
            allocation=100000.0,
            cost_bps=0.0,
            signal=SIGNAL_DRIFT,
            decision_at="2026-09-02T10:15:00Z",
        )
        self.assertEqual(tickets, [])
        self.assertEqual(skipped, [])

    def test_cash_constraint_skips_instead_of_resizing(self):
        book, expected, _skipped = _sized_drift_targets()
        nvda = next(row for row in expected if row["ticker"] == "NVDA")
        tickets, skipped = plan_rebalance_tickets(
            targets=[nvda],
            marks=book["marks"],
            held={},
            cash=1.0,
            allocation=100000.0,
            cost_bps=0.0,
            signal=SIGNAL_DRIFT,
            decision_at="2026-09-02T10:15:00Z",
        )
        self.assertEqual(tickets, [])
        self.assertEqual(skipped, [{"ticker": "NVDA", "reason": "cash_constraint"}])

    def test_allocation_prefers_paper_equity(self):
        self.assertEqual(allocation_base({"equity": "250000", "cash": "1"}, 100000.0), 250000.0)
        self.assertEqual(allocation_base({"cash": "80"}, 100000.0), 80.0)
        self.assertEqual(allocation_base({}, 100000.0), 100000.0)

    def test_paper_held_signs_shorts(self):
        held, skipped = paper_held(
            [
                {"symbol": "NVDA", "qty": "2", "side": "long"},
                {"symbol": "MSFT", "qty": "4", "side": "short"},
            ]
        )
        self.assertEqual(held["NVDA"]["shares"], 2.0)
        self.assertEqual(held["MSFT"]["shares"], -4.0)
        self.assertEqual(skipped, [])

    def test_rank_signal_uses_existing_rank_candidates(self):
        report = proposed_rebalance(
            fixtures=FIXTURES,
            account=_empty_account(),
            positions=[],
            clock=_clock(),
            signal="rank",
        )
        self.assertEqual(report["signal"], "rank-candidates")
        self.assertTrue(report["tickets"])
        self.assertTrue(all(row["side"] == "buy" for row in report["tickets"]))
        self.assertTrue({row["symbol"] for row in report["tickets"]}.issubset(LIQUID_FILLS))

    def test_missing_account_raises_before_http(self):
        with mock.patch("signal_sim.alpaca_paper.urllib.request.urlopen") as urlopen:
            with self.assertRaises(ValueError):
                proposed_rebalance(fixtures=FIXTURES)
        urlopen.assert_not_called()

    def test_planner_does_not_submit(self):
        import inspect

        planner = inspect.getsource(proposed_rebalance)
        self.assertNotIn("submit_paper_order(", planner)
        module = inspect.getsource(
            __import__("signal_sim.rebalance", fromlist=["proposed_rebalance"])
        )
        lowered = module.lower()
        self.assertNotIn("urlopen", lowered)
        self.assertNotIn("insert into orders", lowered)
        self.assertIn("submit_paper_order(", inspect.getsource(apply_local_rebalance))
        with mock.patch("signal_sim.rebalance.submit_paper_order") as submit:
            proposed_rebalance(
                fixtures=FIXTURES,
                account=_empty_account(),
                positions=[],
                clock=_clock(),
            )
        submit.assert_not_called()

    def test_fixture_mark_wins_over_paper_last_trade(self):
        book = load_mark_book()
        client = _FakeMarkClient(
            {
                "NVDA": {
                    "entry_px": 999.0,
                    "kind": "last_trade",
                    "source": "alpaca_paper_data",
                },
                "AAPL": {
                    "entry_px": 200.0,
                    "kind": "last_trade",
                    "source": "alpaca_paper_data",
                },
            }
        )
        report = proposed_rebalance(
            fixtures=FIXTURES,
            account=_empty_account(),
            positions=[],
            clock=_clock(),
            client=client,
        )
        tickets = {row["symbol"]: row for row in report["tickets"]}
        self.assertAlmostEqual(tickets["NVDA"]["mark_px"], book["marks"]["NVDA"]["entry_px"])
        self.assertEqual(tickets["NVDA"]["mark_kind"], "fixture_mark")
        self.assertEqual(tickets["NVDA"]["mark_source"], "fixture")
        self.assertIn("AAPL", tickets)
        self.assertAlmostEqual(tickets["AAPL"]["mark_px"], 200.0)
        self.assertEqual(tickets["AAPL"]["mark_kind"], "last_trade")
        self.assertEqual(tickets["AAPL"]["mark_source"], "alpaca_paper_data")
        self.assertIn("mark=last_trade", tickets["AAPL"]["rationale"])
        self.assertIn("AAPL", report["marks"]["paper_data"])
        self.assertNotIn("AAPL", report["marks"]["unmarked"])
        skip_reasons = {row["ticker"]: row["reason"] for row in report["skipped"]}
        self.assertNotEqual(skip_reasons.get("AAPL"), "no_mark")

    def test_invalid_or_missing_paper_price_stays_no_mark(self):
        client = _FakeMarkClient(
            {
                "AAPL": {"entry_px": 0, "kind": "last_trade", "source": "alpaca_paper_data"},
                "XLK": {"entry_px": "nan", "kind": "last_trade", "source": "alpaca_paper_data"},
                "CMCSA": {"kind": "last_trade", "source": "alpaca_paper_data"},
                "CVX": {
                    "entry_px": 120.0,
                    "kind": "fixture_mark",
                    "source": "fixture",
                },
            }
        )
        report = proposed_rebalance(
            fixtures=FIXTURES,
            account=_empty_account(),
            positions=[],
            clock=_clock(),
            client=client,
        )
        skip_reasons = {row["ticker"]: row["reason"] for row in report["skipped"]}
        for ticker in NO_MARK_PRINTED:
            self.assertEqual(skip_reasons[ticker], "no_mark")
        self.assertFalse({row["symbol"] for row in report["tickets"]} & NO_MARK_PRINTED)

    def test_paper_data_error_does_not_invent_a_price(self):
        class Boom(_FakeMarkClient):
            def sizing_marks(self, symbols):
                raise RuntimeError("paper data HTTP 403")

        report = proposed_rebalance(
            fixtures=FIXTURES,
            account=_empty_account(),
            positions=[],
            clock=_clock(),
            client=Boom(),
        )
        skip_reasons = {row["ticker"]: row["reason"] for row in report["skipped"]}
        for ticker in NO_MARK_PRINTED:
            self.assertEqual(skip_reasons[ticker], "no_mark")

    def test_resolve_sizing_marks_skips_unknown_names(self):
        resolved = resolve_sizing_marks(
            ["AAPL", "TSLA"],
            {},
            _FakeMarkClient(
                {
                    "AAPL": {
                        "entry_px": 10.0,
                        "kind": "snapshot",
                        "source": "alpaca_paper_data",
                    },
                    "TSLA": {
                        "entry_px": 11.0,
                        "kind": "last_trade",
                        "source": "alpaca_paper_data",
                    },
                }
            ),
        )
        self.assertEqual(resolved["AAPL"]["kind"], "snapshot")
        self.assertNotIn("TSLA", resolved)

    def test_live_research_book_drives_rebalance_without_pii(self):
        live = proposed_rebalance(
            fixtures=FIXTURES,
            account=_empty_account(),
            positions=[],
            clock=_clock(),
            live=True,
            live_events=[_live_event()],
        )
        self.assertEqual(live["signal"], "research-live")
        self.assertEqual(live["intensity_cut"], "now")
        self.assertTrue(live["prefer_paper_marks"])
        self.assertIn("NVDA", live["universe"])
        self.assertIn("NVDA", live["intensity"])
        dumped = json.dumps(live)
        self.assertNotIn("SECRET HEADLINE", dumped)
        self.assertNotIn("example.invalid", dumped)
        self.assertNotIn("raw-pii-ref", dumped)
        self.assertFalse(live["submitted"])
        self.assertFalse(live["local_applied"])
        self.assertEqual(live["order_post"], "disabled")
        self.assertTrue(live["tickets"])

    def test_submit_path_prefers_paper_mark_over_fixture_spy(self):
        book = load_mark_book()
        client = _FakeMarkClient(
            {
                "SPY": {
                    "entry_px": 580.0,
                    "kind": "last_trade",
                    "source": "alpaca_paper_data",
                },
                "QQQ": {
                    "entry_px": 480.0,
                    "kind": "snapshot",
                    "source": "alpaca_paper_data",
                },
            }
        )
        offline = proposed_rebalance(
            fixtures=FIXTURES,
            account=_empty_account(),
            positions=[],
            clock=_clock(),
            client=client,
            prefer_paper_marks=False,
        )
        live_sized = proposed_rebalance(
            fixtures=FIXTURES,
            account=_empty_account(),
            positions=[],
            clock=_clock(),
            client=client,
            prefer_paper_marks=True,
        )
        offline_spy = next(row for row in offline["tickets"] if row["symbol"] == "SPY")
        live_spy = next(row for row in live_sized["tickets"] if row["symbol"] == "SPY")
        self.assertAlmostEqual(offline_spy["mark_px"], book["marks"]["SPY"]["entry_px"])
        self.assertAlmostEqual(live_spy["mark_px"], 580.0)
        self.assertEqual(live_spy["mark_kind"], "last_trade")
        self.assertLess(abs(live_spy["qty"]), abs(offline_spy["qty"]))
        live_qqq = next(row for row in live_sized["tickets"] if row["symbol"] == "QQQ")
        self.assertAlmostEqual(live_qqq["mark_px"], 480.0)

    def test_leftover_close_sell_for_name_not_in_target(self):
        client = _FakeMarkClient(
            {
                "TSLA": {
                    "entry_px": 250.0,
                    "kind": "last_trade",
                    "source": "alpaca_paper_data",
                }
            },
            positions=[{"symbol": "TSLA", "qty": "4", "side": "long"}],
        )
        report = proposed_rebalance(
            fixtures=FIXTURES,
            account=_empty_account(),
            positions=[{"symbol": "TSLA", "qty": "4", "side": "long"}],
            clock=_clock(),
            client=client,
            prefer_paper_marks=True,
        )
        tsla = next(row for row in report["tickets"] if row["symbol"] == "TSLA")
        self.assertEqual(tsla["side"], "sell")
        self.assertEqual(tsla["action"], "close")
        self.assertAlmostEqual(tsla["qty"], -4.0)
        self.assertIn("close leftover", tsla["rationale"])


class RebalanceSubmitSellTests(unittest.TestCase):
    def test_submit_paper_posts_a_leftover_sell(self):
        posted = []

        class _SubmitClient(_FakeMarkClient):
            def post_paper_order(self, proposal, *, explicit):
                posted.append(dict(proposal))
                return {
                    "id": "ord-sell-1",
                    "status": "accepted",
                    "symbol": proposal["symbol"],
                    "side": proposal["side"],
                    "qty": proposal["qty"],
                    "submitted": True,
                    "duplicate": False,
                }

        book = load_mark_book()
        tickets, skipped = plan_rebalance_tickets(
            targets=[],
            marks=book["marks"],
            held={"SPY": {"shares": 10.0, "side": "long"}},
            cash=100000.0,
            allocation=100000.0,
            cost_bps=0.0,
            signal=SIGNAL_DRIFT,
            decision_at="2026-09-02T10:15:00Z",
        )
        self.assertEqual(skipped, [])
        self.assertEqual(tickets[0]["side"], "sell")
        report = {
            "tickets": tickets,
            "universe": ["SPY"],
            "ok": True,
        }
        with mock.patch("signal_sim.rebalance.require_paper_submit"):
            submitted = submit_paper_rebalance(report, _SubmitClient(), limit=1, explicit=True)
        self.assertTrue(submitted["submitted"])
        self.assertEqual(posted[0]["side"], "sell")
        self.assertEqual(posted[0]["symbol"], "SPY")
        self.assertEqual(submitted["n_paper_submitted"], 1)


class RebalanceApplyLocalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ledger = str(Path(self.tmp) / "apply.sqlite")

    def _fills(self):
        if not os.path.exists(self.ledger):
            return []
        connection = sqlite3.connect(self.ledger)
        try:
            return connection.execute(
                "SELECT o.ticker, o.side, f.price FROM orders o "
                "JOIN fills f ON f.order_id = o.order_id ORDER BY o.ticker"
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        finally:
            connection.close()

    def test_apply_local_writes_fixture_fills_only(self):
        book = load_mark_book()
        report = proposed_rebalance(
            fixtures=FIXTURES,
            account=_empty_account(),
            positions=[],
            clock=_clock(),
            client=_FakeMarkClient(
                {
                    "AAPL": {
                        "entry_px": 220.5,
                        "kind": "last_trade",
                        "source": "alpaca_paper_data",
                    }
                }
            ),
        )
        self.assertIn("AAPL", {row["symbol"] for row in report["tickets"]})
        applied = apply_local_rebalance(
            report,
            ledger_path=self.ledger,
            fixtures=FIXTURES,
            kill_root=self.tmp,
        )
        self.assertEqual(applied["mode"], "paper-rebalance-apply-local")
        self.assertTrue(applied["local_applied"])
        self.assertFalse(applied["submitted"])
        self.assertEqual(applied["order_post"], "disabled")
        self.assertEqual(applied["apply_gate"], APPLY_GATE)
        self.assertGreater(applied["n_applied"], 0)
        fills = {ticker: (side, price) for ticker, side, price in self._fills()}
        self.assertEqual(len(fills), applied["n_applied"])
        self.assertNotIn("AAPL", fills)
        self.assertIn("NVDA", fills)
        self.assertAlmostEqual(fills["NVDA"][1], book["marks"]["NVDA"]["entry_px"])
        aapl_skip = next(row for row in applied["apply_skipped"] if row["ticker"] == "AAPL")
        self.assertEqual(aapl_skip["reason"], PAPER_MARK_SKIP)
        aapl_ticket = next(row for row in applied["tickets"] if row["symbol"] == "AAPL")
        self.assertFalse(aapl_ticket["submitted"])
        self.assertFalse(aapl_ticket["local_filled"])
        nvda_ticket = next(row for row in applied["tickets"] if row["symbol"] == "NVDA")
        self.assertFalse(nvda_ticket["submitted"])
        self.assertTrue(nvda_ticket["local_filled"])

    def test_print_only_does_not_write_ledger(self):
        report = proposed_rebalance(
            fixtures=FIXTURES,
            account=_empty_account(),
            positions=[],
            clock=_clock(),
        )
        self.assertFalse(report["local_applied"])
        self.assertFalse(os.path.exists(self.ledger))
        self.assertEqual(self._fills(), [])
        self.assertTrue(report["tickets"])

    def test_paper_mark_gate_is_explicit(self):
        self.assertEqual(
            local_apply_failure(
                {
                    "mark_kind": "last_trade",
                    "mark_source": "alpaca_paper_data",
                }
            ),
            PAPER_MARK_SKIP,
        )
        self.assertEqual(
            local_apply_failure({"mark_kind": "fixture_mark", "mark_source": "fixture"}),
            None,
        )
        self.assertEqual(
            local_apply_failure({"mark_kind": "fixture_mark"}),
            "execution mark must be fixture_mark",
        )

    def test_missing_mark_labels_do_not_default_to_fixture_fills(self):
        report = proposed_rebalance(
            fixtures=FIXTURES,
            account=_empty_account(),
            positions=[],
            clock=_clock(),
        )
        report = dict(report)
        report["tickets"] = [
            {
                **report["tickets"][0],
                "mark_kind": "",
                "mark_source": "",
            }
        ]
        applied = apply_local_rebalance(
            report,
            ledger_path=self.ledger,
            fixtures=FIXTURES,
            kill_root=self.tmp,
        )
        self.assertEqual(applied["n_applied"], 0)
        self.assertEqual(self._fills(), [])
        self.assertEqual(applied["apply_skipped"][0]["reason"], "execution mark must be fixture_mark")


class RebalanceCliTests(unittest.TestCase):
    def test_requires_fixtures(self):
        error = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(error):
            code = cli.main(["rebalance"])
        self.assertEqual(code, 2)
        self.assertIn("requires --fixtures", error.getvalue())

    def test_missing_keys_exit_2(self):
        error = io.StringIO()
        with mock.patch("signal_sim.paper.read_env", return_value=None), redirect_stdout(
            io.StringIO()
        ), redirect_stderr(error):
            code = cli.main(["rebalance", "--fixtures"])
        self.assertEqual(code, 2)
        self.assertIn("ALPACA_PAPER_API_KEY", error.getvalue())
        self.assertIn("ALPACA_PAPER_API_SECRET", error.getvalue())
        self.assertNotIn("paper-secret", error.getvalue())

    def test_intensity_with_rank_exits_2(self):
        error = io.StringIO()
        with mock.patch("signal_sim.paper.read_env", side_effect=_env), redirect_stdout(
            io.StringIO()
        ), redirect_stderr(error):
            code = cli.main(["rebalance", "--fixtures", "--rank", "--intensity"])
        self.assertEqual(code, 2)
        self.assertIn("intensity", error.getvalue())

    def test_live_with_rank_exits_2(self):
        error = io.StringIO()
        with mock.patch("signal_sim.paper.read_env", side_effect=_env), redirect_stdout(
            io.StringIO()
        ), redirect_stderr(error):
            code = cli.main(["rebalance", "--fixtures", "--rank", "--live"])
        self.assertEqual(code, 2)
        self.assertIn("live", error.getvalue())

    def test_live_missing_intel_keys_exit_2(self):
        error = io.StringIO()
        with mock.patch("signal_sim.paper.read_env", side_effect=_env), mock.patch(
            "signal_sim.live_feeds.read_env", return_value=None
        ), mock.patch("signal_sim.sources.altdata.live") as quiver, mock.patch(
            "signal_sim.sources.worldmonitor.live"
        ) as world, redirect_stdout(io.StringIO()), redirect_stderr(error):
            code = cli.main(["rebalance", "--fixtures", "--live"])
        self.assertEqual(code, 2)
        self.assertIn("QUIVER_API_KEY", error.getvalue())
        self.assertIn("WORLD_MONITOR_KEY", error.getvalue())
        quiver.assert_not_called()
        world.assert_not_called()

    def test_cli_prints_tickets_with_get_only(self):
        calls = []
        printed = io.StringIO()
        error = io.StringIO()
        with mock.patch("signal_sim.paper.read_env", side_effect=_env), mock.patch(
            "signal_sim.alpaca_paper.urllib.request.urlopen",
            side_effect=_paper_urlopen(calls),
        ), redirect_stdout(printed), redirect_stderr(error):
            code = cli.main(["rebalance", "--fixtures"])
        self.assertEqual(code, 0)
        payload = json.loads(printed.getvalue())
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["submitted"])
        self.assertEqual(payload["order_post"], "disabled")
        self.assertGreaterEqual(payload["n_tickets"], 1)
        self.assertTrue(all(row["submitted"] is False for row in payload["tickets"]))
        dumped = printed.getvalue() + error.getvalue()
        self.assertNotIn("paper-secret", dumped)
        self.assertNotIn("PA123HIDE", dumped)
        self.assertNotIn("uuid-hide", dumped)
        self.assertGreaterEqual(len(calls), 3)
        self.assertTrue(all(method == "GET" for _url, method in calls))
        self.assertTrue(all("/v2/orders" not in url for url, _method in calls))
        self.assertTrue(
            all(PAPER_BROKER_HOST in url or PAPER_DATA_HOST in url for url, _method in calls)
        )
        self.assertTrue(any("/v2/account" in url for url, _method in calls))

    def test_cli_uses_paper_last_trade_for_unmarked_names(self):
        calls = []
        printed = io.StringIO()
        with mock.patch("signal_sim.paper.read_env", side_effect=_env), mock.patch(
            "signal_sim.alpaca_paper.urllib.request.urlopen",
            side_effect=_paper_urlopen(
                calls,
                trades={
                    "AAPL": {"p": 220.5, "t": "2026-09-04T16:00:00Z"},
                    "XLK": {"p": 0},
                },
                snapshots={
                    "CMCSA": {"latestTrade": {"p": 32.1}},
                    "CVX": {"latestQuote": {"ap": 160.0, "bp": 159.0}},
                },
            ),
        ), redirect_stdout(printed), redirect_stderr(io.StringIO()):
            code = cli.main(["rebalance", "--fixtures"])
        self.assertEqual(code, 0)
        payload = json.loads(printed.getvalue())
        tickets = {row["symbol"]: row for row in payload["tickets"]}
        self.assertIn("AAPL", tickets)
        self.assertAlmostEqual(tickets["AAPL"]["mark_px"], 220.5)
        self.assertEqual(tickets["AAPL"]["mark_kind"], "last_trade")
        skip_reasons = {row["ticker"]: row["reason"] for row in payload["skipped"]}
        self.assertEqual(skip_reasons.get("XLK"), "no_mark")
        self.assertEqual(skip_reasons.get("CVX"), "no_mark")
        if "CMCSA" in tickets:
            self.assertEqual(tickets["CMCSA"]["mark_kind"], "snapshot")
        self.assertFalse(payload["submitted"])
        self.assertTrue(all(method == "GET" for _url, method in calls))
        self.assertTrue(all("/v2/orders" not in url for url, _method in calls))
        self.assertTrue(any(PAPER_DATA_HOST in url for url, _method in calls))
        self.assertTrue(any("feed=iex" in url for url, _method in calls))

    def test_cli_live_intensity_is_print_only(self):
        calls = []

        def env(name):
            values = dict(_FAKE_KEYS)
            values["QUIVER_API_KEY"] = "quiver-key"
            values["WORLD_MONITOR_KEY"] = "wm-key"
            return values.get(name)

        printed = io.StringIO()
        error = io.StringIO()
        missing = Path(tempfile.mkdtemp()) / "no-research.json"
        with mock.patch("signal_sim.paper.read_env", side_effect=env), mock.patch(
            "signal_sim.live_feeds.read_env", side_effect=env
        ), mock.patch(
            "signal_sim.sources.altdata.live",
            return_value=[{"ticker": "NVDA", "person": "Rep. Hidden"}],
        ), mock.patch(
            "signal_sim.sources.worldmonitor.live", return_value=[_live_event("XLE", "wm-xle")]
        ), mock.patch(
            "signal_sim.research.research_artifact_path", return_value=missing
        ), mock.patch(
            "signal_sim.alpaca_paper.urllib.request.urlopen",
            side_effect=_paper_urlopen(calls),
        ), redirect_stdout(printed), redirect_stderr(error):
            code = cli.main(["rebalance", "--fixtures", "--live"])
        self.assertEqual(code, 0)
        payload = json.loads(printed.getvalue())
        self.assertEqual(payload["signal"], "research-live")
        self.assertEqual(payload["intensity_cut"], "now")
        self.assertTrue(payload["prefer_paper_marks"])
        self.assertIn("live_intel", payload)
        self.assertEqual(payload["live_intel"]["worldmonitor"]["tickers"], {"XLE": 1})
        dumped = printed.getvalue() + error.getvalue()
        self.assertNotIn("SECRET HEADLINE", dumped)
        self.assertNotIn("Rep. Hidden", dumped)
        self.assertNotIn("example.invalid", dumped)
        self.assertFalse(payload["submitted"])
        self.assertTrue(all(method == "GET" for _url, method in calls))
        self.assertTrue(all("/v2/orders" not in url for url, _method in calls))

    def test_submit_flag_still_does_not_post(self):
        calls = []

        def env(name):
            if name == "SIGNAL_SIM_ALPACA_PAPER_SUBMIT":
                return "1"
            return _env(name)

        printed = io.StringIO()
        error = io.StringIO()
        with mock.patch("signal_sim.paper.read_env", side_effect=env), mock.patch(
            "signal_sim.runtime_env.read_env", side_effect=env
        ), mock.patch(
            "signal_sim.alpaca_paper.urllib.request.urlopen",
            side_effect=_paper_urlopen(calls),
        ), redirect_stdout(printed), redirect_stderr(error):
            code = cli.main(["rebalance", "--fixtures"])
        self.assertEqual(code, 0)
        payload = json.loads(printed.getvalue())
        self.assertEqual(payload["submit_flag"], "1")
        self.assertFalse(payload["submitted"])
        self.assertTrue(all(method == "GET" for _url, method in calls))
        self.assertIn("requires --submit-paper", error.getvalue())

    def test_apply_local_requires_ledger(self):
        error = io.StringIO()
        with mock.patch("signal_sim.paper.read_env", side_effect=_env), redirect_stdout(
            io.StringIO()
        ), redirect_stderr(error):
            code = cli.main(["rebalance", "--fixtures", "--apply-local"])
        self.assertEqual(code, 2)
        self.assertIn("requires --ledger", error.getvalue())

    def test_print_only_cli_does_not_write_ledger(self):
        tmp = tempfile.mkdtemp()
        ledger = str(Path(tmp) / "print-only.sqlite")
        calls = []
        printed = io.StringIO()
        with mock.patch("signal_sim.paper.read_env", side_effect=_env), mock.patch(
            "signal_sim.alpaca_paper.urllib.request.urlopen",
            side_effect=_paper_urlopen(calls),
        ), redirect_stdout(printed), redirect_stderr(io.StringIO()):
            code = cli.main(["rebalance", "--fixtures", "--ledger", ledger])
        self.assertEqual(code, 0)
        payload = json.loads(printed.getvalue())
        self.assertFalse(payload["local_applied"])
        self.assertFalse(payload["submitted"])
        self.assertFalse(os.path.exists(ledger))
        self.assertTrue(all(method == "GET" for _url, method in calls))
        self.assertTrue(all("/v2/orders" not in url for url, _method in calls))

    def test_apply_local_cli_writes_ledger_without_posting(self):
        tmp = tempfile.mkdtemp()
        ledger = str(Path(tmp) / "apply-local.sqlite")
        calls = []
        printed = io.StringIO()
        error = io.StringIO()
        with mock.patch("signal_sim.paper.read_env", side_effect=_env), mock.patch(
            "signal_sim.alpaca_paper.urllib.request.urlopen",
            side_effect=_paper_urlopen(
                calls,
                trades={"AAPL": {"p": 220.5, "t": "2026-09-04T16:00:00Z"}},
            ),
        ), redirect_stdout(printed), redirect_stderr(error):
            code = cli.main(
                ["rebalance", "--fixtures", "--apply-local", "--ledger", ledger]
            )
        self.assertEqual(code, 0)
        payload = json.loads(printed.getvalue())
        self.assertEqual(payload["mode"], "paper-rebalance-apply-local")
        self.assertTrue(payload["local_applied"])
        self.assertFalse(payload["submitted"])
        self.assertEqual(payload["order_post"], "disabled")
        self.assertGreater(payload["n_applied"], 0)
        self.assertTrue(os.path.exists(ledger))
        connection = sqlite3.connect(ledger)
        try:
            n_fills = connection.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
            tickers = {
                row[0]
                for row in connection.execute("SELECT ticker FROM orders").fetchall()
            }
        finally:
            connection.close()
        self.assertEqual(n_fills, payload["n_applied"])
        self.assertNotIn("AAPL", tickers)
        self.assertIn("NVDA", tickers)
        self.assertTrue(all(method == "GET" for _url, method in calls))
        self.assertTrue(all("/v2/orders" not in url for url, _method in calls))
        self.assertNotIn("paper-secret", printed.getvalue() + error.getvalue())

    def test_submit_paper_without_flag_never_posts(self):
        calls = []

        def env(name):
            if name == "SIGNAL_SIM_ALPACA_PAPER_SUBMIT":
                return "0"
            return _env(name)

        printed = io.StringIO()
        error = io.StringIO()
        with mock.patch("signal_sim.paper.read_env", side_effect=env), mock.patch(
            "signal_sim.runtime_env.read_env", side_effect=env
        ), mock.patch(
            "signal_sim.alpaca_paper.urllib.request.urlopen",
            side_effect=_paper_urlopen(calls),
        ), redirect_stdout(printed), redirect_stderr(error):
            code = cli.main(["rebalance", "--fixtures", "--submit-paper"])
        self.assertEqual(code, 2)
        self.assertIn("SIGNAL_SIM_ALPACA_PAPER_SUBMIT", error.getvalue())
        self.assertTrue(all(method == "GET" for _url, method in calls))
        self.assertTrue(all("/v2/orders" not in url for url, _method in calls))
        self.assertNotIn("paper-secret", printed.getvalue() + error.getvalue())

    def test_submit_paper_and_apply_local_are_refused_together(self):
        error = io.StringIO()
        with mock.patch("signal_sim.paper.read_env", side_effect=_env), redirect_stdout(
            io.StringIO()
        ), redirect_stderr(error):
            code = cli.main(
                [
                    "rebalance",
                    "--fixtures",
                    "--submit-paper",
                    "--apply-local",
                    "--ledger",
                    "x.sqlite",
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("separate", error.getvalue().lower())

    def test_submit_paper_mocked_post_when_flag_one(self):
        calls = []

        def env(name):
            if name == "SIGNAL_SIM_ALPACA_PAPER_SUBMIT":
                return "1"
            return _env(name)

        def urlopen(request, timeout=None):
            calls.append((request.full_url, request.get_method()))
            url = request.full_url
            if url.endswith("/v2/account"):
                return _json_response(_empty_account())
            if url.endswith("/v2/positions"):
                return _json_response([])
            if url.endswith("/v2/clock"):
                return _json_response(_clock())
            if "/v2/stocks/trades/latest" in url or "/v2/stocks/snapshots" in url:
                return _json_response({})
            if "orders:by_client_order_id" in url:
                raise urllib.error.HTTPError(url, 404, "not found", hdrs=None, fp=io.BytesIO(b""))
            if request.get_method() == "POST" and url.endswith("/v2/orders"):
                body = json.loads(request.data.decode("utf-8"))
                return _json_response(
                    {
                        "id": "ord-" + body["symbol"],
                        "client_order_id": body["client_order_id"],
                        "status": "accepted",
                        "symbol": body["symbol"],
                        "qty": body.get("qty"),
                        "side": body["side"],
                    }
                )
            raise AssertionError(url)

        printed = io.StringIO()
        error = io.StringIO()
        with mock.patch("signal_sim.paper.read_env", side_effect=env), mock.patch(
            "signal_sim.runtime_env.read_env", side_effect=env
        ), mock.patch(
            "signal_sim.alpaca_paper.urllib.request.urlopen", side_effect=urlopen
        ), redirect_stdout(printed), redirect_stderr(error):
            code = cli.main(
                ["rebalance", "--fixtures", "--submit-paper", "--limit", "1"]
            )
        self.assertEqual(code, 0)
        payload = json.loads(printed.getvalue())
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["submitted"])
        self.assertEqual(payload["order_post"], "paper")
        self.assertEqual(payload["n_paper_submitted"], 1)
        self.assertTrue(any(method == "POST" and url.endswith("/v2/orders") for url, method in calls))
        self.assertEqual(sum(1 for url, method in calls if method == "POST"), 1)
        self.assertTrue(all(PAPER_BROKER_HOST in url or PAPER_DATA_HOST in url for url, _method in calls))
        dumped = printed.getvalue() + error.getvalue()
        self.assertNotIn("paper-secret", dumped)
        self.assertNotIn("PA123HIDE", dumped)

    def test_live_submit_paper_mocked_post_when_flag_one(self):
        calls = []

        def env(name):
            if name == "SIGNAL_SIM_ALPACA_PAPER_SUBMIT":
                return "1"
            if name in {"QUIVER_API_KEY", "WORLD_MONITOR_KEY"}:
                return "intel-key"
            return _env(name)

        def urlopen(request, timeout=None):
            calls.append((request.full_url, request.get_method()))
            url = request.full_url
            if url.endswith("/v2/account"):
                return _json_response(_empty_account())
            if url.endswith("/v2/positions"):
                return _json_response([])
            if url.endswith("/v2/clock"):
                return _json_response(_clock())
            if "/v2/stocks/trades/latest" in url or "/v2/stocks/snapshots" in url:
                return _json_response({})
            if "orders:by_client_order_id" in url:
                raise urllib.error.HTTPError(url, 404, "not found", hdrs=None, fp=io.BytesIO(b""))
            if request.get_method() == "POST" and url.endswith("/v2/orders"):
                body = json.loads(request.data.decode("utf-8"))
                return _json_response(
                    {
                        "id": "ord-" + body["symbol"],
                        "client_order_id": body["client_order_id"],
                        "status": "accepted",
                        "symbol": body["symbol"],
                        "qty": body.get("qty"),
                        "side": body["side"],
                    }
                )
            raise AssertionError(url)

        printed = io.StringIO()
        error = io.StringIO()
        missing = Path(tempfile.mkdtemp()) / "no-research.json"
        with mock.patch("signal_sim.paper.read_env", side_effect=env), mock.patch(
            "signal_sim.runtime_env.read_env", side_effect=env
        ), mock.patch("signal_sim.live_feeds.read_env", side_effect=env), mock.patch(
            "signal_sim.sources.altdata.live",
            return_value=[{"ticker": "NVDA", "person": "Rep. Hidden"}],
        ), mock.patch(
            "signal_sim.sources.worldmonitor.live", return_value=[_live_event("XLE", "wm-xle")]
        ), mock.patch(
            "signal_sim.research.research_artifact_path", return_value=missing
        ), mock.patch(
            "signal_sim.alpaca_paper.urllib.request.urlopen", side_effect=urlopen
        ), redirect_stdout(printed), redirect_stderr(error):
            code = cli.main(
                ["rebalance", "--fixtures", "--live", "--submit-paper", "--limit", "20"]
            )
        self.assertEqual(code, 0)
        payload = json.loads(printed.getvalue())
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["submitted"])
        self.assertFalse(payload["local_applied"])
        self.assertEqual(payload["order_post"], "paper")
        self.assertEqual(payload["submit_limit"], 20)
        self.assertGreaterEqual(payload["n_paper_submitted"], 1)
        self.assertIn("live_intel", payload)
        dumped = printed.getvalue() + error.getvalue()
        self.assertNotIn("SECRET HEADLINE", dumped)
        self.assertNotIn("Rep. Hidden", dumped)
        self.assertNotIn("paper-secret", dumped)
        self.assertTrue(any(method == "POST" and url.endswith("/v2/orders") for url, method in calls))
        self.assertTrue(all(PAPER_BROKER_HOST in url or PAPER_DATA_HOST in url for url, _method in calls))

    def test_apply_local_submit_flag_still_does_not_post(self):
        tmp = tempfile.mkdtemp()
        ledger = str(Path(tmp) / "apply-flag.sqlite")
        calls = []

        def env(name):
            if name == "SIGNAL_SIM_ALPACA_PAPER_SUBMIT":
                return "1"
            return _env(name)

        printed = io.StringIO()
        error = io.StringIO()
        with mock.patch("signal_sim.paper.read_env", side_effect=env), mock.patch(
            "signal_sim.runtime_env.read_env", side_effect=env
        ), mock.patch(
            "signal_sim.alpaca_paper.urllib.request.urlopen",
            side_effect=_paper_urlopen(calls),
        ), redirect_stdout(printed), redirect_stderr(error):
            code = cli.main(
                ["rebalance", "--fixtures", "--apply-local", "--ledger", ledger]
            )
        self.assertEqual(code, 0)
        payload = json.loads(printed.getvalue())
        self.assertEqual(payload["submit_flag"], "1")
        self.assertTrue(payload["local_applied"])
        self.assertFalse(payload["submitted"])
        self.assertTrue(all(method == "GET" for _url, method in calls))
        self.assertTrue(all("/v2/orders" not in url for url, _method in calls))
        self.assertIn("requires --submit-paper", error.getvalue())
        self.assertIn("local ledger", error.getvalue())


def _paper_keys_present():
    return bool(os.environ.get("ALPACA_PAPER_API_KEY", "").strip()) and bool(
        os.environ.get("ALPACA_PAPER_API_SECRET", "").strip()
    )


@unittest.skipUnless(_paper_keys_present(), "ALPACA_PAPER_API_KEY/SECRET not set")
class RebalanceLiveReadTests(unittest.TestCase):
    def test_live_paper_read_prints_tickets_without_posting(self):
        client = paper_broker_client(paper_host())
        for name in ("submit", "submit_order", "place_order", "submit_paper_order"):
            self.assertFalse(hasattr(client, name), name)
        report = proposed_rebalance(fixtures=FIXTURES, client=client)
        self.assertEqual(report["mode"], "paper-rebalance-dry-run")
        self.assertTrue(report["ok"])
        self.assertFalse(report["submitted"])
        self.assertFalse(report["local_applied"])
        self.assertEqual(report["order_post"], "disabled")
        self.assertIsInstance(report["tickets"], list)
        self.assertTrue(all(row["submitted"] is False for row in report["tickets"]))
        dumped = json.dumps(report)
        self.assertNotIn(os.environ["ALPACA_PAPER_API_KEY"], dumped)
        self.assertNotIn(os.environ["ALPACA_PAPER_API_SECRET"], dumped)
        self.assertNotIn("account_number", dumped)


if __name__ == "__main__":
    unittest.main()
