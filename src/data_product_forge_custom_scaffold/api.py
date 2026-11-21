"""Library entry point: generate a project from a contract with a function call.

    from data_product_forge_custom_scaffold import generate

    result = generate(contract, "build/scaffold", contract_dir="path/to/contract")
    for f in result.files:
        print(f.path, f.sha256, f.size)

``fluid custom-scaffold`` is a thin wrapper over :func:`generate`, so a host that
needs the written files (to record them in a manifest, say) gets exactly what the
command produces — without printing, exiting, or parsing ``--json``.

Design notes, with the prior art each follows:

* The result row is ``(path, sha256, size)`` — the shape of a wheel ``RECORD`` row
  (PEP 376 / 427) — with the digest as lowercase hex, as the SDK and the OCI
  descriptor spec write it. copier's ``run_copy`` returns a ``Worker`` and
  cookiecutter returns a path; neither reports the files it wrote, which is why
  this module defines its own record.
* ``dry_run`` plans and checks without writing, like copier's ``pretend``.
* Destination paths are confined by :mod:`.confinement`; failures are the typed
  hierarchy in :mod:`.errors`.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple, Union

from fluid_sdk import ExecutionResult

from .confinement import confine
from .dialect import DEFAULT as DEFAULT_DIALECT
from .dialect import ScaffoldDialect
from .engine import Engine, EngineRunResult
from .errors import ApplyError, PlanError
from .lockfile import LOCKFILE_NAME

__all__ = ["generate", "GenerationResult", "GeneratedFile"]

PathLike = Union[str, "os.PathLike[str]"]

_READ_CHUNK = 1 << 16
# Cap on failed paths quoted in an ApplyError message (the full list stays in
# ``error.run.apply_result.results``).
_MAX_LISTED = 10


@dataclass(frozen=True)
class GeneratedFile:
    """One file in the output tree: where it is and what it holds.

    Attributes:
        path: location relative to ``out_dir``, with ``/`` separators.
        sha256: lowercase hex SHA-256 of the file's bytes.
        size: length of the file in bytes.
    """

    path: str
    sha256: str
    size: int


@dataclass(frozen=True)
class GenerationResult:
    """What :func:`generate` produced.

    Attributes:
        files: every file the run left in ``out_dir``, sorted by ``path``. Scaffold
            files plus the reproducibility lock (``fluid-scaffold.lock``), so the
            list accounts for the whole generated tree. On a dry run it is what a
            real run would write.
        dry_run: whether nothing was written.
        run: the underlying :class:`~data_product_forge_custom_scaffold.engine.EngineRunResult`
            (planned actions, the SDK's apply summary, resolved libraries and their
            versions) for a caller that needs per-action detail. Not part of
            equality or ``repr``.
    """

    files: Tuple[GeneratedFile, ...]
    dry_run: bool
    run: EngineRunResult = field(compare=False, repr=False)


def generate(
    contract: Mapping[str, Any],
    out_dir: PathLike,
    *,
    contract_dir: Optional[PathLike] = None,
    dry_run: bool = False,
    pattern_filter: Optional[Sequence[str]] = None,
    library_filter: Optional[Sequence[str]] = None,
    pin: bool = False,
    dialect: ScaffoldDialect = DEFAULT_DIALECT,
) -> GenerationResult:
    """Generate the files a contract's ``extensions.customScaffold`` block describes.

    Runs the pipeline ``fluid custom-scaffold`` runs — resolve each library
    (``path``, ``git`` or ``entrypoint``), render each pattern, write the files,
    record ``fluid-scaffold.lock`` — and returns what is now in *out_dir*. It never
    prints, exits, or reads ``sys.argv``.

    Args:
        contract: the loaded contract. Validate it first (``fluid validate``, or
            :func:`~data_product_forge_custom_scaffold.make_validator`). A contract
            with no ``extensions.customScaffold`` block is a no-op: no files.
        out_dir: the output root. Created on demand; a dry run does not create it.
        contract_dir: the directory a relative ``path:`` source is resolved against.
            Defaults to the current working directory, so pass the contract file's
            directory for a result that does not depend on where the process runs.
        dry_run: plan and check, write nothing. ``files`` is then what a real run
            would write, with the digests of the planned bytes.
        pattern_filter: only run patterns whose ``use:`` value is listed.
        library_filter: only resolve libraries whose id is listed.
        pin: resolve git sources to the commit recorded in an existing lock rather
            than following the contract's ``ref``.
        dialect: a white-label dialect; the default is the built-in fluid one.

    Returns:
        A :class:`GenerationResult`. ``files`` lists each :class:`GeneratedFile`
        sorted by path — independent of pattern order, filesystem order and
        platform. A path two patterns both write is listed once, describing the
        later pattern's bytes. After a real run each digest is computed from the
        file on disk, so it describes the bytes actually written.

    Raises:
        ContractError: a pattern names a library the block does not declare.
        BundleError: a library does not resolve, its manifest is invalid, it has no
            such pattern, or its plugin class fails to initialise.
        PlanError: a pattern's ``plan()`` failed.
        UnsafePathError: a planned path would land outside *out_dir* (checked before
            anything is written, and on a dry run too).
        ApplyError: ``apply()`` failed or reported failed files. Its ``run``
            attribute holds the engine run when failures were reported.
        TypeError: *contract* is not a mapping.

    All of these derive from :class:`~data_product_forge_custom_scaffold.errors.EngineError`.
    A failure inside third-party plugin code is reported by exception type only.
    """
    if not isinstance(contract, Mapping):
        raise TypeError(f"contract must be a mapping, got {type(contract).__name__}")

    engine = Engine(
        output_root=Path(out_dir),
        contract_dir=None if contract_dir is None else Path(contract_dir),
        dialect=dialect,
    )
    run = engine.run(
        contract,
        dry_run=dry_run,
        pattern_filter=None if pattern_filter is None else list(pattern_filter),
        library_filter=None if library_filter is None else list(library_filter),
        pin=pin,
    )

    applied = run.apply_result
    if applied is not None and applied.failed > 0:
        raise ApplyError(_failed_message(applied), run=run)

    files = _describe_output(run, engine.output_root, dry_run=dry_run)
    return GenerationResult(files=files, dry_run=dry_run, run=run)


def _failed_message(applied: ExecutionResult) -> str:
    """Summarise failed files by path. Per-action error text stays on ``run``."""
    failed = sorted(
        str(r.get("resource_id") or r.get("path") or "?")
        for r in applied.results
        if r.get("status") == "failed"
    )
    listed = ", ".join(failed[:_MAX_LISTED])
    if len(failed) > _MAX_LISTED:
        listed += f" (+{len(failed) - _MAX_LISTED} more)"
    detail = f": {listed}" if failed else ""
    return (
        f"apply failed for {applied.failed} of {applied.applied + applied.failed} file(s){detail}"
    )


def _describe_output(
    run: EngineRunResult, out_root: Path, *, dry_run: bool
) -> Tuple[GeneratedFile, ...]:
    """Build the sorted file list: planned bytes on a dry run, disk bytes otherwise."""
    planned = _planned_content(run.actions, out_root)
    entries: Dict[str, GeneratedFile] = {}
    if dry_run:
        for rel, data in planned.items():
            entries[rel] = _describe(rel, data)
        if run.lock_text is not None:
            entries[LOCKFILE_NAME] = _describe(LOCKFILE_NAME, run.lock_text.encode("utf-8"))
    else:
        for rel in planned:
            entries[rel] = _read_back(out_root, rel)
        if run.lockfile is not None:
            entries[LOCKFILE_NAME] = _read_back(out_root, LOCKFILE_NAME)
    return tuple(entries[rel] for rel in sorted(entries))


def _planned_content(actions: Iterable[Mapping[str, Any]], out_root: Path) -> Dict[str, bytes]:
    """Map each confined, normalised ``write_file`` path to its planned bytes.

    The later of two actions that name the same file wins, as it does on disk.
    """
    planned: Dict[str, bytes] = {}
    for action in actions:
        if action.get("op") != "write_file":
            continue
        params = action.get("params") or {}
        content = params.get("content_b64")
        if not isinstance(content, str):
            raise PlanError("a planned write_file action has no usable content")
        try:
            data = base64.b64decode(content)
        except (binascii.Error, ValueError):
            raise PlanError("a planned write_file action has content that is not base64") from None
        planned[confine(out_root, params.get("path"))] = data
    return planned


def _describe(rel: str, data: bytes) -> GeneratedFile:
    return GeneratedFile(path=rel, sha256=hashlib.sha256(data).hexdigest(), size=len(data))


def _read_back(out_root: Path, rel: str) -> GeneratedFile:
    """Hash a file as it is on disk (chunked; the size is the bytes actually read)."""
    digest = hashlib.sha256()
    size = 0
    try:
        with (out_root / rel).open("rb") as handle:
            while True:
                block = handle.read(_READ_CHUNK)
                if not block:
                    break
                digest.update(block)
                size += len(block)
    except OSError as e:
        raise ApplyError(
            f"{rel!r} was planned but could not be read back after apply: {type(e).__name__}"
        ) from e
    return GeneratedFile(path=rel, sha256=digest.hexdigest(), size=size)
