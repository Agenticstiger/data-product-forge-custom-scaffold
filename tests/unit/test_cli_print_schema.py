"""Unit tests for the `--print-schema` flag + make_register binding (B2, C)."""

from __future__ import annotations

import argparse
import json

from data_product_forge_custom_scaffold import ScaffoldDialect, make_register
from data_product_forge_custom_scaffold.cli import register as default_register

ACME = ScaffoldDialect(
    extension_key="acmeScaffold",
    manifest_api_versions=("acme.dev/scaffold.v1",),
    command_name="acme-scaffold",
    contract_default_path="contract.acme.yaml",
)


def _build_parser(register) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers()
    register(sub)
    return parser


def test_default_register_subcommand_name_and_contract_default() -> None:
    parser = _build_parser(default_register)
    ns = parser.parse_args(["custom-scaffold"])
    assert ns.contract == "contract.fluid.yaml"
    assert hasattr(ns, "func")


def test_branded_register_subcommand_name_and_contract_default() -> None:
    parser = _build_parser(make_register(ACME))
    ns = parser.parse_args(["acme-scaffold"])
    assert ns.contract == "contract.acme.yaml"


def test_print_schema_json(capsys) -> None:
    parser = _build_parser(default_register)
    ns = parser.parse_args(["custom-scaffold", "--print-schema", "--json"])
    rc = ns.func(ns)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["schema"]["title"] == "extensions.customScaffold"
    assert "customScaffold" in out["example"]


def test_print_schema_branded_needs_no_contract(tmp_path, monkeypatch, capsys) -> None:
    # --print-schema must short-circuit before any contract is required.
    monkeypatch.chdir(tmp_path)  # empty cwd: no contract.acme.yaml present
    parser = _build_parser(make_register(ACME))
    ns = parser.parse_args(["acme-scaffold", "--print-schema", "--json"])
    rc = ns.func(ns)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["schema"]["title"] == "extensions.acmeScaffold"
    assert "acmeScaffold" in out["example"]
