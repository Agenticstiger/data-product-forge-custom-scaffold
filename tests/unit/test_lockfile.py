"""Tests for the fluid-scaffold.lock writer (reproducibility record)."""

from __future__ import annotations

from pathlib import Path

import yaml

from data_product_forge_custom_scaffold import Engine
from data_product_forge_custom_scaffold.lockfile import (
    LOCKFILE_NAME,
    build_lock,
    dump_lock,
    locked_library_from_bundle,
    read_lock,
    write_lock,
)
from data_product_forge_custom_scaffold.resolvers.base import ResolvedBundle

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _git_bundle() -> ResolvedBundle:
    return ResolvedBundle(
        kind="git",
        resolved_version="a" * 40,
        mirror_url="https://github.com/org/repo",
        extra={"ref": "main", "subdir": "bundles/x"},
    )


def test_locked_library_projects_credential_free_shape():
    lib = locked_library_from_bundle(_git_bundle())
    assert lib.kind == "git"
    assert lib.src == "https://github.com/org/repo"
    assert lib.ref == "main"
    assert lib.commit == "a" * 40
    assert lib.subdir == "bundles/x"


def test_build_lock_records_libraries_and_patterns():
    lock = build_lock(
        engine_version="0.1.2",
        resolved={"myorg": _git_bundle()},
        patterns=[
            {"use": "myorg:starter", "variables": {"region": "us-east-1"}},
            {"use": "myorg:ci"},  # no variables
            {"variables": {"x": 1}},  # no 'use' — skipped
        ],
    )
    assert lock["lockfileVersion"] == 1
    assert lock["engineVersion"] == "0.1.2"
    assert lock["libraries"]["myorg"]["commit"] == "a" * 40
    assert lock["libraries"]["myorg"]["ref"] == "main"
    uses = [p["use"] for p in lock["patterns"]]
    assert uses == ["myorg:starter", "myorg:ci"]  # the use-less entry is dropped
    assert lock["patterns"][0]["variables"] == {"region": "us-east-1"}
    assert "variables" not in lock["patterns"][1]


def test_lock_never_records_auth_or_tokens():
    # The lock is built from the resolved bundle, which never carries auth; assert
    # no secret-shaped key leaks even if a bundle's extra somehow carried one.
    bundle = ResolvedBundle(
        kind="git",
        resolved_version="b" * 40,
        mirror_url="https://github.com/org/repo",
        extra={
            "ref": "v1",
            "subdir": "",
            "auth": {"secret_ref": "GITHUB_TOKEN"},
            "token": "sekret",
        },
    )
    lock = build_lock(engine_version="0.1.2", resolved={"l": bundle}, patterns=[])
    text = dump_lock(lock)
    assert "secret_ref" not in text
    assert "sekret" not in text
    assert "token" not in text
    assert "GITHUB_TOKEN" not in text


def test_dump_lock_is_deterministic_and_headed():
    lock = build_lock(engine_version="0.1.2", resolved={"a": _git_bundle()}, patterns=[])
    out1 = dump_lock(lock)
    out2 = dump_lock(lock)
    assert out1 == out2
    assert out1.startswith("# fluid-scaffold.lock")


def test_write_then_read_round_trip(tmp_path: Path):
    lock = build_lock(engine_version="0.1.2", resolved={"a": _git_bundle()}, patterns=[])
    path = write_lock(tmp_path, lock)
    assert path.name == LOCKFILE_NAME
    loaded = read_lock(tmp_path)
    assert loaded["libraries"]["a"]["commit"] == "a" * 40
    # read_lock on an empty dir is {}.
    assert read_lock(tmp_path / "nope") == {}


# ── engine integration ──────────────────────────────────────────────

_CONTRACT = {
    "fluidVersion": "0.7.4",
    "kind": "DataProduct",
    "id": "p",
    "name": "P",
    "description": "lockfile integration",
    "metadata": {"owner": {"team": "t", "email": "t@t.t"}},
    "environments": {
        "dev": {"metadata": {"labels": {"cloud.region": "us-east-1"}}},
        "prod": {"metadata": {"labels": {"cloud.region": "us-east-1"}}},
    },
    "extensions": {
        "customScaffold": {
            "libraries": [
                {
                    "id": "ref",
                    "source": {"kind": "path", "path": str(FIXTURES / "reference_bundle")},
                }
            ],
            "patterns": [{"use": "ref:basic", "variables": {"teamName": "data-platform"}}],
        }
    },
}


def test_engine_writes_lock_after_run(tmp_path: Path):
    out = tmp_path / "out"
    result = Engine(output_root=out).run(_CONTRACT)
    assert result.lockfile is not None
    assert result.lockfile == out / LOCKFILE_NAME
    data = yaml.safe_load(result.lockfile.read_text(encoding="utf-8"))
    assert "ref" in data["libraries"]
    assert data["libraries"]["ref"]["kind"] == "path"
    assert data["patterns"][0]["use"] == "ref:basic"


def test_engine_dry_run_writes_no_lock(tmp_path: Path):
    out = tmp_path / "out"
    result = Engine(output_root=out).run(_CONTRACT, dry_run=True)
    assert result.lockfile is None
    assert not (out / LOCKFILE_NAME).exists()


def test_pin_warns_for_non_git_source(tmp_path: Path, caplog):
    # --pin on a path/entry-point source can't reproducibly lock it; the engine
    # must say so rather than imply a false 'local' pin.
    out = tmp_path / "out"
    Engine(output_root=out).run(_CONTRACT)  # generate (path source) → writes lock
    with caplog.at_level("WARNING"):
        Engine(output_root=out).run(_CONTRACT, pin=True)
    assert any(
        "cannot reproducibly lock" in r.getMessage() for r in caplog.records
    ), "pinning a non-git source must warn"
