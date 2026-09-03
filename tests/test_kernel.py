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
        self.assertTrue(all(set(item) == {"ticker", "score", "news_breakout", "insider_confirm"} for item in payload))
        nvda = next(item for item in payload if item["ticker"] == "NVDA")
        self.assertGreaterEqual(nvda["insider_confirm"], 1)

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


if __name__ == "__main__":
    unittest.main()
