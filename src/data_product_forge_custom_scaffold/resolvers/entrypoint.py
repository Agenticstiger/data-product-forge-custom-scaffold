"""Entry-point resolver — Python-plugin bundles registered via
``importlib.metadata``.

Source shape::

    {
      "kind": "entrypoint",
      "name": "my-org-scaffold"          # entry-point name registered in
                                          # the fluid_build.custom_scaffolds group
    }

Any Python package can expose a :class:`fluid_sdk.CustomScaffold` subclass
by declaring an entry-point in its ``pyproject.toml``::

    [project.entry-points."fluid_build.custom_scaffolds"]
    my-org-scaffold = "my_pkg.scaffold:MyOrgScaffold"

When a contract references such a plugin via ``source: { kind: entrypoint,
name: my-org-scaffold }``, this resolver loads the class and returns a
:class:`ResolvedBundle` carrying it. The engine then instantiates the
class directly rather than the built-in :class:`TemplatedCustomScaffold`.

No filesystem bundle is needed — the plugin's ``plan(contract)`` method
produces actions purely from contract data (and whatever the plugin
author imports internally).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional

from .base import ResolutionError, ResolvedBundle, Resolver

ENTRY_POINT_GROUP = "fluid_build.custom_scaffolds"


class EntryPointResolver(Resolver):
    kind = "entrypoint"

    def resolve(
        self,
        source: Mapping[str, Any],
        *,
        contract_dir: Optional[Path] = None,
    ) -> ResolvedBundle:
        name = source.get("name")
        if not name or not isinstance(name, str):
            raise ResolutionError(
                f"entrypoint source missing required 'name' "
                f"(should match an entry-point declared in the "
                f"{ENTRY_POINT_GROUP!r} group)"
            )

        try:
            import importlib.metadata as _md
        except ImportError as e:
            raise ResolutionError(
                "entrypoint resolver requires importlib.metadata (Python 3.8+)"
            ) from e

        try:
            eps = _md.entry_points(group=ENTRY_POINT_GROUP)
        except TypeError:
            # Python < 3.10 — entry_points() returns a dict of groups
            eps = _md.entry_points().get(ENTRY_POINT_GROUP, [])

        matches = [ep for ep in eps if ep.name == name]
        if not matches:
            available = sorted(ep.name for ep in eps)
            raise ResolutionError(
                f"no plugin named {name!r} found under entry-point group "
                f"{ENTRY_POINT_GROUP!r}. Install the package that provides it, "
                f"then re-run. Available plugins: {available or '(none installed)'}"
            )
        if len(matches) > 1:
            # Two installed packages claimed the same name — refuse rather
            # than guess which one the user meant.
            raise ResolutionError(
                f"plugin name {name!r} is claimed by multiple installed "
                f"distributions: {[str(ep) for ep in matches]}"
            )

        ep = matches[0]
        try:
            plugin_class = ep.load()
        except Exception as e:
            raise ResolutionError(f"failed to load entry-point {name!r}: {e}") from e

        # Best-effort: capture the version of the providing distribution
        # for the lockfile / reporting.
        version = ""
        try:
            dist = getattr(ep, "dist", None)
            if dist is not None and hasattr(dist, "version"):
                version = str(dist.version)
        except Exception:
            pass

        return ResolvedBundle(
            kind=self.kind,
            bundle_root=None,
            plugin_class=plugin_class,
            resolved_version=version or "unknown",
            mirror_url=None,
            extra={"entry_point_name": name, "module": getattr(ep, "value", "")},
        )
