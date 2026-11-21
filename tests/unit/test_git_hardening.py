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
from tests.helpers import completed, patch_run

TOKEN = "s3cret-token-value"
PLAIN_URL = "https://example.com/org/repo"


def _spy_run(monkeypatch) -> List[Dict[str, Any]]:
    """Replace subprocess.run in the resolver; return the keyword args each call was given."""
    calls = patch_run(monkeypatch, git_resolver, lambda args, kwargs: completed(args, "abc123\n"))

    class Seen(list):
        def __len__(self) -> int:
            return len(calls)

        def __getitem__(self, index):  # type: ignore[override]
            return calls[index][1]

        def __iter__(self):
            return (kwargs for _, kwargs in calls)

    return Seen()


def test_git_subprocesses_never_prompt_by_default(monkeypatch) -> None:
    monkeypatch.delenv("GIT_TERMINAL_PROMPT", raising=False)
    seen = _spy_run(monkeypatch)

    GitResolver._git(["status"], url=PLAIN_URL, what="status")
    GitResolver._git_ok(["status"])
    GitResolver._head_sha(Path("."))

    assert len(seen) == 4  # `_git` also asks git how it will use the url
    assert all(kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0" for kwargs in seen)


def test_an_explicit_prompt_setting_is_respected(monkeypatch) -> None:
    # The default is "no prompt"; a caller that deliberately sets the variable keeps it.
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "1")
    seen = _spy_run(monkeypatch)

    GitResolver._git(["status"], url=PLAIN_URL, what="status")

    assert seen[0]["env"]["GIT_TERMINAL_PROMPT"] == "1"


def test_a_source_with_no_token_is_cloned_and_nothing_else(monkeypatch, tmp_path: Path) -> None:
    calls: List[List[str]] = []

    def fake_git(args, *, url, what, credential=None):
        calls.append(list(args))
        Path(args[-1]).mkdir(parents=True)  # `git clone` creates the directory it clones into

    monkeypatch.setattr(GitResolver, "_git", staticmethod(fake_git))

    GitResolver()._clone_into_cache(PLAIN_URL, "v1", tmp_path / "clone", {})

    assert [call[0] for call in calls] == ["clone"]
    assert (tmp_path / "clone").is_dir()  # published under its own name


def _git_that_makes_its_directory_then(monkeypatch, failure: BaseException) -> None:
    """Stub GitResolver._git: create the directory git would create, then fail."""

    def stops(args, *, url, what, credential=None):
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
    monkeypatch.setenv("FLUID_CUSTOM_SCAFFOLD_AUTH_HOSTS", "example.com")
    monkeypatch.delenv("FLUID_SCAFFOLD_NO_SUCH_TOKEN", raising=False)

    with pytest.raises(ResolutionError, match="unset"):
        GitResolver()._clone_into_cache(
            PLAIN_URL, "v1", cache, {"secret_ref": "FLUID_SCAFFOLD_NO_SUCH_TOKEN"}
        )

    assert (cache / "keep").read_text() == "the old clone"


def test_a_refresh_replaces_the_older_clone_and_leaves_nothing_else(tmp_path: Path) -> None:
    cache = tmp_path / "cache" / "clone"
    cache.mkdir(parents=True)
    (cache / "old").write_text("old")
    staging = tmp_path / "cache" / ".partial-x"
    staging.mkdir()
    (staging / "new").write_text("new")

    git_resolver._publish(staging, cache, replace=True)

    assert sorted(p.name for p in cache.iterdir()) == ["new"]
    assert [p.name for p in cache.parent.iterdir()] == ["clone"]


def test_a_first_run_never_removes_a_clone_another_run_published(tmp_path: Path) -> None:
    # The other run may be reading it right now: keep theirs, drop ours.
    cache = tmp_path / "cache" / "clone"
    cache.mkdir(parents=True)
    (cache / "theirs").write_text("theirs")
    staging = tmp_path / "cache" / ".partial-x"
    staging.mkdir()
    (staging / "mine").write_text("mine")

    git_resolver._publish(staging, cache, replace=False)

    assert [p.name for p in cache.iterdir()] == ["theirs"]
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

    git_resolver._publish(staging, cache, replace=False)  # no error: a finished clone is in place

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
        git_resolver._publish(staging, cache, replace=False)


def test_an_error_does_not_keep_the_failed_command_or_the_credential(monkeypatch) -> None:
    # The error is raised outside its handler, so the CalledProcessError is not chained as
    # __context__ for a reporter to find, and what git printed is redacted.
    credential = git_resolver._Credential(url=PLAIN_URL, token=TOKEN)
    basic = credential._basic

    def fail(args, **kwargs):
        if args[1:3] == ["ls-remote", "--get-url"]:  # git is asked how it will use the url
            return subprocess.CompletedProcess(args, 0, stdout=PLAIN_URL + "\n", stderr="")
        raise subprocess.CalledProcessError(
            1, args, stderr=f"fatal: bad header {basic} for {TOKEN} at '{PLAIN_URL}'"
        )

    monkeypatch.setattr(git_resolver.subprocess, "run", fail)

    with pytest.raises(ResolutionError) as raised:
        GitResolver._git(["fetch", PLAIN_URL], url=PLAIN_URL, what="fetch", credential=credential)

    assert TOKEN not in str(raised.value) and basic not in str(raised.value)
    assert raised.value.__context__ is None and raised.value.__cause__ is None


