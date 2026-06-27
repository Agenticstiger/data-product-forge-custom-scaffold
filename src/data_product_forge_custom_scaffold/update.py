"""``fluid custom-scaffold --update`` — re-render an evolved template and
3-way-merge it against the user's working tree (copier's update model).

The lockfile records the commit a project was generated from plus its variables.
To update, we render the template TWICE in memory — at the **locked** commit
(the baseline the user started from) and at the **new** target ref — and then,
per file, run a 3-way merge:

    base   = the old render (what was originally generated)
    ours   = the file currently on disk (the user's edits)
    theirs = the new render (the evolved template)

Non-overlapping template and user changes merge cleanly; overlapping ones get
Git-style conflict markers for the user to resolve. The merge itself is delegated
to ``git merge-file`` — git's own 3-way engine — rather than hand-rolled.
"""

from __future__ import annotations

import base64
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping

# Conflict-marker labels (shown in the <<<<<<< / >>>>>>> blocks).
_L_OURS = "current (your edits)"
_L_BASE = "base (locked template)"
_L_THEIRS = "new (updated template)"


class UpdateError(RuntimeError):
    """Raised when an update cannot proceed (e.g. no lockfile)."""


@dataclass(frozen=True)
class FileUpdate:
    """The outcome for one file in an update."""

    path: str
    status: str  # added | merged | conflict | unchanged | removed-upstream


@dataclass
class UpdateResult:
    """What an update produced."""

    files: List[FileUpdate] = field(default_factory=list)
    applied: bool = True  # False for a dry-run

    @property
    def conflicts(self) -> List[FileUpdate]:
        return [f for f in self.files if f.status == "conflict"]

    @property
    def changed(self) -> List[FileUpdate]:
        return [f for f in self.files if f.status in ("added", "merged", "conflict")]


def three_way_merge(base: bytes, ours: bytes, theirs: bytes) -> tuple[bytes, bool]:
    """Three-way merge via ``git merge-file``. Returns (merged, clean).

    ``clean`` is False when conflict markers were written. Binary-safe (operates
    on bytes through temp files).
    """
    if ours == theirs:
        return ours, True
    if base == theirs:
        # Template didn't change this file — keep the user's version verbatim.
        return ours, True
    if base == ours:
        # User didn't touch this file — take the template's new version.
        return theirs, True
    with tempfile.TemporaryDirectory() as d:
        dp = Path(d)
        op, bp, tp = dp / "ours", dp / "base", dp / "theirs"
        op.write_bytes(ours)
        bp.write_bytes(base)
        tp.write_bytes(theirs)
        proc = subprocess.run(
            [
                "git",
                "merge-file",
                "-p",
                "-L",
                _L_OURS,
                "-L",
                _L_BASE,
                "-L",
                _L_THEIRS,
                str(op),
                str(bp),
                str(tp),
            ],
            capture_output=True,
        )
        # git merge-file: rc 0 = clean, rc > 0 = number of conflicts, rc < 0 = error.
        if proc.returncode < 0:
            raise UpdateError(
                f"git merge-file failed: {proc.stderr.decode('utf-8', 'replace').strip()}"
            )
        return proc.stdout, proc.returncode == 0


def merge_renders(
    *,
    old: Mapping[str, bytes],
    new: Mapping[str, bytes],
    output_root: Path,
    dry_run: bool = False,
) -> UpdateResult:
    """Merge the ``new`` render onto the working tree, using ``old`` as the base.

    ``old`` / ``new`` map relative path -> rendered bytes (e.g. from the engine's
    plan at the locked commit and at the target ref). The current on-disk files
    under ``output_root`` are the user's edits.
    """
    output_root = Path(output_root)
    result = UpdateResult(applied=not dry_run)

    for path in sorted(set(old) | set(new)):
        dest = output_root / path
        on_disk = dest.read_bytes() if dest.is_file() else None

        if path in new and path not in old:
            # Brand-new template file. Merge against an empty base so a
            # pre-existing user file (if any) is reconciled rather than clobbered.
            base = b""
            theirs = new[path]
            ours = on_disk if on_disk is not None else b""
            merged, clean = three_way_merge(base, ours, theirs)
            status = "added" if on_disk is None else ("merged" if clean else "conflict")
        elif path in old and path not in new:
            # Template dropped this file. Leave the user's copy in place (safer
            # than deleting) and report it.
            result.files.append(FileUpdate(path=path, status="removed-upstream"))
            continue
        else:
            base = old[path]
            theirs = new[path]
            ours = on_disk if on_disk is not None else base
            merged, clean = three_way_merge(base, ours, theirs)
            if merged == ours:
                result.files.append(FileUpdate(path=path, status="unchanged"))
                continue
            status = "merged" if clean else "conflict"

        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(merged)
        result.files.append(FileUpdate(path=path, status=status))

    return result


def actions_to_render(actions: List[Mapping]) -> Dict[str, bytes]:
    """Project ``write_file`` plan actions to a path -> content map.

    Handles the SDK action shape ``{op: write_file, params: {path,
    content_b64}}`` (content base64-encoded) as well as a flat ``{path,
    content}`` shape, so callers can pass either real plan output or a simple
    fixture.
    """
    out: Dict[str, bytes] = {}
    for a in actions:
        if a.get("op") not in (None, "write_file"):
            continue
        params = a.get("params") or {}
        path = params.get("path") or a.get("path") or a.get("resource_id")
        if not path:
            continue
        if "content_b64" in params:
            content: bytes = base64.b64decode(params["content_b64"])
        else:
            raw = params.get("content", a.get("content", b""))
            content = raw.encode("utf-8") if isinstance(raw, str) else raw
        out[str(path)] = content
    return out
