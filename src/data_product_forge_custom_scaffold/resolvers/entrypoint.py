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

import os
from pathlib import Path
from typing import Any, Mapping, Optional

from .base import ResolutionError, ResolvedBundle, Resolver

ENTRY_POINT_GROUP = "fluid_build.custom_scaffolds"


def _plugin_allowed(name: str) -> bool:
    """Mirror the FLUID CLI's operator allow/block policy (blocklist wins).

    ``FLUID_PLUGINS_BLOCKLIST`` (comma-separated entry-point names) is always
    honoured; if ``FLUID_PLUGINS_ALLOWLIST`` is set, only listed names load. This
    is the SAME policy the CLI's ``fluid_build.plugin_manager.is_allowed`` enforces
    for providers / validators / catalog / iac plugins — replicated here (zero
    dependency on the CLI) so the scaffold engine's ``fluid_build.custom_scaffolds``
    plugins are governed by the same trust boundary instead of bypassing it.
    """
    block = {
        x.strip() for x in os.environ.get("FLUID_PLUGINS_BLOCKLIST", "").split(",") if x.strip()
    }
    if name in block:
        return False
    allow = {
        x.strip() for x in os.environ.get("FLUID_PLUGINS_ALLOWLIST", "").split(",") if x.strip()
    }
    if allow and name not in allow:
        return False
    return True


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

        # Operator allow/block gate — enforce BEFORE ep.load() so a blocked
        # plugin's code never executes (the same trust boundary the CLI applies).
        if not _plugin_allowed(name):
            raise ResolutionError(
                f"plugin {name!r} is blocked by the operator allow/block policy "
                f"(FLUID_PLUGINS_ALLOWLIST / FLUID_PLUGINS_BLOCKLIST)"
            )

        try:
            plugin_class = ep.load()
        except Exception as e:
            # Type only — an arbitrary load exception's text may carry secrets.
            raise ResolutionError(f"failed to load entry-point {name!r}: {type(e).__name__}") from e

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
