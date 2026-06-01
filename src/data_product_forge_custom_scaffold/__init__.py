"""data-product-forge-custom-scaffold — turns a fluid contract into a
generated project scaffold.

Public objects (the engine is mostly consumed via the FLUID CLI subcommand
``fluid generate custom-scaffold``; these exports are for programmatic use):

* :class:`Engine` — top-level orchestrator
* :class:`BundleManifest` — parsed ``fluid-scaffold.yaml``
* :class:`TemplatedCustomScaffold` — built-in CustomScaffold subclass
  for YAML/Jinja bundles
* :func:`resolve_bundle` — fetch a bundle from path or git

White-label ("custom spec dialect") primitives — build a branded engine that
reuses this one under your own ``extensions.<key>`` / manifest ``apiVersion`` /
subcommand, then register the bound callables under the ``fluid_build.*`` groups
with your own entry-point names:

* :class:`ScaffoldDialect` + :data:`DEFAULT_DIALECT`
* :func:`make_register` — a ``fluid generate <command>`` registrar bound to a dialect
* :func:`make_validator` — a ``fluid_build.extension_validators`` callable bound to a dialect
* :func:`make_schema_provider` / :func:`get_extension_schema` — a
  ``fluid_build.extension_schemas`` provider so the ``fluid forge`` copilot can
  natively generate + validate the extension block
* :func:`load_schema` — the shipped customScaffold JSON-Schema
"""

from __future__ import annotations

from .cli import make_register, register
from .dialect import DEFAULT as DEFAULT_DIALECT
from .dialect import ScaffoldDialect
from .engine import Engine
from .manifest import BundleManifest, PatternEntry, TemplateEntry
from .resolvers import resolve_bundle
from .templated import TemplatedCustomScaffold
from .validation import (
    CUSTOM_SCAFFOLD_SCHEMA,
    get_extension_schema,
    load_schema,
    make_schema_provider,
    make_validator,
    validate,
)
from .version import ENGINE_VERSION

__version__ = ENGINE_VERSION

__all__ = [
    # Core engine
    "Engine",
    "BundleManifest",
    "PatternEntry",
    "TemplateEntry",
    "TemplatedCustomScaffold",
    "resolve_bundle",
    "ENGINE_VERSION",
    # White-label dialect primitives
    "ScaffoldDialect",
    "DEFAULT_DIALECT",
    "make_register",
    "register",
    "make_validator",
    "validate",
    "make_schema_provider",
    "get_extension_schema",
    "load_schema",
    "CUSTOM_SCAFFOLD_SCHEMA",
]
