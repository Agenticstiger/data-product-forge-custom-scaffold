"""The custom_scaffolds entry-point resolver honours the operator allow/block
policy and surfaces load failures by type only (no secret leak)."""

from __future__ import annotations

import importlib.metadata as md

import pytest

from data_product_forge_custom_scaffold.resolvers import ResolutionError, resolve_bundle
from data_product_forge_custom_scaffold.resolvers.entrypoint import _plugin_allowed


class _FakeEP:
    value = "my_pkg.scaffold:MyScaffold"
    dist = None

    def __init__(self, name, loader):
        self.name = name
        self._loader = loader

    def load(self):
        return self._loader()


def _patch_eps(monkeypatch, ep):
    monkeypatch.setattr(md, "entry_points", lambda **kw: [ep])


# ── policy helper ─────────────────────────────────────────────────────


def test_plugin_allowed_default(monkeypatch):
    monkeypatch.delenv("FLUID_PLUGINS_ALLOWLIST", raising=False)
    monkeypatch.delenv("FLUID_PLUGINS_BLOCKLIST", raising=False)
    assert _plugin_allowed("anything") is True


def test_blocklist_wins(monkeypatch):
    monkeypatch.setenv("FLUID_PLUGINS_BLOCKLIST", "evil")
    assert _plugin_allowed("evil") is False
    assert _plugin_allowed("good") is True


def test_allowlist_pins(monkeypatch):
    monkeypatch.setenv("FLUID_PLUGINS_ALLOWLIST", "only-this")
    monkeypatch.delenv("FLUID_PLUGINS_BLOCKLIST", raising=False)
    assert _plugin_allowed("only-this") is True
    assert _plugin_allowed("other") is False


# ── gate enforced before load() ───────────────────────────────────────


def test_blocked_plugin_refused_without_loading(monkeypatch):
    def _boom_loader():
        raise AssertionError("load() must not be called for a blocked plugin")

    _patch_eps(monkeypatch, _FakeEP("toy", _boom_loader))
    monkeypatch.setenv("FLUID_PLUGINS_BLOCKLIST", "toy")
    monkeypatch.delenv("FLUID_PLUGINS_ALLOWLIST", raising=False)
    with pytest.raises(ResolutionError, match="blocked by the operator allow/block policy"):
        resolve_bundle({"kind": "entrypoint", "name": "toy"})


def test_allowlisted_plugin_loads(monkeypatch):
    class _Scaffold:
        name = "toy"

    _patch_eps(monkeypatch, _FakeEP("toy", lambda: _Scaffold))
    monkeypatch.setenv("FLUID_PLUGINS_ALLOWLIST", "toy")
    monkeypatch.delenv("FLUID_PLUGINS_BLOCKLIST", raising=False)
    bundle = resolve_bundle({"kind": "entrypoint", "name": "toy"})
    assert bundle.plugin_class is _Scaffold


# ── load-failure redaction ────────────────────────────────────────────


def test_load_failure_is_type_only(monkeypatch):
    def _leaky_loader():
        raise ValueError("leaked-secret-token-abc123")

    _patch_eps(monkeypatch, _FakeEP("toy", _leaky_loader))
    monkeypatch.delenv("FLUID_PLUGINS_ALLOWLIST", raising=False)
    monkeypatch.delenv("FLUID_PLUGINS_BLOCKLIST", raising=False)
    with pytest.raises(ResolutionError) as ei:
        resolve_bundle({"kind": "entrypoint", "name": "toy"})
    msg = str(ei.value)
    assert "ValueError" in msg  # type surfaced
    assert "leaked-secret-token-abc123" not in msg  # raw message NOT surfaced
