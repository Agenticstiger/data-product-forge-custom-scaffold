"""Fixtures shared by the unit tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def isolated_git_cache(tmp_path: Path, monkeypatch) -> Path:
    """An empty engine cache under *tmp_path*, and a git that ignores the developer's config."""
    monkeypatch.setenv("FLUID_CUSTOM_SCAFFOLD_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("FLUID_CUSTOM_SCAFFOLD_NOCACHE", raising=False)
    config = tmp_path / "gitconfig"
    config.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    return tmp_path
