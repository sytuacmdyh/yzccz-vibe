from __future__ import annotations

from pathlib import Path
import sys


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def app_icon_path() -> Path | None:
    for name in ("app_icon.png", "app.ico"):
        path = app_root() / "assets" / name
        if path.is_file():
            return path
    return None
