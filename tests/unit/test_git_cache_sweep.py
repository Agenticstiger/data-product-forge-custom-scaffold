"""A killed run's leftovers are swept at the start of every run, and links are refused.

A run killed between two renames leaves a ``.old-<hex>`` clone (which, if it came from an
earlier version, may hold a token) or a ``.partial-<hex>`` directory. A clone that an earlier
version made with the token in the url sits under a key that is no longer used, so nothing
would ever clean it: the sweep deletes it. And a cache entry that is a symbolic link, or whose
``.git/config`` is one, would be reused or written through.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import pytest

from data_product_forge_custom_scaffold.resolvers import git as git_resolver
from data_product_forge_custom_scaffold.resolvers.base import ResolutionError
from data_product_forge_custom_scaffold.resolvers.git import GitResolver

TOKEN = "s3cret-token-value"
URL = "https://example.invalid/org/repo"
TAINTED = f'[remote "origin"]\n\turl = https://x-access-token:{TOKEN}@example.invalid/org/repo\n'
MANIFEST = "fluid-scaffold.yaml"
HOUR = 3600
DAY = 24 * HOUR


@pytest.fixture
def git_cache(isolated_git_cache: Path) -> Path:
    (isolated_git_cache / "cache" / "git" / "0123456789abcdef").mkdir(parents=True)
    return isolated_git_cache / "cache" / "git"


def _leftover(url_dir: Path, name: str, *, config: str = "", mtime_age: float = 0.0) -> Path:
    """A directory shaped like a clone, whose files last changed *mtime_age* seconds ago.

    Only the modification time can be set back: the inode change time is always now.
    """
    path = url_dir / name
    (path / ".git").mkdir(parents=True)
    (path / ".git" / "config").write_text(config)
    when = time.time() - mtime_age
    os.utime(path, (when, when))
    return path


def _sweep(monkeypatch, *, later: float = 0.0, keep: Optional[Path] = None) -> None:
    """Sweep as if *later* seconds had passed since the directories were made."""
    now = time.time() + later
    monkeypatch.setattr(git_resolver, "time", SimpleNamespace(time=lambda: now))
    git_resolver._sweep_leftovers(keep=keep)


def test_a_leftover_that_holds_a_token_goes_at_once(git_cache: Path, monkeypatch) -> None:
    url_dir = next(git_cache.iterdir())
    old = _leftover(url_dir, ".old-aaaa", config=TAINTED)  # a clone that was being deleted
    partial = _leftover(url_dir, ".partial-bbbb", config=TAINTED)

    _sweep(monkeypatch)

    assert not old.exists() and not partial.exists()


def test_a_young_clean_staging_directory_is_a_live_run_and_stays(
    git_cache: Path, monkeypatch
) -> None:
    url_dir = next(git_cache.iterdir())
    live = _leftover(url_dir, ".partial-live")

    _sweep(monkeypatch, later=60)

    assert live.exists()


@pytest.mark.parametrize("name", [".partial-stale", ".old-stale"])
def test_a_leftover_older_than_a_day_goes(git_cache: Path, monkeypatch, name: str) -> None:
    url_dir = next(git_cache.iterdir())
    stale = _leftover(url_dir, name)

    _sweep(monkeypatch, later=DAY + 60)

    assert not stale.exists()


@pytest.mark.parametrize("name", [".partial-slow", ".old-slow"])
def test_a_leftover_a_few_hours_old_may_be_a_slow_live_fetch_and_stays(
    git_cache: Path, monkeypatch, name: str
) -> None:
    url_dir = next(git_cache.iterdir())
    slow = _leftover(url_dir, name)

    _sweep(monkeypatch, later=DAY - HOUR)

    assert slow.exists()


def test_a_clone_just_renamed_aside_is_young_whatever_its_files_say(
    git_cache: Path, monkeypatch
) -> None:
    # A rename sets the directory's change time and leaves its modification time, so a clone
    # that is only now being replaced (renamed to `.old-*`, about to be deleted by the run that
    # renamed it) looks ten days old by its modification time.
    url_dir = next(git_cache.iterdir())
    replaced = _leftover(url_dir, ".old-just-now", mtime_age=10 * DAY)

    _sweep(monkeypatch, later=60)

    assert replaced.exists()


def test_every_url_directory_is_swept_and_other_names_are_left_alone(
    git_cache: Path, monkeypatch
) -> None:
    first = next(git_cache.iterdir())
    second = git_cache / "fedcba9876543210"
    second.mkdir()
    a = _leftover(first, ".old-one")
    b = _leftover(second, ".partial-two")
    ref_dir = _leftover(first, "v1")  # a finished clone, however old
    lookalike = _leftover(first, ".partialx")  # not the staging name

    _sweep(monkeypatch, later=DAY + 1)

    assert not a.exists() and not b.exists()
    assert ref_dir.exists() and lookalike.exists()


def test_a_link_named_like_a_leftover_is_removed_and_never_followed(
    git_cache: Path, tmp_path: Path, monkeypatch
) -> None:
    url_dir = next(git_cache.iterdir())
    outside = tmp_path / "outside"
    (outside / ".git").mkdir(parents=True)
    (outside / ".git" / "config").write_text(TAINTED)
    (url_dir / ".old-link").symlink_to(outside, target_is_directory=True)

    _sweep(monkeypatch)

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
    (cache_dir / MANIFEST).write_text("apiVersion: forge.scaffold/v1\n")

    GitResolver().resolve({"kind": "git", "url": URL, "ref": "v1"})

    assert not leftover.exists()


# ── clones an earlier version made with the token in the url ─────────────────


def test_a_clone_that_holds_a_token_goes_whatever_its_name_and_age(
    git_cache: Path, monkeypatch
) -> None:
    # An earlier version cloned with the token in the url, under the key a source without
    # `auth` has: no source with `auth` reaches it now, so it would keep the token for good.
    url_dir = next(git_cache.iterdir())
    legacy = _leftover(url_dir, "v1", config=TAINTED)
    clean = _leftover(
        url_dir, "v2", config='[remote "origin"]\n\turl = https://example.invalid/x\n'
    )

    _sweep(monkeypatch)

    assert not legacy.exists()  # renamed away and deleted
    assert clean.exists()
    assert sorted(p.name for p in url_dir.iterdir()) == ["v2"]


def test_the_clone_this_run_is_about_to_use_is_left_to_the_cleaner(
    git_cache: Path, monkeypatch
) -> None:
    in_use = _leftover(next(git_cache.iterdir()), "v1", config=TAINTED)
    other = _leftover(next(git_cache.iterdir()), "v2", config=TAINTED)

    _sweep(monkeypatch, keep=in_use)

    assert in_use.exists()  # `_drop_stored_credentials` cleans it in place, or refuses
    assert not other.exists()


def test_a_clone_that_is_or_holds_a_link_is_left_alone_not_deleted_through(
    git_cache: Path, tmp_path: Path, monkeypatch
) -> None:
    url_dir = next(git_cache.iterdir())
    victim = tmp_path / "victim.cfg"
    victim.write_text(TAINTED)
    linked_config = _leftover(url_dir, "v1")
    (linked_config / ".git" / "config").unlink()
    (linked_config / ".git" / "config").symlink_to(victim)
    outside = tmp_path / "outside"
    (outside / ".git").mkdir(parents=True)
    (outside / ".git" / "config").write_text(TAINTED)
    (url_dir / "v2").symlink_to(outside, target_is_directory=True)

    _sweep(monkeypatch)

    assert (url_dir / "v2").is_symlink() and linked_config.exists()  # using them is refused
    assert victim.read_text() == TAINTED and (outside / ".git" / "config").read_text() == TAINTED


def test_a_url_directory_that_is_a_link_is_never_swept_through(
    git_cache: Path, tmp_path: Path, monkeypatch
) -> None:
    # The sweep deletes only inside `<cache>/git/<hash>/`; a link there leads somewhere else.
    outside = tmp_path / "outside"
    legacy = _leftover(outside, "v1", config=TAINTED)
    stale = _leftover(outside, ".old-stale")
    (git_cache / "linked-hash").symlink_to(outside, target_is_directory=True)

    _sweep(monkeypatch, later=DAY + 60)

    assert legacy.exists() and stale.exists()


def test_a_run_for_one_source_removes_the_old_clone_of_another(
    git_cache: Path, monkeypatch
) -> None:
    # The upgrade: a source with `auth` now has a key of its own, so the clone an earlier
    # version made for it (token and all) is no longer anyone's. Resolving any source cleans it.
    monkeypatch.setenv("FLUID_CUSTOM_SCAFFOLD_AUTH_HOSTS", "example.invalid")
    monkeypatch.setenv("FLUID_SCAFFOLD_TOKEN", TOKEN)
    legacy = GitResolver._cache_dir_for(URL, "v1")  # the key it had: the url alone
    (legacy / ".git").mkdir(parents=True)
    (legacy / ".git" / "config").write_text(TAINTED)
    binding = git_resolver._bind_auth(URL, {"secret_ref": "FLUID_SCAFFOLD_TOKEN"})
    current = GitResolver._cache_dir_for(URL, "v1", binding)
    current.mkdir(parents=True)
    (current / MANIFEST).write_text("apiVersion: forge.scaffold/v1\n")

    GitResolver().resolve(
        {"kind": "git", "url": URL, "ref": "v1", "auth": {"secret_ref": "FLUID_SCAFFOLD_TOKEN"}}
    )

    assert not legacy.exists()
    assert current.is_dir()


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
