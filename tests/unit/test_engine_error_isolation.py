"""Engine error isolation + redaction.

`plan()` and `apply()` both run third-party plugin code. A plugin that raises an
unexpected (non-`PluginError`) exception must be isolated and surfaced by exception
TYPE only — the raw message may carry secrets and must never reach the engine's
error text. `apply()` used to have no try/except at all (a failing apply crashed
the run with a raw traceback); this pins both fixes.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fluid_sdk import PluginError

from data_product_forge_custom_scaffold import Engine
from data_product_forge_custom_scaffold.engine import EngineError

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
SECRET = "sk-live-supersecret-do-not-leak"


def _contract(bundle: Path) -> dict:
    return {
        "fluidVersion": "0.7.3",
        "kind": "DataProduct",
        "id": "iso-product",
        "name": "Isolation Test",
        "metadata": {"owner": {"team": "x", "email": "x@example.com"}},
        "extensions": {
            "customScaffold": {
                "libraries": [{"id": "ref", "source": {"kind": "path", "path": str(bundle)}}],
                "patterns": [{"use": "ref:basic", "variables": {"teamName": "platform"}}],
            }
        },
    }


def _engine_with_fake_scaffold(tmp_path, monkeypatch, fake):
    bundle = tmp_path / "reference_bundle"
    if not bundle.exists():
        shutil.copytree(FIXTURES / "reference_bundle", bundle)
    out = tmp_path / "out"
    out.mkdir()
    engine = Engine(output_root=out, contract_dir=tmp_path)
    # Library resolution uses the real bundle; the scaffold itself is our fake.
    monkeypatch.setattr(Engine, "_instantiate_scaffold", lambda self, *a, **k: fake)
    return engine, bundle


class _PlanBoom:
    def plan(self, contract):
        raise ValueError(SECRET)

    def apply(self, actions):
        return None


class _ApplyBoom:
    def plan(self, contract):
        return []

    def apply(self, actions):
        raise RuntimeError(SECRET)


class _PluginErrPlan:
    def plan(self, contract):
        raise PluginError("missing required field: teamName")

    def apply(self, actions):
        return None


def test_plan_unexpected_exception_is_type_only(tmp_path, monkeypatch):
    engine, bundle = _engine_with_fake_scaffold(tmp_path, monkeypatch, _PlanBoom())
    with pytest.raises(EngineError) as ei:
        engine.run(_contract(bundle))
    msg = str(ei.value)
    assert "ValueError" in msg  # the type is surfaced
    assert SECRET not in msg  # the raw exception text is NOT


def test_apply_is_isolated_and_type_only(tmp_path, monkeypatch):
    # apply() previously had no try/except — a raise crashed the run raw.
    engine, bundle = _engine_with_fake_scaffold(tmp_path, monkeypatch, _ApplyBoom())
    with pytest.raises(EngineError) as ei:
        engine.run(_contract(bundle))
    msg = str(ei.value)
    assert "apply failed" in msg
    assert "RuntimeError" in msg
    assert SECRET not in msg


def test_plugin_error_message_is_surfaced(tmp_path, monkeypatch):
    # PluginError is user-actionable by the SDK contract — its message DOES surface.
    engine, bundle = _engine_with_fake_scaffold(tmp_path, monkeypatch, _PluginErrPlan())
    with pytest.raises(EngineError, match="missing required field: teamName"):
        engine.run(_contract(bundle))
