"""Resolver base class + shared types."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Optional


class ResolutionError(RuntimeError):
    """Raised when a bundle cannot be fetched / validated."""


@dataclass(frozen=True)
class ResolvedBundle:
    """The result of resolving a source kind.

    A resolver returns one of two flavours:

    * **YAML/Jinja bundle** — ``bundle_root`` points at a directory
      containing ``fluid-scaffold.yaml`` + ``templates/`` + optional
      ``static/``. The engine instantiates :class:`TemplatedCustomScaffold`
      pointed at this root.
    * **Python-plugin bundle** — ``plugin_class`` is set to a
      :class:`fluid_sdk.CustomScaffold` subclass loaded via
      ``importlib.metadata`` entry-points. The engine instantiates this
      class directly. ``bundle_root`` is ``None`` for this kind because
      there's no filesystem bundle to render — the plugin is the bundle.

    Fields:
        kind: The source kind that produced this bundle (``"path"``,
            ``"git"``, ``"entrypoint"``).
        bundle_root: Local filesystem path with ``fluid-scaffold.yaml``,
            or ``None`` for Python-plugin bundles.
        plugin_class: A :class:`fluid_sdk.CustomScaffold` subclass to
            instantiate directly, or ``None`` for YAML/Jinja bundles.
        resolved_version: Stable identifier (git sha, pypi version, the
            entry-point's distribution version, ``"local"`` for path).
        mirror_url: Optional canonical URL for reproducibility.
        extra: Resolver-specific metadata.
    """

    kind: str
    bundle_root: Optional[Path] = None
    plugin_class: Optional[type] = None
    resolved_version: str = ""
    mirror_url: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


class Resolver(ABC):
    """Abstract base for source-kind resolvers."""

    kind: str = ""

    @abstractmethod
    def resolve(
        self,
        source: Mapping[str, Any],
        *,
        contract_dir: Optional[Path] = None,
    ) -> ResolvedBundle:
        """Materialise the bundle from *source* and return a
        :class:`ResolvedBundle`.

        Args:
            source: The ``source:`` block from the contract.
            contract_dir: The directory containing the contract that
                referenced this source. Resolvers that interpret
                relative paths (e.g. :class:`PathResolver`) anchor them
                here instead of the invoking process's cwd.
        """


# ---------------------------------------------------------------------------
# Shared cache-directory helper
# ---------------------------------------------------------------------------


def cache_root() -> Path:
    """The engine's per-user cache directory.

    Default: ``~/.cache/fluid/custom-scaffold/``. Honours
    ``XDG_CACHE_HOME`` and the engine-specific override
    ``FLUID_CUSTOM_SCAFFOLD_CACHE``.
    """
    override = os.environ.get("FLUID_CUSTOM_SCAFFOLD_CACHE")
    if override:
        return Path(override).expanduser().resolve()
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg).expanduser() / "fluid" / "custom-scaffold"
    return Path.home() / ".cache" / "fluid" / "custom-scaffold"
