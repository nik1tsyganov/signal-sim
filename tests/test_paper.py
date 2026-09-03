"""Tests for the single paper-order path (docs/alt-data-and-safety.md section 3, section 5 item 5).

submit_paper_order() is the only code path that can create an order row.
These tests pin R1 (paper-only consult), R2 (no live endpoint constructible),
R3 (fail-closed kill-switch), R8 (audit line before success), and R9 (the
plain-code validator, including the duplicate-proposal refusal).

Broker hosts are assembled from pieces so no contiguous live-broker fragment
appears in this repo (SafetyRailTests scans signal_sim/; tests stay clean too).
No test touches the repo-root KILL file - kill-switch tests use temp dirs.
"""

import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from datetime import datetime
from unittest import mock

from signal_sim.indicators import UNIVERSE
from signal_sim.paper import (
    LiveEndpointError,
    OrderRefused,
    paper_broker_client,
    submit_paper_order,
)

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LIVE_BROKER_HOST = "api." + "alpaca" + ".markets"
PAPER_BROKER_HOST = "paper-api." + "alpaca" + ".markets"
IBKR_LIVE_PORTS = (7496, 4001)
IBKR_PAPER_PORT = 7497


def good_proposal(**overrides):
    values = {
        "ticker": "NVDA",
        "side": "buy",
        "size_frac": 0.25,
        "confidence": 0.6,
        "rationale": "news breakout with insider confirm (fixture-derived)",
        "event_ids": ["ptr-test-1", "form4-test-1"],
        "idempotency_key": "prop-0001",
    }
    values.update(overrides)
    return values


class PaperOrderPathBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ledger = os.path.join(self.tmp, "ledger.sqlite")
        self.audit = os.path.join(self.tmp, "audit.jsonl")

    def _submit(self, proposal=None, **overrides):
        kwargs = {
            "ledger_path": self.ledger,
            "audit_path": self.audit,
            "mark_px": 178.5,
            "kill_root": self.tmp,
        }
        kwargs.update(overrides)
        if proposal is None:
            proposal = good_proposal()
        return submit_paper_order(proposal, **kwargs)

    def _rows(self, table):
        if not os.path.exists(self.ledger):
            return []
        con = sqlite3.connect(self.ledger)
        try:
            try:
                cur = con.execute(f"SELECT * FROM {table}")
            except sqlite3.OperationalError:
                return []
            cur.row_factory = sqlite3.Row
            return cur.fetchall()
        finally:
            con.close()

    def _audit_lines(self):
        if not os.path.exists(self.audit):
            return []
        with open(self.audit, "r", encoding="utf-8") as f:
            return [json.loads(line) for line in f.read().splitlines() if line]

    def _assert_refused(self, proposal=None, **overrides):
        with self.assertRaises(OrderRefused):
            self._submit(proposal, **overrides)
        self.assertEqual(self._rows("orders"), [])
        self.assertEqual(self._rows("fills"), [])


