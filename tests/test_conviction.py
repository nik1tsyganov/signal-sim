"""Declared score' and conviction sizing. Not alpha. Not a fit."""

import json
import math
import unittest
from datetime import datetime, timezone
from pathlib import Path

from signal_sim.conviction import (
    EQUAL_WEIGHT_2026_09_04,
    compare_equal_weight_book,
    conviction_targets,
    research_rank_rows,
    score_features_from_research_artifact,
    score_prime_terms,
)
from signal_sim.events import Event
from signal_sim.params import (
    CONVICTION_MAX_NAME_FRAC,
    CONVICTION_MIN_SCORE,
    CONVICTION_W_CONGRESS,
    CONVICTION_W_INSIDER,
    HALF_LIFE_HOURS,
    conviction_params,
)
from signal_sim.rebalance import plan_rebalance_tickets
from signal_sim.research import run_research


REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "fixtures"
ARTIFACT = REPO / "docs" / "research" / "2026-09-04.json"
EQUAL_WEIGHT_ARTIFACT = REPO / "docs" / "research" / "2026-09-04-equal-weight.json"
UTC = timezone.utc


def _event(ticker, source="quiver", kind="news", event_id=None, **overrides):
    values = {
        "id": event_id or f"{source}-{kind}-{ticker}",
        "source": source,
        "kind": kind,
        "ticker": ticker,
        "entities": [ticker],
        "headline": "SECRET HEADLINE about a person",
        "url": "https://example.invalid/pii",
        "occurred_at": "2026-09-04T16:00:00Z",
        "filed_at": "2026-09-04T16:00:00Z" if kind in {"congress_trade", "insider"} else None,
        "observed_at": "2026-09-04T16:00:00Z",
        "confidence": 1.0,
        "raw_ref": "raw-pii-ref",
    }
    values.update(overrides)
    return Event.from_dict(values)


class ScorePrimeTests(unittest.TestCase):
    def test_formula_terms_match_declared_weights(self):
        terms = score_prime_terms(
            news_breakout=2,
            congress_confirm=1,
            insider_confirm=1,
            gov_confirm=1,
            quiver_count=20,
            intel_brief=1,
            wm_intel=1,
            chokepoint=1,
            lag_h=0.0,
        )
        self.assertAlmostEqual(terms["news_term"], math.log1p(2))
        self.assertAlmostEqual(terms["q_term"], 1.0)
        self.assertAlmostEqual(terms["wm_term"], 3.0)
        self.assertAlmostEqual(terms["rec_term"], 1.0)
        expected = (
            0.75 * math.log1p(2)
            + 3.0
            + 3.0
            + 2.0
            + 3.0
            + 2.0 * 3.0
            + 2.0
        )
        self.assertAlmostEqual(terms["score"], expected)

    def test_missing_quiver_and_lag_are_zero_terms(self):
        terms = score_prime_terms(news_breakout=1)
        self.assertAlmostEqual(terms["q_term"], 0.0)
        self.assertAlmostEqual(terms["rec_term"], 0.0)
        self.assertAlmostEqual(terms["score"], 0.75 * math.log1p(1))

    def test_unlumps_congress_from_insider(self):
        congress = score_prime_terms(congress_confirm=1)
        insider = score_prime_terms(insider_confirm=1)
        both = score_prime_terms(congress_confirm=1, insider_confirm=1)
        self.assertAlmostEqual(congress["score"], CONVICTION_W_CONGRESS)
        self.assertAlmostEqual(insider["score"], CONVICTION_W_INSIDER)
        self.assertAlmostEqual(both["score"], CONVICTION_W_CONGRESS + CONVICTION_W_INSIDER)
        self.assertGreater(both["score"], congress["score"])

    def test_recency_uses_declared_half_life(self):
        terms = score_prime_terms(lag_h=HALF_LIFE_HOURS)
        self.assertAlmostEqual(terms["rec_term"], math.exp(-1.0))


