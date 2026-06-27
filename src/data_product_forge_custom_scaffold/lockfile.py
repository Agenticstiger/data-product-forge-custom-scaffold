"""``fluid-scaffold.lock`` — a record of what generated an output tree.

Reproducibility, borrowed from copier's ``.copier-answers.yml`` model: the file
records, per resolved library, the *exact commit* it resolved to (alongside the
floating ``ref`` the contract asked for) plus the patterns and variables used.
A later run can then re-pin a moving ``ref`` to the locked commit instead of
silently drifting, and tooling can diff the locked commit against the upstream
to report "the template moved".

The file is deterministic (sorted keys), credential-free (only the source URL,
ref, and resolved commit — never the ``auth`` block or any token), and lives at
the output root, mirroring copier's per-project answers file.

This module only *builds* and *writes* the lock; consuming it to pin a re-run is
layered on top (see the resolver/engine pin path).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping

import yaml

from .resolvers.base import ResolvedBundle

LOCKFILE_NAME = "fluid-scaffold.lock"

_HEADER = (
    "# fluid-scaffold.lock — records what generated this output tree, for\n"
    "# reproducible re-runs. Managed by data-product-forge-custom-scaffold;\n"
    "# commit it alongside the generated files. Do not edit by hand.\n"
)


@dataclass(frozen=True)
class LockedLibrary:
    """One resolved library, as recorded in the lock."""

    kind: str
    src: str  # the source location (git URL / path / entry-point dist), credential-free
    ref: str  # the ref the contract asked for (tag/branch/sha), if any
    commit: str  # the EXACT resolved commit/version — the reproducible pin
    subdir: str

    def to_dict(self) -> Dict[str, Any]:
        # Omit empty optionals so the file stays terse and stable.
        out: Dict[str, Any] = {"kind": self.kind, "src": self.src}
        if self.ref:
            out["ref"] = self.ref
        if self.commit:
            out["commit"] = self.commit
        if self.subdir:
            out["subdir"] = self.subdir
        return out


def locked_library_from_bundle(bundle: ResolvedBundle) -> LockedLibrary:
    """Project a :class:`ResolvedBundle` onto the credential-free lock shape."""
    extra = bundle.extra or {}
    return LockedLibrary(
        kind=str(bundle.kind or ""),
        src=str(bundle.mirror_url or ""),
        ref=str(extra.get("ref") or ""),
        commit=str(bundle.resolved_version or ""),
        subdir=str(extra.get("subdir") or ""),
    )


def build_lock(
    *,
    engine_version: str,
    resolved: Mapping[str, ResolvedBundle],
    patterns: List[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Build the deterministic lock document.

    ``patterns`` is the contract's ``patterns[]`` list (each with ``use`` and
    optional ``variables``); only the fields that drive generation are recorded.
    """
    libraries = {
        lib_id: locked_library_from_bundle(bundle).to_dict()
        for lib_id, bundle in sorted(resolved.items())
    }
    recorded_patterns: List[Dict[str, Any]] = []
    for decl in patterns:
        use = decl.get("use")
        if not use:
            continue
        entry: Dict[str, Any] = {"use": str(use)}
        variables = decl.get("variables") or {}
        if variables:
            entry["variables"] = dict(variables)
        recorded_patterns.append(entry)

    return {
        "lockfileVersion": 1,
        "engineVersion": str(engine_version),
        "libraries": libraries,
        "patterns": recorded_patterns,
    }


def dump_lock(lock: Mapping[str, Any]) -> str:
    """Serialise the lock to deterministic YAML (sorted keys + header)."""
    body = yaml.safe_dump(dict(lock), sort_keys=True, default_flow_style=False)
    return _HEADER + body


def write_lock(output_root: Path, lock: Mapping[str, Any]) -> Path:
    """Write the lock to ``<output_root>/fluid-scaffold.lock`` and return its path."""
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    path = output_root / LOCKFILE_NAME
    path.write_text(dump_lock(lock), encoding="utf-8")
    return path


def read_lock(output_root: Path) -> Dict[str, Any]:
    """Load an existing lock, or ``{}`` if none is present."""
    path = Path(output_root) / LOCKFILE_NAME
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def pin_source(source: Mapping[str, Any], locked: Mapping[str, Any]) -> Dict[str, Any]:
    """Return *source* with its git ref overridden by the locked commit.

    This is the reproducibility lever: a re-run with pinning resolves a git
    library to the exact commit recorded in the lock instead of following the
    floating ``ref`` (which may have moved). Only git sources with a recorded
    commit are pinned; path / entry-point sources, or a missing commit, are
    returned unchanged.
    """
    out = dict(source)
    commit = (locked or {}).get("commit")
    if out.get("kind") == "git" and commit:
        out["ref"] = str(commit)
    return out
