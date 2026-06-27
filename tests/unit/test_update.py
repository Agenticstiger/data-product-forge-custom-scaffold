"""Tests for `fluid custom-scaffold --update` (copier-style 3-way merge)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from data_product_forge_custom_scaffold import Engine
from data_product_forge_custom_scaffold.lockfile import read_lock
from data_product_forge_custom_scaffold.resolvers import git as git_resolver
from data_product_forge_custom_scaffold.update import (
    UpdateError,
    actions_to_render,
    merge_renders,
    three_way_merge,
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary not on PATH")


# ── merge core ──────────────────────────────────────────────────────


def test_three_way_merge_clean_non_overlapping():
    base = b"a\nb\nc\nd\ne\n"
    ours = b"a-mine\nb\nc\nd\ne\n"  # user changed line 1
    theirs = b"a\nb\nc\nd\ne-new\n"  # template changed line 5
    merged, clean = three_way_merge(base, ours, theirs)
    assert clean
    assert merged == b"a-mine\nb\nc\nd\ne-new\n"


def test_three_way_merge_conflict_same_line():
    base = b"title: original\n"
    ours = b"title: mine\n"
    theirs = b"title: theirs\n"
    merged, clean = three_way_merge(base, ours, theirs)
    assert not clean
    assert b"<<<<<<<" in merged and b">>>>>>>" in merged
    assert b"mine" in merged and b"theirs" in merged


def test_three_way_merge_shortcuts():
    assert three_way_merge(b"x", b"x", b"x") == (b"x", True)
    # template unchanged → keep user's
    assert three_way_merge(b"base", b"mine", b"base") == (b"mine", True)
    # user unchanged → take template's
    assert three_way_merge(b"base", b"base", b"new") == (b"new", True)


def test_actions_to_render_handles_bytes_and_str():
    rendered = actions_to_render(
        [
            {"path": "a.txt", "content": b"bytes-content"},
            {"path": "b.txt", "content": "str-content"},
            {"content": "no-path-skipped"},
        ]
    )
    assert rendered == {"a.txt": b"bytes-content", "b.txt": b"str-content"}


def test_merge_renders_statuses(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "keep.txt").write_bytes(b"v1\n")  # user has it, unchanged in template
    # user changed the HEADER; template changes the FOOTER — separated by `body`,
    # so the 3-way merge is clean.
    (out / "edited.txt").write_bytes(b"USER-HEADER\nbody\nfooter\n")
    old = {
        "keep.txt": b"v1\n",
        "edited.txt": b"header\nbody\nfooter\n",
        "gone.txt": b"removed upstream\n",
    }
    new = {
        "keep.txt": b"v1\n",
        "edited.txt": b"header\nbody\nNEW-FOOTER\n",
        "fresh.txt": b"brand new\n",
    }
    result = merge_renders(old=old, new=new, output_root=out)
    by = {f.path: f.status for f in result.files}
    assert by["keep.txt"] == "unchanged"
    assert by["edited.txt"] == "merged"  # non-overlapping → clean
    assert by["fresh.txt"] == "added"
    assert by["gone.txt"] == "removed-upstream"
    # user edit preserved + template change applied
    assert (out / "edited.txt").read_bytes() == b"USER-HEADER\nbody\nNEW-FOOTER\n"
    assert (out / "fresh.txt").read_bytes() == b"brand new\n"


# ── engine.update end-to-end against a real evolving git template ────

_BUNDLE_V1 = """\
apiVersion: fluid.dev/custom-scaffold.v1
bundle: {name: u, version: 1.0.0}
patterns:
  - name: p
    templates:
      - from: templates/README.md.j2
        to: README.md
"""
# version bump is on an EARLY line; status/notes are unchanged trailing context, so
# a user edit appended at the end merges cleanly (no overlap with the bump).
_README_V1 = "# {{ product_id }}\n\nowner: {{ team }}\nversion: 1\nstatus: active\nnotes: none\n"
_README_V2 = "# {{ product_id }}\n\nowner: {{ team }}\nversion: 2\nstatus: active\nnotes: none\n"


def _git(repo: Path, *a: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *a], check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo: Path, readme: str, msg: str) -> str:
    (repo / "templates").mkdir(exist_ok=True)
    (repo / "fluid-scaffold.yaml").write_text(_BUNDLE_V1, encoding="utf-8")
    (repo / "templates" / "README.md.j2").write_text(readme, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def _git_env(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUID_CUSTOM_SCAFFOLD_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("FLUID_CUSTOM_SCAFFOLD_NOCACHE", "1")
    monkeypatch.setattr(
        git_resolver, "_ALLOWED_SCHEMES", git_resolver._ALLOWED_SCHEMES + ("file://",)
    )
    return tmp_path


def _contract(repo: Path) -> dict:
    return {
        "fluidVersion": "0.7.4",
        "kind": "DataProduct",
        "id": "prod",
        "name": "Prod",
        "description": "update e2e",
        "metadata": {"owner": {"team": "t", "email": "t@t.t"}},
        "extensions": {
            "customScaffold": {
                "libraries": [
                    {"id": "lib", "source": {"kind": "git", "url": f"file://{repo}", "ref": "main"}}
                ],
                "patterns": [{"use": "lib:p", "variables": {"team": "data-platform"}}],
            }
        },
    }


def test_update_merges_template_evolution_with_user_edits(_git_env):
    repo = _git_env / "tpl"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    sha1 = _commit(repo, _README_V1, "v1")

    out = _git_env / "out"
    contract = _contract(repo)

    # Generate from v1 → lock records sha1.
    Engine(output_root=out).run(contract)
    readme = out / "README.md"
    assert "version: 1" in readme.read_text()
    assert read_lock(out)["libraries"]["lib"]["commit"] == sha1

    # The user edits the generated README (a line the template does NOT touch).
    text = readme.read_text() + "\n## My custom section\n"
    readme.write_text(text, encoding="utf-8")

    # Template evolves to v2 (bumps version + adds a line, away from the user's edit).
    sha2 = _commit(repo, _README_V2, "v2")
    assert sha2 != sha1

    # Update → 3-way merge: template's v2 changes applied, user's section preserved.
    result = Engine(output_root=out).update(contract)
    merged = readme.read_text()
    assert "version: 2" in merged  # template change applied
    assert "version: 1" not in merged  # old version gone
    assert "## My custom section" in merged  # user edit preserved
    assert not result.conflicts
    # lock advanced to sha2
    assert read_lock(out)["libraries"]["lib"]["commit"] == sha2


def test_update_marks_conflicts_when_edits_overlap(_git_env):
    repo = _git_env / "tpl"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    _commit(repo, _README_V1, "v1")

    out = _git_env / "out"
    contract = _contract(repo)
    Engine(output_root=out).run(contract)

    # User edits the SAME line the template will change (the version line).
    readme = out / "README.md"
    readme.write_text(readme.read_text().replace("version: 1", "version: MINE"), encoding="utf-8")
    _commit(repo, _README_V2, "v2")  # template changes version: 1 -> 2 on that line

    result = Engine(output_root=out).update(contract)
    assert result.conflicts, "overlapping edits must conflict"
    merged = readme.read_text()
    assert "<<<<<<<" in merged and ">>>>>>>" in merged
    assert "MINE" in merged and "version: 2" in merged


def test_update_without_lock_raises(_git_env):
    repo = _git_env / "tpl"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    _commit(repo, _README_V1, "v1")
    with pytest.raises(UpdateError):
        Engine(output_root=_git_env / "empty").update(_contract(repo))
