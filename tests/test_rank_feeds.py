import unittest
from datetime import datetime, timezone

from signal_sim.events import Event
from signal_sim.indicators import rank_candidates


UTC = timezone.utc
WINDOW_START = datetime(2026, 9, 2, 9, tzinfo=UTC)
WINDOW_END = datetime(2026, 9, 2, 11, tzinfo=UTC)


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


class GovContractConfirmationTests(unittest.TestCase):
    def test_filed_gov_contract_confirms_and_raises_score(self):
        events = [
            event(id="nvda-news", kind="news", ticker="NVDA"),
            event(
                id="nvda-gov",
                kind="gov_contract",
                ticker="NVDA",
                filed_at="2026-09-02T09:30:00Z",
                observed_at="2026-09-02T10:30:00Z",
            ),
            event(id="dis-news", kind="news", ticker="DIS"),
            event(
                id="xle-gov",
                kind="gov_contract",
                ticker="XLE",
                filed_at="2026-09-02T09:30:00Z",
                observed_at="2026-09-02T10:30:00Z",
            ),
        ]

        candidates = rank_candidates(events, window_start=WINDOW_START, window_end=WINDOW_END)

        by_ticker = {row["ticker"]: row for row in candidates}
        self.assertEqual([row["ticker"] for row in candidates], ["NVDA", "DIS", "XLE"])
        self.assertEqual(by_ticker["NVDA"]["score"], 2)
        self.assertEqual(by_ticker["NVDA"]["news_breakout"], 1)
        self.assertEqual(by_ticker["NVDA"]["insider_confirm"], 0)
        self.assertEqual(by_ticker["NVDA"]["gov_confirm"], 1)
        self.assertEqual(by_ticker["XLE"]["score"], 1)
        self.assertEqual(by_ticker["XLE"]["gov_confirm"], 1)

    def test_gov_confirm_is_separate_from_insider_confirm(self):
        events = [
            event(id="nvda-news"),
            event(
                id="nvda-insider",
                kind="insider",
                ticker="NVDA",
                filed_at="2026-09-02T09:30:00Z",
                observed_at="2026-09-02T10:30:00Z",
            ),
            event(
                id="nvda-gov",
                kind="gov_contract",
                ticker="NVDA",
                filed_at="2026-09-02T09:30:00Z",
                observed_at="2026-09-02T10:35:00Z",
            ),
        ]

        candidates = rank_candidates(events, window_start=WINDOW_START, window_end=WINDOW_END)

        self.assertEqual(candidates[0]["ticker"], "NVDA")
        self.assertEqual(candidates[0]["insider_confirm"], 1)
        self.assertEqual(candidates[0]["gov_confirm"], 1)
        self.assertEqual(candidates[0]["score"], 3)

    def test_gov_contract_without_filed_at_does_not_confirm(self):
        events = [
            event(id="nvda-news"),
            event(
                id="nvda-gov-unfiled",
                kind="gov_contract",
                ticker="NVDA",
                filed_at=None,
                observed_at="2026-09-02T10:30:00Z",
            ),
        ]

        candidates = rank_candidates(events, window_start=WINDOW_START, window_end=WINDOW_END)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["score"], 1)
        self.assertNotIn("gov_confirm", candidates[0])

    def test_gov_contract_observed_after_window_end_does_not_confirm(self):
        events = [
            event(id="nvda-news"),
            event(
                id="nvda-gov-late",
                kind="gov_contract",
                ticker="NVDA",
                filed_at="2026-09-02T09:30:00Z",
                observed_at="2026-09-02T12:00:00Z",
            ),
        ]

        candidates = rank_candidates(events, window_start=WINDOW_START, window_end=WINDOW_END)

        self.assertEqual(candidates[0]["score"], 1)
        self.assertNotIn("gov_confirm", candidates[0])

    def test_news_insider_only_rows_keep_legacy_schema(self):
        events = [
            event(id="nvda-news"),
            event(
                id="nvda-insider",
                kind="insider",
                ticker="NVDA",
                filed_at="2026-09-02T09:30:00Z",
                observed_at="2026-09-02T10:30:00Z",
            ),
            event(id="dis-news", kind="news", ticker="DIS"),
        ]

        candidates = rank_candidates(events, window_start=WINDOW_START, window_end=WINDOW_END)

        for row in candidates:
            self.assertEqual(set(row), {"ticker", "score", "news_breakout", "insider_confirm"})
        self.assertEqual([row["ticker"] for row in candidates], ["NVDA", "DIS"])
        self.assertEqual(candidates[0]["score"], 2)


if __name__ == "__main__":
    unittest.main()
