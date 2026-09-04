"""Owner product lock: paper allocation across the frozen N-name universe."""

import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from signal_sim.events import Event
from signal_sim.indicators import SECTORS, UNIVERSE, load_universe, rank_candidates
from signal_sim.sizer import size_targets
from signal_sim.sources.altdata import QuiverSource, load_events
from signal_sim.sources import worldmonitor
from signal_sim.safety import LookaheadError


REPO_FIXTURES = __import__("pathlib").Path(__file__).resolve().parent.parent / "fixtures"


class ProductLockTests(unittest.TestCase):
    def test_unit_tests_keep_a_three_name_event_basket(self):
        events = [
            Event.from_dict(
                {
                    "id": f"lock-{ticker}",
                    "source": "fixture",
                    "kind": "news",
                    "ticker": ticker,
                    "entities": [ticker],
                    "headline": "fixture",
                    "url": "",
                    "occurred_at": "2026-09-02T09:00:00Z",
                    "filed_at": None,
                    "observed_at": "2026-09-02T10:00:00Z",
                    "confidence": 1.0,
                    "raw_ref": f"fixture:{ticker}",
                }
            )
            for ticker in ("NVDA", "XLE", "DIS")
        ]
        rows = rank_candidates(events, universe=("NVDA", "XLE", "DIS"))
        self.assertEqual({row["ticker"] for row in rows}, {"NVDA", "XLE", "DIS"})

    def test_sizer_emits_signed_target_and_horizon_for_more_than_three_names(self):
        names = [{"ticker": ticker, "score": 1} for ticker in UNIVERSE]
        targets, skipped = size_targets(names, size_frac=0.05, horizon_hours=34.75)
        self.assertGreater(len(targets), 3)
        self.assertTrue(all(row["side"] == "buy" for row in targets))
        self.assertTrue(all(row["horizon_hours"] == 34.75 for row in targets))
        self.assertLess(len(skipped), len(names))

    def test_frozen_universe_is_larger_than_three_and_covers_the_sectors(self):
        universe = load_universe()
        self.assertGreater(len(universe), 3)
        self.assertTrue({"NVDA", "XLE", "DIS", "SPY", "XOM"}.issubset(set(universe)))
        self.assertEqual(universe, UNIVERSE)
        self.assertEqual(set(SECTORS), {"tech", "energy", "media", "etf"})
        self.assertEqual({ticker for names in SECTORS.values() for ticker in names}, set(universe))

    def test_quiver_and_world_monitor_live_stay_stubbed_without_keys(self):
        with mock.patch("signal_sim.sources.altdata.read_env", return_value=None):
            with self.assertRaisesRegex(NotImplementedError, r"no verified key \+ terms"):
                QuiverSource().live()
        with mock.patch("signal_sim.sources.worldmonitor.read_env", return_value=None):
            with mock.patch("signal_sim.sources.worldmonitor.urllib.request.urlopen") as urlopen:
                with self.assertRaisesRegex(ValueError, "WORLD_MONITOR_KEY is missing"):
                    worldmonitor.live()
            urlopen.assert_not_called()

    def test_lookahead_poison_fails_and_congress_orders_on_observed_not_trade_date(self):
        with self.assertRaises(LookaheadError):
            load_events(str(REPO_FIXTURES / "altdata"))
        congress = Event.from_dict(
            {
                "id": "lock-congress",
                "source": "fixture",
                "kind": "congress_trade",
                "ticker": "NVDA",
                "entities": ["NVIDIA"],
                "headline": "",
                "url": "",
                "occurred_at": "2026-07-15T00:00:00Z",
                "filed_at": "2026-08-10T21:00:00Z",
                "observed_at": "2026-08-11T14:02:00Z",
                "confidence": 1.0,
                "raw_ref": "fixture:lock-congress",
            }
        )
        self.assertEqual(congress.first_seen_at, congress.observed_at)
        self.assertLess(congress.occurred_at, congress.filed_at)
        self.assertLessEqual(congress.filed_at, congress.observed_at)
        window_end = datetime(2026, 7, 20, tzinfo=timezone.utc)
        self.assertEqual(
            rank_candidates([congress], window_end=window_end, universe=("NVDA",)),
            [],
        )

    def test_pyproject_is_stdlib_only_and_editable(self):
        import tomllib

        repo = Path(__file__).resolve().parent.parent
        raw = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(raw["project"]["dependencies"], [])
        self.assertIn("3.12", raw["project"]["requires-python"])
        text = (repo / "pyproject.toml").read_text(encoding="utf-8").lower()
        for fragment in ("yfinance", "yahoo", "stooq", "alpaca", "ibkr", "quiver"):
            self.assertNotIn(fragment, text, fragment)

    def test_package_has_no_yfinance_or_vendor_bar_client(self):
        package = __import__("pathlib").Path(__file__).resolve().parent.parent / "signal_sim"
        source = "\n".join(path.read_text(encoding="utf-8") for path in package.rglob("*.py")).lower()
        for fragment in ("yfinance", "stooq", "yahoo"):
            self.assertNotIn(fragment, source, fragment)

    def test_sim_does_not_import_cli(self):
        import ast
        import subprocess
        import sys

        from signal_sim import sim

        tree = ast.parse(Path(sim.__file__).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module)
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
        self.assertNotIn("cli", imported)
        self.assertNotIn("signal_sim.cli", imported)
        probe = (
            "import signal_sim.sim, sys; "
            "raise SystemExit(0 if 'signal_sim.cli' not in sys.modules else 1)"
        )
        completed = subprocess.run([sys.executable, "-c", probe], check=False)
        self.assertEqual(completed.returncode, 0)
