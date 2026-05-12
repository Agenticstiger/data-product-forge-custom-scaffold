"""Bundle resolvers.

A *resolver* materialises a bundle (a directory containing
``fluid-scaffold.yaml`` + ``templates/`` + optional ``static/``, OR a
Python class loaded via entry-points) and returns a
:class:`ResolvedBundle`. Three source kinds ship today:

* ``path`` — bundle already on disk; relative paths anchor to the
  contract directory, not the invoking process's cwd. Used for local
  development and monorepos.
* ``git`` — clones the repo into the user's cache. Used for shared
  bundles distributed via a git repo (no PyPI / npm release needed).
* ``entrypoint`` — loads a :class:`fluid_sdk.CustomScaffold` subclass
  from any installed Python package that registered itself via the
  ``fluid_build.custom_scaffolds`` entry-point group. Used when a bundle
  needs programmatic control (external API calls, complex contract
  inspection, conditional logic Jinja can't express).

Public entrypoint::

    from data_product_forge_custom_scaffold.resolvers import resolve_bundle
    bundle_root = resolve_bundle({"kind": "path", "path": "./my-bundle"})
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional

from .base import ResolutionError, ResolvedBundle
from .entrypoint import EntryPointResolver
from .git import GitResolver
from .path import PathResolver

_RESOLVERS = {
    "path": PathResolver(),
    "git": GitResolver(),
    "entrypoint": EntryPointResolver(),
}


def resolve_bundle(
    source: Mapping[str, Any],
    *,
    contract_dir: Optional[Path] = None,
) -> ResolvedBundle:
    """Dispatch to the right resolver based on ``source.kind``."""
    if not isinstance(source, Mapping):
        raise ResolutionError(f"source must be a mapping, got {type(source).__name__}")
    kind = source.get("kind")
    if not kind:
        raise ResolutionError("source missing required 'kind'")
    resolver = _RESOLVERS.get(kind)
    if resolver is None:
        known = ", ".join(sorted(_RESOLVERS))
        raise ResolutionError(f"unknown source kind {kind!r} (known: {known})")
    return resolver.resolve(source, contract_dir=contract_dir)


__all__ = [
    "resolve_bundle",
    "ResolvedBundle",
    "ResolutionError",
    "PathResolver",
    "GitResolver",
    "EntryPointResolver",
]
