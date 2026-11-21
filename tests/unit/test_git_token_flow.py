"""A token used to fetch a branch or tag never reaches the cache on disk.

``git clone <url>`` writes the URL, token included, to ``.git/config`` before it transfers
anything, so a run killed mid-transfer leaves the token behind. A token fetch goes through
``git init`` + ``git fetch <url>`` + ``git checkout`` instead, which records no remote.

The tests run the real resolver and real git without a network: git rewrites the token
URL to a local directory (``url.<base>.insteadOf``), the way it would rewrite a mirror.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List

import pytest

from data_product_forge_custom_scaffold.resolvers.base import ResolutionError
from data_product_forge_custom_scaffold.resolvers.git import GitResolver

TOKEN = "s3cret-token-value"
URL = "https://example.invalid/org/repo"
MANIFEST = "fluid-scaffold.yaml"
AUTH = {"secret_ref": "DPF_TEST_TOKEN"}

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary not on PATH")


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t.t", "-c", "user.name=t", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


@pytest.fixture
def served(tmp_path: Path, monkeypatch) -> SimpleNamespace:
    """A bundle repository that ``https://example.invalid/`` reaches, with or without a token.

    ``main`` and an annotated tag ``v1`` sit on one commit. The name ``rel`` is both a branch
    (one commit further on) and a lightweight tag (on the first commit).
    """
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q")
    _git(work, "symbolic-ref", "HEAD", "refs/heads/main")
    (work / MANIFEST).write_text("apiVersion: forge.scaffold/v1\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "c1")
    _git(work, "tag", "-a", "-m", "v1", "v1")  # annotated: FETCH_HEAD names the tag object
    _git(work, "tag", "rel")
    _git(work, "checkout", "-q", "-b", "rel")
    (work / MANIFEST).write_text("apiVersion: forge.scaffold/v1\nextra: true\n", encoding="utf-8")
    _git(work, "-c", "commit.gpgsign=false", "commit", "-q", "-am", "c2")
    rel_branch = _git(work, "rev-parse", "refs/heads/rel")
    _git(work, "checkout", "-q", "main")
    server = tmp_path / "server" / "org"
    server.mkdir(parents=True)
    subprocess.run(
        ["git", "clone", "-q", "--bare", str(work), str(server / "repo.git")], check=True
    )

    config = tmp_path / "gitconfig"
    config.write_text(
        f'[url "file://{tmp_path / "server"}/"]\n'
        f"\tinsteadOf = https://x-access-token:{TOKEN}@example.invalid/\n"
        f"\tinsteadOf = https://example.invalid/\n"
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("FLUID_CUSTOM_SCAFFOLD_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("FLUID_CUSTOM_SCAFFOLD_NOCACHE", raising=False)
    monkeypatch.setenv("DPF_TEST_TOKEN", TOKEN)
    return SimpleNamespace(
        sha=_git(work, "rev-parse", "HEAD"), rel_branch=rel_branch, root=tmp_path
    )


def _source(ref: str, *, token: bool = True) -> dict:
    source = {"kind": "git", "url": URL, "ref": ref}
    return {**source, "auth": AUTH} if token else source


def _text_under(root: Path) -> str:
    return "\n".join(p.read_text(errors="replace") for p in root.rglob("*") if p.is_file())


@pytest.mark.parametrize("ref", ["main", "v1"], ids=["branch", "annotated tag"])
def test_a_token_fetch_of_a_branch_or_tag_leaves_no_token_in_the_cache(
    served: SimpleNamespace, ref: str
) -> None:
    bundle = GitResolver().resolve(_source(ref))

    assert (bundle.bundle_root / MANIFEST).is_file()
    assert bundle.resolved_version == served.sha  # the commit, not the tag object
    assert TOKEN not in _text_under(served.root / "cache")
    remotes = _git(bundle.bundle_root, "remote")
    assert remotes == ""  # no remote was recorded for the token to sit in


@pytest.mark.parametrize("ref", ["main", "v1", "commit"])
def test_no_git_step_leaves_the_token_on_disk(served: SimpleNamespace, monkeypatch, ref) -> None:
    # A kill can land after any step, so look after every one of them.
    ref = served.sha if ref == "commit" else ref
    cache_dir = served.root / "cache" / "clone"
    real_git, real_git_ok = GitResolver._git, GitResolver._git_ok
    after_each_step: List[str] = []

    def watching_git(args, *, clone_url, url, what):
        real_git(args, clone_url=clone_url, url=url, what=what)
        after_each_step.append(_text_under(cache_dir.parent))  # the clone is built beside it

    def watching_git_ok(args, *, url=""):
        ok = real_git_ok(args, url=url)
        after_each_step.append(_text_under(cache_dir.parent))
        return ok

    monkeypatch.setattr(GitResolver, "_git", staticmethod(watching_git))
    monkeypatch.setattr(GitResolver, "_git_ok", staticmethod(watching_git_ok))

    GitResolver()._clone_into_cache(URL, ref, cache_dir, AUTH)

    assert len(after_each_step) >= 3  # init, fetch (twice for a tag), checkout
    assert all(TOKEN not in text for text in after_each_step)


def test_a_ref_that_is_not_there_fails_with_git_s_reason_and_leaves_nothing(
    served: SimpleNamespace,
) -> None:
    with pytest.raises(ResolutionError) as raised:
        GitResolver().resolve(_source("no-such-ref"))

    message = str(raised.value)
    assert "couldn't find remote ref refs/tags/no-such-ref" in message
    assert "as a branch, then as a tag" in message
    assert TOKEN not in message
    cache_dir = GitResolver._cache_dir_for(URL, "no-such-ref")
    assert list(cache_dir.parent.iterdir()) == []  # no cache directory, no staging directory


def test_a_ref_shaped_like_an_option_is_still_only_a_ref(served: SimpleNamespace) -> None:
    # Without `--`, git reads this as `--upload-pack=...` and runs the command locally.
    marker = served.root / "ran"
    ref = f"--upload-pack=touch {marker}"

    with pytest.raises(ResolutionError):
        GitResolver().resolve(_source(ref))

    assert not marker.exists()


def test_a_name_that_is_both_a_branch_and_a_tag_is_the_branch_with_or_without_a_token(
    served: SimpleNamespace, monkeypatch
) -> None:
    # `git clone --branch rel` takes the branch; the fetch must take it too, or the commit
    # in the lock would depend on whether a token was used.
    with_token = GitResolver().resolve(_source("rel"))
    monkeypatch.setenv("FLUID_CUSTOM_SCAFFOLD_NOCACHE", "1")  # the cache key ignores the token
    without_token = GitResolver().resolve(_source("rel", token=False))

    assert with_token.resolved_version == served.rel_branch
    assert without_token.resolved_version == served.rel_branch


@pytest.mark.parametrize("ref", ["HEAD", "refs/heads/main", "refs/tags/v1"])
def test_a_ref_that_clone_branch_refuses_is_refused_with_a_token_too(
    served: SimpleNamespace, ref: str
) -> None:
    with pytest.raises(ResolutionError):
        GitResolver().resolve(_source(ref, token=False))
    with pytest.raises(ResolutionError):
        GitResolver().resolve(_source(ref))


def _recorded_git(monkeypatch) -> List[List[str]]:
    calls: List[List[str]] = []

    def fake_git(args, *, clone_url, url, what):
        calls.append(list(args))
        if args[0] in ("clone", "init"):
            Path(args[-1]).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(GitResolver, "_git", staticmethod(fake_git))
    return calls


def _steps(calls: List[List[str]]) -> List[str]:
    return [call[0] if call[0] != "-C" else call[2] for call in calls]


def test_the_token_goes_to_fetch_after_a_branch_lookup_and_never_to_clone(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DPF_TEST_TOKEN", TOKEN)
    calls = _recorded_git(monkeypatch)
    as_branch: List[List[str]] = []
    monkeypatch.setattr(
        GitResolver,
        "_git_ok",
        staticmethod(lambda args, url="": as_branch.append(list(args)) or False),
    )

    GitResolver()._clone_into_cache(URL, "v1", tmp_path / "cache" / "clone", AUTH)

    assert _steps(calls) == ["init", "fetch", "checkout"]  # the tag fetch, after the branch
    token_url = as_branch[0][-2]
    assert token_url.startswith("https://x-access-token:") and TOKEN in token_url
    assert as_branch[0][-3:] == ["--", token_url, "refs/heads/v1"]
    assert calls[1][-3:] == [
        "--",
        token_url,
        "refs/tags/v1",
    ]  # everything after -- is not an option


def test_the_commit_pin_path_is_unchanged(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DPF_TEST_TOKEN", TOKEN)
    calls: List[Any] = _recorded_git(monkeypatch)
    monkeypatch.setattr(GitResolver, "_git_ok", staticmethod(lambda args, url="": True))

    GitResolver()._clone_into_cache(URL, "a" * 40, tmp_path / "cache" / "clone", AUTH)

    assert _steps(calls) == ["init", "checkout"]
