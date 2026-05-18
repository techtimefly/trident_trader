"""Read and write .env for the settings panel.

The .env path is anchored to the project root the same way settings.py does it,
so this module always edits the right file regardless of working directory.
"""
from __future__ import annotations

from pathlib import Path

# src/trident/dashboard/settings_io.py
# parents[0] = src/trident/dashboard/
# parents[1] = src/trident/
# parents[2] = src/
# parents[3] = project root (where .env lives)
_ENV_PATH = Path(__file__).resolve().parents[3] / ".env"


def env_path() -> Path:
    return _ENV_PATH


def rewrite_env(updates: dict[str, str]) -> None:
    """Update .env in-place with the given key=value pairs.

    Existing key lines are replaced; keys absent from the file are appended.
    Comments and blank lines are preserved as-is.
    """
    path = _ENV_PATH
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True) if path.exists() else []

    written: set[str] = set()
    new_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in updates:
            new_lines.append(f"{key}={updates[key]}\n")
            written.add(key)
        else:
            new_lines.append(line)

    for key, val in updates.items():
        if key not in written:
            new_lines.append(f"{key}={val}\n")

    path.write_text("".join(new_lines), encoding="utf-8")
