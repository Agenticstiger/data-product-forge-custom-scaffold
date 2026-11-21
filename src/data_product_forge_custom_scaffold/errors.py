"""Typed errors for the engine and its library entry point.

Every failure the engine can attribute is an :class:`EngineError`, so a caller
needs one ``except EngineError`` and can still branch on the subclass:

* :class:`ContractError` — the contract's ``customScaffold`` block cannot drive a
  run (a pattern names a library the block never declared).
* :class:`BundleError` — a library's bundle cannot be used: it does not resolve
  (``ResolutionError``), its manifest is invalid (``ManifestError``), it has no
  such pattern, or its plugin class fails to initialise.
* :class:`PlanError` — a pattern's ``plan()`` failed.
* :class:`RenderError` — a template failed to render, or a path escapes the output
  root. :class:`UnsafePathError` is the path-escape case.
* :class:`ApplyError` — ``apply()`` failed.

Messages for a failure inside third-party plugin code carry the exception *type*
only, because an arbitrary exception's text can hold a credential. Engine-authored
errors and the SDK's user-actionable ``PluginError`` keep their message. The
original exception is always chained as ``__cause__``.

The shape (one base class, a distinct subclass for an unsafe destination path)
follows copier's ``CopierError`` / ``ForbiddenPathError`` and CPython's
``tarfile.FilterError`` / ``OutsideDestinationError``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids an import cycle
    from .engine import EngineRunResult


class EngineError(RuntimeError):
    """Raised when the engine can't complete a run (config error, missing
    library, etc.)."""


class ContractError(EngineError):
    """The contract's ``customScaffold`` block cannot drive a run."""


class BundleError(EngineError):
    """A library's bundle cannot be resolved, parsed or initialised."""


class PlanError(EngineError):
    """A pattern's ``plan()`` failed."""


class RenderError(EngineError):
    """Raised when a template fails to render or a path escapes the output root."""


class UnsafePathError(RenderError):
    """A planned output path resolves outside the output directory (an absolute
    path, a ``..`` segment, or a symlink that leads out)."""


class ApplyError(EngineError):
    """``apply()`` failed.

    When the failure was reported by ``apply()`` (some actions failed) rather than
    raised, :attr:`run` holds the engine run so a caller can still inspect the
    per-action detail. It is ``None`` when ``apply()`` raised.
    """

    def __init__(self, message: str, *, run: Optional["EngineRunResult"] = None) -> None:
        super().__init__(message)
        self.run = run
