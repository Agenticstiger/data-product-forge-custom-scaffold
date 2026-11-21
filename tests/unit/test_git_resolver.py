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
from data_product_forge_custom_scaffold.resolvers.git import (
    GitResolver,
    _looks_like_full_sha,
    _safe_ref,
)

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


def test_a_failed_run_does_not_poison_the_cache_for_the_next_one(_cache):
    # `git init` has already made the cache directory when the fetch fails. The next run must
    # not find that empty repository and take it for a finished clone.
    info = _make_upstream(_cache)
    (_cache / "upstream").rename(_cache / "elsewhere")  # the URL points at nothing (yet)
    source = {"kind": "git", "url": info["url"], "ref": info["sha_c2"]}
    with pytest.raises(ResolutionError):
        GitResolver().resolve(source)
    assert not GitResolver._cache_dir_for(info["url"], info["sha_c2"]).exists()

    (_cache / "elsewhere").rename(_cache / "upstream")  # it is there now
    assert (GitResolver().resolve(source).bundle_root / MANIFEST).is_file()


@pytest.mark.parametrize(
    ("ref", "directory"),
    [
        (".", "_"),
        ("..", "__"),
        ("...", "___"),
        ("v1.2.0", "v1.2.0"),
        ("feature/x", "feature_x"),
        ("..a", "..a"),
        ("a..", "a.."),
        # The sweep deletes what starts like a killed run's leftover: no ref may name one.
        (".partial-x", "_.partial-x"),
        (".old-x", "_.old-x"),
        ("a.old-x", "a.old-x"),
    ],
)
def test_a_ref_never_names_a_directory_outside_its_own_url(ref, directory):
    # `.` and `..` would name the url's directory or its parent, the whole git cache.
    assert _safe_ref(ref) == directory
    assert GitResolver._cache_dir_for("https://example.invalid/x", ref).parent == (
        GitResolver._cache_dir_for("https://example.invalid/x", "v1").parent
    )


def test_a_ref_made_of_dots_leaves_other_cached_sources_alone(_cache):
    info = _make_upstream(_cache)
    other = _cache / "cache" / "git" / "another-url-hash" / "main"
    other.mkdir(parents=True)
    (other / "keep").write_text("another source's clone")

    for ref in (".", "..", "..."):
        with pytest.raises(ResolutionError):
            GitResolver().resolve({"kind": "git", "url": info["url"], "ref": ref})

    assert (other / "keep").is_file()


def test_a_refresh_that_fails_keeps_the_clone_it_was_to_replace(_cache, monkeypatch):
    info = _make_upstream(_cache)
    source = {"kind": "git", "url": info["url"], "ref": info["tag"]}
    first = GitResolver().resolve(source)

    monkeypatch.setenv("FLUID_CUSTOM_SCAFFOLD_NOCACHE", "1")
    (_cache / "upstream").rename(_cache / "elsewhere")  # the refresh cannot reach it
    with pytest.raises(ResolutionError):
        GitResolver().resolve(source)

    assert (first.bundle_root / MANIFEST).is_file()  # the old clone is still there
    assert [p.name for p in first.bundle_root.parent.iterdir()] == [first.bundle_root.name]


def test_a_refresh_replaces_the_cached_clone_with_a_new_one(_cache, monkeypatch):
    info = _make_upstream(_cache)
    source = {"kind": "git", "url": info["url"], "ref": info["tag"]}
    first = GitResolver().resolve(source)
    (first.bundle_root / "added-by-hand").write_text("not in the repository")

    monkeypatch.setenv("FLUID_CUSTOM_SCAFFOLD_NOCACHE", "1")
    second = GitResolver().resolve(source)

    assert second.bundle_root == first.bundle_root
    assert not (second.bundle_root / "added-by-hand").exists()  # a new clone took its place
    assert (second.bundle_root / MANIFEST).is_file()
    assert [p.name for p in second.bundle_root.parent.iterdir()] == [second.bundle_root.name]
