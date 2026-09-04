"""Daily paper telemetry pack. Read-only. Not alpha."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from signal_sim import cli
from signal_sim.telemetry import (
    build_telemetry_pack,
    default_telemetry_path,
    write_telemetry_pack,
)


class TelemetryPackTests(unittest.TestCase):
    def test_pack_has_math_eng_fields_and_no_future_join(self):
        root = Path(tempfile.mkdtemp())
        research = {
            "date": "2026-09-04",
            "research_at": "2026-09-04T16:00:00Z",
            "proposed_book": {
                "targets": [
                    {
                        "ticker": "NVDA",
                        "score": 10.0,
                        "target_frac": 0.2,
                        "news_term": 1.0,
                        "sent_term": -0.5,
                    }
                ],
                "max_gross_invest": 0.8,
                "cash_reserve_frac": 0.2,
                "book_gross": 0.2,
            },
            "rank": [{"ticker": "NVDA", "news_term": 1.0, "sent_term": -0.5}],
            "feeds": {"quiver": {"n": 4, "tickers": {"NVDA": 4}}, "worldmonitor": {"n": 1}},
            "sentiment": {"llm": False, "n_negative": 1},
            "universe": {"operating": ["NVDA"]},
        }
        performance = {
            "account": {"cash": "20000", "equity": "100000"},
            "positions": {"n": 1, "symbols": {"NVDA": "10"}},
            "n_fills": 2,
            "label": "paper",
        }
        prior = {"date": "2026-09-03", "account": {"cash": "25000", "equity": "99000"}}
        rebalance = {
            "tickets": [{"symbol": "NFLX", "sell_reason": "drop_from_book"}],
            "marks": {"fixture": ["NVDA"], "paper_data": [], "unmarked": []},
        }
        pack = build_telemetry_pack(
            root=root,
            when=datetime(2026, 9, 4, 16, 0, tzinfo=timezone.utc),
            research=research,
            performance=performance,
            prior_performance=prior,
            rebalance=rebalance,
        )
        self.assertEqual(pack["mode"], "paper-telemetry")
        self.assertTrue(pack["not_alpha"])
        self.assertTrue(pack["paper_only"])
        self.assertFalse(pack["submitted"])
        self.assertEqual(pack["date"], "2026-09-04")
        self.assertEqual(pack["research_at"], "2026-09-04T16:00:00Z")
        self.assertEqual(pack["decision_at"], "2026-09-04T16:00:00Z")
        self.assertIn("params_sha256", pack)
        self.assertEqual(pack["equity"], "100000")
        self.assertEqual(pack["cash"], "20000")
        self.assertAlmostEqual(pack["gross"], 0.2)
        self.assertAlmostEqual(pack["cash_reserve_frac"], 0.2)
        self.assertAlmostEqual(pack["equity_delta"], 1000.0)
        self.assertEqual(pack["feeds_n"]["quiver"], 4)
        self.assertIn("sent_term", pack["score_prime_drivers"])
        self.assertEqual(pack["book"][0]["ticker"], "NVDA")
        self.assertEqual(pack["sell_reasons"]["NFLX"], ["drop_from_book"])
        self.assertIn("NFLX", {row["ticker"] for row in pack["book"]})
        self.assertFalse(pack["sentiment"]["llm"])
        self.assertEqual(pack["mark_kinds"]["fixture"], ["NVDA"])
        self.assertNotIn("decision_verdict", pack)
        dumped = json.dumps(pack)
        self.assertNotIn("headline", dumped.lower())

        (root / "docs" / "decision").mkdir(parents=True)
        (root / "docs" / "decision" / "2026-09-04.json").write_text(
            json.dumps({"verdict": "HOLD", "recommend_submit": False, "reasons": ["open orders"]}),
            encoding="utf-8",
        )
        (root / "docs" / "baseline").mkdir(parents=True)
        (root / "docs" / "baseline" / "2026-09-04.json").write_text(
            json.dumps(
                {
                    "equity_delta_conviction": 12.5,
                    "equity_delta_equal": 4.0,
                    "delta_conviction_minus_equal": 8.5,
                }
            ),
            encoding="utf-8",
        )
        cited = build_telemetry_pack(
            root=root,
            when=datetime(2026, 9, 4, 16, 0, tzinfo=timezone.utc),
            research=research,
            performance=performance,
            prior_performance=prior,
            rebalance=rebalance,
        )
        self.assertEqual(cited["decision_verdict"], "HOLD")
        self.assertFalse(cited["recommend_submit"])
        self.assertAlmostEqual(cited["equity_delta_conviction"], 12.5)
        self.assertAlmostEqual(cited["equity_delta_equal"], 4.0)

    def test_write_and_cli_are_read_only(self):
        tmp = Path(tempfile.mkdtemp())
        out = tmp / "2026-09-04.json"
        pack = build_telemetry_pack(
            root=tmp,
            when=datetime(2026, 9, 4, tzinfo=timezone.utc),
            research={
                "date": "2026-09-04",
                "research_at": "2026-09-04T16:00:00Z",
                "proposed_book": {
                    "targets": [{"ticker": "NVDA", "score": 2, "target_frac": 0.1}],
                    "max_gross_invest": 0.8,
                    "cash_reserve_frac": 0.2,
                    "book_gross": 0.1,
                },
                "universe": {"operating": ["NVDA"]},
            },
            performance={"account": {"cash": "1", "equity": "2"}},
        )
        written = write_telemetry_pack(pack, out, markdown=True)
        self.assertTrue(written.is_file())
        self.assertTrue(written.with_suffix(".md").is_file())
        self.assertIn("Not alpha", written.with_suffix(".md").read_text(encoding="utf-8"))
        self.assertEqual(
            default_telemetry_path(tmp, datetime(2026, 9, 4, tzinfo=timezone.utc)),
            tmp / "docs" / "telemetry" / "2026-09-04.json",
        )

        printed = io.StringIO()
        error = io.StringIO()
        with mock.patch("signal_sim.cli.missing_paper_keys", return_value=["ALPACA_PAPER_API_KEY"]), mock.patch(
            "signal_sim.cli.build_telemetry_pack", return_value=pack
        ), redirect_stdout(printed), redirect_stderr(error):
            code = cli.main(["telemetry", "--write", "--out", str(out)])
        self.assertEqual(code, 0)
        payload = json.loads(printed.getvalue())
        self.assertTrue(payload["not_alpha"])
        self.assertFalse(payload["submitted"])
        self.assertIn("paper, not alpha", error.getvalue())


if __name__ == "__main__":
    unittest.main()
