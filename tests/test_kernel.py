import io
import json
import sqlite3
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

from signal_sim import cli
from signal_sim.events import Event, EventValidationError
from signal_sim.indicators import rank_candidates
from signal_sim.store import EventStore


UTC = timezone.utc


def event(**overrides):
    values = {
        "id": "event-1",
        "source": "fixture",
        "kind": "news",
        "ticker": "NVDA",
        "entities": ["NVIDIA"],
        "headline": "Fixture headline",
        "url": "https://example.invalid/event-1",
        "occurred_at": "2026-09-02T09:00:00Z",
        "filed_at": None,
        "observed_at": "2026-09-02T10:00:00Z",
        "confidence": 0.9,
        "raw_ref": "fixture:event-1",
    }
    values.update(overrides)
    return Event.from_dict(values)


class EventValidationTests(unittest.TestCase):
    def test_from_dict_normalizes_altdata_without_canonical_optional_fields(self):
        values = {
            "id": "ptr-house-2026-0001",
            "source": "house-clerk",
            "kind": "congress_trade",
            "ticker": "NVDA",
            "entities": ["NVIDIA"],
            "person": "Rep. Example Member",
            "transaction": "purchase",
            "amount_range_usd": [15001, 50000],
            "occurred_at": "2026-07-15T00:00:00Z",
            "filed_at": "2026-08-10T21:00:00Z",
            "observed_at": "2026-08-11T14:02:00Z",
            "raw_ref": "fixture:house-clerk-ptr:example-doc-1",
        }

        parsed = Event.from_dict(values)

        self.assertEqual(parsed.source, "fixture")
        self.assertEqual(parsed.headline, "")
        self.assertEqual(parsed.url, "")
        self.assertEqual(parsed.confidence, 0.0)

    def test_from_dict_maps_sec_edgar_source(self):
        parsed = event(source="sec-edgar")

        self.assertEqual(parsed.source, "edgar")

    def test_first_seen_at_is_observed_at_not_occurred_at(self):
        parsed = event(
            occurred_at="2026-07-15T00:00:00Z",
            observed_at="2026-08-11T14:02:00Z",
        )
        self.assertEqual(parsed.first_seen_at, parsed.observed_at)
        self.assertNotEqual(parsed.first_seen_at, parsed.occurred_at)

    def test_from_dict_rejects_first_seen_at_that_disagrees_with_observed_at(self):
        with self.assertRaisesRegex(EventValidationError, "first_seen_at"):
            event(
                observed_at="2026-09-02T10:00:00Z",
                first_seen_at="2026-09-02T09:00:00Z",
            )

    def test_from_dict_accepts_first_seen_at_as_observed_at_when_missing(self):
        values = {
            "id": "alias-first-seen",
            "source": "fixture",
            "kind": "news",
            "ticker": "NVDA",
            "entities": ["NVIDIA"],
            "headline": "Fixture headline",
            "url": "https://example.invalid/alias-first-seen",
            "occurred_at": "2026-09-02T09:00:00Z",
            "filed_at": None,
            "first_seen_at": "2026-09-02T10:00:00Z",
            "confidence": 0.9,
            "raw_ref": "fixture:alias-first-seen",
        }
        parsed = Event.from_dict(values)
        self.assertEqual(parsed.observed_at, parsed.first_seen_at)
        self.assertEqual(parsed.first_seen_at.isoformat().replace("+00:00", "Z"), "2026-09-02T10:00:00Z")

    def test_from_dict_accepts_matching_first_seen_at_and_observed_at(self):
        parsed = event(
            observed_at="2026-09-02T10:00:00Z",
            first_seen_at="2026-09-02T10:00:00Z",
        )
        self.assertEqual(parsed.first_seen_at, parsed.observed_at)

    def test_from_dict_rejects_published_at_that_disagrees_with_occurred_at(self):
        with self.assertRaisesRegex(EventValidationError, "published_at"):
            event(
                occurred_at="2026-09-02T09:00:00Z",
                published_at="2026-09-02T08:00:00Z",
            )

    def test_event_store_sql_is_insert_only(self):
        source = Path(EventStore.__module__.replace(".", "/") + ".py")
        if not source.exists():
            source = Path(__file__).resolve().parent.parent / "signal_sim" / "store.py"
        text = source.read_text(encoding="utf-8")
        self.assertIn("INSERT INTO events", text)
        lowered = text.lower()
        self.assertNotIn("update ", lowered)
        self.assertNotIn("replace", lowered)
        self.assertNotIn("on conflict", lowered)
        self.assertNotIn("delete from events", lowered)

    def test_published_at_is_occurred_at_not_observed_at(self):
        parsed = event(
            occurred_at="2026-07-15T00:00:00Z",
            observed_at="2026-08-11T14:02:00Z",
        )
        self.assertEqual(parsed.published_at, parsed.occurred_at)
        self.assertNotEqual(parsed.published_at, parsed.observed_at)

    def test_rejects_congress_trade_observed_before_filing(self):
        with self.assertRaises(EventValidationError):
            event(
                kind="congress_trade",
                filed_at="2026-09-02T11:00:00Z",
                observed_at="2026-09-02T10:59:59Z",
            )