@pytest.mark.skipif(shutil.which("git") is None, reason="git binary not on PATH")
def test_scrub_removes_the_token_from_a_real_repository_config(tmp_path: Path) -> None:
    repo = tmp_path / "clone"
    token_url = f"https://x-access-token:{TOKEN}@example.com/org/repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", token_url], check=True)
    assert TOKEN in (repo / ".git" / "config").read_text()  # what a token clone leaves behind

    _scrub_origin(str(repo / ".git"), token_url, PLAIN_URL)

    config = (repo / ".git" / "config").read_text()
    assert TOKEN not in config
    assert PLAIN_URL in config


def _interrupt_when_deleting_the_old_clone(monkeypatch) -> None:
    real_rmtree = git_resolver.shutil.rmtree

    def rmtree(path, *args, **kwargs):
        if Path(path).name.startswith(".old-"):
            raise KeyboardInterrupt
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(git_resolver.shutil, "rmtree", rmtree)


def test_a_directory_is_never_left_half_deleted(monkeypatch, tmp_path: Path) -> None:
    # A kill or Ctrl-C while the files go: the directory is already renamed away, so the
    # path is absent, not a damaged clone that a later run would reuse.
    clone = tmp_path / "cache" / "clone"
    (clone / ".git").mkdir(parents=True)
    _interrupt_when_deleting_the_old_clone(monkeypatch)

    with pytest.raises(KeyboardInterrupt):
        git_resolver._discard(clone)

    assert not clone.exists()
    assert [p.name.startswith(".old-") for p in clone.parent.iterdir()] == [True]


def test_a_refresh_interrupted_while_the_old_clone_goes_keeps_the_new_one(
    monkeypatch, tmp_path: Path
) -> None:
    cache = tmp_path / "cache" / "clone"
    cache.mkdir(parents=True)
    (cache / "old").write_text("old")
    staging = cache.parent / ".partial-x"
    staging.mkdir()
    (staging / "new").write_text("new")
    _interrupt_when_deleting_the_old_clone(monkeypatch)

    with pytest.raises(KeyboardInterrupt):
        git_resolver._publish(staging, cache, replace=True)

    assert [p.name for p in cache.iterdir()] == ["new"]  # whole, and the new one


def test_a_refresh_that_cannot_publish_puts_the_older_clone_back(
    monkeypatch, tmp_path: Path
) -> None:
    cache = tmp_path / "cache" / "clone"
    cache.mkdir(parents=True)
    (cache / "old").write_text("old")
    staging = cache.parent / ".partial-x"
    staging.mkdir()
    real_replace = git_resolver.os.replace

    def replace(src, dst):
        if Path(src) == staging:
            raise OSError("no space left")
        return real_replace(src, dst)

    monkeypatch.setattr(git_resolver.os, "replace", replace)

    with pytest.raises(OSError, match="no space"):
        git_resolver._publish(staging, cache, replace=True)

    assert [p.name for p in cache.iterdir()] == ["old"]


@pytest.mark.parametrize("ref", ["main:refs/heads/other", "a:b", ":"])
def test_a_ref_with_a_colon_is_refused_before_any_git_command(monkeypatch, ref: str) -> None:
    # git ref names cannot contain ":"; with a token it would have become a refspec.
    monkeypatch.setattr(GitResolver, "_git", staticmethod(lambda *a, **k: 1 / 0))

    with pytest.raises(ResolutionError, match="not a valid git ref name"):
        GitResolver().resolve({"kind": "git", "url": PLAIN_URL, "ref": ref})


def test_discard_says_what_is_left(monkeypatch, tmp_path: Path) -> None:
    gone = tmp_path / "gone"
    (gone / ".git").mkdir(parents=True)
    assert git_resolver._discard(gone) is None and not gone.exists()

    stuck = tmp_path / "stuck"
    (stuck / ".git").mkdir(parents=True)
    monkeypatch.setattr(git_resolver.shutil, "rmtree", lambda *args, **kwargs: None)
    left = git_resolver._discard(stuck)  # renamed aside, but its files remain
    assert left is not None and left.name.startswith(".old-") and left.is_dir()
    assert not stuck.exists()


def test_a_directory_that_cannot_be_renamed_is_left_whole(monkeypatch, tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    (clone / ".git").mkdir(parents=True)
    (clone / ".git" / "config").write_text("kept")

    def cannot(src, dst):
        raise OSError("operation not permitted")

    monkeypatch.setattr(git_resolver.os, "replace", cannot)

    assert git_resolver._discard(clone) == clone  # it is still where it was
    assert (clone / ".git" / "config").read_text() == "kept"  # not deleted in place


def test_a_refresh_races_another_refresh_for_the_old_clone(monkeypatch, tmp_path: Path) -> None:
    cache = tmp_path / "cache" / "clone"
    cache.mkdir(parents=True)
    staging = cache.parent / ".partial-x"
    staging.mkdir()
    (staging / "new").write_text("new")
    real_replace = git_resolver.os.replace

    def another_refresh_moved_it_first(src, dst):
        if Path(src) == cache:
            shutil_rmtree(cache)  # gone before this run can rename it
        return real_replace(src, dst)

    shutil_rmtree = shutil.rmtree
    monkeypatch.setattr(git_resolver.os, "replace", another_refresh_moved_it_first)

    git_resolver._publish(staging, cache, replace=True)  # no error

    assert [p.name for p in cache.iterdir()] == ["new"]
