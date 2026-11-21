"""Characterisation of ``fluid custom-scaffold``: output shape and exit codes.

Pins what the command prints and returns today (README "Exit codes", the
``--json`` payload keys, the human summary) so the command can be re-plumbed onto
the library entry point without drifting. Every assertion here held before the
re-plumbing.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import textwrap
from pathlib import Path
from typing import List, Tuple

from fluid_sdk import ExecutionResult, write_file_action

from data_product_forge_custom_scaffold import cli
from data_product_forge_custom_scaffold.engine import Engine

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
SECRET = "sk-live-supersecret-do-not-leak"
DURATION = re.compile(r"\(\d+(\.\d+)?(e-\d+)?s\)")


def _project(tmp_path: Path, *, use: str = "ref:basic") -> Tuple[Path, Path]:
    """A contract next to a copy of the reference bundle; returns (contract, out_dir)."""
    bundle = tmp_path / "bundle"
    shutil.copytree(FIXTURES / "reference_bundle", bundle)
    contract = tmp_path / "contract.fluid.yaml"
    contract.write_text(
        textwrap.dedent(f"""\
            fluidVersion: "0.7.4"
            kind: DataProduct
            id: characterise
            name: Characterise
            description: CLI characterisation fixture
            metadata: {{owner: {{team: t, email: t@t.t}}}}
            environments:
              dev: {{metadata: {{labels: {{cloud.region: us-east-1}}}}}}
            extensions:
              customScaffold:
                libraries:
                  - id: ref
                    source: {{kind: path, path: "{bundle}"}}
                patterns:
                  - use: {use}
                    variables: {{teamName: characterise}}
            """),
        encoding="utf-8",
    )
    return contract, tmp_path / "out"


def _cli(capsys, *argv: str) -> Tuple[int, str, str]:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    cli.make_register()(sub)
    ns = parser.parse_args(["custom-scaffold", *argv])
    rc = ns.func(ns)
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def _fake_scaffold(monkeypatch, *, plan, apply) -> None:
    class _Fake:
        def plan(self, contract):
            return plan(contract) if callable(plan) else plan

        def apply(self, actions):
            return apply(actions)

    monkeypatch.setattr(Engine, "_instantiate_scaffold", lambda self, *a, **k: _Fake())


_ONE_FILE: List[dict] = [write_file_action(path="a.txt", content=b"a").to_dict()]


def _failed_apply(actions) -> ExecutionResult:
    return ExecutionResult(
        plugin="p",
        role="custom_scaffold",
        applied=0,
        failed=1,
        duration_sec=0.0,
        timestamp="T",
        results=[{"op": "write_file", "resource_id": "a.txt", "status": "failed"}],
    )


def _boom(_):
    raise ValueError(SECRET)


# ── success paths ────────────────────────────────────────────────────────────


def test_human_summary_lists_libraries_then_written_files(tmp_path, capsys):
    contract, out = _project(tmp_path)
    rc, stdout, stderr = _cli(capsys, "-c", str(contract), "-o", str(out))

    assert rc == 0
    assert stderr == ""
    lines = stdout.splitlines()
    assert lines[:3] == ["Resolved libraries:", "  ref  (path)  version=local", ""]
    assert re.fullmatch(r"✓ 3 files written, 0 failed \(.+s\)", lines[3])
    assert DURATION.search(lines[3])
    assert lines[4:] == [f"  {out / p}" for p in (".gitlab-ci.yml", "README.md", "docs/runbook.md")]
    assert (out / "README.md").is_file()


def test_json_payload_keys_and_values(tmp_path, capsys):
    contract, out = _project(tmp_path)
    rc, stdout, _ = _cli(capsys, "-c", str(contract), "-o", str(out), "--json")

    assert rc == 0
    payload = json.loads(stdout)
    assert set(payload) == {"dry_run", "planned_actions", "resolved_libraries", "apply"}
    assert payload["dry_run"] is False
    assert payload["planned_actions"] == 3
    assert payload["resolved_libraries"] == [
        {"id": "ref", "kind": "path", "version": "local", "mirror_url": None}
    ]
    apply = payload["apply"]
    assert set(apply) == {
        "plugin",
        "role",
        "applied",
        "failed",
        "duration_sec",
        "timestamp",
        "results",
        "artifacts",
        "warnings",
    }
    assert (apply["plugin"], apply["role"]) == ("custom-scaffold-engine", "custom_scaffold")
    assert (apply["applied"], apply["failed"]) == (3, 0)
    assert [r["path"] for r in apply["results"]] == [
        ".gitlab-ci.yml",
        "README.md",
        "docs/runbook.md",
    ]


def test_dry_run_human_lists_relative_paths_and_writes_nothing(tmp_path, capsys):
    contract, out = _project(tmp_path)
    rc, stdout, _ = _cli(capsys, "-c", str(contract), "-o", str(out), "--dry-run")

    assert rc == 0
    assert stdout.splitlines() == [
        "Resolved libraries:",
        "  ref  (path)  version=local",
        "",
        "Planned 3 file(s) (dry run — nothing written):",
        "  .gitlab-ci.yml",
        "  README.md",
        "  docs/runbook.md",
    ]
    assert not out.exists()


def test_dry_run_json_has_no_apply_block(tmp_path, capsys):
    contract, out = _project(tmp_path)
    rc, stdout, _ = _cli(capsys, "-c", str(contract), "-o", str(out), "--dry-run", "--json")

    assert rc == 0
    payload = json.loads(stdout)
    assert set(payload) == {"dry_run", "planned_actions", "resolved_libraries"}
    assert (payload["dry_run"], payload["planned_actions"]) == (True, 3)
    assert not out.exists()


def test_pattern_filter_matching_nothing_prints_only_the_libraries(tmp_path, capsys):
    contract, out = _project(tmp_path)
    rc, stdout, _ = _cli(
        capsys, "-c", str(contract), "-o", str(out), "--pattern", "nothing:matches"
    )

    assert rc == 0
    assert stdout.splitlines() == ["Resolved libraries:", "  ref  (path)  version=local", ""]


def test_contract_without_the_block_is_a_silent_no_op(tmp_path, capsys):
    contract = tmp_path / "contract.fluid.yaml"
    contract.write_text("fluidVersion: '0.7.4'\nkind: DataProduct\nid: x\nname: X\n")
    rc, stdout, stderr = _cli(capsys, "-c", str(contract), "-o", str(tmp_path / "out"))

    assert (rc, stdout, stderr) == (0, "", "")

    rc, stdout, _ = _cli(
        capsys, "-c", str(contract), "-o", str(tmp_path / "out"), "--dry-run", "--json"
    )
    assert rc == 0
    assert json.loads(stdout) == {"dry_run": True, "planned_actions": 0, "resolved_libraries": []}


# ── failure paths: exit codes and messages ───────────────────────────────────


def test_missing_contract_exits_1(tmp_path, capsys):
    rc, stdout, stderr = _cli(capsys, "-c", str(tmp_path / "nope.yaml"))

    assert rc == 1
    assert stdout == ""
    assert stderr == f"error: contract not found at {(tmp_path / 'nope.yaml').resolve()}\n"


def test_undeclared_library_exits_2(tmp_path, capsys):
    contract, out = _project(tmp_path, use="ghost:basic")
    rc, stdout, stderr = _cli(capsys, "-c", str(contract), "-o", str(out))

    assert (rc, stdout) == (2, "")
    assert stderr == (
        "error: pattern 'ghost:basic' references library 'ghost', "
        "which was not declared (known: ['ref'])\n"
    )


def test_plan_failure_exits_2_with_the_plugin_message(tmp_path, capsys):
    contract, out = _project(tmp_path)
    contract.write_text(contract.read_text().replace("email: t@t.t", ""), encoding="utf-8")
    rc, stdout, stderr = _cli(capsys, "-c", str(contract), "-o", str(out))

    assert (rc, stdout) == (2, "")
    assert stderr == (
        "error: plan failed for pattern 'ref:basic': Pattern 'basic' requires contract "
        "field 'metadata.owner.email', which is missing or empty.\n"
    )
    assert not out.exists()


def test_plan_exception_exits_2_and_names_only_its_type(tmp_path, capsys, monkeypatch):
    contract, out = _project(tmp_path)
    _fake_scaffold(monkeypatch, plan=_boom, apply=lambda a: None)
    rc, stdout, stderr = _cli(capsys, "-c", str(contract), "-o", str(out))

    assert (rc, stdout) == (2, "")
    assert stderr == "error: plan failed for pattern 'ref:basic': ValueError\n"
    assert SECRET not in stderr


def test_apply_exception_exits_2_and_names_only_its_type(tmp_path, capsys, monkeypatch):
    contract, out = _project(tmp_path)
    _fake_scaffold(monkeypatch, plan=_ONE_FILE, apply=_boom)
    rc, stdout, stderr = _cli(capsys, "-c", str(contract), "-o", str(out))

    assert (rc, stdout) == (2, "")
    assert stderr == "error: apply failed for pattern 'ref:basic': ValueError\n"
    assert SECRET not in stderr


def test_failed_apply_action_prints_the_summary_and_exits_3(tmp_path, capsys, monkeypatch):
    contract, out = _project(tmp_path)
    _fake_scaffold(monkeypatch, plan=_ONE_FILE, apply=_failed_apply)
    rc, stdout, stderr = _cli(capsys, "-c", str(contract), "-o", str(out))

    assert rc == 3
    assert stderr == ""
    lines = stdout.splitlines()
    assert lines[:3] == ["Resolved libraries:", "  ref  (path)  version=local", ""]
    assert re.fullmatch(r"✓ 0 files written, 1 failed \(.+s\)", lines[3])
    assert lines[4:] == [f"  {out / 'a.txt'}"]


def test_failed_apply_action_json_carries_the_apply_block_and_exits_3(
    tmp_path, capsys, monkeypatch
):
    contract, out = _project(tmp_path)
    _fake_scaffold(monkeypatch, plan=_ONE_FILE, apply=_failed_apply)
    rc, stdout, _ = _cli(capsys, "-c", str(contract), "-o", str(out), "--json")

    assert rc == 3
    payload = json.loads(stdout)
    assert payload["planned_actions"] == 1
    assert (payload["apply"]["applied"], payload["apply"]["failed"]) == (0, 1)
    assert payload["apply"]["results"] == [
        {"op": "write_file", "resource_id": "a.txt", "status": "failed"}
    ]