class RankingTests(unittest.TestCase):
    def test_news_plus_honest_insider_confirmation_outranks_news_only(self):
        events = [
            event(id="nvda-news", kind="news", ticker="NVDA"),
            event(
                id="nvda-insider",
                kind="insider",
                ticker="NVDA",
                filed_at="2026-09-02T09:30:00Z",
                observed_at="2026-09-02T10:30:00Z",
            ),
            event(id="dis-news", kind="news", ticker="DIS"),
        ]

        candidates = rank_candidates(
            events,
            window_start=datetime(2026, 9, 2, 9, tzinfo=UTC),
            window_end=datetime(2026, 9, 2, 11, tzinfo=UTC),
        )

        self.assertEqual([candidate["ticker"] for candidate in candidates[:2]], ["NVDA", "DIS"])
        self.assertEqual(candidates[0]["score"], 2)
        self.assertEqual(candidates[1]["score"], 1)

    def test_sqlite_store_round_trips_canonical_events(self):
        connection = sqlite3.connect(":memory:")
        store = EventStore(connection)
        original = event(id="stored")

        store.add(original)

        self.assertEqual(store.all(), [original])
        with self.assertRaises(ValueError):
            store.add(event(id="stored", headline="rewrite"))
        self.assertEqual(store.all()[0].headline, original.headline)

    def test_amendment_is_a_new_event_not_an_edit(self):
        connection = sqlite3.connect(":memory:")
        store = EventStore(connection)
        original = event(
            id="ptr-1",
            kind="congress_trade",
            occurred_at="2026-07-15T00:00:00Z",
            filed_at="2026-08-10T21:00:00Z",
            observed_at="2026-08-11T14:02:00Z",
        )
        store.add(original)
        amendment = event(
            id="ptr-1-amend",
            kind="congress_trade",
            occurred_at="2026-07-15T00:00:00Z",
            filed_at="2026-08-20T21:00:00Z",
            observed_at="2026-08-21T14:02:00Z",
            headline="corrected amount",
        )
        store.amend(amendment, supersedes=original.id)
        rows = {row.id: row for row in store.all()}
        self.assertEqual(set(rows), {"ptr-1", "ptr-1-amend"})
        self.assertEqual(rows["ptr-1"].to_dict(), original.to_dict())
        self.assertEqual(rows["ptr-1"].observed_at, original.observed_at)
        self.assertEqual(rows["ptr-1-amend"].headline, "corrected amount")
        self.assertGreater(rows["ptr-1-amend"].observed_at, rows["ptr-1"].observed_at)
        with self.assertRaisesRegex(ValueError, "new event id"):
            store.amend(event(id="ptr-1", headline="rewrite"), supersedes="ptr-1")
        self.assertEqual(rows["ptr-1"].to_dict(), original.to_dict())


