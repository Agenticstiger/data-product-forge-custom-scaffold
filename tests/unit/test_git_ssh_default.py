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
import subprocess
from pathlib import Path
from typing import Dict, List

import pytest

from data_product_forge_custom_scaffold.resolvers.base import ResolutionError
from data_product_forge_custom_scaffold.resolvers.git import (
    GitResolver,
    _batch_mode_blocked,
    _git_env,
    _git_version,
)

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


needs_has_config = pytest.mark.skipif(
    shutil.which("git") is None or _git_version() < (2, 36),
    reason="includeIf hasconfig: needs git 2.36",
)


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
        GitResolver._git(["ls-remote", url], url=url, what="ls-remote")
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


def _clone(url: str, dest: Path) -> None:
    """The command the resolver runs for a plain branch or tag: `git clone`, outside any repo."""
    with pytest.raises(ResolutionError):
        GitResolver._git(["clone", "--depth", "1", url, str(dest)], url=url, what="clone")


@pytest.mark.parametrize(
    "directive",
    ["[include]\n\tpath = {extra}\n", '[includeIf "gitdir:/somewhere/"]\n\tpath = {extra}\n'],
    ids=["include", "includeIf"],
)
def test_an_include_in_git_config_means_the_default_is_not_guessed(
    world, tmp_path: Path, directive: str
) -> None:
    # An include can set core.sshCommand for one host or one directory, and only git can say
    # whether it applies. Guessing wrong would switch off the caller's own key.
    extra = tmp_path / "extra.cfg"
    extra.write_text("[user]\n\tname = someone\n")
    world["config"].write_text(directive.format(extra=extra))

    assert "GIT_SSH_COMMAND" not in _git_env(SSH_URLS[0])


@needs_has_config
def test_a_command_set_for_one_host_through_an_include_is_what_a_clone_uses(
    world, tmp_path: Path
) -> None:
    url = SSH_URLS[0]
    for_this_host = tmp_path / "host.cfg"
    for_this_host.write_text(f"[core]\n\tsshCommand = {world['mine']}\n")
    world["config"].write_text(
        '[includeIf "hasconfig:remote.*.url:ssh://git@example.invalid/**"]\n'
        f"\tpath = {for_this_host}\n"
    )

    _clone(url, tmp_path / "dest")

    calls = _calls(world)
    assert calls and all(call.startswith("mine ") for call in calls)
    assert not any("BatchMode" in call for call in calls)


def test_the_current_directorys_own_config_does_not_decide(
    world, tmp_path: Path, monkeypatch
) -> None:
    # `git clone` never reads the config of the repository it is run from, so a
    # core.sshCommand there must not switch the default off.
    repo = tmp_path / "elsewhere"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "core.sshCommand", str(world["mine"])], check=True
    )
    monkeypatch.chdir(repo)

    _clone(SSH_URLS[0], tmp_path / "dest")

    calls = _calls(world)
    assert len(calls) == 1 and calls[0].startswith("ssh -o BatchMode=yes ")


@pytest.mark.parametrize(
    ("text", "caused_by_batch_mode"),
    [
        ("Host key verification failed.", True),
        ("git@example.invalid: Permission denied (publickey).", True),
        ("fatal: could not create work tree dir 'x': Permission denied", False),
        ("fatal: repository not found", False),
    ],
)
def test_only_what_batch_mode_causes_gets_the_hint(text: str, caused_by_batch_mode: bool) -> None:
    assert _batch_mode_blocked(text, {"GIT_SSH_COMMAND": BATCH_MODE}) is caused_by_batch_mode


def test_no_hint_when_the_caller_set_the_same_command_themselves(monkeypatch) -> None:
    monkeypatch.setenv("GIT_SSH_COMMAND", BATCH_MODE)

    assert not _batch_mode_blocked("Host key verification failed.", {"GIT_SSH_COMMAND": BATCH_MODE})


def test_a_template_directory_means_the_default_is_not_guessed(world, tmp_path: Path) -> None:
    # A template's config is copied into every repository `git init` makes, where it can set
    # core.sshCommand for the fetch that follows.
    world["config"].write_text(f"[init]\n\ttemplateDir = {tmp_path / 'templates'}\n")

    assert "GIT_SSH_COMMAND" not in _git_env(SSH_URLS[0])


def test_a_template_directory_in_the_environment_means_the_same(world, monkeypatch) -> None:
    monkeypatch.setenv("GIT_TEMPLATE_DIR", "/somewhere/templates")

    assert "GIT_SSH_COMMAND" not in _git_env(SSH_URLS[0])


def test_an_https_url_that_insteadof_rewrites_to_ssh_runs_in_batch_mode(
    world, tmp_path: Path
) -> None:
    # git will use ssh for it, whatever scheme the contract wrote.
    world["config"].write_text(
        '[url "ssh://git@example.invalid/"]\n\tinsteadOf = https://example.invalid/\n'
    )
    url = "https://example.invalid/org/repo"

    assert _git_env(url)["GIT_SSH_COMMAND"] == BATCH_MODE
    _clone(url, tmp_path / "dest")
    calls = _calls(world)
    assert len(calls) == 1 and calls[0].startswith("ssh -o BatchMode=yes ")


def test_an_ssh_url_that_insteadof_rewrites_to_https_gets_no_ssh_default(world) -> None:
    world["config"].write_text(
        '[url "https://example.invalid/"]\n\tinsteadOf = git@example.invalid:\n'
    )

    assert "GIT_SSH_COMMAND" not in _git_env("git@example.invalid:org/repo")
