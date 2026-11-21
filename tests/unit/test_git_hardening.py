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
from data_product_forge_custom_scaffold.resolvers.git import GitResolver

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
    monkeypatch.setattr(
        GitResolver,
        "_git",
        staticmethod(lambda args, *, clone_url, url, what: calls.append(list(args))),
    )

    GitResolver()._clone_into_cache(PLAIN_URL, "v1", tmp_path / "clone", {})

    assert [call[0] for call in calls] == ["clone"]


@pytest.mark.parametrize("failure", [ResolutionError("git failed"), KeyboardInterrupt()])
@pytest.mark.parametrize("ref", ["v1", "a" * 40])
def test_a_clone_that_stops_part_way_leaves_nothing_to_reuse(
    monkeypatch, tmp_path: Path, ref: str, failure: BaseException
) -> None:
    cache = tmp_path / "cache" / "clone"

    def stops(args, *, clone_url, url, what):
        cache.mkdir(parents=True, exist_ok=True)  # git has made the directory by now
        raise failure

    monkeypatch.setattr(GitResolver, "_git", staticmethod(stops))

    with pytest.raises(type(failure)):
        GitResolver()._clone_into_cache(PLAIN_URL, ref, cache, {})

    assert not cache.exists()


@pytest.mark.skipif(shutil.which("git") is None, reason="git binary not on PATH")
def test_scrub_removes_the_token_from_a_real_repository_config(tmp_path: Path) -> None:
    repo = tmp_path / "clone"
    token_url = f"https://x-access-token:{TOKEN}@example.com/org/repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", token_url], check=True)
    assert TOKEN in (repo / ".git" / "config").read_text()  # what a token clone leaves behind

    GitResolver()._scrub_token(repo, token_url, PLAIN_URL)

    config = (repo / ".git" / "config").read_text()
    assert TOKEN not in config
    assert PLAIN_URL in config
