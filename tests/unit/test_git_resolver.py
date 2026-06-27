"""Unit + end-to-end tests for the git resolver — in particular SHA pinning.

The headline case: a contract pinned to a full commit SHA (the only truly
reproducible git ref) must resolve. The previous ``git clone --branch <sha>``
implementation hard-failed on commit ids; these tests pin the fetch+checkout
path against a real ``file://`` repository (no network required).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from data_product_forge_custom_scaffold.resolvers import git as git_resolver
from data_product_forge_custom_scaffold.resolvers.base import ResolutionError
from data_product_forge_custom_scaffold.resolvers.git import GitResolver, _looks_like_full_sha

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary not on PATH")

MANIFEST = "fluid-scaffold.yaml"


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


def _make_upstream(root: Path) -> dict:
    """Build a real 2-commit repo with a tag; return its ref coordinates."""
    repo = root / "upstream"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    (repo / MANIFEST).write_text("apiVersion: forge.scaffold/v1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "c1")
    sha_c1 = _git(repo, "rev-parse", "HEAD")
    _git(repo, "tag", "v1.0.0")
    (repo / MANIFEST).write_text("apiVersion: forge.scaffold/v1\nextra: true\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "c2")
    sha_c2 = _git(repo, "rev-parse", "HEAD")
    return {"url": f"file://{repo}", "sha_c1": sha_c1, "sha_c2": sha_c2, "tag": "v1.0.0"}


@pytest.fixture
def _cache(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUID_CUSTOM_SCAFFOLD_CACHE", str(tmp_path / "cache"))
    # Permit file:// for hermetic, network-free tests. The production scheme
    # allowlist (https/ssh only) is a separate control with its own coverage;
    # here we exercise the clone/fetch/checkout logic through the real resolve().
    monkeypatch.setattr(
        git_resolver, "_ALLOWED_SCHEMES", git_resolver._ALLOWED_SCHEMES + ("file://",)
    )
    return tmp_path


def test_looks_like_full_sha():
    assert _looks_like_full_sha("a" * 40)  # SHA-1
    assert _looks_like_full_sha("0" * 64)  # SHA-256
    assert not _looks_like_full_sha("main")
    assert not _looks_like_full_sha("v1.0.0")
    assert not _looks_like_full_sha("a" * 39)  # too short
    assert not _looks_like_full_sha("g" * 40)  # non-hex
    assert not _looks_like_full_sha("A" * 40)  # must be lowercase hex


def test_resolve_pins_tip_sha(_cache):
    info = _make_upstream(_cache)
    bundle = GitResolver().resolve({"kind": "git", "url": info["url"], "ref": info["sha_c2"]})
    assert (bundle.bundle_root / MANIFEST).is_file()
    assert bundle.resolved_version == info["sha_c2"]


def test_resolve_pins_non_tip_sha(_cache):
    # The exact case the old --branch clone could never reach: a commit that is
    # not a branch tip. Must resolve to that commit's content.
    info = _make_upstream(_cache)
    bundle = GitResolver().resolve({"kind": "git", "url": info["url"], "ref": info["sha_c1"]})
    assert bundle.resolved_version == info["sha_c1"]
    # c1's manifest is the single-line version (c2 added a second line).
    assert (bundle.bundle_root / MANIFEST).read_text(encoding="utf-8").count("\n") == 1


def test_resolve_tag_still_works(_cache):
    # Regression guard: tags/branches keep the shallow --branch clone path.
    info = _make_upstream(_cache)
    bundle = GitResolver().resolve({"kind": "git", "url": info["url"], "ref": info["tag"]})
    assert (bundle.bundle_root / MANIFEST).is_file()


def test_sha_path_does_not_persist_remote_url(_cache):
    # The token-bearing URL is passed inline to `git fetch`, never `git remote
    # add`, so a sha-pinned cache must carry no remote URL in .git/config.
    info = _make_upstream(_cache)
    GitResolver().resolve({"kind": "git", "url": info["url"], "ref": info["sha_c2"]})
    cache_dirs = list((_cache / "cache" / "git").rglob(".git/config"))
    assert cache_dirs, "expected a cloned cache dir"
    for cfg in cache_dirs:
        assert "url = " not in cfg.read_text(encoding="utf-8")


def test_unknown_sha_raises_cleanly(_cache):
    info = _make_upstream(_cache)
    missing = "0" * 40
    with pytest.raises(ResolutionError):
        GitResolver().resolve({"kind": "git", "url": info["url"], "ref": missing})
