"""Declared operate constants must match the checked-in manifest."""

import json
import unittest
from pathlib import Path

from signal_sim import drift, hawkes, params, shadow, walkforward
from signal_sim.sim import load_mark_book


REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "fixtures" / "params.json"


class ParamsManifestTests(unittest.TestCase):
    def test_module_constants_match_manifest(self):
        raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(hawkes.BASELINE, raw["hawkes_baseline"])
        self.assertEqual(hawkes.EXCITATION, raw["hawkes_excitation"])
        self.assertEqual(hawkes.DECAY, raw["hawkes_decay"])
        self.assertEqual(drift.HALF_LIFE_HOURS, raw["half_life_hours"])
        self.assertEqual(drift.MIN_RELATIVE_STATE, raw["min_relative_state"])
        self.assertEqual(walkforward.PLACEBO_SEED, raw["placebo_seed"])
        self.assertEqual(params.COST_BPS, raw["cost_bps"])
        self.assertEqual(params.DECISION_DELAY_HOURS, raw["decision_delay_hours"])
        self.assertEqual(params.STARTING_CASH, raw["starting_cash"])
        self.assertEqual(params.MAX_DRAWDOWN, raw["max_drawdown"])
        self.assertEqual(params.MAX_GROSS_FRAC, raw["max_gross_frac"])
        self.assertEqual(params.MAX_NAME_FRAC, raw["max_name_frac"])
        self.assertAlmostEqual(params.CONVICTION_MAX_NAME_FRAC, raw["conviction"]["max_name_frac"])
        self.assertAlmostEqual(params.CONVICTION_MAX_GROSS_INVEST, raw["conviction"]["max_gross_invest"])
        self.assertAlmostEqual(params.CONVICTION_W_SENT, raw["conviction"]["w_sent"])
        self.assertAlmostEqual(params.CONVICTION_SOFT_STOP, raw["conviction"]["soft_stop"])
        self.assertAlmostEqual(params.CONVICTION_DECAY_FLOOR, raw["conviction"]["decay_floor"])
        self.assertAlmostEqual(params.CONVICTION_W_NEWS, raw["conviction"]["w_news"])
        self.assertIn("not fitted", raw["conviction"]["note"].lower())
        self.assertEqual(params.HAWKES_BASELINE, raw["hawkes_baseline"])
        self.assertEqual(params.PLACEBO_SEED, raw["placebo_seed"])
        from signal_sim.sizer import MAX_GROSS_FRAC, MAX_NAME_FRAC

        self.assertEqual(MAX_GROSS_FRAC, raw["max_gross_frac"])
        self.assertEqual(MAX_NAME_FRAC, raw["max_name_frac"])

    def test_shadow_and_default_mark_books_read_the_same_manifest(self):
        from signal_sim.sim import TWO_NAME_MARKS, load_mark_path
        from signal_sim.walkforward import load_walkforward_folds

        raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
        frozen = shadow.frozen_params()
        self.assertEqual(frozen["half_life_hours"], raw["half_life_hours"])
        self.assertEqual(frozen["placebo_seed"], raw["placebo_seed"])
        self.assertEqual(frozen["hawkes_baseline"], raw["hawkes_baseline"])
        self.assertEqual(frozen["cost_bps"], raw["cost_bps"])
        self.assertEqual(frozen["decision_delay_hours"], raw["decision_delay_hours"])
        self.assertEqual(frozen["starting_cash"], raw["starting_cash"])
        self.assertEqual(frozen["max_drawdown"], raw["max_drawdown"])
        self.assertEqual(frozen["max_gross_frac"], raw["max_gross_frac"])
        self.assertEqual(frozen["max_name_frac"], raw["max_name_frac"])
        self.assertIn("not fitted", frozen["note"].lower())
        books = [load_mark_book(), load_mark_book(TWO_NAME_MARKS), *load_mark_path()]
        books.extend(load_walkforward_folds())
        for book in books:
            self.assertEqual(book["cost_bps"], raw["cost_bps"])
            self.assertEqual(book["decision_delay_hours"], raw["decision_delay_hours"])
            self.assertEqual(book["starting_cash"], raw["starting_cash"])
            self.assertEqual(book["max_drawdown"], raw["max_drawdown"])
            self.assertEqual(book["max_gross_frac"], raw["max_gross_frac"])
            self.assertEqual(book["max_name_frac"], raw["max_name_frac"])
        folds = json.loads((REPO / "fixtures" / "marks" / "walkforward.json").read_text(encoding="utf-8"))
        self.assertEqual(folds["cost_bps"], raw["cost_bps"])
        self.assertEqual(folds["decision_delay_hours"], raw["decision_delay_hours"])
        self.assertEqual(folds["starting_cash"], raw["starting_cash"])
        self.assertEqual(folds["max_drawdown"], raw["max_drawdown"])
        self.assertEqual(folds["max_gross_frac"], raw["max_gross_frac"])
        self.assertEqual(folds["max_name_frac"], raw["max_name_frac"])

    def test_mark_book_cannot_override_manifest_cost_or_delay(self):
        import os
        import tempfile

        from signal_sim.sim import load_mark_book

        raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
        book = json.loads((REPO / "fixtures" / "marks" / "universe.json").read_text(encoding="utf-8"))
        book["cost_bps"] = float(raw["cost_bps"]) + 1
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        self.addCleanup(os.unlink, handle.name)
        handle.write(json.dumps(book))
        handle.close()
        with self.assertRaisesRegex(ValueError, "cost_bps must match"):
            load_mark_book(handle.name)
        book["cost_bps"] = raw["cost_bps"]
        book["decision_delay_hours"] = float(raw["decision_delay_hours"]) + 1
        Path(handle.name).write_text(json.dumps(book), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "decision_delay_hours must match"):
            load_mark_book(handle.name)
        book["decision_delay_hours"] = raw["decision_delay_hours"]
        for field, bump in (
            ("starting_cash", 1),
            ("max_drawdown", 0.01),
            ("max_gross_frac", 0.1),
            ("max_name_frac", 0.1),
        ):
            book[field] = float(raw[field]) + bump
            Path(handle.name).write_text(json.dumps(book), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, f"{field} must match"):
                load_mark_book(handle.name)
            book[field] = raw[field]
        book["size_frac"] = 0.25
        Path(handle.name).write_text(json.dumps(book), encoding="utf-8")
        parsed = load_mark_book(handle.name)
        self.assertAlmostEqual(parsed["size_frac"], 0.25)

    def test_hardcoded_constant_without_manifest_update_fails(self):
        raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
        drifted = hawkes.BASELINE + 1.0
        self.assertNotEqual(drifted, raw["hawkes_baseline"])
        self.assertEqual(hawkes.BASELINE, raw["hawkes_baseline"])

    def test_operate_reports_share_one_params_digest(self):
        from signal_sim.diagnose import fixture_diagnostics
        from signal_sim.drift import fixture_drift_book
        from signal_sim.fixture_load import load_fixture_events
        from signal_sim.hawkes import fixture_intensity

        digest = params.params_sha256()
        stamp = params.operate_stamp()
        self.assertEqual(len(digest), 64)
        self.assertEqual(stamp["params_sha256"], digest)
        events = load_fixture_events(REPO / "fixtures")
        diagnose = fixture_diagnostics(events)
        drift = fixture_drift_book(REPO / "fixtures")
        intensity = fixture_intensity(REPO / "fixtures")
        self.assertEqual(diagnose["params_sha256"], digest)
        self.assertEqual(drift["params_sha256"], digest)
        self.assertEqual(intensity["params_sha256"], digest)
        self.assertEqual(intensity["cut"], "decision_at")
        self.assertGreaterEqual(intensity["stats"]["n_events_after_decision"], 1)
        self.assertEqual(shadow.frozen_params(), diagnose["params"])
        raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(diagnose["params"]["half_life_hours"], raw["half_life_hours"])


if __name__ == "__main__":
    unittest.main()
