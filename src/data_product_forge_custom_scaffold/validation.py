"""Validator + schema provider for ``contract.extensions.<dialect.extension_key>``.

By default these serve the built-in fluid dialect (``customScaffold``):

* :data:`validate` is registered as ``fluid_build.extension_validators / customScaffold``.
  The forge CLI calls it with the contract's ``extensions`` block; it checks the
  sub-block against the shipped JSON-Schema and appends any errors to the
  caller-supplied list.
* :func:`get_extension_schema` is registered as ``fluid_build.extension_schemas /
  customScaffold`` so the forge CLI copilot can ground contract generation on the
  schema and validate generated blocks (see the SDK's ``iter_extension_schemas``).

The fluid core schema treats ``extensions:`` as ``additionalProperties: true`` — it
doesn't know the shape of the customScaffold sub-block. These callables fill that
gap. White-label packages bind :func:`make_validator` / :func:`make_schema_provider`
to their own :class:`~data_product_forge_custom_scaffold.dialect.ScaffoldDialect`
and register them under their own entry-point names.

The JSON-Schema itself ships as package data (``schemas/custom-scaffold.v1.json``)
and is loaded here via :func:`load_schema` as the single source of truth.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Any, Callable, Dict, List, Mapping, Optional

from .dialect import DEFAULT as DEFAULT_DIALECT
from .dialect import ScaffoldDialect

_SCHEMA_RESOURCE = "custom-scaffold.v1.json"


@lru_cache(maxsize=1)
def load_schema() -> Dict[str, Any]:
    """Load the customScaffold JSON-Schema from the shipped package resource.

    The schema is shipped as package data (``schemas/custom-scaffold.v1.json``)
    and is the single source of truth. Addressed via the parent package so it
    resolves whether the package is installed as a directory or inside a
    wheel/zip (``importlib.resources``).
    """
    text = (
        resources.files("data_product_forge_custom_scaffold")
        .joinpath("schemas", _SCHEMA_RESOURCE)
        .read_text(encoding="utf-8")
    )
    return json.loads(text)


# Back-compat module constant — the loaded schema dict. Existing imports of
# ``CUSTOM_SCAFFOLD_SCHEMA`` keep working unchanged.
CUSTOM_SCAFFOLD_SCHEMA: Dict[str, Any] = load_schema()


def make_schema_provider(
    dialect: ScaffoldDialect = DEFAULT_DIALECT,
) -> Callable[[Optional[str]], Dict[str, Any]]:
    """Build an extension-schema provider callable bound to *dialect*.

    The returned callable has the ``fluid_build.extension_schemas`` provider
    signature ``provider(fluid_version=None) -> dict`` and yields the shipped
    schema with its ``title`` set to ``extensions.<dialect.extension_key>``.
    Mirrors ``validate-pyproject``'s ``tool_schema`` convention: the schema
    describes the data *under* the extension key (it does not wrap the key).
    """

    def _provider(fluid_version: Optional[str] = None) -> Dict[str, Any]:
        schema = dict(load_schema())  # shallow copy; only the top-level title differs
        schema["title"] = dialect.schema_title
        return schema

    return _provider


def get_extension_schema(fluid_version: Optional[str] = None) -> Dict[str, Any]:
    """Return the JSON-Schema for the built-in ``customScaffold`` extension block.

    Registered as ``fluid_build.extension_schemas / customScaffold``. The forge
    CLI copilot enumerates that group to ground contract generation and to
    validate generated extension blocks. ``fluid_version`` is accepted for
    forward-compatibility (the schema is version-independent today).
    """
    return make_schema_provider(DEFAULT_DIALECT)(fluid_version)


def make_validator(
    dialect: ScaffoldDialect = DEFAULT_DIALECT,
) -> Callable[[Mapping[str, Any], List[str]], None]:
    """Build an extension-validator callable bound to *dialect*.

    Returns a function with the forge CLI's required signature
    ``validate(extensions, errors) -> None`` that reads
    ``extensions[dialect.extension_key]`` and prefixes diagnostics with the
    dialect's branding. No-op when the sub-block is absent (the engine is
    opt-in).
    """
    key = dialect.extension_key

    def _validate(extensions: Mapping[str, Any], errors: List[str]) -> None:
        if not isinstance(extensions, Mapping):
            return
        block = extensions.get(key)
        if block is None:
            return  # not opted in

        # Lazy import: keep jsonschema OFF the `fluid --help` / plugin-registration
        # path. forge-cli eagerly loads every `fluid_build.commands` registrar to
        # build the parser, and its startup-budget guard
        # (tests/perf/test_startup_budget.py) forbids importing jsonschema there.
        # It is only needed when a customScaffold block is actually validated.
        try:
            from jsonschema import Draft7Validator
        except ImportError:
            Draft7Validator = None  # type: ignore[assignment]  # noqa: N806

        if Draft7Validator is None:
            errors.append(
                f"{dialect.error_prefix}: validation requires the "
                f"'jsonschema' package; install via `pip install jsonschema`."
            )
            return

        validator = Draft7Validator(CUSTOM_SCAFFOLD_SCHEMA)
        for err in sorted(validator.iter_errors(block), key=lambda e: e.path):
            path = ".".join(str(p) for p in err.path) or "<root>"
            errors.append(f"extensions.{key}.{path}: {err.message}")

        # Cross-reference checks: every pattern's `use` must resolve to a
        # declared library id.
        libraries_by_id = {
            lib["id"]
            for lib in (block.get("libraries") or [])
            if isinstance(lib, Mapping) and isinstance(lib.get("id"), str)
        }
        for i, pat in enumerate(block.get("patterns") or []):
            if not isinstance(pat, Mapping):
                continue
            use = pat.get("use", "")
            if ":" not in use:
                continue
            lib_id, _ = use.split(":", 1)
            if lib_id not in libraries_by_id:
                errors.append(
                    f"extensions.{key}.patterns[{i}].use references "
                    f"unknown library id {lib_id!r} "
                    f"(known: {sorted(libraries_by_id) or '(none declared)'})"
                )

    return _validate


# Default validator bound to the built-in fluid dialect.
_default_validator = make_validator(DEFAULT_DIALECT)


def validate(extensions: Mapping[str, Any], errors: List[str]) -> None:
    """Validate ``extensions.customScaffold`` and append errors to *errors*.

    Registered as ``fluid_build.extension_validators / customScaffold``; called
    by the forge CLI's contract-validation orchestrator when our entry point is
    loaded. Thin wrapper around :func:`make_validator` bound to the default
    dialect, kept as a module-level function for stable introspection and
    back-compat.

    No-op when the customScaffold sub-block is absent (the engine is opt-in).
    """
    _default_validator(extensions, errors)
