"""Frozen shadow-paper operate report. Not a parameter search."""

import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from signal_sim import cli
from signal_sim.hawkes import BASELINE, DECAY, EXCITATION
from signal_sim.shadow import REPORT_NAME, frozen_params, run_shadow_report
from signal_sim.walkforward import PLACEBO_SEED


REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "fixtures"


class ShadowReportTests(unittest.TestCase):
    def test_params_are_declared_constants(self):
        params = frozen_params()
        self.assertEqual(params["placebo_seed"], PLACEBO_SEED)
        self.assertEqual(params["hawkes_baseline"], BASELINE)
        self.assertEqual(params["hawkes_excitation"], EXCITATION)
        self.assertEqual(params["hawkes_decay"], DECAY)
        self.assertIn("not fitted", params["note"].lower())

    def test_cli_requires_fixtures_and_writes_artifacts_when_present(self):
        error = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(error):
            self.assertEqual(cli.main(["shadow"]), 2)
        self.assertIn("requires --fixtures", error.getvalue())

        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        dest = Path(tmp) / "report.json"
        printed = io.StringIO()
        with redirect_stdout(printed):
            self.assertEqual(cli.main(["shadow", "--fixtures", "--out", str(dest)]), 0)
        payload = json.loads(printed.getvalue())
        self.assertEqual(payload["mode"], "local-paper-shadow")
        self.assertIn("not a search", payload["note"].lower())
        self.assertEqual(payload["params"]["placebo_seed"], PLACEBO_SEED)
        self.assertEqual(payload["walkforward"]["n_folds"], 2)
        first = payload["walkforward"]["folds"][0]
        self.assertEqual(set(first["comparisons"]), {"no_news", "shuffled_news", "news_only"})
        self.assertTrue(dest.is_file())
        self.assertEqual(json.loads(dest.read_text(encoding="utf-8"))["mode"], "local-paper-shadow")
        rendered = json.dumps(payload).lower()
        self.assertNotIn("sharpe", rendered)
        self.assertNotIn("best_fold", rendered)
        self.assertNotIn("yahoo", rendered)

    def test_stdout_only_when_no_artifacts_dir(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        ledger = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, ledger, ignore_errors=True)
        missing = Path(tmp) / "missing"
        with patch.dict(os.environ, {"SIGNAL_SIM_ARTIFACTS": str(missing)}, clear=False):
            with patch("signal_sim.shadow.artifacts_dir", return_value=None):
                report = run_shadow_report(fixtures=FIXTURES, ledger_dir=ledger)
        self.assertNotIn("report_path", report)
        self.assertEqual(report["walkforward"]["folds"][0]["comparisons"]["no_news"]["total_pnl"], 0)

    def test_writes_default_name_into_artifacts_dir(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        ledger = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, ledger, ignore_errors=True)
        folder = Path(tmp) / "artifacts"
        folder.mkdir()
        with patch("signal_sim.shadow.artifacts_dir", return_value=folder):
            report = run_shadow_report(fixtures=FIXTURES, ledger_dir=ledger)
        dest = folder / REPORT_NAME
        self.assertEqual(report["report_path"], str(dest))
        self.assertTrue(dest.is_file())


if __name__ == "__main__":
    unittest.main()
