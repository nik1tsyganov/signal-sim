"""Frozen-params smoke of the paper fixture operate loop."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from signal_sim import cli
from signal_sim.params import params_sha256
from signal_sim.smoke import STEP_NAMES, run_smoke


REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "fixtures"


class SmokeTests(unittest.TestCase):
    def test_cli_requires_fixtures(self):
        error = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(error):
            self.assertEqual(cli.main(["smoke"]), 2)
        self.assertIn("requires --fixtures", error.getvalue())

    def test_smoke_fixtures_runs_every_step_and_prints_digest(self):
        printed = io.StringIO()
        error = io.StringIO()
        with redirect_stdout(printed), redirect_stderr(error):
            code = cli.main(["smoke", "--fixtures"])
        payload = json.loads(printed.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["mode"], "local-paper-smoke")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["params_sha256"], params_sha256())
        self.assertIn("fixture-mark", payload["pnl_note"].lower())
        self.assertEqual(set(payload["steps"]), set(STEP_NAMES))
        self.assertTrue(all(step.get("ok") is True for step in payload["steps"].values()))
        self.assertIn("fixture-mark", payload["steps"]["replay"]["pnl_note"].lower())
        self.assertIn(payload["params_sha256"], error.getvalue())
        rendered = json.dumps(payload).lower()
        self.assertNotIn("sharpe", rendered)
        self.assertNotIn("yahoo", rendered)

    def test_helper_stamps_the_same_digest(self):
        tmp = tempfile.mkdtemp()
        report = run_smoke(fixtures=FIXTURES, ledger_dir=tmp, write_artifact=False)
        self.assertTrue(report["ok"])
        self.assertEqual(report["params_sha256"], params_sha256())


if __name__ == "__main__":
    unittest.main()
