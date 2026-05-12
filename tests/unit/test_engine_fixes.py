"""Regression tests for engine fixes from the E2E review:

* contract_dir threading to PathResolver
* graceful library_filter (skip stale patterns instead of crashing)
* bundle identity surfaced in render context
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from data_product_forge_custom_scaffold import Engine

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _consumer_contract_with_relative_path():
    """A contract that references the reference bundle by a RELATIVE path,
    so the test depends on contract_dir-aware resolution."""
    return {
        "fluidVersion": "0.7.3",
        "kind": "DataProduct",
        "id": "relpath-product",
        "name": "Relative Path Product",
        "metadata": {"owner": {"team": "x", "email": "x@example.com"}},
        "environments": {"dev": {"metadata": {"labels": {"cloud.accountId": "111"}}}},
        "extensions": {
            "customScaffold": {
                "libraries": [
                    # NB: relative path — anchored to contract_dir, NOT cwd.
                    {"id": "ref", "source": {"kind": "path", "path": "../reference_bundle"}}
                ],
                "patterns": [{"use": "ref:basic", "variables": {"teamName": "platform"}}],
            }
        },
    }


def test_relative_path_anchors_to_contract_dir(tmp_path: Path) -> None:
    """A relative `path:` source resolves against the contract's directory,
    not the cwd where the user happens to run the engine."""
    # Set up a layout where contract_dir is a sibling of the reference bundle.
    # contract_dir = tmp_path / "consumer"
    # bundle_root   = tmp_path / "reference_bundle"
    consumer_dir = tmp_path / "consumer"
    consumer_dir.mkdir()
    # Symlink (or copy) the fixture bundle next to the consumer dir.
    target_bundle = tmp_path / "reference_bundle"
    if not target_bundle.exists():
        # Copy the fixture so the test is self-contained.
        import shutil

        shutil.copytree(FIXTURES / "reference_bundle", target_bundle)

    out = tmp_path / "out"
    out.mkdir()

    # Invoke engine from a DIFFERENT cwd (not consumer_dir) to prove
    # cwd is not what gets used.
    original_cwd = os.getcwd()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    try:
        os.chdir(elsewhere)
        engine = Engine(output_root=out, contract_dir=consumer_dir)
        result = engine.run(_consumer_contract_with_relative_path())
    finally:
        os.chdir(original_cwd)

    # If contract_dir threading works, the bundle resolved correctly and files were generated.
    assert result.apply_result is not None
    assert result.apply_result.applied > 0
    assert result.apply_result.failed == 0


def test_library_filter_skips_patterns_referencing_excluded_lib(tmp_path: Path) -> None:
    """When ``--lib`` filters out a library, patterns that reference the
    excluded library are skipped with a warning, not a hard failure."""
    bundle = tmp_path / "reference_bundle"
    if not bundle.exists():
        import shutil

        shutil.copytree(FIXTURES / "reference_bundle", bundle)
    consumer_dir = tmp_path / "consumer"
    consumer_dir.mkdir()
    out = tmp_path / "out"
    out.mkdir()

    contract = {
        "fluidVersion": "0.7.3",
        "kind": "DataProduct",
        "id": "filter-product",
        "name": "Filter Test",
        "metadata": {"owner": {"team": "x", "email": "x@example.com"}},
        "environments": {"dev": {"metadata": {"labels": {"cloud.accountId": "111"}}}},
        "extensions": {
            "customScaffold": {
                "libraries": [
                    {"id": "kept", "source": {"kind": "path", "path": str(bundle)}},
                    {"id": "filtered", "source": {"kind": "path", "path": str(bundle)}},
                ],
                "patterns": [
                    {"use": "kept:basic", "variables": {"teamName": "platform"}},
                    {"use": "filtered:basic", "variables": {"teamName": "platform"}},  # skipped
                ],
            }
        },
    }

    engine = Engine(output_root=out, contract_dir=consumer_dir)
    # Should not raise — the filtered library's pattern is silently skipped.
    result = engine.run(contract, library_filter=["kept"])

    # Only the kept lib was used; the filtered pattern was skipped.
    assert "kept" in result.resolved_libraries
    assert "filtered" not in result.resolved_libraries


def test_internally_inconsistent_contract_still_raises(tmp_path: Path) -> None:
    """When NO filter is active and a pattern references a non-existent
    library, the engine raises (this is a real contract bug)."""
    from data_product_forge_custom_scaffold.engine import EngineError

    bundle = tmp_path / "reference_bundle"
    if not bundle.exists():
        import shutil

        shutil.copytree(FIXTURES / "reference_bundle", bundle)
    out = tmp_path / "out"
    out.mkdir()

    contract = {
        "fluidVersion": "0.7.3",
        "kind": "DataProduct",
        "id": "broken-product",
        "name": "Broken",
        "metadata": {"owner": {"team": "x", "email": "x@example.com"}},
        "environments": {"dev": {"metadata": {"labels": {"cloud.accountId": "111"}}}},
        "extensions": {
            "customScaffold": {
                "libraries": [{"id": "a", "source": {"kind": "path", "path": str(bundle)}}],
                "patterns": [
                    {"use": "nonexistent:basic"},  # ← lib doesn't exist
                ],
            }
        },
    }
    # No library_filter: this is a real bug, not a filter artifact.
    with pytest.raises(EngineError, match="not declared"):
        Engine(output_root=out, contract_dir=tmp_path).run(contract)


def test_bundle_identity_in_render_context(tmp_path: Path) -> None:
    """The render context exposes the bundle's identity (name, version,
    description, author, pattern_name) under ``bundle``."""
    # Build a tiny bundle that uses {{ bundle.name }}, {{ bundle.version }},
    # and {{ bundle.pattern_name }} in its template.
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "templates").mkdir()
    (bundle / "templates" / "header.txt.j2").write_text(
        "{{ bundle.name }} v{{ bundle.version }} pattern={{ bundle.pattern_name }}\n"
    )
    (bundle / "fluid-scaffold.yaml").write_text(
        "apiVersion: fluid.dev/custom-scaffold.v1\n"
        "bundle:\n"
        "  name: identity-test-bundle\n"
        "  version: 2.3.4\n"
        "  description: Demo of bundle identity in render context.\n"
        "  author: Identity Test Author\n"
        "patterns:\n"
        "  - name: only-pattern\n"
        "    templates:\n"
        "      - {from: templates/header.txt.j2, to: header.txt}\n"
    )

    consumer = tmp_path / "consumer"
    consumer.mkdir()
    out = tmp_path / "out"
    out.mkdir()

    contract = {
        "id": "x",
        "extensions": {
            "customScaffold": {
                "libraries": [{"id": "b", "source": {"kind": "path", "path": str(bundle)}}],
                "patterns": [{"use": "b:only-pattern"}],
            }
        },
    }

    Engine(output_root=out, contract_dir=consumer).run(contract)
    content = (out / "header.txt").read_text()
    assert content == "identity-test-bundle v2.3.4 pattern=only-pattern\n"
