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

import errno
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping

import yaml

from .confinement import confine
from .errors import UnsafePathError
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


def lock_path(output_root: Path) -> Path:
    """Where the lock lives under *output_root*, refusing a symlink planted there.

    ``fluid-scaffold.lock`` is the engine's own file and is never a symlink. Writing
    through one would put the lock — which records contract variables verbatim — into
    the link's target (a file outside the output directory, or another file inside
    it), and reading through one would parse that target. Raises
    :class:`~data_product_forge_custom_scaffold.errors.UnsafePathError`.
    """
    root = Path(output_root).resolve()
    path = root / LOCKFILE_NAME
    if path.is_symlink():
        raise UnsafePathError(
            f"{LOCKFILE_NAME!r} in the output directory is a symlink; refusing to use it"
        )
    confine(root, LOCKFILE_NAME)  # and it resolves to a file inside the root
    return path


def write_lock(output_root: Path, lock: Mapping[str, Any]) -> Path:
    """Write the lock to ``<output_root>/fluid-scaffold.lock`` and return its path.

    Never writes through a symlink at that name (``UnsafePathError``): the path is
    checked first, and where the platform has ``O_NOFOLLOW`` the open itself refuses a
    link that appeared since.
    """
    path = lock_path(output_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    # A new lock is created owner-only (0o600); an existing one keeps its mode, because
    # O_TRUNC does not change it. The umask can only narrow the mode further.
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise UnsafePathError(
                f"{LOCKFILE_NAME!r} in the output directory is a symlink; refusing to use it"
            ) from None
        raise
    # newline="\n": text mode would turn "\n" into CRLF on Windows, and the lock's
    # bytes (and so its digest) must not depend on the platform.
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(dump_lock(lock))
    return path


def read_lock(output_root: Path) -> Dict[str, Any]:
    """Load an existing lock, or ``{}`` if none is present.

    Refuses a symlink at the lock's name (``UnsafePathError``), as :func:`write_lock`
    does.
    """
    path = lock_path(output_root)
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