class ConvictionSizerTests(unittest.TestCase):
    def test_higher_score_gets_more_of_the_gross(self):
        rows = [
            {"ticker": "NVDA", "score": 12.0},
            {"ticker": "HD", "score": 5.0},
            {"ticker": "SPY", "score": 1.2},
        ]
        uncapped, skipped = conviction_targets(rows, horizon_hours=24.0, max_name_frac=1.0)
        self.assertEqual(skipped, [])
        by_ticker = {row["ticker"]: row["target_frac"] for row in uncapped}
        self.assertGreater(by_ticker["NVDA"], by_ticker["HD"])
        self.assertGreater(by_ticker["HD"], by_ticker["SPY"])
        self.assertAlmostEqual(sum(by_ticker.values()), 1.0)
        capped, _ = conviction_targets(rows, horizon_hours=24.0)
        self.assertTrue(all(row["target_frac"] <= CONVICTION_MAX_NAME_FRAC + 1e-12 for row in capped))
        self.assertGreater(capped[0]["target_frac"], 0.0)

    def test_name_cap_and_low_score_zero(self):
        rows = [
            {"ticker": "NVDA", "score": 100.0},
            {"ticker": "SPY", "score": 0.5},
        ]
        targets, skipped = conviction_targets(
            rows, horizon_hours=24.0, max_gross_frac=1.0, max_name_frac=0.2
        )
        self.assertEqual([row["ticker"] for row in targets], ["NVDA"])
        self.assertAlmostEqual(targets[0]["target_frac"], 0.2)
        self.assertEqual(skipped, [{"ticker": "SPY", "reason": "below_min_score"}])

    def test_top_k_skips_the_rest(self):
        rows = [{"ticker": f"T{i:02d}", "score": float(20 - i)} for i in range(12)]
        targets, skipped = conviction_targets(rows, horizon_hours=24.0, top_k=10)
        self.assertEqual(len(targets), 10)
        self.assertEqual({row["reason"] for row in skipped}, {"outside_top_k"})


class ArtifactABTests(unittest.TestCase):
    def test_score_prime_on_2026_09_04_features_matches_illustrative_book(self):
        raw = json.loads(EQUAL_WEIGHT_ARTIFACT.read_text(encoding="utf-8"))
        comparison = compare_equal_weight_book(raw)
        self.assertEqual(tuple(comparison["before"]), EQUAL_WEIGHT_2026_09_04)
        self.assertEqual(
            [row["ticker"] for row in raw["proposed_book"]["targets"]],
            list(EQUAL_WEIGHT_2026_09_04),
        )
        self.assertEqual(
            set(comparison["enter"]),
            {"GOOGL", "HD", "ABT", "AMAT", "UNH", "AMZN"},
        )
        self.assertEqual(
            set(comparison["exit"]),
            {"NFLX", "CMCSA", "CVX", "DIS", "SPY", "XOM"},
        )
        self.assertAlmostEqual(comparison["nvda_frac"], 0.18, delta=0.03)
        self.assertGreater(comparison["nvda_frac"], comparison["xle_frac"])
        self.assertLess(comparison["xle_frac"], 0.15)
        nvda = next(row for row in comparison["targets"] if row["ticker"] == "NVDA")
        self.assertGreater(nvda["target_frac"], 0.1)
        self.assertTrue(all(row["target_frac"] <= 0.2 + 1e-12 for row in comparison["targets"]))

    def test_live_ops_artifact_stays_conviction_book(self):
        raw = json.loads(ARTIFACT.read_text(encoding="utf-8"))
        live_names = tuple(row["ticker"] for row in raw["proposed_book"]["targets"])
        self.assertEqual(
            live_names,
            ("NVDA", "XLE", "MSFT", "AAPL", "GOOGL", "HD", "ABT", "AMAT", "UNH", "AMZN"),
        )
        comparison = compare_equal_weight_book(raw)
        self.assertEqual(tuple(comparison["before"]), EQUAL_WEIGHT_2026_09_04)
        self.assertEqual(
            set(comparison["enter"]),
            {"GOOGL", "HD", "ABT", "AMAT", "UNH", "AMZN"},
        )

    def test_artifact_reconstruction_unlumps_congress(self):
        raw = json.loads(EQUAL_WEIGHT_ARTIFACT.read_text(encoding="utf-8"))
        rows = {row["ticker"]: row for row in score_features_from_research_artifact(raw)}
        self.assertEqual(rows["HD"]["congress_confirm"], 1)
        self.assertEqual(rows["HD"]["insider_confirm"], 0)
        self.assertEqual(rows["NFLX"]["congress_confirm"], 1)
        self.assertEqual(rows["NFLX"]["insider_confirm"], 0)
        self.assertGreater(rows["HD"]["score"], rows["NFLX"]["score"])


