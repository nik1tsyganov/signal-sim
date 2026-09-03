"""Environment secret reader."""

import os

def read_env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    if value:
        return value
    return None
