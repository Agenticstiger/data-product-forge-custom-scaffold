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
