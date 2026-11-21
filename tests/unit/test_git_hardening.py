"""git subprocess hardening: never prompt for a credential, never keep the token.

A library call in a non-interactive job must not hang on a credential prompt, and a
token injected into a clone URL must not stay in the cache's ``.git/config`` (git
records the URL a clone was given as the remote).
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


def _recording_git(monkeypatch, cache: Path, *, fail_on: str = "") -> List[List[str]]:
    """Stub GitResolver._git: record the arguments and create the clone directory."""
    calls: List[List[str]] = []

    def fake_git(args, *, clone_url, url, what):
        calls.append(list(args))
        if args[0] == "clone":
            cache.mkdir(parents=True)
        if fail_on and fail_on in args:
            raise ResolutionError("git failed")

    monkeypatch.setattr(GitResolver, "_git", staticmethod(fake_git))
    return calls


def test_a_branch_clone_with_a_token_resets_the_remote_to_the_plain_url(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DPF_TEST_TOKEN", TOKEN)
    cache = tmp_path / "cache" / "clone"
    calls = _recording_git(monkeypatch, cache)

    GitResolver()._clone_into_cache(PLAIN_URL, "v1", cache, {"secret_ref": "DPF_TEST_TOKEN"})

    assert calls[0][:5] == ["clone", "--depth", "1", "--branch", "v1"]
    assert TOKEN in calls[0][5]  # the clone itself needs it
    assert calls[1] == ["-C", str(cache), "remote", "set-url", "origin", PLAIN_URL]
    assert TOKEN not in " ".join(calls[1])


def test_a_clone_without_a_token_is_not_rewritten(monkeypatch, tmp_path: Path) -> None:
    cache = tmp_path / "cache" / "clone"
    calls = _recording_git(monkeypatch, cache)

    GitResolver()._clone_into_cache(PLAIN_URL, "v1", cache, {})

    assert [c[0] for c in calls] == ["clone"]


def test_a_failed_scrub_deletes_the_clone_instead_of_keeping_the_token(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DPF_TEST_TOKEN", TOKEN)
    cache = tmp_path / "cache" / "clone"
    _recording_git(monkeypatch, cache, fail_on="set-url")

    with pytest.raises(ResolutionError):
        GitResolver()._clone_into_cache(PLAIN_URL, "v1", cache, {"secret_ref": "DPF_TEST_TOKEN"})

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
