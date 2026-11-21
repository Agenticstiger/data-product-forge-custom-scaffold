"""git subprocess hardening: never prompt for a credential, never leave half a clone.

A library call in a non-interactive job must not hang on a credential prompt, and a fetch
that fails or is interrupted must not leave a directory the next run takes for a clone.
(Where a token may be written is covered by ``test_git_token_flow.py`` and
``test_git_cached_credentials.py``.)
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List

import pytest

from data_product_forge_custom_scaffold.resolvers import git as git_resolver
from data_product_forge_custom_scaffold.resolvers.base import ResolutionError
from data_product_forge_custom_scaffold.resolvers.git import GitResolver, _scrub_origin

TOKEN = "s3cret-token-value"
PLAIN_URL = "https://example.com/org/repo"


def _spy_run(monkeypatch) -> List[Dict[str, Any]]:
    """Replace subprocess.run in the resolver; return the list of keyword args it saw."""
    seen: List[Dict[str, Any]] = []

    def fake_run(args, **kwargs):
        seen.append(kwargs)
        return subprocess.CompletedProcess(args, 0, stdout="abc123\n", stderr="")

    monkeypatch.setattr(git_resolver.subprocess, "run", fake_run)
    return seen


def test_git_subprocesses_never_prompt_by_default(monkeypatch) -> None:
    monkeypatch.delenv("GIT_TERMINAL_PROMPT", raising=False)
    seen = _spy_run(monkeypatch)

    GitResolver._git(["status"], clone_url=PLAIN_URL, url=PLAIN_URL, what="status")
    GitResolver._git_ok(["status"])
    GitResolver._head_sha(Path("."))

    assert len(seen) == 3
    assert all(kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0" for kwargs in seen)


def test_an_explicit_prompt_setting_is_respected(monkeypatch) -> None:
    # The default is "no prompt"; a caller that deliberately sets the variable keeps it.
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "1")
    seen = _spy_run(monkeypatch)

    GitResolver._git(["status"], clone_url=PLAIN_URL, url=PLAIN_URL, what="status")

    assert seen[0]["env"]["GIT_TERMINAL_PROMPT"] == "1"


def test_a_source_with_no_token_is_cloned_and_nothing_else(monkeypatch, tmp_path: Path) -> None:
    calls: List[List[str]] = []

    def fake_git(args, *, clone_url, url, what):
        calls.append(list(args))
        Path(args[-1]).mkdir(parents=True)  # `git clone` creates the directory it clones into

    monkeypatch.setattr(GitResolver, "_git", staticmethod(fake_git))

    GitResolver()._clone_into_cache(PLAIN_URL, "v1", tmp_path / "clone", {})

    assert [call[0] for call in calls] == ["clone"]
    assert (tmp_path / "clone").is_dir()  # published under its own name


def _git_that_makes_its_directory_then(monkeypatch, failure: BaseException) -> None:
    """Stub GitResolver._git: create the directory git would create, then fail."""

    def stops(args, *, clone_url, url, what):
        if args[0] in ("clone", "init"):
            Path(args[-1]).mkdir(parents=True, exist_ok=True)
        raise failure

    monkeypatch.setattr(GitResolver, "_git", staticmethod(stops))


@pytest.mark.parametrize("failure", [ResolutionError("git failed"), KeyboardInterrupt()])
@pytest.mark.parametrize("ref", ["v1", "a" * 40])
def test_a_clone_that_stops_part_way_leaves_nothing_behind(
    monkeypatch, tmp_path: Path, ref: str, failure: BaseException
) -> None:
    cache = tmp_path / "cache" / "clone"
    _git_that_makes_its_directory_then(monkeypatch, failure)

    with pytest.raises(type(failure)):
        GitResolver()._clone_into_cache(PLAIN_URL, ref, cache, {})

    assert list(cache.parent.iterdir()) == []  # neither the cache directory nor a staging one


def test_a_run_killed_outright_never_leaves_a_directory_that_looks_finished(
    monkeypatch, tmp_path: Path
) -> None:
    # A kill gives the process no chance to clean up: simulate that by making the cleanup
    # a no-op. The half-made clone is in a staging directory, not at the cache path.
    cache = tmp_path / "cache" / "clone"
    _git_that_makes_its_directory_then(monkeypatch, KeyboardInterrupt())

    with monkeypatch.context() as no_cleanup:
        no_cleanup.setattr(git_resolver.shutil, "rmtree", lambda *args, **kwargs: None)
        with pytest.raises(KeyboardInterrupt):
            GitResolver()._clone_into_cache(PLAIN_URL, "v1", cache, {})

    assert not cache.exists()
    assert [p.name for p in cache.parent.iterdir() if p.name.startswith(".partial-")]


def test_a_refresh_that_fails_keeps_the_clone_it_was_to_replace(
    monkeypatch, tmp_path: Path
) -> None:
    cache = tmp_path / "cache" / "clone"
    cache.mkdir(parents=True)
    (cache / "keep").write_text("the old clone")
    _git_that_makes_its_directory_then(monkeypatch, ResolutionError("git failed"))

    with pytest.raises(ResolutionError):
        GitResolver()._clone_into_cache(PLAIN_URL, "v1", cache, {})

    assert (cache / "keep").read_text() == "the old clone"


def test_a_missing_token_is_reported_before_anything_is_deleted(
    monkeypatch, tmp_path: Path
) -> None:
    cache = tmp_path / "cache" / "clone"
    cache.mkdir(parents=True)
    (cache / "keep").write_text("the old clone")
    monkeypatch.delenv("DPF_NO_SUCH_TOKEN", raising=False)

    with pytest.raises(ResolutionError, match="unset"):
        GitResolver()._clone_into_cache(PLAIN_URL, "v1", cache, {"secret_ref": "DPF_NO_SUCH_TOKEN"})

    assert (cache / "keep").read_text() == "the old clone"


def test_a_finished_clone_replaces_an_older_one_and_nothing_else_is_left(tmp_path: Path) -> None:
    cache = tmp_path / "cache" / "clone"
    cache.mkdir(parents=True)
    (cache / "old").write_text("old")
    staging = tmp_path / "cache" / ".partial-x"
    staging.mkdir()
    (staging / "new").write_text("new")

    git_resolver._publish(staging, cache)

    assert sorted(p.name for p in cache.iterdir()) == ["new"]
    assert [p.name for p in cache.parent.iterdir()] == ["clone"]


def test_when_another_run_publishes_first_theirs_is_kept(monkeypatch, tmp_path: Path) -> None:
    cache = tmp_path / "cache" / "clone"
    cache.parent.mkdir()
    staging = cache.parent / ".partial-x"
    staging.mkdir()
    (staging / "mine").write_text("mine")

    def lose_the_race(src, dst):
        Path(dst).mkdir()
        (Path(dst) / "theirs").write_text("theirs")
        raise OSError("directory not empty")

    monkeypatch.setattr(git_resolver.os, "replace", lose_the_race)

    git_resolver._publish(staging, cache)  # no error: a finished clone is in place

    assert [p.name for p in cache.iterdir()] == ["theirs"]
    assert not staging.exists()


def test_a_publish_that_fails_for_another_reason_is_an_error(monkeypatch, tmp_path: Path) -> None:
    cache = tmp_path / "cache" / "clone"
    cache.parent.mkdir()
    staging = cache.parent / ".partial-x"
    staging.mkdir()

    def fail(src, dst):
        raise OSError("permission denied")

    monkeypatch.setattr(git_resolver.os, "replace", fail)

    with pytest.raises(OSError, match="permission denied"):
        git_resolver._publish(staging, cache)


def test_an_error_does_not_keep_the_failed_command_with_its_token(monkeypatch) -> None:
    # The CalledProcessError's argument list holds the token URL. The new error is raised
    # outside its handler, so it is not chained as __context__ for a reporter to find.
    token_url = f"https://x-access-token:{TOKEN}@example.com/org/repo"

    def fail(args, **kwargs):
        raise subprocess.CalledProcessError(
            1, args, stderr=f"fatal: unable to access '{token_url}'"
        )

    monkeypatch.setattr(git_resolver.subprocess, "run", fail)

    with pytest.raises(ResolutionError) as raised:
        GitResolver._git(["fetch", token_url], clone_url=token_url, url=PLAIN_URL, what="fetch")

    assert TOKEN not in str(raised.value)
    assert raised.value.__context__ is None and raised.value.__cause__ is None


@pytest.mark.skipif(shutil.which("git") is None, reason="git binary not on PATH")
def test_scrub_removes_the_token_from_a_real_repository_config(tmp_path: Path) -> None:
    repo = tmp_path / "clone"
    token_url = f"https://x-access-token:{TOKEN}@example.com/org/repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", token_url], check=True)
    assert TOKEN in (repo / ".git" / "config").read_text()  # what a token clone leaves behind

    _scrub_origin(repo, token_url, PLAIN_URL)

    config = (repo / ".git" / "config").read_text()
    assert TOKEN not in config
    assert PLAIN_URL in config
