"""The registered command must accept the FLUID CLI's dispatch signature.

The FLUID CLI invokes a registered command as ``args.func(args, logger)`` (two
positional args; see fluid_build/cli/__init__.py). A one-arg handler raises
``TypeError: ... takes 1 positional argument but 2 were given`` and breaks
``fluid custom-scaffold`` entirely — this pins the contract so it can't regress.
"""

from __future__ import annotations

import argparse

from data_product_forge_custom_scaffold import cli


def _custom_scaffold_subparser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="cmd")
    cli.make_register()(sub)
    return sub.choices["custom-scaffold"]


def test_command_func_accepts_two_positional_args(capsys):
    # --print-schema short-circuits run() without needing a contract/output.
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    cli.make_register()(sub)
    ns = parser.parse_args(["custom-scaffold", "--print-schema"])

    # Mimic the FLUID dispatcher: func(args, logger) — two positionals.
    rc = ns.func(ns, object())
    assert rc == 0
    out = capsys.readouterr().out
    assert out  # the schema was printed


def test_command_func_also_works_with_one_arg(capsys):
    # Back-compat: a direct one-arg call must still work.
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    cli.make_register()(sub)
    ns = parser.parse_args(["custom-scaffold", "--print-schema"])
    assert ns.func(ns) == 0
    assert capsys.readouterr().out
