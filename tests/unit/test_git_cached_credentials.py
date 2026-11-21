"""A cached clone that carries a credential is cleaned before it is reused.

An earlier version cloned with the token in the URL, and ``git clone`` records that URL as
``origin`` in ``.git/config`` before it transfers anything. A run killed during the transfer
(or a clone made by that version) leaves the token in the cache, where every later run
reuses it. The resolver now looks at ``origin`` before it reuses a clone.
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
    _origin_url,
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
    return tmp_path


def _git(*args: str) -> None:
    subprocess.run(["git", *args], check=True, capture_output=True)


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


def _source(url: str = PLAIN, ref: str = "v1") -> dict:
    return {"kind": "git", "url": url, "ref": ref}


def _never_fetch(monkeypatch) -> None:
    def fail(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("the cached clone should have been reused, not fetched again")

    monkeypatch.setattr(GitResolver, "_clone_into_cache", fail)


def _must_not_run(*args: Any, **kwargs: Any) -> None:
    raise AssertionError("git should not have been run")


def _tree_text(root: Path) -> str:
    return "\n".join(p.read_text(errors="replace") for p in root.rglob("*") if p.is_file())


@needs_git
@pytest.mark.parametrize(
    "tainted",
    [WITH_TOKEN, f"https://{TOKEN}@example.invalid/org/repo"],
    ids=["user and token", "token as the user name"],
)
def test_a_token_left_in_a_cached_clone_is_removed_before_the_clone_is_reused(
    cache: Path, monkeypatch, tainted: str
) -> None:
    cache_dir = _cached_clone(cache, origin=tainted)
    assert TOKEN in (cache_dir / ".git" / "config").read_text()  # what the old clone left
    _never_fetch(monkeypatch)

    bundle = GitResolver().resolve(_source())

    assert (bundle.bundle_root / MANIFEST).is_file()
    assert TOKEN not in _tree_text(cache_dir / ".git")
    assert _origin_url(cache_dir) == PLAIN


@pytest.mark.parametrize(
    "origin",
    [PLAIN, "ssh://git@example.invalid/org/repo", "git@example.invalid:org/repo"],
    ids=["plain https", "ssh user name", "scp-style"],
)
def test_a_clone_with_no_credential_is_reused_as_it_is(cache: Path, monkeypatch, origin) -> None:
    cache_dir = GitResolver._cache_dir_for(PLAIN, "v1")
    (cache_dir / ".git").mkdir(parents=True)
    (cache_dir / ".git" / "config").write_text(f'[remote "origin"]\n\turl = {origin}\n')
    (cache_dir / MANIFEST).write_text("apiVersion: forge.scaffold/v1\n")
    _never_fetch(monkeypatch)
    monkeypatch.setattr(GitResolver, "_git", staticmethod(_must_not_run))

    GitResolver().resolve(_source())  # no `git remote set-url`, no fetch


def test_credentials_the_source_url_itself_carries_are_left_alone(cache: Path, monkeypatch) -> None:
    # They are the contract author's own choice, and rewriting them would re-fetch on every run.
    cache_dir = GitResolver._cache_dir_for(WITH_TOKEN, "v1")
    (cache_dir / ".git").mkdir(parents=True)
    (cache_dir / ".git" / "config").write_text(f'[remote "origin"]\n\turl = {WITH_TOKEN}\n')
    (cache_dir / MANIFEST).write_text("apiVersion: forge.scaffold/v1\n")
    _never_fetch(monkeypatch)
    monkeypatch.setattr(GitResolver, "_git", staticmethod(_must_not_run))

    GitResolver().resolve(_source(url=WITH_TOKEN))


def _tainted_clone_that_cannot_be_scrubbed(cache: Path, monkeypatch) -> Path:
    cache_dir = GitResolver._cache_dir_for(PLAIN, "v1")
    (cache_dir / ".git").mkdir(parents=True)
    (cache_dir / ".git" / "config").write_text(f'[remote "origin"]\n\turl = {WITH_TOKEN}\n')
    (cache_dir / MANIFEST).write_text("apiVersion: forge.scaffold/v1\n")

    def failing_git(args: List[str], *, clone_url: str, url: str, what: str) -> None:
        raise ResolutionError("git reset the remote URL failed")

    monkeypatch.setattr(GitResolver, "_git", staticmethod(failing_git))
    return cache_dir


def test_a_clone_that_cannot_be_scrubbed_is_deleted_and_fetched_again(
    cache: Path, monkeypatch
) -> None:
    cache_dir = _tainted_clone_that_cannot_be_scrubbed(cache, monkeypatch)
    fetched: List[Path] = []

    def fetch(self, url: str, ref: str, target: Path, auth: Any) -> None:
        fetched.append(target)
        assert not target.exists()  # the tainted clone is gone before the new one is made
        target.mkdir(parents=True)
        (target / MANIFEST).write_text("apiVersion: forge.scaffold/v1\n")

    monkeypatch.setattr(GitResolver, "_clone_into_cache", fetch)

    bundle = GitResolver().resolve(_source())

    assert fetched == [cache_dir]
    assert TOKEN not in _tree_text(cache_dir)
    assert (bundle.bundle_root / MANIFEST).is_file()


def test_a_clone_that_can_be_neither_scrubbed_nor_deleted_is_an_error(
    cache: Path, monkeypatch
) -> None:
    cache_dir = _tainted_clone_that_cannot_be_scrubbed(cache, monkeypatch)
    monkeypatch.setattr(git_resolver.shutil, "rmtree", lambda *args, **kwargs: None)
    _never_fetch(monkeypatch)

    with pytest.raises(ResolutionError):
        GitResolver().resolve(_source())

    assert cache_dir.exists()  # nothing was reused: the error came first


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
    ("config", "expected"),
    [
        ('[remote "origin"]\n\turl = https://a@h/x\n\tfetch = +refs/heads/*:x\n', "https://a@h/x"),
        (
            '[core]\n\tbare = false\n[remote "origin"]\n\turl = "https://a:b@h/x"\n',
            "https://a:b@h/x",
        ),
        ("[remote.origin]\n\turl = https://a@h/x\n", "https://a@h/x"),
        ('[remote "upstream"]\n\turl = https://a@h/x\n', ""),
        ('[remote "origin"]\n\tfetch = x\n[branch "main"]\n\turl = nope\n', ""),
        ("", ""),
    ],
)
def test_origin_url_is_read_from_the_origin_section_only(
    tmp_path: Path, config: str, expected: str
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text(config)

    assert _origin_url(tmp_path) == expected


def test_a_directory_with_no_git_config_has_no_origin(tmp_path: Path) -> None:
    assert _origin_url(tmp_path) == ""