class PaperOnlyCliTests(unittest.TestCase):
    def test_rank_output_contains_no_live_trading_hosts(self):
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = cli.main(["rank", "--fixtures"])

        payload = json.loads(output.getvalue())
        rendered = json.dumps(payload).lower()
        package = Path(__file__).resolve().parent.parent / "signal_sim"
        source = "\n".join(path.read_text(encoding="utf-8") for path in package.rglob("*.py")).lower()
        self.assertEqual(exit_code, 0)
        for host in ("api." + "alpaca.markets", "local" + "host", "127.0.0.1"):
            self.assertNotIn(host, rendered)
            self.assertNotIn(host, source)
        allowed = {"ticker", "score", "news_breakout", "insider_confirm", "gov_confirm"}
        self.assertTrue(all(set(item) <= allowed for item in payload))
        self.assertTrue(all({"ticker", "score", "news_breakout", "insider_confirm"} <= set(item) for item in payload))
        nvda = next(item for item in payload if item["ticker"] == "NVDA")
        self.assertGreaterEqual(nvda["insider_confirm"], 1)
        self.assertGreaterEqual(nvda.get("gov_confirm", 0), 1)
        self.assertEqual(nvda["news_breakout"], 1)

    def test_rank_fixtures_matches_replay_candidates_not_post_decision_prints(self):
        import os
        import shutil
        import tempfile

        from signal_sim.sim import run_fixture_replay

        rank_out = io.StringIO()
        with redirect_stdout(rank_out):
            self.assertEqual(cli.main(["rank", "--fixtures"]), 0)
        ranked = json.loads(rank_out.getvalue())
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        replay = run_fixture_replay(
            fixtures=Path(__file__).resolve().parent.parent / "fixtures",
            ledger_path=os.path.join(tmp, "ledger.sqlite"),
            audit_path=os.path.join(tmp, "audit.jsonl"),
            kill_root=tmp,
        )
        self.assertEqual(
            [row["ticker"] for row in ranked],
            [row["ticker"] for row in replay["candidates"]],
        )
        nvda = next(item for item in ranked if item["ticker"] == "NVDA")
        self.assertEqual(nvda["news_breakout"], 1)
        ranked_names = {row["ticker"] for row in ranked}
        self.assertTrue({"AAPL", "CVX", "CMCSA", "XLK"}.issubset(ranked_names))
        self.assertFalse({"AMZN", "GOOGL", "META"} & ranked_names)

    def test_rank_without_fixtures_flag_is_refused(self):
        output = io.StringIO()
        error = io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            exit_code = cli.main(["rank"])

        self.assertEqual(exit_code, 2)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("requires --fixtures", error.getvalue())

    def test_intensity_without_fixtures_flag_is_refused(self):
        output = io.StringIO()
        error = io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            exit_code = cli.main(["intensity"])

        self.assertEqual(exit_code, 2)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("requires --fixtures", error.getvalue())

    def test_marks_without_fixtures_flag_is_refused(self):
        output = io.StringIO()
        error = io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            exit_code = cli.main(["marks"])

        self.assertEqual(exit_code, 2)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("requires --fixtures", error.getvalue())

    def test_drift_without_fixtures_flag_is_refused(self):
        output = io.StringIO()
        error = io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            exit_code = cli.main(["drift"])

        self.assertEqual(exit_code, 2)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("requires --fixtures", error.getvalue())

    def test_diagnose_without_fixtures_flag_is_refused(self):
        output = io.StringIO()
        error = io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            exit_code = cli.main(["diagnose"])

        self.assertEqual(exit_code, 2)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("requires --fixtures", error.getvalue())


if __name__ == "__main__":
    unittest.main()
