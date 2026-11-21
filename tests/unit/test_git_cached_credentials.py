"""A cached clone that carries a credential is cleaned before it is reused.

An earlier version cloned with the token in the URL, and ``git clone`` records that URL as
``origin`` in ``.git/config`` before it transfers anything. A run killed during the transfer
(or a clone made by that version) leaves the token in the cache, where every later run
reuses it. The resolver now looks for credentials in the clone's config before it reuses it.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, List

import pytest

from data_product_forge_custom_scaffold.resolvers import git as git_resolver
from data_product_forge_custom_scaffold.resolvers.base import ResolutionError
from data_product_forge_custom_scaffold.resolvers.git import (
    GitResolver,
    _has_credentials,
    _stored_credentials,
)

TOKEN = "s3cret-token-value"
PLAIN = "https://example.invalid/org/repo"
WITH_TOKEN = f"https://x-access-token:{TOKEN}@example.invalid/org/repo"
MANIFEST = "fluid-scaffold.yaml"

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git binary not on PATH")


@pytest.fixture
def cache(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("FLUID_CUSTOM_SCAFFOLD_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("FLUID_CUSTOM_SCAFFOLD_NOCACHE", raising=False)
    config = tmp_path / "gitconfig"  # the developer's own git config must not matter
    config.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    return tmp_path


def _git(*args: str) -> str:
    done = subprocess.run(["git", *args], check=True, capture_output=True, text=True)
    return done.stdout.strip()


def _cached_clone(tmp_path: Path, origin: str, url: str = PLAIN, ref: str = "v1") -> Path:
    """The directory the resolver reuses for (*url*, *ref*): a real clone, origin set to *origin*."""
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    (upstream / MANIFEST).write_text("apiVersion: forge.scaffold/v1\n", encoding="utf-8")
    _git("-C", str(upstream), "init", "-q")
    _git("-C", str(upstream), "add", "-A")
    identity = ["-c", "user.email=t@t.t", "-c", "user.name=t", "-c", "commit.gpgsign=false"]
    _git("-C", str(upstream), *identity, "commit", "-q", "-m", "c1")
    cache_dir = GitResolver._cache_dir_for(url, ref)
    cache_dir.parent.mkdir(parents=True)
    _git("clone", "-q", str(upstream), str(cache_dir))
    _git("-C", str(cache_dir), "remote", "set-url", "origin", origin)
    return cache_dir


def _unfinished_clone(origin: str, url: str = PLAIN, ref: str = "v1") -> Path:
    """What a run killed mid-transfer leaves: a repository with a config and no commit."""
    cache_dir = GitResolver._cache_dir_for(url, ref)
    (cache_dir / ".git").mkdir(parents=True)
    (cache_dir / ".git" / "config").write_text(f'[remote "origin"]\n\turl = {origin}\n')
    return cache_dir


def _source(url: str = PLAIN, ref: str = "v1") -> dict:
    return {"kind": "git", "url": url, "ref": ref}


def _never_fetch(monkeypatch) -> None:
    def fail(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("the cached clone should have been reused, not fetched again")

    monkeypatch.setattr(GitResolver, "_clone_into_cache", fail)


def _must_not_run(*args: Any, **kwargs: Any) -> None:
    raise AssertionError("git should not have been run")


def _scrub_that_fails(monkeypatch) -> None:
    def failing_git(args: List[str], *, clone_url: str, url: str, what: str) -> None:
        raise ResolutionError("git reset the remote URL failed")

    monkeypatch.setattr(GitResolver, "_git", staticmethod(failing_git))


def _fetch_that_records(monkeypatch) -> List[Path]:
    """Replace the fetch with one that checks the old clone is gone and makes a bundle."""
    fetched: List[Path] = []

    def fetch(self, url: str, ref: str, target: Path, auth: Any) -> None:
        fetched.append(target)
        assert not target.exists()  # the tainted clone is gone before the new one is made
        target.mkdir(parents=True)
        (target / MANIFEST).write_text("apiVersion: forge.scaffold/v1\n")

    monkeypatch.setattr(GitResolver, "_clone_into_cache", fetch)
    return fetched


def _tree_text(root: Path) -> str:
    return "\n".join(p.read_text(errors="replace") for p in root.rglob("*") if p.is_file())


@needs_git
@pytest.mark.parametrize(
    "tainted",
    [WITH_TOKEN, f"https://{TOKEN}@example.invalid/org/repo"],
    ids=["user and token", "token as the user name"],
)
def test_a_token_left_in_a_finished_clone_is_removed_and_the_clone_is_kept(
    cache: Path, monkeypatch, tainted: str
) -> None:
    cache_dir = _cached_clone(cache, origin=tainted)
    assert TOKEN in (cache_dir / ".git" / "config").read_text()  # what the old clone left
    _never_fetch(monkeypatch)

    bundle = GitResolver().resolve(_source())

    assert (bundle.bundle_root / MANIFEST).is_file()
    assert TOKEN not in _tree_text(cache_dir / ".git")
    assert _git("-C", str(cache_dir), "config", "--get", "remote.origin.url") == PLAIN


def test_an_unfinished_clone_that_holds_a_token_is_deleted_not_scrubbed_and_kept(
    cache: Path, monkeypatch
) -> None:
    # A run killed mid-transfer has no commit: keeping it would only fail later with
    # "missing fluid-scaffold.yaml", for good. It is fetched again instead.
    cache_dir = _unfinished_clone(WITH_TOKEN)
    monkeypatch.setattr(GitResolver, "_git", staticmethod(_must_not_run))  # no scrub attempted
    fetched = _fetch_that_records(monkeypatch)

    bundle = GitResolver().resolve(_source())

    assert fetched == [cache_dir]
    assert TOKEN not in _tree_text(cache_dir)
    assert (bundle.bundle_root / MANIFEST).is_file()


@needs_git
def test_a_finished_clone_whose_scrub_fails_is_deleted_and_fetched_again(
    cache: Path, monkeypatch
) -> None:
    cache_dir = _cached_clone(cache, origin=WITH_TOKEN)
    _scrub_that_fails(monkeypatch)
    fetched = _fetch_that_records(monkeypatch)

    bundle = GitResolver().resolve(_source())

    assert fetched == [cache_dir]
    assert TOKEN not in _tree_text(cache_dir)
    assert (bundle.bundle_root / MANIFEST).is_file()


@needs_git
def test_a_token_in_a_place_the_scrub_does_not_reach_is_not_kept(cache: Path, monkeypatch) -> None:
    # `remote set-url origin` rewrites the fetch URL only; the check runs again afterwards.
    cache_dir = _cached_clone(cache, origin=WITH_TOKEN)
    _git("-C", str(cache_dir), "config", "remote.origin.pushurl", WITH_TOKEN)
    fetched = _fetch_that_records(monkeypatch)

    GitResolver().resolve(_source())

    assert fetched == [cache_dir]
    assert TOKEN not in _tree_text(cache_dir)


@needs_git
def test_a_clone_that_can_be_neither_scrubbed_nor_deleted_is_an_error(
    cache: Path, monkeypatch
) -> None:
    cache_dir = _cached_clone(cache, origin=WITH_TOKEN)
    _scrub_that_fails(monkeypatch)

    def cannot(*args: Any, **kwargs: Any) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(git_resolver.os, "replace", cannot)  # no renaming it away ...
    monkeypatch.setattr(
        git_resolver.shutil, "rmtree", lambda *args, **kwargs: None
    )  # ... or deleting
    _never_fetch(monkeypatch)

    with pytest.raises(ResolutionError, match="could not be removed"):
        GitResolver().resolve(_source())

    assert (cache_dir / ".git" / "config").is_file()  # left whole, not half-deleted


def test_a_tainted_clone_is_never_left_half_deleted(cache: Path, monkeypatch) -> None:
    cache_dir = _unfinished_clone(WITH_TOKEN)
    real_rmtree = shutil.rmtree

    def interrupted(path, *args, **kwargs):
        if Path(path).name.startswith(".old-"):
            raise KeyboardInterrupt
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(git_resolver.shutil, "rmtree", interrupted)
    _never_fetch(monkeypatch)

    with pytest.raises(KeyboardInterrupt):
        GitResolver().resolve(_source())

    assert not cache_dir.exists()  # renamed away before the delete began


@needs_git
def test_a_failed_refresh_does_not_leave_a_token_in_the_old_clone(cache: Path, monkeypatch) -> None:
    cache_dir = _cached_clone(cache, origin=WITH_TOKEN)
    monkeypatch.setenv("FLUID_CUSTOM_SCAFFOLD_NOCACHE", "1")

    def failing_fetch(self, *args: Any, **kwargs: Any) -> None:
        raise ResolutionError("git fetch failed")

    monkeypatch.setattr(GitResolver, "_clone_into_cache", failing_fetch)

    with pytest.raises(ResolutionError):
        GitResolver().resolve(_source())

    assert TOKEN not in _tree_text(cache_dir / ".git")  # the old clone stays, without the token


@needs_git
def test_a_broken_clone_never_makes_git_rewrite_a_parent_repository(
    cache: Path, monkeypatch
) -> None:
    # Under $HOME as a dotfiles repository, say. `git -C <broken clone>` would fall through to it.
    parent = cache / "parent"
    parent.mkdir()
    _git("-C", str(parent), "init", "-q")
    identity = ["-c", "user.email=t@t.t", "-c", "user.name=t", "-c", "commit.gpgsign=false"]
    _git("-C", str(parent), *identity, "commit", "-q", "--allow-empty", "-m", "c")
    _git("-C", str(parent), "remote", "add", "origin", "https://parent.example/dotfiles")
    monkeypatch.setenv("FLUID_CUSTOM_SCAFFOLD_CACHE", str(parent / "cache"))
    _unfinished_clone(WITH_TOKEN)
    fetched = _fetch_that_records(monkeypatch)

    GitResolver().resolve(_source())

    assert len(fetched) == 1  # the broken clone was deleted and fetched again
    assert _git("-C", str(parent), "config", "--get", "remote.origin.url") == (
        "https://parent.example/dotfiles"
    )


@pytest.mark.parametrize(
    "origin",
    [PLAIN, "ssh://git@example.invalid/org/repo", "git@example.invalid:org/repo"],
    ids=["plain https", "ssh user name", "scp-style"],
)
def test_a_clone_with_no_credential_is_reused_as_it_is(cache: Path, monkeypatch, origin) -> None:
    cache_dir = _unfinished_clone(origin)
    (cache_dir / MANIFEST).write_text("apiVersion: forge.scaffold/v1\n")
    _never_fetch(monkeypatch)
    monkeypatch.setattr(GitResolver, "_git", staticmethod(_must_not_run))
    monkeypatch.setattr(GitResolver, "_git_ok", staticmethod(_must_not_run))

    GitResolver().resolve(_source())  # no git command of any kind, no fetch


def test_credentials_the_source_url_itself_carries_are_left_alone(cache: Path, monkeypatch) -> None:
    # They are the contract author's own choice, and rewriting them would re-fetch on every run.
    cache_dir = _unfinished_clone(WITH_TOKEN, url=WITH_TOKEN)
    (cache_dir / MANIFEST).write_text("apiVersion: forge.scaffold/v1\n")
    _never_fetch(monkeypatch)
    monkeypatch.setattr(GitResolver, "_git", staticmethod(_must_not_run))
    monkeypatch.setattr(GitResolver, "_git_ok", staticmethod(_must_not_run))

    GitResolver().resolve(_source(url=WITH_TOKEN))


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (WITH_TOKEN, True),
        (f"https://{TOKEN}@example.invalid/org/repo", True),
        ("http://user:pw@example.invalid/x", True),
        ("HTTPS://x:y@example.invalid/x", True),
        ("ssh://user:pw@example.invalid/x", True),
        (PLAIN, False),
        ("https://example.invalid/org/repo@v1", False),  # an @ in the path is not a user part
        ("ssh://git@example.invalid/org/repo", False),
        ("git+ssh://git@example.invalid/org/repo", False),
        ("git@example.invalid:org/repo", False),
        ("", False),
    ],
)
def test_what_counts_as_a_credential(url: str, expected: bool) -> None:
    assert _has_credentials(url) is expected


@pytest.mark.parametrize(
    "config",
    [
        '[remote "origin"]\n\turl = https://a:b@h/x\n\tfetch = +refs/heads/*:x\n',
        '[core]\n\tbare = false\n[remote "origin"]\n\turl = "https://a:b@h/x"\n',
        "[remote.origin]\n\turl = https://a:b@h/x\n",
        '[remote "origin"]\n\turl = https://p@h/y\n\turl = https://a:b@h/x\n',  # a second url
        '[remote "origin"]\n\turl = https://h/y\n\tpushurl = https://a:b@h/x\n',
        '[remote "origin"] # a comment on the header\n\turl = https://a:b@h/x\n',
        '[remote "origin"]\n\turl = https://a:b@h/x # a comment on the value\n',
        '[remote "origin"]\n\turl = https://a:b@\\\nh/x\n',  # a line that ends in a backslash
        '[remote "upstream"]\n\turl = https://a:b@h/x\n',  # any remote, not only origin
    ],
    ids=[
        "origin",
        "quoted",
        "old section spelling",
        "second url line",
        "pushurl",
        "header comment",
        "value comment",
        "continued line",
        "another remote",
    ],
)
def test_a_token_is_found_however_the_config_spells_it(tmp_path: Path, config: str) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text(config)

    assert "https://a:b@h/x" in _stored_credentials(tmp_path, PLAIN)


@pytest.mark.parametrize(
    ("config", "url"),
    [
        ('[remote "origin"]\n\turl = https://example.invalid/org/repo\n', PLAIN),
        ('[remote "origin"]\n\turl = ssh://git@example.invalid/org/repo\n', PLAIN),
        (f'[remote "origin"]\n\turl = {WITH_TOKEN}\n', WITH_TOKEN),  # the source's own url
        ("[core]\n\tbare = false\n", PLAIN),
        ("", PLAIN),
    ],
    ids=["plain", "ssh user name", "the source's own url", "no url at all", "empty"],
)
def test_nothing_is_reported_when_there_is_no_credential_to_remove(
    tmp_path: Path, config: str, url: str
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text(config)

    assert _stored_credentials(tmp_path, url) == []


def test_a_directory_with_no_git_config_has_no_stored_credentials(tmp_path: Path) -> None:
    assert _stored_credentials(tmp_path, PLAIN) == []


def test_a_token_that_cannot_be_deleted_is_an_error_even_after_the_rename(
    cache: Path, monkeypatch
) -> None:
    # The clone is renamed aside, so the cache path is clear, but the files cannot go (a read-only
    # directory, say). The token is still on disk, so this must not pass silently, and the
    # message says where it is.
    _unfinished_clone(WITH_TOKEN)
    real_rmtree = shutil.rmtree

    def stuck(path, *args, **kwargs):
        if Path(path).name.startswith(".old-"):
            return None  # nothing could be deleted
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(git_resolver.shutil, "rmtree", stuck)
    _never_fetch(monkeypatch)

    with pytest.raises(
        ResolutionError, match=r"could not be removed: delete .*\.old-[0-9a-f]+ by hand"
    ):
        GitResolver().resolve(_source())


def test_leftovers_that_hold_no_credential_are_not_an_error(cache: Path, monkeypatch) -> None:
    # A delete that leaves only read-only object files (common on Windows) has removed the token:
    # the run goes on and fetches again, the leftovers are litter.
    cache_dir = _unfinished_clone(WITH_TOKEN)
    (cache_dir / ".git" / "objects").mkdir()
    (cache_dir / ".git" / "objects" / "pack.pack").write_text("read-only")
    real_rmtree = shutil.rmtree

    def removes_all_but_the_objects(path, *args, **kwargs):
        if Path(path).name.startswith(".old-"):
            (Path(path) / ".git" / "config").unlink()
            return None
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(git_resolver.shutil, "rmtree", removes_all_but_the_objects)
    fetched = _fetch_that_records(monkeypatch)

    bundle = GitResolver().resolve(_source())

    assert fetched == [cache_dir]
    assert (bundle.bundle_root / MANIFEST).is_file()


def test_a_first_run_that_loses_the_race_keeps_the_clone_the_other_run_published(
    cache: Path, monkeypatch
) -> None:
    # Another run publishes the same clone while this one is still fetching (both saw no cache).
    cache_dir = GitResolver._cache_dir_for(PLAIN, "v1")

    def fetch_while_the_other_run_publishes(self, clone_url, ref, target, url):
        target.mkdir(parents=True)
        (target / MANIFEST).write_text("apiVersion: ours\n")
        cache_dir.mkdir(parents=True)  # the other run's clone appears first
        (cache_dir / MANIFEST).write_text("apiVersion: theirs\n")

    monkeypatch.setattr(GitResolver, "_clone_ref_into_cache", fetch_while_the_other_run_publishes)

    bundle = GitResolver().resolve(_source())

    assert (bundle.bundle_root / MANIFEST).read_text() == "apiVersion: theirs\n"
    assert [p.name for p in cache_dir.parent.iterdir()] == [cache_dir.name]  # no staging left


def test_a_refresh_replaces_the_clone_another_run_published(cache: Path, monkeypatch) -> None:
    # Only a refresh that was asked for replaces an existing clone.
    monkeypatch.setenv("FLUID_CUSTOM_SCAFFOLD_NOCACHE", "1")
    cache_dir = GitResolver._cache_dir_for(PLAIN, "v1")
    cache_dir.mkdir(parents=True)
    (cache_dir / MANIFEST).write_text("apiVersion: old\n")

    def fetch(self, clone_url, ref, target, url):
        target.mkdir(parents=True)
        (target / MANIFEST).write_text("apiVersion: new\n")

    monkeypatch.setattr(GitResolver, "_clone_ref_into_cache", fetch)

    bundle = GitResolver().resolve(_source())

    assert (bundle.bundle_root / MANIFEST).read_text() == "apiVersion: new\n"
