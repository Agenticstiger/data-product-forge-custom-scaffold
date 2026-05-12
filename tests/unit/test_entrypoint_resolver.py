"""Regression tests for the entry-point resolver and engine dispatch
to Python plugins."""

from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# A minimal in-process plugin for tests. Registered manually with the
# resolver rather than via importlib.metadata (which would require an
# installed distribution).
# ---------------------------------------------------------------------------
from fluid_sdk import CustomScaffold, write_file_action

from data_product_forge_custom_scaffold.engine import Engine
from data_product_forge_custom_scaffold.resolvers import ResolutionError, resolve_bundle
from data_product_forge_custom_scaffold.resolvers.entrypoint import EntryPointResolver


class _ToyPlugin(CustomScaffold):
    """Plugin that emits one file derived from the contract id."""

    name = "toy-plugin"

    def __init__(self, *, output_root=None, pattern_name=None, variables=None, **kwargs):
        super().__init__(output_root=output_root, **kwargs)
        self.pattern_name = pattern_name
        self.variables = dict(variables or {})

    def plan(self, contract):
        pid = (contract or {}).get("id", "unknown")
        greeting = (self.variables or {}).get("greeting", "Hello")
        return [
            write_file_action(
                path=f"plugin-output/{pid}.txt",
                content=f"{greeting} from {self.name}!\n".encode("utf-8"),
                description="plugin-generated",
            ).to_dict(),
        ]


# ---------------------------------------------------------------------------
# Entry-point resolver unit tests (don't need a real entry-point)
# ---------------------------------------------------------------------------


def test_entrypoint_resolver_rejects_missing_name() -> None:
    with pytest.raises(ResolutionError, match="missing required 'name'"):
        resolve_bundle({"kind": "entrypoint"})


def test_entrypoint_resolver_rejects_unknown_name() -> None:
    with pytest.raises(ResolutionError, match="no plugin named"):
        resolve_bundle({"kind": "entrypoint", "name": "nonexistent-plugin-12345-xyz"})


def test_entrypoint_resolver_lists_known_plugins_on_miss() -> None:
    """The error message should help the user diagnose typos."""
    with pytest.raises(ResolutionError) as exc_info:
        resolve_bundle({"kind": "entrypoint", "name": "definitely-not-installed"})
    # The error names the entry-point group so the user knows where to register.
    assert "fluid_build.custom_scaffolds" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Engine dispatch to plugins — uses a monkey-patched resolver since
# registering a real entry-point requires an installed distribution.
# ---------------------------------------------------------------------------


def test_engine_dispatches_to_python_plugin(monkeypatch, tmp_path: Path) -> None:
    """When the resolver returns a ResolvedBundle with plugin_class set,
    the engine instantiates that class instead of TemplatedCustomScaffold."""
    from data_product_forge_custom_scaffold.resolvers import base

    # Stub the EntryPointResolver to return our toy plugin.
    def fake_resolve(self, source, *, contract_dir=None):
        return base.ResolvedBundle(
            kind="entrypoint",
            bundle_root=None,
            plugin_class=_ToyPlugin,
            resolved_version="test",
            mirror_url=None,
            extra={"entry_point_name": source.get("name", "?")},
        )

    monkeypatch.setattr(EntryPointResolver, "resolve", fake_resolve)

    contract = {
        "id": "my-plugin-product",
        "name": "Plugin Test Product",
        "extensions": {
            "customScaffold": {
                "libraries": [
                    {"id": "plug", "source": {"kind": "entrypoint", "name": "toy-plugin"}}
                ],
                "patterns": [{"use": "plug:default", "variables": {"greeting": "Howdy"}}],
            }
        },
    }

    out = tmp_path / "out"
    out.mkdir()
    engine = Engine(output_root=out, contract_dir=tmp_path)
    result = engine.run(contract)

    assert result.apply_result is not None
    assert result.apply_result.applied == 1
    written = out / "plugin-output" / "my-plugin-product.txt"
    assert written.is_file()
    assert written.read_text() == "Howdy from toy-plugin!\n"


def test_engine_passes_variables_to_plugin(monkeypatch, tmp_path: Path) -> None:
    """The plugin's __init__ receives the contract's variables block."""
    from data_product_forge_custom_scaffold.resolvers import base

    monkeypatch.setattr(
        EntryPointResolver,
        "resolve",
        lambda self, src, *, contract_dir=None: base.ResolvedBundle(
            kind="entrypoint", plugin_class=_ToyPlugin, resolved_version="t"
        ),
    )

    contract = {
        "id": "var-product",
        "extensions": {
            "customScaffold": {
                "libraries": [{"id": "p", "source": {"kind": "entrypoint", "name": "x"}}],
                "patterns": [{"use": "p:default", "variables": {"greeting": "Bonjour"}}],
            }
        },
    }
    out = tmp_path / "out"
    out.mkdir()
    Engine(output_root=out, contract_dir=tmp_path).run(contract)

    written = (out / "plugin-output" / "var-product.txt").read_text()
    assert written.startswith("Bonjour from")


def test_engine_handles_plugin_without_explicit_kwargs(monkeypatch, tmp_path: Path) -> None:
    """A plugin whose __init__ doesn't accept pattern_name/variables
    explicitly still works — the engine retries without them."""

    class _LegacyPlugin(CustomScaffold):
        name = "legacy-plugin"

        # No pattern_name/variables kwargs.
        def __init__(self, *, output_root=None, **kwargs):
            super().__init__(output_root=output_root, **kwargs)

        def plan(self, contract):
            return [write_file_action(path="legacy.txt", content=b"hi").to_dict()]

    from data_product_forge_custom_scaffold.resolvers import base

    monkeypatch.setattr(
        EntryPointResolver,
        "resolve",
        lambda self, src, *, contract_dir=None: base.ResolvedBundle(
            kind="entrypoint", plugin_class=_LegacyPlugin, resolved_version="t"
        ),
    )

    contract = {
        "id": "legacy-product",
        "extensions": {
            "customScaffold": {
                "libraries": [{"id": "p", "source": {"kind": "entrypoint", "name": "x"}}],
                "patterns": [{"use": "p:default"}],
            }
        },
    }
    out = tmp_path / "out"
    out.mkdir()
    result = Engine(output_root=out, contract_dir=tmp_path).run(contract)
    assert result.apply_result.applied == 1


# ---------------------------------------------------------------------------
# Validation surfaces — confirm entrypoint kind passes validation
# ---------------------------------------------------------------------------


def test_validation_accepts_entrypoint_source_kind() -> None:
    """The customScaffold JSON-Schema must list 'entrypoint' as a valid
    source.kind value alongside path/git."""
    from data_product_forge_custom_scaffold.validation import validate

    errors = []
    validate(
        {
            "customScaffold": {
                "libraries": [{"id": "p", "source": {"kind": "entrypoint", "name": "my-plugin"}}],
                "patterns": [{"use": "p:default"}],
            }
        },
        errors,
    )
    # Any errors should NOT be about 'entrypoint' being an unknown kind.
    for err in errors:
        assert (
            "entrypoint" not in err or "is not one of" not in err
        ), f"validation rejected 'entrypoint' source kind: {err}"
