"""Cross-package contract: `fluid custom-scaffold` must work end-to-end.

The engine is registered into the FLUID CLI via the ``fluid_build.commands``
entry point and invoked by the host's dispatcher as ``func(args, logger)``. The
scaffold's own test suite exercises the Python API (``Engine``), NOT this
cross-package seam — which is exactly why a one-arg command handler shipped to
PyPI and crashed ``fluid custom-scaffold`` on first invocation (v0.1.2).

These tests drive the REAL host dispatch via ``python -m fluid_build.cli`` in a
subprocess, so a host/plugin signature drift fails CI here instead of silently
on a user's machine. They skip unless ``data-product-forge`` (``fluid_build``)
is installed alongside this package (the ``cli-integration`` extra / the
dedicated CI job).
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_HAS_FLUID = importlib.util.find_spec("fluid_build") is not None

pytestmark = pytest.mark.skipif(
    not _HAS_FLUID,
    reason="data-product-forge (fluid_build) not installed; install '.[cli-integration]' to run",
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _fluid(*args: str) -> subprocess.CompletedProcess:
    """Invoke the real FLUID CLI dispatch (host → registered scaffold command)."""
    return subprocess.run(
        [sys.executable, "-m", "fluid_build.cli", *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_fluid_custom_scaffold_print_schema():
    # The minimal smoke: the host dispatcher must call the scaffold's registered
    # command without a signature error. A one-arg handler crashes here.
    r = _fluid("custom-scaffold", "--print-schema")
    assert r.returncode == 0, f"rc={r.returncode}\nstderr={r.stderr}\nstdout={r.stdout[:800]}"
    assert "patterns" in r.stdout, f"unexpected --print-schema output: {r.stdout[:400]}"


def test_fluid_custom_scaffold_dry_run(tmp_path):
    # The fuller path through the real dispatch: resolve (path source, no network)
    # + plan, end-to-end via `fluid`.
    bundle = FIXTURES / "reference_bundle"
    contract = tmp_path / "contract.fluid.yaml"
    contract.write_text(
        textwrap.dedent(f"""\
            fluidVersion: "0.7.4"
            kind: DataProduct
            id: xpkg
            name: Cross Package
            description: cross-package CLI integration smoke
            metadata: {{owner: {{team: t, email: t@t.t}}}}
            environments:
              dev: {{metadata: {{labels: {{cloud.region: us-east-1}}}}}}
            extensions:
              customScaffold:
                libraries:
                  - id: ref
                    source: {{kind: path, path: "{bundle}"}}
                patterns:
                  - use: ref:basic
                    variables: {{teamName: xpkg}}
            """),
        encoding="utf-8",
    )
    r = _fluid(
        "custom-scaffold",
        "--contract",
        str(contract),
        "--output",
        str(tmp_path / "out"),
        "--dry-run",
    )
    assert r.returncode == 0, f"rc={r.returncode}\nstderr={r.stderr}\nstdout={r.stdout[:800]}"


# ── the other two cross-package seams: extension_schemas + extension_validators ──
#
# The scaffold also registers fluid_build.extension_schemas (copilot grounding)
# and fluid_build.extension_validators (`fluid validate`). These drive the host's
# REAL loader/caller (iter_extension_schemas / run_extension_validators) against
# the scaffold's entry points, so a signature/return-shape drift on either seam
# fails CI here rather than silently degrading validation or copilot grounding.


def test_host_loads_scaffold_extension_schema():
    from fluid_build.extension_schemas import iter_extension_schemas

    schemas = iter_extension_schemas()
    assert "customScaffold" in schemas, f"host did not load the scaffold schema: {list(schemas)}"
    schema = schemas["customScaffold"]
    assert isinstance(schema, dict) and schema, "scaffold extension schema must be a non-empty dict"


def test_host_validator_flags_invalid_customscaffold_block():
    from fluid_build.extension_schemas import run_extension_validators

    # A pattern missing the required 'use' must be rejected by the scaffold validator.
    contract = {
        "extensions": {
            "customScaffold": {
                "libraries": [{"id": "x", "source": {"kind": "path", "path": "./b"}}],
                "patterns": [{}],
            }
        }
    }
    errors = run_extension_validators(contract)
    # Assert the REAL schema message ('use' is a required property), not just any
    # error: a signature break would surface as "validator … raised", which would
    # not mention the missing field — so this distinguishes "really validated"
    # from "the seam blew up".
    assert any("use" in e and "required" in e for e in errors), f"not real validation: {errors}"


def test_host_validator_passes_valid_customscaffold_block():
    from fluid_build.extension_schemas import run_extension_validators

    contract = {
        "extensions": {
            "customScaffold": {
                "libraries": [
                    {
                        "id": "x",
                        "source": {"kind": "git", "url": "https://github.com/o/r", "ref": "v1"},
                    }
                ],
                "patterns": [{"use": "x:p"}],
            }
        }
    }
    cs_errors = [e for e in run_extension_validators(contract) if "customScaffold" in e]
    assert not cs_errors, f"valid block produced errors: {cs_errors}"


# ── --pin / --update must reach the engine through the REAL CLI dispatch ──
#
# Both flags were guarded only by direct Engine.run(pin=)/Engine.update() unit
# calls; a host arg-wiring drift (flag not plumbed through the dispatcher) would
# escape. These drive them via `python -m fluid_build.cli`.


def _write_path_contract(tmp_path) -> Path:
    bundle = FIXTURES / "reference_bundle"
    contract = tmp_path / "contract.fluid.yaml"
    contract.write_text(
        textwrap.dedent(f"""\
            fluidVersion: "0.7.4"
            kind: DataProduct
            id: smoke
            name: Smoke
            description: pin/update CLI dispatch smoke
            metadata: {{owner: {{team: t, email: t@t.t}}}}
            environments:
              dev: {{metadata: {{labels: {{cloud.region: us-east-1}}}}}}
            extensions:
              customScaffold:
                libraries:
                  - id: ref
                    source: {{kind: path, path: "{bundle}"}}
                patterns:
                  - use: ref:basic
                    variables: {{teamName: smoke}}
            """),
        encoding="utf-8",
    )
    return contract


def test_fluid_custom_scaffold_pin_flag_via_dispatch(tmp_path):
    c = _write_path_contract(tmp_path)
    out = tmp_path / "out"
    assert _fluid("custom-scaffold", "--contract", str(c), "--output", str(out)).returncode == 0
    r = _fluid("custom-scaffold", "--contract", str(c), "--output", str(out), "--pin")
    assert r.returncode == 0, f"--pin rc={r.returncode}\nstderr={r.stderr}"


def test_fluid_custom_scaffold_update_flag_via_dispatch(tmp_path):
    c = _write_path_contract(tmp_path)
    out = tmp_path / "out"
    assert _fluid("custom-scaffold", "--contract", str(c), "--output", str(out)).returncode == 0
    # No edits + unchanged template → 'unchanged', rc 0 (not a conflict).
    r = _fluid("custom-scaffold", "--contract", str(c), "--output", str(out), "--update")
    assert r.returncode == 0, f"--update rc={r.returncode}\nstderr={r.stderr}\nstdout={r.stdout}"
    assert "Updated" in r.stdout
