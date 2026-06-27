"""Pin-on-rerun: a locked git library re-resolves to its recorded commit.

The reproducibility payoff of the lockfile — proven end-to-end against a real
`file://` repo whose branch moves between runs.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from data_product_forge_custom_scaffold import Engine
from data_product_forge_custom_scaffold.lockfile import pin_source, read_lock
from data_product_forge_custom_scaffold.resolvers import git as git_resolver

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary not on PATH")

MANIFEST = "fluid-scaffold.yaml"
BUNDLE_MANIFEST = """\
apiVersion: fluid.dev/custom-scaffold.v1
bundle: {name: pintest, version: 1.0.0}
patterns:
  - name: p
    templates:
      - from: templates/out.txt.j2
        to: out.txt
"""


def test_pin_source_overrides_git_ref_with_commit():
    src = {"kind": "git", "url": "https://x/r", "ref": "main"}
    pinned = pin_source(src, {"commit": "c" * 40})
    assert pinned["ref"] == "c" * 40
    assert pinned["url"] == "https://x/r"  # untouched
    assert src["ref"] == "main"  # original not mutated


def test_pin_source_leaves_non_git_and_missing_commit_untouched():
    path_src = {"kind": "path", "path": "./b"}
    assert pin_source(path_src, {"commit": "c" * 40}) == path_src
    git_no_commit = {"kind": "git", "url": "https://x/r", "ref": "main"}
    assert pin_source(git_no_commit, {}) == git_no_commit


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit_bundle(repo: Path, body: str, msg: str) -> str:
    (repo / "templates").mkdir(exist_ok=True)
    (repo / MANIFEST).write_text(BUNDLE_MANIFEST, encoding="utf-8")
    (repo / "templates" / "out.txt.j2").write_text(body, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def _git_env(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUID_CUSTOM_SCAFFOLD_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("FLUID_CUSTOM_SCAFFOLD_NOCACHE", "1")  # always re-clone (see the moved ref)
    monkeypatch.setattr(
        git_resolver, "_ALLOWED_SCHEMES", git_resolver._ALLOWED_SCHEMES + ("file://",)
    )
    return tmp_path


def _contract(repo: Path) -> dict:
    return {
        "fluidVersion": "0.7.4",
        "kind": "DataProduct",
        "id": "p",
        "name": "P",
        "description": "pin e2e",
        "metadata": {"owner": {"team": "t", "email": "t@t.t"}},
        "extensions": {
            "customScaffold": {
                "libraries": [
                    {"id": "lib", "source": {"kind": "git", "url": f"file://{repo}", "ref": "main"}}
                ],
                "patterns": [{"use": "lib:p"}],
            }
        },
    }


def test_pin_resolves_locked_commit_after_ref_moves(_git_env):
    repo = _git_env / "upstream"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    sha1 = _commit_bundle(repo, "gen v1\n", "c1")

    out = _git_env / "out"
    contract = _contract(repo)

    # Run 1 — follow main → resolves c1, lock records c1.
    r1 = Engine(output_root=out).run(contract)
    assert r1.resolved_libraries["lib"].resolved_version == sha1
    assert read_lock(out)["libraries"]["lib"]["commit"] == sha1

    # Move main forward.
    sha2 = _commit_bundle(repo, "gen v2\n", "c2")
    assert sha2 != sha1

    # Run 2 — pinned → still resolves c1 (the locked commit), lock unchanged.
    r2 = Engine(output_root=out).run(contract, pin=True)
    assert r2.resolved_libraries["lib"].resolved_version == sha1
    assert read_lock(out)["libraries"]["lib"]["commit"] == sha1

    # Run 3 — not pinned → follows main to c2, lock moves to c2.
    r3 = Engine(output_root=out).run(contract)
    assert r3.resolved_libraries["lib"].resolved_version == sha2
    assert read_lock(out)["libraries"]["lib"]["commit"] == sha2
