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

from data_product_forge_custom_scaffold import BundleError, generate
from data_product_forge_custom_scaffold.lockfile import LOCKFILE_NAME
from data_product_forge_custom_scaffold.resolvers import git as git_resolver

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary not on PATH")

MANIFEST = {
    "apiVersion": "fluid.dev/custom-scaffold.v1",
    "bundle": {"name": "git-bundle", "version": "1.0.0"},
    "patterns": [{"name": "basic", "templates": [{"from": "templates/t.j2", "to": "out.txt"}]}],
}


@pytest.fixture
def upstream(tmp_path: Path, monkeypatch) -> Dict[str, str]:
    """A one-commit bundle repository, tagged ``v1``."""
    monkeypatch.setenv("FLUID_CUSTOM_SCAFFOLD_CACHE", str(tmp_path / "cache"))
    # file:// is allowed here only so the resolver can be exercised offline; the
    # production scheme allowlist has its own coverage.
    monkeypatch.setattr(
        git_resolver, "_ALLOWED_SCHEMES", git_resolver._ALLOWED_SCHEMES + ("file://",)
    )
    repo = tmp_path / "upstream"
    (repo / "templates").mkdir(parents=True)
    (repo / "templates" / "t.j2").write_text("version one\n")
    (repo / "fluid-scaffold.yaml").write_text(yaml.safe_dump(MANIFEST))
    steps = [
        ["init", "-q"],
        ["config", "user.email", "t@example.com"],
        ["config", "user.name", "t"],
        ["add", "-A"],
        ["-c", "commit.gpgsign=false", "commit", "-q", "-m", "c1"],
        ["tag", "v1"],
        ["rev-parse", "HEAD"],
    ]
    out = [
        subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True)
        for a in steps
    ]
    return {"url": f"file://{repo}", "sha": out[-1].stdout.strip()}


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


def test_git_source_files_and_the_resolved_commit(tmp_path: Path, upstream) -> None:
    out = tmp_path / "out"
    result = generate(_contract(upstream["url"], "v1"), out)

    rows = {f.path: f for f in result.files}
    assert list(rows) == [LOCKFILE_NAME, "out.txt"]
    expected = b"version one\n"
    assert (rows["out.txt"].sha256, rows["out.txt"].size) == (
        hashlib.sha256(expected).hexdigest(),
        len(expected),
    )

    library = result.run.resolved_libraries["lib"]
    assert library.kind == "git" and library.resolved_version == upstream["sha"]
    lock = (out / LOCKFILE_NAME).read_bytes()
    assert f"commit: {upstream['sha']}".encode() in lock
    # The lock is part of the listing, and its row describes the file on disk.
    assert rows[LOCKFILE_NAME].sha256 == hashlib.sha256(lock).hexdigest()


def test_pin_resolves_the_locked_commit_even_when_the_ref_is_gone(tmp_path: Path, upstream) -> None:
    out = tmp_path / "out"
    first = generate(_contract(upstream["url"], "v1"), out)

    # The contract's ref now names something that does not exist upstream.
    moved = _contract(upstream["url"], "v2-renamed")
    with pytest.raises(BundleError, match="git clone"):
        generate(moved, out)  # following the ref fails ...

    pinned = generate(moved, out, pin=True)  # ... pinning uses the commit in the lock
    assert pinned.run.resolved_libraries["lib"].resolved_version == upstream["sha"]
    scaffold = [f for f in first.files if f.path != LOCKFILE_NAME]
    assert [f for f in pinned.files if f.path != LOCKFILE_NAME] == scaffold
