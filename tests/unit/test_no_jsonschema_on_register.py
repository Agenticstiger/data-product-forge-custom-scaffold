# Copyright 2024-2026 Agentics Transformation Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Registering the plugin's CLI command must NOT import jsonschema.

forge-cli eagerly loads every ``fluid_build.commands`` registrar to build the
parser for ``fluid --help``, and its startup-budget guard
(``tests/perf/test_startup_budget.py``) forbids importing jsonschema on that
path. So importing this plugin and registering its command must stay
jsonschema-free; jsonschema is imported lazily, only when a customScaffold block
or a bundle's variables are actually validated.

Run in a FRESH subprocess so ``sys.modules`` reflects only the registration path
(another test in this process may already have imported jsonschema). The check is
return-code based — the assertion lives in the child — so there is no stdout
parsing to flake on.
"""

import subprocess
import sys


def test_registering_command_does_not_import_jsonschema() -> None:
    code = (
        "import sys, argparse\n"
        "from data_product_forge_custom_scaffold.cli import register\n"
        "register(argparse.ArgumentParser().add_subparsers())\n"
        "leaked = sorted(m for m in sys.modules if m == 'jsonschema' or m.startswith('jsonschema.'))\n"
        "assert not leaked, f'jsonschema imported on the registration path: {leaked}'\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr or r.stdout
