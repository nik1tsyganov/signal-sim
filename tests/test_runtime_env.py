"""Presence-only runtime env status. Never assert or print secret values."""

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from signal_sim import cli
from signal_sim.runtime_env import (
    CLOUD_ENVIRONMENT_NAME,
    RUNTIME_ENV_NAMES,
    RUNTIME_SECRET_NAMES,
    SUBMIT_FLAG_DEFAULT,
    paper_submit_flag,
    runtime_env_status,
)


REPO = Path(__file__).resolve().parent.parent
FAKE_SECRET = "should-never-appear-in-output"


class RuntimeEnvTests(unittest.TestCase):
    def test_status_is_presence_only_and_defaults_submit_off(self):
        with mock.patch("signal_sim.runtime_env.read_env", return_value=None):
            report = runtime_env_status()
        self.assertEqual(report["mode"], "runtime-env")
        self.assertEqual(report["cloud_environment"], "signal-sim-paper")
        self.assertEqual(CLOUD_ENVIRONMENT_NAME, "signal-sim-paper")
        self.assertEqual(report["submit_flag"], SUBMIT_FLAG_DEFAULT)
        self.assertEqual(report["submit_flag"], "0")
        self.assertFalse(report["ok"])
        self.assertEqual(report["missing"], list(RUNTIME_SECRET_NAMES))
        self.assertTrue(all(report["secrets"][name] is False for name in RUNTIME_SECRET_NAMES))
        self.assertTrue(all(report["env"][name] is False for name in RUNTIME_ENV_NAMES))
        dumped = json.dumps(report)
        self.assertNotIn(FAKE_SECRET, dumped)
        self.assertNotIn("present=True", dumped)

    def test_submit_flag_is_one_only_when_explicitly_one(self):
        def env(name):
            values = {
                "SIGNAL_SIM_ALPACA_PAPER_SUBMIT": "0",
                "ALPACA_PAPER_API_KEY": FAKE_SECRET,
            }
            return values.get(name)

        with mock.patch("signal_sim.runtime_env.read_env", side_effect=env):
            self.assertEqual(paper_submit_flag(), "0")
        with mock.patch("signal_sim.runtime_env.read_env", return_value="1"):
            self.assertEqual(paper_submit_flag(), "1")
        with mock.patch("signal_sim.runtime_env.read_env", return_value="true"):
            self.assertEqual(paper_submit_flag(), "0")

    def test_cli_prints_booleans_not_values(self):
        def env(name):
            if name in RUNTIME_SECRET_NAMES:
                return FAKE_SECRET
            if name == "ALPACA_PAPER_API_BASE_URL":
                return "https://" + "paper-api." + "alpaca" + ".markets"
            if name == "SIGNAL_SIM_ALPACA_PAPER_SUBMIT":
                return "0"
            return None

        printed = io.StringIO()
        error = io.StringIO()
        with mock.patch("signal_sim.runtime_env.read_env", side_effect=env), redirect_stdout(
            printed
        ), redirect_stderr(error):
            code = cli.main(["runtime-env"])
        self.assertEqual(code, 0)
        payload = json.loads(printed.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["submit_flag"], "0")
        self.assertTrue(all(payload["secrets"].values()))
        combined = printed.getvalue() + error.getvalue()
        self.assertNotIn(FAKE_SECRET, combined)
        self.assertNotIn("paper-api." + "alpaca" + ".markets", combined)

    def test_cli_missing_secrets_exit_2(self):
        error = io.StringIO()
        printed = io.StringIO()
        with mock.patch("signal_sim.runtime_env.read_env", return_value=None), redirect_stdout(
            printed
        ), redirect_stderr(error):
            code = cli.main(["runtime-env"])
        self.assertEqual(code, 2)
        payload = json.loads(printed.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["submit_flag"], "0")

    def test_docs_name_runtime_secrets_and_paper_environment(self):
        text = (REPO / "docs" / "operate-readiness.md").read_text(encoding="utf-8")
        self.assertIn("Runtime Secrets", text)
        self.assertIn("signal-sim-paper", text)
        for name in RUNTIME_SECRET_NAMES:
            self.assertIn(name, text)
        self.assertIn("ALPACA_PAPER_API_BASE_URL", text)
        self.assertIn("SIGNAL_SIM_ALPACA_PAPER_SUBMIT", text)
        self.assertIn("defaults to `0`", text)
        self.assertNotIn(FAKE_SECRET, text)


if __name__ == "__main__":
    unittest.main()
