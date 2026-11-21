"""The engine raises a typed error hierarchy and confines planned paths before apply.

Covers:

* every failure the engine attributes is an ``EngineError`` with a precise subclass;
* failures inside third-party plugin code stay redacted (exception type only);
* a plan that would write outside the output directory is refused before
  ``apply()`` runs — for templated bundles and for Python plugins, on a dry run too.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

import pytest
from fluid_sdk import ExecutionResult, PluginError, write_file_action

from data_product_forge_custom_scaffold import Engine
from data_product_forge_custom_scaffold import errors as errors_mod
from data_product_forge_custom_scaffold import manifest as manifest_mod
from data_product_forge_custom_scaffold.engine import EngineError
from data_product_forge_custom_scaffold.errors import (
    ApplyError,
    BundleError,
    ContractError,
    PlanError,
    RenderError,
    UnsafePathError,
)
from data_product_forge_custom_scaffold.renderer import RenderError as RendererRenderError
from data_product_forge_custom_scaffold.resolvers import ResolutionError
from tests.helpers import (
    SECRET,
    ScriptedScaffold,
    SdkScaffold,
    path_contract,
    reference_bundle,
    reference_contract,
    use_scaffold,
    write_bundle,
)


def _ref_contract(bundle: Path, *, use: str = "ref:basic", variables=None):
    """A contract with library ``ref`` over *bundle*, running one pattern *use*."""
    return path_contract(bundle, [use], lib="ref", variables=variables)


# ── the hierarchy ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "exc",
    [
        ContractError,
        BundleError,
        PlanError,
        ApplyError,
        RenderError,
        UnsafePathError,
        ResolutionError,
        manifest_mod.ManifestError,
    ],
)
def test_every_typed_error_is_an_engine_error(exc: type) -> None:
    assert issubclass(exc, EngineError)


def test_existing_error_types_keep_their_old_bases() -> None:
    # ManifestError was a ValueError and ResolutionError a RuntimeError: code that
    # catches those keeps working after re-parenting under EngineError.
    assert issubclass(manifest_mod.ManifestError, ValueError)
    assert issubclass(ResolutionError, RuntimeError)
    assert issubclass(manifest_mod.ManifestError, BundleError)
    assert issubclass(ResolutionError, BundleError)


def test_names_stay_importable_from_their_old_homes() -> None:
    assert EngineError is errors_mod.EngineError
    assert RendererRenderError is RenderError
    assert issubclass(UnsafePathError, RenderError)


# ── precise types for the failures a library caller sees ────────────────────


def test_undeclared_library_is_a_contract_error(tmp_path: Path) -> None:
    bundle = reference_bundle(tmp_path)
    with pytest.raises(ContractError, match="not declared"):
        Engine(output_root=tmp_path / "out", contract_dir=tmp_path).run(
            _ref_contract(bundle, use="ghost:basic")
        )


def test_unresolvable_bundle_is_a_bundle_error(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-bundle"
    with pytest.raises(BundleError, match="not a directory") as ei:
        Engine(output_root=tmp_path / "out", contract_dir=tmp_path).run(_ref_contract(missing))
    assert isinstance(ei.value, ResolutionError)


def test_unknown_pattern_is_a_bundle_error_with_the_sdk_message(tmp_path: Path) -> None:
    bundle = reference_bundle(tmp_path)
    with pytest.raises(BundleError, match=r"pattern 'ref:nope'.*no pattern named 'nope'"):
        Engine(output_root=tmp_path / "out", contract_dir=tmp_path).run(
            _ref_contract(bundle, use="ref:nope")
        )


def test_invalid_manifest_is_a_bundle_error(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path, {"basic": {"x.txt": "hello\n"}})
    (bundle / "fluid-scaffold.yaml").write_text("apiVersion: nope/v9\nbundle: {name: x}\n")
    with pytest.raises(BundleError, match="unsupported apiVersion"):
        Engine(output_root=tmp_path / "out", contract_dir=tmp_path).run(_ref_contract(bundle))


def test_scaffold_constructor_failure_is_redacted_to_its_type(tmp_path: Path, monkeypatch) -> None:
    bundle = reference_bundle(tmp_path)

    def _ctor_boom(self, *a, **k):
        raise ValueError(SECRET)

    monkeypatch.setattr(Engine, "_instantiate_scaffold", _ctor_boom)
    with pytest.raises(BundleError) as ei:
        Engine(output_root=tmp_path / "out", contract_dir=tmp_path).run(_ref_contract(bundle))

    assert "ValueError" in str(ei.value)
    assert SECRET not in str(ei.value)
    assert isinstance(ei.value.__cause__, ValueError)  # the traceback is still chained


def test_scaffold_constructor_plugin_error_keeps_its_message(tmp_path: Path, monkeypatch) -> None:
    bundle = reference_bundle(tmp_path)

    def _ctor_user_error(self, *a, **k):
        raise PluginError("set the 'region' variable")

    monkeypatch.setattr(Engine, "_instantiate_scaffold", _ctor_user_error)
    with pytest.raises(BundleError, match="set the 'region' variable"):
        Engine(output_root=tmp_path / "out", contract_dir=tmp_path).run(_ref_contract(bundle))


def test_plan_and_apply_failures_have_their_own_types(tmp_path: Path, monkeypatch) -> None:
    bundle = reference_bundle(tmp_path)
    engine = Engine(output_root=tmp_path / "out", contract_dir=tmp_path)

    class _PlanBoom:
        def plan(self, contract):
            raise RuntimeError(SECRET)

    use_scaffold(monkeypatch, _PlanBoom())
    with pytest.raises(PlanError, match="plan failed for pattern 'ref:basic': RuntimeError") as ei:
        engine.run(_ref_contract(bundle))
    assert SECRET not in str(ei.value)

    class _ApplyBoom:
        def plan(self, contract):
            return []

        def apply(self, actions):
            raise RuntimeError(SECRET)

    use_scaffold(monkeypatch, _ApplyBoom())
    with pytest.raises(
        ApplyError, match="apply failed for pattern 'ref:basic': RuntimeError"
    ) as ei:
        engine.run(_ref_contract(bundle))
    assert SECRET not in str(ei.value)
    assert ei.value.run is None  # raised by apply(), not reported by it


def test_a_non_path_render_error_stays_redacted(tmp_path: Path) -> None:
    # Only the output-path refusal gained a message; every other RenderError keeps
    # surfacing as the exception type (a template's error text is not trusted).
    bundle = write_bundle(tmp_path, {"basic": {"x.txt": "{{ undefined_name.attr }}\n"}})
    with pytest.raises(PlanError) as ei:
        Engine(output_root=tmp_path / "out", contract_dir=tmp_path).run(_ref_contract(bundle))
    assert str(ei.value) == "plan failed for pattern 'ref:basic': RenderError"


# ── confinement happens before apply ─────────────────────────────────────────


ABSOLUTE = "<absolute path outside the output dir>"


def _escaping(raw: str, tmp_path: Path) -> str:
    return str(tmp_path / "abs-escape.txt") if raw == ABSOLUTE else raw


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize(
    "to, dest",
    [
        ("../escape.txt", None),
        ("sub/../../escape.txt", None),
        ("{{ dest }}", ABSOLUTE),
        ("{{ dest }}", "../escape.txt"),
    ],
)
def test_templated_bundle_cannot_write_outside_the_output_dir(
    tmp_path: Path, dry_run: bool, to: str, dest
) -> None:
    bundle = write_bundle(tmp_path, {"basic": {to: "hello\n"}})
    out = tmp_path / "out"
    variables = None if dest is None else {"dest": _escaping(dest, tmp_path)}

    with pytest.raises(UnsafePathError) as ei:
        Engine(output_root=out, contract_dir=tmp_path).run(
            _ref_contract(bundle, variables=variables), dry_run=dry_run
        )

    assert str(ei.value).startswith("plan failed for pattern 'ref:basic': output path must ")
    assert not (tmp_path / "escape.txt").exists()
    assert not (tmp_path / "abs-escape.txt").exists()
    assert not out.exists() or list(out.rglob("*")) == []


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("raw", ["../evil.txt", "a/../../evil.txt", ABSOLUTE])
def test_plugin_plan_outside_the_output_dir_is_refused_before_apply(
    tmp_path: Path, monkeypatch, dry_run: bool, raw: str
) -> None:
    bundle = reference_bundle(tmp_path)
    out = tmp_path / "out"
    path = _escaping(raw, tmp_path)
    scaffold = ScriptedScaffold([write_file_action(path=path, content=b"x").to_dict()])
    use_scaffold(monkeypatch, scaffold)

    with pytest.raises(UnsafePathError, match="resolves outside the output directory") as ei:
        Engine(output_root=out, contract_dir=tmp_path).run(_ref_contract(bundle), dry_run=dry_run)

    assert f"{path!r}" in str(ei.value)
    assert scaffold.apply_calls == 0
    assert not (tmp_path / "evil.txt").exists()
    assert not (tmp_path / "abs-escape.txt").exists()


def test_a_symlink_planted_in_the_output_dir_cannot_redirect_a_write(
    tmp_path: Path, monkeypatch
) -> None:
    bundle = reference_bundle(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (out / "link").symlink_to(outside, target_is_directory=True)
    scaffold = ScriptedScaffold([write_file_action(path="link/x.txt", content=b"x").to_dict()])
    use_scaffold(monkeypatch, scaffold)

    with pytest.raises(UnsafePathError):
        Engine(output_root=out, contract_dir=tmp_path).run(_ref_contract(bundle))

    assert scaffold.apply_calls == 0
    assert list(outside.iterdir()) == []


def test_a_harmless_dot_dot_that_stays_inside_is_still_written(tmp_path: Path, monkeypatch) -> None:
    # The SDK's apply() has always accepted ``a/../b.txt`` (it resolves inside the
    # root); the pre-apply check must not turn that into a new refusal.
    bundle = reference_bundle(tmp_path)
    out = tmp_path / "out"
    plan = [write_file_action(path="a/../b.txt", content=b"ok").to_dict()]
    use_scaffold(monkeypatch, SdkScaffold(plan, output_root=out))

    result = Engine(output_root=out, contract_dir=tmp_path).run(_ref_contract(bundle))

    assert result.apply_result is not None and result.apply_result.failed == 0
    assert (out / "b.txt").read_bytes() == b"ok"


def test_non_write_actions_and_unusable_paths_are_left_to_apply(
    tmp_path: Path, monkeypatch
) -> None:
    # An empty/non-string path or a non-file op is not the confinement check's job:
    # apply() reports it as a failed/skipped action, exactly as before.
    bundle = reference_bundle(tmp_path)
    odd = [
        {"op": "noop", "params": {"path": "../x"}},
        {"op": "write_file", "params": {"path": ""}},
        {"op": "write_file", "params": {"path": None}},
        {"op": "write_file"},
    ]
    seen: List[List[dict]] = []

    class _Lenient:
        def plan(self, contract):
            return odd

        def apply(self, actions):
            seen.append(list(actions))
            return ExecutionResult(plugin="p", role="custom_scaffold", applied=0, failed=3)

    use_scaffold(monkeypatch, _Lenient())
    result = Engine(output_root=tmp_path / "out", contract_dir=tmp_path).run(_ref_contract(bundle))

    assert seen == [odd]  # apply() saw every action, unrefused
    assert result.apply_result is not None and result.apply_result.failed == 3


# ── the lock text is available on a dry run ──────────────────────────────────


def test_dry_run_previews_the_lock_a_real_run_writes(tmp_path: Path) -> None:
    contract = reference_contract(reference_bundle(tmp_path))

    dry = Engine(output_root=tmp_path / "dry", contract_dir=tmp_path).run(contract, dry_run=True)
    real_out = tmp_path / "real"
    real = Engine(output_root=real_out, contract_dir=tmp_path).run(contract)

    assert dry.lockfile is None and not (tmp_path / "dry").exists()
    assert dry.lock_text is not None
    assert real.lockfile == real_out / "fluid-scaffold.lock"
    assert real.lockfile.read_text(encoding="utf-8") == dry.lock_text == real.lock_text


def test_no_resolved_library_means_no_lock_text(tmp_path: Path) -> None:
    result = Engine(output_root=tmp_path / "out").run({"id": "x"}, dry_run=True)
    assert result.lock_text is None and result.lockfile is None
