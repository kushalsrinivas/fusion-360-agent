"""Shared fixtures: offline config + mock bridge graph runs."""
from __future__ import annotations

import pytest

from app.config import get_config


@pytest.fixture()
def offline_config(tmp_path):
    """No API key -> deterministic heuristic mode; isolated data dir."""
    cfg = get_config(
        fusion_mode="mock",
        llm_api_key="",  # force offline heuristics
    )
    cfg.data_dir = tmp_path
    # Rebuild path-dependent stores lazily via env override.
    import os
    os.environ["FUSION_AI_DATA_DIR"] = str(tmp_path)
    yield cfg
    os.environ.pop("FUSION_AI_DATA_DIR", None)
