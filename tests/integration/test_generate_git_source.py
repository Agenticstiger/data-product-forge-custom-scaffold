"""``generate()`` with a ``git`` bundle source, through the real git resolver.

The bundle lives in a local ``file://`` repository (no network). Shows that the
library entry point keeps what the command always did with git sources: resolve to
a commit, record it in the lock, and re-run reproducibly with ``pin=True``.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path
from typing import Dict

import pytest
import yaml

from data_product_forge_custom_scaffold import generate
from data_product_forge_custom_scaffold.lockfile import LOCKFILE_NAME
from data_product_forge_custom_scaffold.resolvers import git as git_resolver

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary not on PATH")

MANIFEST = {
    "apiVersion": "fluid.dev/custom-scaffold.v1",
    "bundle": {"name": "git-bundle", "version": "1.0.0"},
    "patterns": [{"name": "basic", "templates": [{"from": "templates/t.j2", "to": "out.txt"}]}],
}


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), "-c", "commit.gpgsign=false", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


def _commit_text(repo: Path, text: str, message: str) -> str:
    (repo / "templates").mkdir(exist_ok=True)
    (repo / "templates" / "t.j2").write_text(text)
    (repo / "fluid-scaffold.yaml").write_text(yaml.safe_dump(MANIFEST))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def upstream(tmp_path: Path, monkeypatch) -> Dict[str, str]:
    """A bundle repository with two commits on ``main`` and a tag on the first."""
    monkeypatch.setenv("FLUID_CUSTOM_SCAFFOLD_CACHE", str(tmp_path / "cache"))
    # file:// is allowed here only so the resolver can be exercised offline; the
    # production scheme allowlist has its own coverage.
    monkeypatch.setattr(
        git_resolver, "_ALLOWED_SCHEMES", git_resolver._ALLOWED_SCHEMES + ("file://",)
    )
    repo = tmp_path / "upstream"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _git(repo, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    first = _commit_text(repo, "version one\n", "c1")
    _git(repo, "tag", "v1")
    return {"repo": str(repo), "url": f"file://{repo}", "first": first}


def _contract(url: str, ref: str) -> dict:
    return {
        "id": "git-product",
        "extensions": {
            "customScaffold": {
                "libraries": [{"id": "lib", "source": {"kind": "git", "url": url, "ref": ref}}],
                "patterns": [{"use": "lib:basic"}],
            }
        },
    }


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def test_git_source_files_and_the_resolved_commit(tmp_path: Path, upstream) -> None:
    out = tmp_path / "out"
    result = generate(_contract(upstream["url"], "v1"), out)

    rows = {f.path: f for f in result.files}
    assert list(rows) == [LOCKFILE_NAME, "out.txt"]
    assert (rows["out.txt"].sha256, rows["out.txt"].size) == (_sha("version one\n"), 12)

    library = result.run.resolved_libraries["lib"]
    assert library.kind == "git" and library.resolved_version == upstream["first"]
    assert f"commit: {upstream['first']}" in (out / LOCKFILE_NAME).read_text()
    # The lock is part of the listing, and its row describes the file on disk.
    assert (
        rows[LOCKFILE_NAME].sha256 == hashlib.sha256((out / LOCKFILE_NAME).read_bytes()).hexdigest()
    )


def test_pin_reproduces_the_locked_commit_after_the_branch_moves(
    tmp_path: Path, upstream, monkeypatch
) -> None:
    out = tmp_path / "out"
    contract = _contract(upstream["url"], "main")
    first = generate(contract, out)
    assert (out / "out.txt").read_text() == "version one\n"

    # The branch moves on. NOCACHE makes the resolver re-fetch instead of reusing
    # the clone it already holds for this ref.
    second_commit = _commit_text(Path(upstream["repo"]), "version two\n", "c2")
    monkeypatch.setenv("FLUID_CUSTOM_SCAFFOLD_NOCACHE", "1")

    pinned = generate(contract, out, pin=True)
    assert (out / "out.txt").read_text() == "version one\n"
    # Same scaffold bytes. (The lock is not compared: a pinned run records the pinned
    # commit as the ref it resolved, so that line differs from the first run's.)
    scaffold = [f for f in first.files if f.path != LOCKFILE_NAME]
    assert [f for f in pinned.files if f.path != LOCKFILE_NAME] == scaffold
    assert pinned.run.resolved_libraries["lib"].resolved_version == upstream["first"]
    assert f"commit: {upstream['first']}" in (out / LOCKFILE_NAME).read_text()

    followed = generate(contract, out)
    assert (out / "out.txt").read_text() == "version two\n"
    assert followed.run.resolved_libraries["lib"].resolved_version == second_commit
    assert {f.path: f.sha256 for f in followed.files}["out.txt"] == _sha("version two\n")
