"""data-product-forge-custom-scaffold — turns a fluid contract into a
generated project scaffold.

Public objects (the engine is mostly consumed via the FLUID CLI subcommand
``fluid generate custom-scaffold``; these exports are for programmatic use):

* :class:`Engine` — top-level orchestrator
* :class:`BundleManifest` — parsed ``fluid-scaffold.yaml``
* :class:`TemplatedCustomScaffold` — built-in CustomScaffold subclass
  for YAML/Jinja bundles
* :func:`resolve_bundle` — fetch a bundle from path or git
"""

from __future__ import annotations

from .engine import Engine
from .manifest import BundleManifest, PatternEntry, TemplateEntry
from .resolvers import resolve_bundle
from .templated import TemplatedCustomScaffold
from .version import ENGINE_VERSION

__version__ = ENGINE_VERSION

__all__ = [
    "Engine",
    "BundleManifest",
    "PatternEntry",
    "TemplateEntry",
    "TemplatedCustomScaffold",
    "resolve_bundle",
    "ENGINE_VERSION",
]
