"""``fluid generate custom-scaffold`` subcommand.

Registered as a CLI plugin via:

    [project.entry-points."fluid_build.commands"]
    generate-custom-scaffold = "data_product_forge_custom_scaffold.cli:register"

The FLUID CLI calls :func:`register` with its argparse subparser group
and expects a ``func`` attribute set on the parser. We wire that to
:func:`run` which delegates to the :class:`Engine`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

import yaml

from .engine import Engine, EngineError


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``custom-scaffold`` subcommand under ``fluid generate``."""
    parser = subparsers.add_parser(
        "custom-scaffold",
        help="Generate a custom project scaffold from your fluid contract.",
        description=(
            "Reads contract.extensions.customScaffold, resolves each declared "
            "bundle (from path or git), and renders the contract through each "
            "pattern into a deterministic set of files."
        ),
    )
    parser.add_argument(
        "--contract",
        "-c",
        default="contract.fluid.yaml",
        help="Path to the fluid contract (default: ./contract.fluid.yaml)",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=".",
        help="Output root directory (default: cwd)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan only — print the file list, write nothing.",
    )
    parser.add_argument(
        "--pattern",
        action="append",
        default=None,
        help="Restrict to specific patterns (repeatable). Match by 'use:' value.",
    )
    parser.add_argument(
        "--lib",
        action="append",
        default=None,
        help="Restrict to specific library ids (repeatable).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON result instead of a human summary.",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Implementation of ``fluid generate custom-scaffold``."""
    contract_path = Path(args.contract).resolve()
    if not contract_path.is_file():
        print(f"error: contract not found at {contract_path}", file=sys.stderr)
        return 1

    contract = _load_contract(contract_path)
    output_root = Path(args.output).resolve()

    engine = Engine(output_root=output_root, contract_dir=contract_path.parent)

    try:
        result = engine.run(
            contract,
            dry_run=args.dry_run,
            pattern_filter=args.pattern,
            library_filter=args.lib,
        )
    except EngineError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if args.json:
        _emit_json(result, dry_run=args.dry_run)
    else:
        _emit_human(result, dry_run=args.dry_run, output_root=output_root)

    if result.apply_result is not None and result.apply_result.failed > 0:
        return 3
    return 0


def _load_contract(path: Path) -> Mapping[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        return yaml.safe_load(text) or {}
    if path.suffix == ".json":
        return json.loads(text)
    return yaml.safe_load(text) or {}


def _emit_json(result, dry_run: bool) -> None:
    payload = {
        "dry_run": dry_run,
        "planned_actions": len(result.actions),
        "resolved_libraries": [
            {
                "id": lid,
                "kind": rb.kind,
                "version": rb.resolved_version,
                "mirror_url": rb.mirror_url,
            }
            for lid, rb in sorted(result.resolved_libraries.items())
        ],
    }
    if result.apply_result is not None:
        payload["apply"] = result.apply_result.to_dict()
    print(json.dumps(payload, indent=2))


def _emit_human(result, *, dry_run: bool, output_root: Path) -> None:
    if result.resolved_libraries:
        print("Resolved libraries:")
        for lid, rb in sorted(result.resolved_libraries.items()):
            print(f"  {lid}  ({rb.kind})  version={rb.resolved_version}")
        print()

    if dry_run:
        print(f"Planned {len(result.actions)} file(s) (dry run — nothing written):")
    elif result.apply_result is not None:
        ar = result.apply_result
        print(f"✓ {ar.applied} files written, {ar.failed} failed ({ar.duration_sec}s)")

    paths = sorted(
        a.get("params", {}).get("path", "") for a in result.actions if a.get("op") == "write_file"
    )
    for p in paths:
        full = output_root / p
        print(f"  {full}" if not dry_run else f"  {p}")
