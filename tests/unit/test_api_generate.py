"""``generate()`` — the library entry point.

Covers what a host that records the written files relies on: an accurate
``(path, sha256, size)`` row for every file in a deterministic order, a dry run
that writes nothing, refusal of paths that leave the output directory, typed
errors, and no output or side effects beyond ``out_dir``.
"""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest
from fluid_sdk import CustomScaffold, ExecutionResult, write_file_action

import data_product_forge_custom_scaffold as pkg
from data_product_forge_custom_scaffold import (
    ApplyError,
    BundleError,
    ContractError,
    EngineError,
    GeneratedFile,
    GenerationResult,
    PlanError,
    UnsafePathError,
    generate,
)
from data_product_forge_custom_scaffold.lockfile import LOCKFILE_NAME
from tests.helpers import (
    FIXTURES,
    SECRET,
    ScriptedScaffold,
    path_contract,
    reference_bundle,
    reference_contract,
    tree_digests,
    use_scaffold,
    write_bundle,
)

HELLO_SHA256 = "5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03"


# ── builders ────────────────────────────────────────────────────────────────


def _rows(result: GenerationResult) -> Dict[str, Tuple[str, int]]:
    return {f.path: (f.sha256, f.size) for f in result.files}


def _scaffold_files(result: GenerationResult) -> List[GeneratedFile]:
    return [f for f in result.files if f.path != LOCKFILE_NAME]


def _one_file() -> List[dict]:
    return [write_file_action(path="a.txt", content=b"a").to_dict()]


def _reference(tmp_path: Path) -> Dict[str, Any]:
    return reference_contract(reference_bundle(tmp_path))


# ── the result: rows, digests, sizes, order ─────────────────────────────────


