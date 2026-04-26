from __future__ import annotations

import os
from pathlib import Path


FEATURES_ENV_VAR = "FEATURES_ONLY"
FEATURES_ROOT = Path(__file__).parent / "features"


def get_selected_features() -> list[Path]:
    raw = os.getenv(FEATURES_ENV_VAR, "").strip()
    if not raw:
        return sorted(FEATURES_ROOT.rglob("*.feature"))

    selected: list[Path] = []
    for item in raw.split(","):
        candidate = (FEATURES_ROOT / item.strip()).resolve()
        if candidate.exists():
            selected.append(candidate)
    return selected


def iter_feature_files() -> list[Path]:
    return get_selected_features()
