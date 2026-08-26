"""Shared fixtures. Tests never touch the repo's inbox/ or out/."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "samples"))

from make_samples import write_samples  # noqa: E402


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """An isolated copy of config/ with an inbox/ and out/ under tmp_path."""
    (tmp_path / "config").mkdir()
    shutil.copy(REPO_ROOT / "config" / "schema.yaml", tmp_path / "config" / "schema.yaml")
    shutil.copy(REPO_ROOT / "config" / "pipeline.yaml", tmp_path / "config" / "pipeline.yaml")
    (tmp_path / "inbox").mkdir()
    (tmp_path / "out").mkdir()
    return tmp_path


@pytest.fixture
def clean_workspace(workspace: Path) -> Path:
    write_samples(workspace / "inbox", drift=False)
    return workspace


@pytest.fixture
def drifted_workspace(workspace: Path) -> Path:
    write_samples(workspace / "inbox", drift=True)
    return workspace


@pytest.fixture
def additive_drift_workspace(workspace: Path) -> Path:
    write_samples(workspace / "inbox", additive_only=True)
    return workspace