def test_files_match_an_independent_hash_of_the_output_tree(tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = generate(_reference(tmp_path), out)

    assert isinstance(result, GenerationResult) and result.dry_run is False
    # Every file on disk and nothing else, with digest and size agreeing.
    assert _rows(result) == tree_digests(out)
    assert [f.path for f in result.files] == [
        ".gitlab-ci.yml",
        "README.md",
        "docs/runbook.md",
        LOCKFILE_NAME,
    ]
    for f in result.files:
        assert len(f.sha256) == 64 and f.sha256 == f.sha256.lower() and f.size > 0
    # A verbatim static file's digest is the fixture's own — independent of the engine.
    runbook = (FIXTURES / "reference_bundle" / "static" / "docs" / "runbook.md").read_bytes()
    assert _rows(result)["docs/runbook.md"] == (hashlib.sha256(runbook).hexdigest(), len(runbook))


def test_digest_and_size_of_a_known_file(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path, {"p": {"hello.txt": "hello\n"}})
    result = generate(path_contract(bundle, ["p"]), tmp_path / "out")

    hello = next(f for f in result.files if f.path == "hello.txt")
    assert hello == GeneratedFile(path="hello.txt", sha256=HELLO_SHA256, size=6)


def test_the_lock_is_listed_so_the_rows_account_for_the_whole_tree(tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = generate(_reference(tmp_path), out)

    lock = next(f for f in result.files if f.path == LOCKFILE_NAME)
    assert (lock.sha256, lock.size) == tree_digests(out)[LOCKFILE_NAME]
    assert result.run.lockfile == out.resolve() / LOCKFILE_NAME


def test_runs_in_different_directories_agree_byte_for_byte(tmp_path: Path) -> None:
    contract = _reference(tmp_path)

    first = generate(contract, tmp_path / "a")
    second = generate(contract, tmp_path / "deeper" / "b")

    assert first.files == second.files


def test_a_rerun_into_the_same_directory_is_idempotent(tmp_path: Path) -> None:
    contract = _reference(tmp_path)
    out = tmp_path / "out"

    first = generate(contract, out)
    second = generate(contract, out)

    assert first.files == second.files
    assert _rows(second) == tree_digests(out)


def test_listing_is_sorted_and_independent_of_pattern_order(tmp_path: Path) -> None:
    bundle = write_bundle(
        tmp_path,
        {"alpha": {"z.txt": "Z\n", "m.txt": "M\n"}, "beta": {"a.txt": "A\n", "docs/b.txt": "B\n"}},
    )

    forward = generate(path_contract(bundle, ["alpha", "beta"]), tmp_path / "f")
    backward = generate(path_contract(bundle, ["beta", "alpha"]), tmp_path / "b")

    expected = ["a.txt", "docs/b.txt", "m.txt", "z.txt"]
    assert [f.path for f in _scaffold_files(forward)] == expected
    # The lock records pattern order, so only the scaffold files are order-free.
    assert _scaffold_files(forward) == _scaffold_files(backward)
    assert [f.path for f in forward.files] == sorted(f.path for f in forward.files)


@pytest.mark.parametrize(
    "order, winner", [(["first", "second"], "SECOND\n"), (["second", "first"], "FIRST\n")]
)
def test_a_path_two_patterns_write_is_listed_once_with_the_later_bytes(
    tmp_path: Path, order: List[str], winner: str
) -> None:
    bundle = write_bundle(
        tmp_path, {"first": {"same.txt": "FIRST\n"}, "second": {"same.txt": "SECOND\n"}}
    )
    contract = path_contract(bundle, order)
    out = tmp_path / "out"

    real = generate(contract, out)
    dry = generate(contract, tmp_path / "dry", dry_run=True)

    expected = GeneratedFile("same.txt", hashlib.sha256(winner.encode()).hexdigest(), len(winner))
    assert [f for f in real.files if f.path == "same.txt"] == [expected]
    assert (out / "same.txt").read_text() == winner
    assert dry.files == real.files


def test_a_contract_without_the_block_is_a_no_op(tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = generate({"id": "x"}, out)
    dry = generate({"id": "x"}, out, dry_run=True)

    assert result.files == () and dry.files == ()
    assert not out.exists()


def test_filters_restrict_patterns_and_libraries(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path, {"one": {"one.txt": "1\n"}, "two": {"two.txt": "2\n"}})
    contract = path_contract(bundle, ["one", "two"])

    only_two = generate(contract, tmp_path / "p", pattern_filter=["lib:two"])
    assert [f.path for f in only_two.files] == [LOCKFILE_NAME, "two.txt"]

    contract["extensions"]["customScaffold"]["libraries"].append(
        {"id": "other", "source": {"kind": "path", "path": str(bundle)}}
    )
    contract["extensions"]["customScaffold"]["patterns"].append({"use": "other:one"})
    only_lib = generate(contract, tmp_path / "l", library_filter=["lib"])
    assert [f.path for f in only_lib.files] == [LOCKFILE_NAME, "one.txt", "two.txt"]
    assert sorted(only_lib.run.resolved_libraries) == ["lib"]


# ── dry run ──────────────────────────────────────────────────────────────────


def test_dry_run_writes_nothing_and_reports_what_a_real_run_writes(tmp_path: Path) -> None:
    contract = _reference(tmp_path)
    dry_out = tmp_path / "dry"

    dry = generate(contract, dry_out, dry_run=True)
    real = generate(contract, tmp_path / "real")

    assert dry.dry_run is True and dry.run.apply_result is None and dry.run.lockfile is None
    assert not dry_out.exists()  # not even created
    assert dry.files == real.files  # same paths, digests and sizes, lock included


def test_dry_run_leaves_an_existing_directory_untouched(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "keep.txt").write_text("keep")

    result = generate(_reference(tmp_path), out, dry_run=True)

    assert result.files and tree_digests(out) == {"keep.txt": tree_digests(out)["keep.txt"]}


# ── refusal of paths that leave out_dir ─────────────────────────────────────


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("dest", ["../escape.txt", "a/../../escape.txt", "ABSOLUTE"])
def test_a_bundle_path_outside_out_dir_is_refused_and_nothing_is_written(
    tmp_path: Path, dry_run: bool, dest: str
) -> None:
    target = str(tmp_path / "abs-escape.txt") if dest == "ABSOLUTE" else dest
    bundle = write_bundle(tmp_path, {"p": {"ok.txt": "ok\n", target: "x\n"}})
    out = tmp_path / "out"

    with pytest.raises(UnsafePathError) as ei:
        generate(path_contract(bundle, ["p"]), out, dry_run=dry_run)

    assert repr(target) in str(ei.value)
    assert isinstance(ei.value, EngineError)
    assert not (tmp_path / "escape.txt").exists() and not (tmp_path / "abs-escape.txt").exists()
    assert not out.exists()  # the safe file was not written either


@pytest.mark.parametrize("dry_run", [False, True])
def test_a_plugin_plan_outside_out_dir_is_refused_before_apply(
    tmp_path: Path, monkeypatch, dry_run: bool
) -> None:
    scaffold = ScriptedScaffold([write_file_action(path="../evil.txt", content=b"x").to_dict()])
    use_scaffold(monkeypatch, scaffold)
    bundle = write_bundle(tmp_path, {"p": {"x.txt": "x\n"}})

    with pytest.raises(UnsafePathError, match="outside the output directory"):
        generate(path_contract(bundle, ["p"]), tmp_path / "out", dry_run=dry_run)

    assert scaffold.apply_calls == 0
    assert not (tmp_path / "evil.txt").exists()


def test_a_symlink_in_out_dir_cannot_redirect_a_write(tmp_path: Path, monkeypatch) -> None:
    out, outside = tmp_path / "out", tmp_path / "outside"
    out.mkdir()
    outside.mkdir()
    (out / "link").symlink_to(outside, target_is_directory=True)
    scaffold = ScriptedScaffold([write_file_action(path="link/x.txt", content=b"x").to_dict()])
    use_scaffold(monkeypatch, scaffold)
    bundle = write_bundle(tmp_path, {"p": {"x.txt": "x\n"}})

    with pytest.raises(UnsafePathError):
        generate(path_contract(bundle, ["p"]), out)

    assert scaffold.apply_calls == 0 and list(outside.iterdir()) == []


# ── typed errors ─────────────────────────────────────────────────────────────


def test_undeclared_library_is_a_contract_error(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path, {"p": {"x.txt": "x\n"}})
    contract = path_contract(bundle, ["p"])
    contract["extensions"]["customScaffold"]["patterns"] = [{"use": "ghost:p"}]

    with pytest.raises(ContractError, match="not declared"):
        generate(contract, tmp_path / "out")


def test_unresolvable_bundle_is_a_bundle_error(tmp_path: Path) -> None:
    contract = path_contract(tmp_path / "missing", ["p"])

    with pytest.raises(BundleError, match="not a directory"):
        generate(contract, tmp_path / "out")


def test_plan_failure_is_a_plan_error_naming_only_the_exception_type(
    tmp_path: Path, monkeypatch
) -> None:
    def boom(_):
        raise ValueError(SECRET)

    class _PlanBoom:
        plan = staticmethod(boom)

    use_scaffold(monkeypatch, _PlanBoom())
    bundle = write_bundle(tmp_path, {"p": {"x.txt": "x\n"}})

    with pytest.raises(PlanError) as ei:
        generate(path_contract(bundle, ["p"]), tmp_path / "out")

    assert str(ei.value) == "plan failed for pattern 'lib:p': ValueError"
    assert SECRET not in str(ei.value)


def test_apply_exception_is_an_apply_error_naming_only_the_exception_type(
    tmp_path: Path, monkeypatch
) -> None:
    def boom(_):
        raise RuntimeError(SECRET)

    use_scaffold(monkeypatch, ScriptedScaffold(_one_file(), apply=boom))
    bundle = write_bundle(tmp_path, {"p": {"x.txt": "x\n"}})

    with pytest.raises(ApplyError) as ei:
        generate(path_contract(bundle, ["p"]), tmp_path / "out")

    assert str(ei.value) == "apply failed for pattern 'lib:p': RuntimeError"
    assert SECRET not in str(ei.value)
    assert ei.value.run is None


def test_failed_files_raise_apply_error_that_lists_paths_not_error_text(
    tmp_path: Path, monkeypatch
) -> None:
    def failed(_):
        return ExecutionResult(
            plugin="p",
            role="custom_scaffold",
            applied=1,
            failed=2,
            results=[
                {"op": "write_file", "resource_id": "b.txt", "status": "failed", "error": SECRET},
                {"op": "write_file", "resource_id": "ok.txt", "status": "ok", "path": "ok.txt"},
                {"op": "write_file", "resource_id": "a.txt", "status": "failed", "error": SECRET},
            ],
        )

    use_scaffold(monkeypatch, ScriptedScaffold(_one_file(), apply=failed))
    bundle = write_bundle(tmp_path, {"p": {"x.txt": "x\n"}})

    with pytest.raises(ApplyError) as ei:
        generate(path_contract(bundle, ["p"]), tmp_path / "out")

    assert str(ei.value) == "apply failed for 2 of 3 file(s): a.txt, b.txt"
    assert SECRET not in str(ei.value)
    # The per-action detail is still there for a caller that wants it.
    assert ei.value.run is not None
    assert ei.value.run.apply_result.results[0]["error"] == SECRET


def test_a_planned_file_missing_after_apply_is_an_apply_error(tmp_path: Path, monkeypatch) -> None:
    ok = ExecutionResult(plugin="p", role="custom_scaffold", applied=1, failed=0)
    use_scaffold(monkeypatch, ScriptedScaffold(_one_file(), apply=lambda actions: ok))
    bundle = write_bundle(tmp_path, {"p": {"x.txt": "x\n"}})

    with pytest.raises(ApplyError, match="'a.txt' was planned but could not be read back"):
        generate(path_contract(bundle, ["p"]), tmp_path / "out")


def test_contract_must_be_a_mapping(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="contract must be a mapping"):
        generate(["not", "a", "mapping"], tmp_path / "out")  # type: ignore[arg-type]


# ── sources, anchoring, plugins ──────────────────────────────────────────────


def test_a_relative_path_source_anchors_to_contract_dir_not_cwd(
    tmp_path: Path, monkeypatch
) -> None:
    write_bundle(tmp_path, {"p": {"x.txt": "x\n"}})
    consumer, elsewhere = tmp_path / "consumer", tmp_path / "elsewhere"
    consumer.mkdir()
    elsewhere.mkdir()
    contract = path_contract(
        tmp_path / "bundle", ["p"], source={"kind": "path", "path": "../bundle"}
    )
    monkeypatch.chdir(elsewhere)

    anchored = generate(contract, tmp_path / "a", contract_dir=consumer)
    as_text = generate(contract, str(tmp_path / "b"), contract_dir=str(consumer))

    assert [f.path for f in anchored.files] == [LOCKFILE_NAME, "x.txt"]
    assert anchored.files == as_text.files  # str and Path spellings agree

    # With no contract_dir the cwd is the anchor, as for Engine.
    monkeypatch.chdir(consumer)
    assert generate(contract, tmp_path / "c").files == anchored.files


def test_a_python_plugin_scaffold_is_listed_through_its_own_apply(
    tmp_path: Path, monkeypatch
) -> None:
    out = tmp_path / "out"

    class _Plugin(CustomScaffold):  # the SDK's default apply() does the writing
        name = "plug"

        def plan(self, contract):
            return _one_file()

    use_scaffold(monkeypatch, _Plugin(output_root=out))
    bundle = write_bundle(tmp_path, {"p": {"x.txt": "x\n"}})

    result = generate(path_contract(bundle, ["p"]), out)

    assert [f.path for f in result.files] == ["a.txt", LOCKFILE_NAME]
    assert _rows(result) == tree_digests(out)


# ── no printing, no side effects ─────────────────────────────────────────────


def test_generate_prints_nothing_and_touches_only_out_dir(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    contract = _reference(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    before = tree_digests(tmp_path)

    generate(contract, tmp_path / "out", contract_dir=tmp_path)
    generate(contract, tmp_path / "dry", dry_run=True, contract_dir=tmp_path)

    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "")
    assert list(elsewhere.iterdir()) == []  # nothing written to the cwd
    after = {k: v for k, v in tree_digests(tmp_path).items() if not k.startswith("out/")}
    assert after == before


# ── the public surface ───────────────────────────────────────────────────────


def test_signature_is_the_documented_one() -> None:
    params = inspect.signature(generate).parameters

    assert list(params) == [
        "contract",
        "out_dir",
        "contract_dir",
        "dry_run",
        "pattern_filter",
        "library_filter",
        "pin",
        "dialect",
    ]
    positional = [n for n, p in params.items() if p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD]
    assert positional == ["contract", "out_dir"]  # the rest are keyword-only
    assert params["dry_run"].default is False and params["pin"].default is False


def test_result_types_are_immutable_and_equality_ignores_the_raw_run(tmp_path: Path) -> None:
    contract = _reference(tmp_path)
    first = generate(contract, tmp_path / "a")
    second = generate(contract, tmp_path / "b")

    assert isinstance(first.files, tuple)
    assert first == second  # timestamps in .run differ; they are not compared
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.files = ()  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.files[0].sha256 = "x"  # type: ignore[misc]
    assert "EngineRunResult" not in repr(first)  # the raw run stays out of the repr


def test_public_names_are_exported_from_the_package() -> None:
    for name in (
        "generate",
        "GenerationResult",
        "GeneratedFile",
        "EngineError",
        "ContractError",
        "BundleError",
        "PlanError",
        "UnsafePathError",
        "ApplyError",
    ):
        assert name in pkg.__all__ and hasattr(pkg, name)
