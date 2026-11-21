"""Output-path confinement, shared by the engine and the library entry point.

A planned file path is untrusted: a template ``to:`` is rendered from contract
values, and a Python plugin's ``plan()`` can emit anything. :func:`confine`
resolves the destination under the output root — following symlinks that already
exist — and refuses it if it lands outside, the technique copier's
``ForbiddenPathError`` check and CPython's ``tarfile`` ``data`` filter
(``OutsideDestinationError``, PEP 706) use. A purely lexical check such as
werkzeug's ``safe_join`` cannot see a symlink planted inside the output directory.

Limit, shared with those implementations: it checks the tree as it is *now*. It
cannot stop another process from swapping a directory for a symlink between the
check and the write.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from .errors import UnsafePathError


def confine(out_root: Path, raw: object) -> str:
    """Return *raw* as a POSIX path relative to *out_root*.

    *out_root* must already be resolved. *raw* is the untrusted path of a planned
    action, so any object is accepted and a non-string is refused. A relative or
    absolute path is accepted as long as it resolves to a file location strictly
    inside *out_root*. The returned path is the normalised one (``a/../b`` becomes
    ``b``), which is also the key two actions share when they write the same file.

    Raises :class:`~data_product_forge_custom_scaffold.errors.UnsafePathError` for
    anything else: an empty or non-string path, a path that names the output root
    itself, one that resolves outside it, or one the filesystem cannot resolve.
    """
    if not isinstance(raw, str) or not raw:
        raise UnsafePathError(f"output path {raw!r} is not a usable relative file path")
    try:
        rel = (out_root / raw).resolve().relative_to(out_root)
    except (ValueError, OSError, RuntimeError):
        # ValueError: outside the root (or an embedded NUL); OSError / RuntimeError:
        # a symlink loop, depending on the Python version.
        raise UnsafePathError(
            f"output path {raw!r} resolves outside the output directory; "
            "a bundle may only write relative paths inside it"
        ) from None
    if rel == Path("."):
        raise UnsafePathError(f"output path {raw!r} names the output directory itself")
    return rel.as_posix()


def confine_actions(out_root: Path, actions: Iterable[Mapping[str, Any]]) -> None:
    """Refuse a plan whose ``write_file`` paths would land outside *out_root*.

    Runs for every bundle kind, between ``plan()`` and ``apply()``. A non-file op,
    or an empty or non-string path, is left to ``apply()``, which reports it as a
    skipped or failed action, exactly as it always has.
    """
    for action in actions:
        if action.get("op") != "write_file":
            continue
        raw = (action.get("params") or {}).get("path")
        if isinstance(raw, str) and raw:
            confine(out_root, raw)