class SubmitPaperOrderTests(PaperOrderPathBase):
    def test_fill_is_deterministic_at_mark_px(self):
        result = self._submit()
        self.assertEqual(result["status"], "filled")
        self.assertEqual(result["fill_px"], 178.5)
        self.assertEqual(result["ticker"], "NVDA")
        self.assertTrue(result["order_id"])

    def test_order_and_fill_rows_persisted(self):
        result = self._submit()
        orders = self._rows("orders")
        fills = self._rows("fills")
        self.assertEqual(len(orders), 1)
        self.assertEqual(len(fills), 1)
        self.assertEqual(orders[0]["ticker"], "NVDA")
        self.assertEqual(orders[0]["side"], "buy")
        self.assertEqual(orders[0]["status"], "filled")
        self.assertEqual(orders[0]["idempotency_key"], "prop-0001")
        self.assertEqual(fills[0]["order_id"], result["order_id"])
        self.assertEqual(fills[0]["price"], 178.5)

    def test_audit_line_written_with_required_fields(self):
        self._submit()
        lines = self._audit_lines()
        self.assertEqual(len(lines), 1)
        line = lines[0]
        self.assertEqual(line["event_ids"], ["ptr-test-1", "form4-test-1"])
        self.assertEqual(line["ticker"], "NVDA")
        self.assertEqual(line["side"], "buy")
        self.assertEqual(line["size_frac"], 0.25)
        self.assertEqual(line["verdict"], "approved")
        self.assertEqual(line["outcome"], "filled")
        decision_at = datetime.fromisoformat(line["decision_at"])
        self.assertIsNotNone(decision_at.tzinfo)

    def test_default_audit_path_is_derived_from_ledger_path(self):
        submit_paper_order(
            good_proposal(),
            ledger_path=self.ledger,
            mark_px=178.5,
            kill_root=self.tmp,
        )
        self.assertTrue(os.path.exists(self.ledger + ".audit.jsonl"))

    def test_refused_when_paper_only_is_false(self):
        with mock.patch("signal_sim.safety.PAPER_ONLY", False):
            self._assert_refused()
        self.assertEqual(self._audit_lines()[-1]["outcome"], "refused")

    def test_refused_when_paper_only_is_truthy_but_not_true(self):
        with mock.patch("signal_sim.safety.PAPER_ONLY", 1):
            self._assert_refused()

    def test_kill_file_refuses_order(self):
        with open(os.path.join(self.tmp, "KILL"), "w", encoding="utf-8") as f:
            f.write("stop")
        self._assert_refused()
        self.assertEqual(self._audit_lines()[-1]["outcome"], "refused")

    def test_kill_switch_error_fails_closed(self):
        with mock.patch(
            "signal_sim.safety.kill_switch_ok", side_effect=RuntimeError("boom")
        ):
            self._assert_refused()

    def test_kill_root_cannot_bypass_the_repo_root_check(self):
        # The no-arg (repo-root) check trips; the caller-supplied root is clean.
        # A clean kill_root must never override the repo-root refusal.
        def fake_kill_switch(root_dir=None):
            return root_dir is not None

        with mock.patch(
            "signal_sim.safety.kill_switch_ok", side_effect=fake_kill_switch
        ):
            self._assert_refused()

    def test_audit_write_failure_rolls_back_the_fill(self):
        with mock.patch(
            "signal_sim.paper._append_audit", side_effect=OSError("disk full")
        ):
            with self.assertRaises(OSError):
                self._submit()
        self.assertEqual(self._rows("orders"), [])
        self.assertEqual(self._rows("fills"), [])


class ValidatorTests(PaperOrderPathBase):
    def test_every_universe_ticker_is_accepted(self):
        for ticker in UNIVERSE:
            result = self._submit(
                good_proposal(ticker=ticker, idempotency_key=f"prop-{ticker}")
            )
            self.assertEqual(result["status"], "filled")
        self.assertEqual(len(self._rows("orders")), len(UNIVERSE))

    def test_unknown_ticker_is_refused(self):
        self._assert_refused(good_proposal(ticker="TSLA"))

    def test_bad_side_is_refused(self):
        self._assert_refused(good_proposal(side="short"))

    def test_size_frac_of_exactly_one_is_accepted(self):
        result = self._submit(good_proposal(size_frac=1.0))
        self.assertEqual(result["status"], "filled")

    def test_out_of_range_or_non_numeric_size_frac_is_refused(self):
        bad_sizes = (0, -0.1, 1.0001, 2, float("nan"), float("inf"), "0.5", True, None)
        for size_frac in bad_sizes:
            with self.subTest(size_frac=size_frac):
                self._assert_refused(good_proposal(size_frac=size_frac))

    def test_sell_size_frac_above_one_is_accepted_to_flatten(self):
        result = self._submit(good_proposal(side="sell", size_frac=1.5, idempotency_key="sell-flat"))
        self.assertEqual(result["status"], "filled")
        self.assertEqual(result["size_frac"], 1.5)
        self.assertEqual(result["side"], "sell")

    def test_missing_event_ids_is_refused_and_never_logged_filled(self):
        proposal = good_proposal()
        del proposal["event_ids"]
        self._assert_refused(proposal)
        lines = self._audit_lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["outcome"], "refused")
        self.assertNotIn("filled", [line["outcome"] for line in lines])

    def test_empty_or_malformed_event_ids_are_refused(self):
        for event_ids in ([], ["ok", 42], "ptr-test-1", [""]):
            with self.subTest(event_ids=event_ids):
                self._assert_refused(good_proposal(event_ids=event_ids))

    def test_refusal_audit_preserves_malformed_event_ids_as_text(self):
        self._assert_refused(good_proposal(event_ids=["ok", 42]))
        self.assertEqual(self._audit_lines()[-1]["event_ids"], ["ok", "42"])

    def test_missing_or_blank_idempotency_key_is_refused(self):
        proposal = good_proposal()
        del proposal["idempotency_key"]
        self._assert_refused(proposal)
        self.assertIsNone(self._audit_lines()[-1]["idempotency_key"])
        self._assert_refused(good_proposal(idempotency_key=""))

    def test_non_dict_proposal_is_refused(self):
        self._assert_refused(["not", "a", "dict"])

    def test_bad_mark_px_is_refused(self):
        for mark_px in (0, -5, float("nan"), True, "178.5", None):
            with self.subTest(mark_px=mark_px):
                self._assert_refused(mark_px=mark_px)


