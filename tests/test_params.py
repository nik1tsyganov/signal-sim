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
        self.assertEqual(params.HAWKES_BASELINE, raw["hawkes_baseline"])
        self.assertEqual(params.PLACEBO_SEED, raw["placebo_seed"])

    def test_shadow_and_default_mark_books_read_the_same_manifest(self):
        raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
        frozen = shadow.frozen_params()
        self.assertEqual(frozen["half_life_hours"], raw["half_life_hours"])
        self.assertEqual(frozen["placebo_seed"], raw["placebo_seed"])
        self.assertEqual(frozen["hawkes_baseline"], raw["hawkes_baseline"])
        self.assertEqual(frozen["cost_bps"], raw["cost_bps"])
        self.assertEqual(frozen["decision_delay_hours"], raw["decision_delay_hours"])
        self.assertIn("not fitted", frozen["note"].lower())
        book = load_mark_book()
        self.assertEqual(book["cost_bps"], raw["cost_bps"])
        self.assertEqual(book["decision_delay_hours"], raw["decision_delay_hours"])
        folds = json.loads((REPO / "fixtures" / "marks" / "walkforward.json").read_text(encoding="utf-8"))
        self.assertEqual(folds["cost_bps"], raw["cost_bps"])
        self.assertEqual(folds["decision_delay_hours"], raw["decision_delay_hours"])

    def test_hardcoded_constant_without_manifest_update_fails(self):
        raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
        drifted = hawkes.BASELINE + 1.0
        self.assertNotEqual(drifted, raw["hawkes_baseline"])
        self.assertEqual(hawkes.BASELINE, raw["hawkes_baseline"])


if __name__ == "__main__":
    unittest.main()
