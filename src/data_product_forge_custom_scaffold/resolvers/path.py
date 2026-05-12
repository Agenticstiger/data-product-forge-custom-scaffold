"""Path resolver — bundle is already on disk.

Source shape::

    { "kind": "path", "path": "./relative-or-absolute-path" }

Used for:

* Local development of bundles (point at a checked-out clone).
* Internal monorepos that vendor bundles directly.
* Tests that need a synthetic bundle.

Relative paths are anchored to the **contract's directory**, not the
invoking process's cwd. This matches the natural mental model: a
contract says ``../my-bundle`` and means "the my-bundle directory
next to me," regardless of where the user happens to run
``fluid generate custom-scaffold`` from.

No network. No cache.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional

from ..manifest import MANIFEST_FILENAME
from .base import ResolutionError, ResolvedBundle, Resolver


class PathResolver(Resolver):
    kind = "path"

    def resolve(
        self,
        source: Mapping[str, Any],
        *,
        contract_dir: Optional[Path] = None,
    ) -> ResolvedBundle:
        raw_path = source.get("path")
        if not raw_path:
            raise ResolutionError("path source missing required 'path'")

        candidate = Path(str(raw_path)).expanduser()
        # Anchor relative paths to the contract directory so a contract
        # that says ``../my-bundle`` resolves predictably regardless of
        # the user's cwd when invoking the engine.
        if not candidate.is_absolute() and contract_dir is not None:
            candidate = Path(contract_dir) / candidate

        bundle_root = candidate.resolve()

        if not bundle_root.is_dir():
            raise ResolutionError(
                f"path source {raw_path!r} resolved to {bundle_root}, which is not a directory"
            )

        manifest = bundle_root / MANIFEST_FILENAME
        if not manifest.is_file():
            raise ResolutionError(f"path source {bundle_root} is missing {MANIFEST_FILENAME}")

        return ResolvedBundle(
            kind=self.kind,
            bundle_root=bundle_root,
            resolved_version="local",
            mirror_url=None,
            extra={"resolved_from": str(bundle_root)},
        )
