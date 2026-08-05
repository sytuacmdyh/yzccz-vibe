from __future__ import annotations

import json
from pathlib import Path
from typing import List, Tuple


def discover_profiles(profile_dir: Path) -> List[Tuple[str, Path]]:
    profiles: List[Tuple[str, Path]] = []
    for path in sorted(profile_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        label = str(data.get("name") or path.stem)
        profiles.append((label, path))
    return profiles
