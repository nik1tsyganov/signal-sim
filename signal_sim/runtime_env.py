"""Presence-only runtime env status for paper intel and Alpaca paper.

Never returns or prints secret values. Cloud runs should load these from
Cursor Dashboard Runtime Secrets on the saved environment ``signal-sim-paper``.
"""

from __future__ import annotations

from typing import Any

from .secrets import read_env

CLOUD_ENVIRONMENT_NAME = "signal-sim-paper"

RUNTIME_SECRET_NAMES = (
    "ALPACA_PAPER_API_KEY",
    "ALPACA_PAPER_API_SECRET",
    "QUIVER_API_KEY",
    "WORLD_MONITOR_KEY",
)

RUNTIME_ENV_NAMES = (
    "ALPACA_PAPER_API_BASE_URL",
    "SIGNAL_SIM_ALPACA_PAPER_SUBMIT",
)

SUBMIT_FLAG_NAME = "SIGNAL_SIM_ALPACA_PAPER_SUBMIT"
SUBMIT_FLAG_DEFAULT = "0"


def _present(name: str) -> bool:
    return read_env(name) is not None


def paper_submit_flag() -> str:
    """Return '1' only when the later submit gate is explicitly enabled.

    Missing, empty, or any value other than ``1`` is ``0``.
    """
    raw = read_env(SUBMIT_FLAG_NAME)
    if raw == "1":
        return "1"
    return SUBMIT_FLAG_DEFAULT


def runtime_env_status() -> dict[str, Any]:
    secrets = {name: _present(name) for name in RUNTIME_SECRET_NAMES}
    env = {name: _present(name) for name in RUNTIME_ENV_NAMES}
    missing = [name for name, present in secrets.items() if not present]
    return {
        "mode": "runtime-env",
        "cloud_environment": CLOUD_ENVIRONMENT_NAME,
        "secrets": secrets,
        "env": env,
        "submit_flag": paper_submit_flag(),
        "missing": missing,
        "ok": not missing,
        "note": (
            "Presence only. Never logs secret values. "
            "Prefer Cursor Cloud environment signal-sim-paper with Runtime Secrets."
        ),
    }