class ResearchConvictionTests(unittest.TestCase):
    def test_mocked_live_intel_names_enter_on_merit(self):
        events = [
            _event("NVDA", kind="congress_trade", event_id="nvda-c"),
            _event("NVDA", kind="insider", event_id="nvda-i"),
            _event("NVDA", kind="gov_contract", event_id="nvda-g", filed_at="2026-09-04T16:00:00Z"),
            *[_event("NVDA", kind="news", event_id=f"nvda-n{i}") for i in range(8)],
            *[_event("HD", kind="congress_trade", event_id=f"hd-c{i}") for i in range(9)],
            *[_event("ABT", kind="congress_trade", event_id=f"abt-c{i}") for i in range(9)],
            *[_event("AMAT", kind="congress_trade", event_id=f"amat-c{i}") for i in range(8)],
            _event("SPY", kind="news", event_id="spy-n1", source="fixture"),
            _event("SPY", kind="news", event_id="spy-n2", source="fixture"),
        ]
        when = datetime(2026, 9, 4, 16, 0, tzinfo=UTC)
        ranked = research_rank_rows(events, when, universe=("NVDA", "HD", "ABT", "AMAT", "SPY"))
        by_ticker = {row["ticker"]: row for row in ranked}
        self.assertGreater(by_ticker["HD"]["score"], by_ticker["SPY"]["score"])
        self.assertGreater(by_ticker["ABT"]["score"], CONVICTION_MIN_SCORE)
        targets, skipped = conviction_targets(ranked, horizon_hours=34.75)
        names = {row["ticker"] for row in targets}
        self.assertIn("HD", names)
        self.assertIn("ABT", names)
        self.assertIn("AMAT", names)
        self.assertNotIn("SPY", names)
        self.assertTrue(any(row["ticker"] == "SPY" for row in skipped))
        self.assertTrue(all(row["target_frac"] <= CONVICTION_MAX_NAME_FRAC + 1e-12 for row in targets))
        uncapped, _ = conviction_targets(ranked, horizon_hours=34.75, max_name_frac=1.0)
        fracs = [row["target_frac"] for row in uncapped]
        self.assertGreater(len({round(frac, 6) for frac in fracs}), 1)
        nvda_frac = next(row["target_frac"] for row in uncapped if row["ticker"] == "NVDA")
        self.assertEqual(nvda_frac, max(fracs))

    def test_research_artifact_stamps_target_frac_and_formula(self):
        report = run_research(
            fixtures=FIXTURES,
            live_events=[
                _event("TSLA", kind="congress_trade"),
                _event("HD", kind="congress_trade", event_id="hd-c"),
                *[_event("HD", kind="congress_trade", event_id=f"hd-{i}") for i in range(6)],
            ],
            when=datetime(2026, 9, 4, 16, 0, tzinfo=UTC),
        )
        dumped = json.dumps(report)
        self.assertNotIn("SECRET HEADLINE", dumped)
        self.assertIn("not fitted", report["conviction"]["note"].lower())
        self.assertIn("score'", report["conviction"]["formula"])
        self.assertAlmostEqual(report["proposed_book"]["max_name_frac"], 0.2)
        targets = report["proposed_book"]["targets"]
        self.assertTrue(targets)
        self.assertTrue(all("target_frac" in row for row in targets))
        self.assertTrue(all(row["target_frac"] <= 0.2 + 1e-12 for row in targets))
        self.assertIn("HD", {row["ticker"] for row in report["rank"]})
        hd_rank = next(row for row in report["rank"] if row["ticker"] == "HD")
        self.assertGreaterEqual(hd_rank["congress_confirm"], 1)
        self.assertEqual(hd_rank["insider_confirm"], 0)


