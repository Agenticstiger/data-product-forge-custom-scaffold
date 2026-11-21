"""ssh runs without prompts unless the caller chose an ssh command.

A library call in a non-interactive job must not hang on an ssh passphrase or a host-key
question. git consults ``GIT_SSH_COMMAND`` first, then ``core.sshCommand``, then ``GIT_SSH``,
so a default ``GIT_SSH_COMMAND`` would silently switch off a deploy key set up through either
of the others. The default is therefore only set when none of the three is in use.

These tests run real git against stand-in ``ssh`` programs that log how they were called.
"""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path
from typing import Dict, List

import pytest

from data_product_forge_custom_scaffold.resolvers.base import ResolutionError
from data_product_forge_custom_scaffold.resolvers.git import GitResolver, _git_env

pytestmark = [
    pytest.mark.skipif(shutil.which("git") is None, reason="git binary not on PATH"),
    pytest.mark.skipif(os.name == "nt", reason="the stand-in ssh programs are shell scripts"),
]

SSH_URLS = [
    "ssh://git@example.invalid/org/repo",
    "git+ssh://git@example.invalid/org/repo",
    "git@example.invalid:org/repo",
]
HTTPS_URL = "https://example.invalid/org/repo"
BATCH_MODE = "ssh -o BatchMode=yes"


@pytest.fixture
def world(tmp_path: Path, monkeypatch) -> Dict[str, Path]:
    """No ssh setting anywhere, and two stand-in ssh programs that log their arguments.

    ``ssh`` is what git finds when nothing is configured; ``mine`` is the caller's choice.
    Both fail the way ssh fails when it may not prompt, so git reports an error.
    """
    log = tmp_path / "ssh.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("ssh", "mine"):
        program = bin_dir / name
        program.write_text(
            f'#!/bin/sh\necho "{name} $*" >> "{log}"\necho "Host key verification failed." >&2\nexit 255\n'
        )
        program.chmod(program.stat().st_mode | stat.S_IXUSR)
    config = tmp_path / "gitconfig"
    config.write_text("")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("GIT_SSH", raising=False)
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    return {"log": log, "mine": bin_dir / "mine", "config": config}


def _ls_remote(url: str) -> str:
    """Run ``git ls-remote`` through the resolver; return the error it raises."""
    with pytest.raises(ResolutionError) as raised:
        GitResolver._git(["ls-remote", url], clone_url=url, url=url, what="ls-remote")
    return str(raised.value)


def _calls(world: Dict[str, Path]) -> List[str]:
    return world["log"].read_text().splitlines() if world["log"].exists() else []


@pytest.mark.parametrize("url", SSH_URLS)
def test_ssh_runs_in_batch_mode_when_nothing_is_configured(world, url: str) -> None:
    _ls_remote(url)

    calls = _calls(world)
    assert len(calls) == 1
    assert calls[0].startswith("ssh -o BatchMode=yes ")


def test_the_default_is_exactly_ssh_with_batch_mode(world) -> None:
    assert _git_env(SSH_URLS[0])["GIT_SSH_COMMAND"] == BATCH_MODE


@pytest.mark.parametrize("choice", ["GIT_SSH_COMMAND", "GIT_SSH", "core.sshCommand"])
def test_a_caller_chosen_ssh_command_is_left_alone(world, monkeypatch, choice: str) -> None:
    mine = str(world["mine"])
    if choice == "core.sshCommand":
        world["config"].write_text(f"[core]\n\tsshCommand = {mine}\n")
    else:
        monkeypatch.setenv(choice, mine)

    _ls_remote(SSH_URLS[0])

    # git may probe an unfamiliar program name first (``-G``), so more than one call is fine:
    # every one is theirs, and nothing was added to their command.
    calls = _calls(world)
    assert calls and all(call.startswith("mine ") for call in calls)
    assert not any("BatchMode" in call for call in calls)


def test_a_caller_value_that_is_empty_is_still_the_callers_choice(world, monkeypatch) -> None:
    monkeypatch.setenv("GIT_SSH_COMMAND", "")

    assert _git_env(SSH_URLS[0])["GIT_SSH_COMMAND"] == ""


@pytest.mark.parametrize("url", [HTTPS_URL, ""])
def test_urls_that_do_not_use_ssh_get_no_ssh_default(world, url: str) -> None:
    assert "GIT_SSH_COMMAND" not in _git_env(url)


def test_a_failure_caused_by_batch_mode_says_how_to_get_a_prompt_back(world) -> None:
    error = _ls_remote(SSH_URLS[0])

    assert "Host key verification failed." in error
    assert "BatchMode" in error and "GIT_SSH_COMMAND" in error


def test_no_hint_when_the_caller_chose_the_ssh_command(world, monkeypatch) -> None:
    monkeypatch.setenv("GIT_SSH_COMMAND", str(world["mine"]))

    error = _ls_remote(SSH_URLS[0])

    assert "Host key verification failed." in error
    assert "BatchMode" not in error
