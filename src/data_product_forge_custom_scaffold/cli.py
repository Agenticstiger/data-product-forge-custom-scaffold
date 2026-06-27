"""``fluid custom-scaffold`` subcommand.

Registered as a CLI plugin via:

    [project.entry-points."fluid_build.commands"]
    generate-custom-scaffold = "data_product_forge_custom_scaffold.cli:register"

The FLUID CLI calls :func:`register` with its argparse subparser group and
expects a ``func`` attribute set on the parser. We wire that to :func:`run`
which delegates to the :class:`Engine`.

White-label packages call :func:`make_register` with their own
:class:`~data_product_forge_custom_scaffold.dialect.ScaffoldDialect` to get a
``register`` bound to their command name / contract default / extension key,
then register it under ``fluid_build.commands`` with their own entry-point name.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml

from .dialect import DEFAULT as DEFAULT_DIALECT
from .dialect import ScaffoldDialect
from .engine import Engine, EngineError
from .update import UpdateError
from .validation import make_schema_provider


def make_register(
    dialect: ScaffoldDialect = DEFAULT_DIALECT,
) -> Callable[[argparse._SubParsersAction], None]:
    """Build a ``register(subparsers)`` callable bound to *dialect*.

    The returned function registers a ``fluid <dialect.command_name>``
    subcommand whose defaults (contract path, help text, extension key) come
    from *dialect*, and wires ``func`` to :func:`run` bound to the same dialect.
    """

    def register(subparsers: argparse._SubParsersAction) -> None:
        parser = subparsers.add_parser(
            dialect.command_name,
            help=f"Generate a {dialect.display_name} from your fluid contract.",
            description=(
                f"Reads contract.extensions.{dialect.extension_key}, resolves each "
                "declared bundle (from path or git), and renders the contract "
                "through each pattern into a deterministic set of files."
            ),
        )
        parser.add_argument(
            "--contract",
            "-c",
            default=dialect.contract_default_path,
            help=f"Path to the fluid contract (default: ./{dialect.contract_default_path})",
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
            "--pin",
            action="store_true",
            help=(
                "Reproducible re-run: resolve git sources to the commit recorded in "
                "fluid-scaffold.lock instead of following the contract ref. Without it, "
                "the ref is followed and the lock is refreshed."
            ),
        )
        parser.add_argument(
            "--update",
            action="store_true",
            help=(
                "Update an existing output to the evolved template: re-render at the "
                "new ref and 3-way-merge onto your working tree (preserves edits, writes "
                "conflict markers where they overlap). Requires a fluid-scaffold.lock."
            ),
        )
        parser.add_argument(
            "--target",
            default=None,
            help="With --update: the git ref/commit to update TO (default: the contract's ref).",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit machine-readable JSON result instead of a human summary.",
        )
        parser.add_argument(
            "--print-schema",
            action="store_true",
            help=(
                f"Print the extensions.{dialect.extension_key} JSON-Schema (+ a minimal "
                "valid example) and exit. Use it to prime the `fluid forge` copilot so "
                f"AI-generated contracts include a valid extensions.{dialect.extension_key} block."
            ),
        )
        # The FLUID CLI dispatcher invokes a registered command as
        # ``func(args, logger)`` (two positional args); accept and ignore any
        # extra positionals so the command works both via ``fluid`` and when
        # called directly with just ``args``.
        parser.set_defaults(func=lambda args, *_: run(args, dialect=dialect))

    return register


# Back-compat: the historical entry-point target, bound to the built-in fluid
# dialect (``fluid custom-scaffold``).
register = make_register(DEFAULT_DIALECT)


def run(args: argparse.Namespace, *, dialect: ScaffoldDialect = DEFAULT_DIALECT) -> int:
    """Implementation of ``fluid <dialect.command_name>``."""
    # --print-schema short-circuits before any contract is required, so an
    # author (or an agent priming the copilot) can fetch the schema anywhere.
    if getattr(args, "print_schema", False):
        return _print_schema(dialect=dialect, as_json=getattr(args, "json", False))

    contract_path = Path(args.contract).resolve()
    if not contract_path.is_file():
        print(f"error: contract not found at {contract_path}", file=sys.stderr)
        return 1

    contract = _load_contract(contract_path)
    output_root = Path(args.output).resolve()

    engine = Engine(
        output_root=output_root,
        contract_dir=contract_path.parent,
        dialect=dialect,
    )

    if getattr(args, "update", False):
        return _run_update(engine, contract, args=args, output_root=output_root)

    try:
        result = engine.run(
            contract,
            dry_run=args.dry_run,
            pattern_filter=args.pattern,
            library_filter=args.lib,
            pin=getattr(args, "pin", False),
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


def _run_update(engine: Engine, contract: Mapping[str, Any], *, args, output_root: Path) -> int:
    """Dispatch ``--update`` and render its result. Returns 4 on conflicts."""
    try:
        result = engine.update(
            contract,
            target_ref=getattr(args, "target", None),
            dry_run=args.dry_run,
        )
    except (EngineError, UpdateError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if args.json:
        print(
            json.dumps(
                {
                    "update": True,
                    "dry_run": args.dry_run,
                    "files": [{"path": f.path, "status": f.status} for f in result.files],
                    "conflicts": [f.path for f in result.conflicts],
                },
                indent=2,
            )
        )
    else:
        verb = "Would update" if args.dry_run else "Updated"
        print(f"{verb} {output_root}:")
        for f in sorted(result.files, key=lambda f: f.path):
            print(f"  {f.status:16} {f.path}")
        if result.conflicts:
            print(
                f"\n⚠ {len(result.conflicts)} file(s) have conflict markers "
                "(<<<<<<< / >>>>>>>) — resolve them, then commit."
            )

    return 4 if result.conflicts else 0


def _print_schema(*, dialect: ScaffoldDialect, as_json: bool) -> int:
    """Print the dialect's extension JSON-Schema plus a minimal valid example.

    The example mirrors the minimal block the validator accepts, so it stays a
    copy-pasteable starting point (and a grounding sample for the copilot).
    """
    schema = make_schema_provider(dialect)(None)
    key = dialect.extension_key
    example = {
        key: {
            "libraries": [{"id": "ci", "source": {"kind": "path", "path": "./bundle"}}],
            "patterns": [{"use": "ci:basic"}],
        }
    }
    if as_json:
        print(json.dumps({"schema": schema, "example": example}, indent=2))
    else:
        print(f"# extensions.{key} JSON-Schema (draft-07)")
        print(json.dumps(schema, indent=2))
        print()
        print("# Minimal valid example — paste under contract.extensions:")
        print(json.dumps(example, indent=2))
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
