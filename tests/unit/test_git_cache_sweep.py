"""A killed run's leftovers are swept at the start of every run, and links are refused.

A run killed between two renames leaves a ``.old-<hex>`` clone (which, if it came from an
earlier version, may hold a token) or a ``.partial-<hex>`` directory. And a cache entry that is
a symbolic link, or whose ``.git/config`` is one, would be reused or written through.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from data_product_forge_custom_scaffold.resolvers import git as git_resolver
from data_product_forge_custom_scaffold.resolvers.base import ResolutionError
from data_product_forge_custom_scaffold.resolvers.git import GitResolver

TOKEN = "s3cret-token-value"
URL = "https://example.invalid/org/repo"
TAINTED = f'[remote "origin"]\n\turl = https://x-access-token:{TOKEN}@example.invalid/org/repo\n'
HOUR = 3600


@pytest.fixture
def git_cache(isolated_git_cache: Path) -> Path:
    (isolated_git_cache / "cache" / "git" / "0123456789abcdef").mkdir(parents=True)
    return isolated_git_cache / "cache" / "git"


def _leftover(url_dir: Path, name: str, *, config: str = "", age: float = 0.0) -> Path:
    path = url_dir / name
    (path / ".git").mkdir(parents=True)
    (path / ".git" / "config").write_text(config)
    when = time.time() - age
    os.utime(path, (when, when))
    return path


def test_a_leftover_that_holds_a_token_goes_at_once(git_cache: Path) -> None:
    url_dir = next(git_cache.iterdir())
    old = _leftover(url_dir, ".old-aaaa", config=TAINTED)  # a clone that was being deleted
    partial = _leftover(url_dir, ".partial-bbbb", config=TAINTED)

    git_resolver._sweep_leftovers()

    assert not old.exists() and not partial.exists()


def test_a_young_clean_staging_directory_is_a_live_run_and_stays(git_cache: Path) -> None:
    url_dir = next(git_cache.iterdir())
    live = _leftover(url_dir, ".partial-live", age=60)

    git_resolver._sweep_leftovers()

    assert live.exists()


@pytest.mark.parametrize("name", [".partial-stale", ".old-stale"])
def test_a_leftover_older_than_an_hour_goes(git_cache: Path, name: str) -> None:
    url_dir = next(git_cache.iterdir())
    stale = _leftover(url_dir, name, age=HOUR + 60)

    git_resolver._sweep_leftovers()

    assert not stale.exists()


def test_every_url_directory_is_swept_and_other_names_are_left_alone(git_cache: Path) -> None:
    first = next(git_cache.iterdir())
    second = git_cache / "fedcba9876543210"
    second.mkdir()
    a = _leftover(first, ".old-one", age=HOUR + 1)
    b = _leftover(second, ".partial-two", age=HOUR + 1)
    ref_dir = _leftover(first, "v1", age=HOUR + 1)  # a finished clone, however old
    lookalike = _leftover(first, ".partialx", age=HOUR + 1)  # not the staging name

    git_resolver._sweep_leftovers()

    assert not a.exists() and not b.exists()
    assert ref_dir.exists() and lookalike.exists()


def test_a_link_named_like_a_leftover_is_removed_and_never_followed(
    git_cache: Path, tmp_path: Path
) -> None:
    url_dir = next(git_cache.iterdir())
    outside = tmp_path / "outside"
    (outside / ".git").mkdir(parents=True)
    (outside / ".git" / "config").write_text(TAINTED)
    (url_dir / ".old-link").symlink_to(outside, target_is_directory=True)

    git_resolver._sweep_leftovers()

    assert not (url_dir / ".old-link").is_symlink()
    assert (outside / ".git" / "config").read_text() == TAINTED  # the target is untouched


def test_a_cache_that_does_not_exist_is_not_an_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FLUID_CUSTOM_SCAFFOLD_CACHE", str(tmp_path / "nowhere"))

    git_resolver._sweep_leftovers()  # nothing to sweep, nothing raised


def test_a_run_sweeps_before_it_does_anything_else(git_cache: Path) -> None:
    # Wired in: resolving a source (here a cached one) sweeps first.
    leftover = _leftover(next(git_cache.iterdir()), ".old-x", config=TAINTED)
    cache_dir = GitResolver._cache_dir_for(URL, "v1")
    cache_dir.mkdir(parents=True)
    (cache_dir / "fluid-scaffold.yaml").write_text("apiVersion: forge.scaffold/v1\n")

    GitResolver().resolve({"kind": "git", "url": URL, "ref": "v1"})

    assert not leftover.exists()


# ── links ─────────────────────────────────────────────────────────────────────


def test_a_cache_entry_that_is_a_link_is_refused_and_its_target_is_left_alone(
    git_cache: Path, tmp_path: Path
) -> None:
    target = tmp_path / "elsewhere"
    (target / ".git").mkdir(parents=True)
    (target / ".git" / "config").write_text(TAINTED)
    (target / "fluid-scaffold.yaml").write_text("apiVersion: forge.scaffold/v1\n")
    cache_dir = GitResolver._cache_dir_for(URL, "v1")
    cache_dir.parent.mkdir(parents=True, exist_ok=True)
    cache_dir.symlink_to(target, target_is_directory=True)

    with pytest.raises(ResolutionError, match="symbolic link"):
        GitResolver().resolve({"kind": "git", "url": URL, "ref": "v1"})

    assert (target / ".git" / "config").read_text() == TAINTED


def test_a_dangling_link_is_refused_too(git_cache: Path, tmp_path: Path) -> None:
    cache_dir = GitResolver._cache_dir_for(URL, "v1")
    cache_dir.parent.mkdir(parents=True, exist_ok=True)
    cache_dir.symlink_to(tmp_path / "gone", target_is_directory=True)

    with pytest.raises(ResolutionError, match="symbolic link"):
        GitResolver().resolve({"kind": "git", "url": URL, "ref": "v1"})


def test_a_linked_git_config_is_never_written_through(git_cache: Path, tmp_path: Path) -> None:
    # `git remote set-url` would rewrite the file the link points to.
    victim = tmp_path / "victim.cfg"
    victim.write_text(TAINTED)
    cache_dir = GitResolver._cache_dir_for(URL, "v1")
    (cache_dir / ".git").mkdir(parents=True)
    (cache_dir / ".git" / "config").symlink_to(victim)
    (cache_dir / "fluid-scaffold.yaml").write_text("apiVersion: forge.scaffold/v1\n")

    with pytest.raises(ResolutionError, match="symbolic link"):
        GitResolver().resolve({"kind": "git", "url": URL, "ref": "v1"})

    assert victim.read_text() == TAINTED


def test_a_linked_dot_git_is_refused(git_cache: Path, tmp_path: Path) -> None:
    real = tmp_path / "real-dot-git"
    real.mkdir()
    cache_dir = GitResolver._cache_dir_for(URL, "v1")
    cache_dir.mkdir(parents=True)
    (cache_dir / ".git").symlink_to(real, target_is_directory=True)

    with pytest.raises(ResolutionError, match="symbolic link"):
        GitResolver().resolve({"kind": "git", "url": URL, "ref": "v1"})