class IdempotencyTests(PaperOrderPathBase):
    def test_second_submit_with_same_key_is_refused(self):
        self._submit()
        with self.assertRaises(OrderRefused):
            self._submit(good_proposal(size_frac=0.5))
        self.assertEqual(len(self._rows("orders")), 1)
        self.assertEqual(len(self._rows("fills")), 1)
        lines = self._audit_lines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["outcome"], "filled")
        self.assertEqual(lines[1]["outcome"], "refused")
        self.assertEqual(lines[1]["idempotency_key"], "prop-0001")


class SingleOrderPathTests(unittest.TestCase):
    def test_only_paper_py_inserts_order_rows(self):
        package_dir = os.path.join(REPO_ROOT, "signal_sim")
        offenders = []
        scanned = 0
        for root, _dirs, files in os.walk(package_dir):
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(root, name)
                with open(path, "r", encoding="utf-8-sig") as f:
                    source = f.read().lower()
                if "insert into orders" in source and name != "paper.py":
                    offenders.append(path)
                scanned += 1
        self.assertGreater(scanned, 1)
        self.assertEqual(offenders, [])


class PaperBrokerClientTests(unittest.TestCase):
    def test_default_client_is_in_process(self):
        client = paper_broker_client()
        self.assertEqual(client.mode, "in-process")

    def test_in_process_client_holds_no_order_method(self):
        client = paper_broker_client()
        for name in ("submit", "submit_order", "place_order", "submit_paper_order"):
            self.assertFalse(hasattr(client, name), name)

    def test_live_broker_host_raises(self):
        with self.assertRaises(LiveEndpointError):
            paper_broker_client(LIVE_BROKER_HOST, 443)
        with self.assertRaises(LiveEndpointError):
            paper_broker_client(LIVE_BROKER_HOST.upper(), 443)
        with self.assertRaises(LiveEndpointError):
            paper_broker_client(LIVE_BROKER_HOST)

    def test_ibkr_live_ports_raise_on_any_host(self):
        hosts = ("localhost", "127.0.0.1", "gw.example.internal")
        for host in hosts:
            for port in IBKR_LIVE_PORTS:
                with self.subTest(host=host, port=port):
                    with self.assertRaises(LiveEndpointError):
                        paper_broker_client(host, port)

    def test_paper_broker_host_is_still_refused_in_v0(self):
        with self.assertRaises(ValueError):
            paper_broker_client(PAPER_BROKER_HOST, 443)

    def test_ibkr_paper_port_is_still_refused_in_v0(self):
        with self.assertRaises(ValueError):
            paper_broker_client("localhost", IBKR_PAPER_PORT)

    def test_no_forbidden_broker_fragments_in_paper_source(self):
        path = os.path.join(REPO_ROOT, "signal_sim", "paper.py")
        with open(path, "r", encoding="utf-8-sig") as f:
            source = f.read().lower()
        for fragment in (
            "alpaca" + ".markets",
            ":" + "7496",
            ":" + "4001",
            "interactive" + "brokers",
            "tradier",
            "tradestation",
        ):
            self.assertNotIn(fragment, source)


if __name__ == "__main__":
    unittest.main()