class SellRuleTests(unittest.TestCase):
    def test_leftover_close_and_partial_trim(self):
        marks = {
            "NFLX": {"entry_px": 100.0, "kind": "fixture_mark", "source": "fixture"},
            "NVDA": {"entry_px": 200.0, "kind": "fixture_mark", "source": "fixture"},
        }
        tickets, skipped = plan_rebalance_tickets(
            targets=[{"ticker": "NVDA", "target_frac": 0.10, "score": 8.0}],
            marks=marks,
            held={
                "NFLX": {"shares": 5.0, "side": "long"},
                "NVDA": {"shares": 80.0, "side": "long"},
            },
            cash=100000.0,
            allocation=100000.0,
            cost_bps=0.0,
            signal="research-live",
            decision_at="2026-09-02T10:15:00Z",
            session="20260904",
            min_score=1.0,
            trim_band=0.02,
        )
        self.assertEqual(skipped, [])
        by_symbol = {row["symbol"]: row for row in tickets}
        self.assertEqual(by_symbol["NFLX"]["action"], "close")
        self.assertEqual(by_symbol["NFLX"]["side"], "sell")
        self.assertAlmostEqual(by_symbol["NFLX"]["qty"], -5.0)
        self.assertIn("close leftover", by_symbol["NFLX"]["rationale"])
        self.assertEqual(by_symbol["NVDA"]["action"], "adjust")
        self.assertEqual(by_symbol["NVDA"]["side"], "sell")
        self.assertIn("overweight beyond band", by_symbol["NVDA"]["rationale"])
        self.assertAlmostEqual(by_symbol["NVDA"]["qty"], 50.0 - 80.0)

    def test_score_below_threshold_closes_even_if_named(self):
        marks = {"SPY": {"entry_px": 40.0, "kind": "fixture_mark", "source": "fixture"}}
        tickets, skipped = plan_rebalance_tickets(
            targets=[{"ticker": "SPY", "target_frac": 0.10, "score": 0.4}],
            marks=marks,
            held={"SPY": {"shares": 10.0, "side": "long"}},
            cash=100000.0,
            allocation=100000.0,
            cost_bps=0.0,
            signal="research-live",
            decision_at="2026-09-02T10:15:00Z",
            session="20260904",
            min_score=1.0,
            trim_band=0.02,
        )
        self.assertEqual(skipped, [])
        self.assertEqual(tickets[0]["action"], "close")
        self.assertIn("score below min_score", tickets[0]["rationale"])

    def test_within_band_does_not_trim(self):
        marks = {"NVDA": {"entry_px": 200.0, "kind": "fixture_mark", "source": "fixture"}}
        tickets, skipped = plan_rebalance_tickets(
            targets=[{"ticker": "NVDA", "target_frac": 0.10, "score": 8.0}],
            marks=marks,
            held={"NVDA": {"shares": 51.0, "side": "long"}},
            cash=100000.0,
            allocation=100000.0,
            cost_bps=0.0,
            signal="research-live",
            decision_at="2026-09-02T10:15:00Z",
            session="20260904",
            min_score=1.0,
            trim_band=0.02,
        )
        self.assertEqual(tickets, [])
        self.assertEqual(skipped, [])


class ConvictionParamTests(unittest.TestCase):
    def test_declared_constants_are_not_in_locked_digest(self):
        from signal_sim.params import frozen_operate_params, params_sha256

        frozen = frozen_operate_params()
        self.assertNotIn("conviction", frozen)
        self.assertNotIn("w_news", frozen)
        stamp = conviction_params()
        self.assertAlmostEqual(stamp["max_name_frac"], 0.2)
        self.assertIn("not fitted", stamp["note"].lower())
        self.assertNotEqual(params_sha256(), params_sha256(stamp))


if __name__ == "__main__":
    unittest.main()
