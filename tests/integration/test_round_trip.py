"""End-to-end round-trip: contract → engine → files on disk → determinism."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from data_product_forge_custom_scaffold import Engine

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


CONTRACT = {
    "fluidVersion": "0.7.4",
    "kind": "DataProduct",
    "id": "my-test-product",
    "name": "My Test Product",
    "description": "End-to-end round-trip fixture.",
    "domain": "platform",
    "metadata": {
        "owner": {"team": "platform", "email": "platform@example.com"},
        "labels": {
            "repository.name": "my-test-product",
            "repository.branch": "main",
        },
    },
    "environments": {
        "dev": {"metadata": {"labels": {"cloud.accountId": "111", "cloud.region": "us-east-1"}}},
        "prod": {"metadata": {"labels": {"cloud.accountId": "333", "cloud.region": "us-east-1"}}},
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


def _hash_tree(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def test_full_round_trip(tmp_path: Path) -> None:
    out_a = tmp_path / "run_a"
    out_a.mkdir()
    engine = Engine(output_root=out_a)
    result = engine.run(CONTRACT)

    # 2 rendered templates + 1 static file = 3 files
    expected_paths = {
        "README.md",
        ".gitlab-ci.yml",
        "docs/runbook.md",
    }
    actual_paths = {p.relative_to(out_a).as_posix() for p in out_a.rglob("*") if p.is_file()}
    assert actual_paths == expected_paths, f"unexpected file set: {actual_paths}"

    assert result.apply_result is not None
    assert result.apply_result.failed == 0
    assert result.apply_result.applied == len(expected_paths)

    # README content uses the variable override
    readme = (out_a / "README.md").read_text(encoding="utf-8")
    assert "My Test Product" in readme
    assert "platform@example.com" in readme
    assert "data-platform" in readme

    # CI definition: one deploy job per env, prod is `when: manual`
    ci = (out_a / ".gitlab-ci.yml").read_text(encoding="utf-8")
    assert "deploy:dev:" in ci
    assert "deploy:prod:" in ci
    prod_block = ci[ci.index("deploy:prod:") :]
    assert "when: manual" in prod_block

    # Static file copied verbatim
    assert (out_a / "docs/runbook.md").read_text(encoding="utf-8").startswith("# Runbook")


def test_determinism_byte_identical(tmp_path: Path) -> None:
    """Same contract twice in separate tempdirs → byte-identical output."""
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    out_a.mkdir()
    out_b.mkdir()

    Engine(output_root=out_a).run(CONTRACT)
    Engine(output_root=out_b).run(CONTRACT)

    assert _hash_tree(out_a) == _hash_tree(out_b)


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    out = tmp_path / "dry"
    out.mkdir()
    result = Engine(output_root=out).run(CONTRACT, dry_run=True)

    assert result.apply_result is None
    assert len(result.actions) == 3
    assert [p for p in out.rglob("*") if p.is_file()] == []


def test_missing_required_contract_field_fails_fast(tmp_path: Path) -> None:
    from data_product_forge_custom_scaffold.engine import EngineError

    bad = dict(CONTRACT)
    bad["metadata"] = {"owner": {"team": "x"}}  # email removed
    out = tmp_path / "bad"
    out.mkdir()

    with pytest.raises(EngineError, match="metadata.owner.email"):
        Engine(output_root=out).run(bad)

    assert list(out.rglob("*")) == []


def test_pattern_filter(tmp_path: Path) -> None:
    """--pattern restricts to a subset."""
    out = tmp_path / "filtered"
    out.mkdir()
    result = Engine(output_root=out).run(CONTRACT, pattern_filter=["ref:basic"])
    assert len(result.actions) == 3

    out2 = tmp_path / "filtered2"
    out2.mkdir()
    result2 = Engine(output_root=out2).run(CONTRACT, pattern_filter=["nothing:matches"])
    assert result2.actions == []
