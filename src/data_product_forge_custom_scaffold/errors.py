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
* :class:`OutputError` — the run finished, but a file it planned cannot be described
  (``generate()`` only; ``fluid custom-scaffold`` does not need the description).

A failure while a pattern's plugin class is constructed, or its ``plan()`` or
``apply()`` runs, carries the exception *type* only, because an arbitrary exception's
text can hold a credential — and a template can interpolate contract values into one.
That includes a failed template render: it surfaces as :class:`PlanError` with the
``RenderError`` chained as ``__cause__``. The exceptions are the SDK's user-actionable
``PluginError`` and :class:`UnsafePathError`, which name the offending path. Errors
raised while resolving a bundle or parsing its manifest are engine-authored and keep
their message. The original exception is always chained as ``__cause__``.

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


class OutputError(EngineError):
    """The run finished, but a file it planned cannot be described.

    Raised by ``generate()`` after ``apply()`` reported no failure (or on a dry run):
    the planned file is missing from the output directory (a plugin whose own
    ``apply()`` keeps its output elsewhere), or the planned action has no usable path
    or content. :attr:`run` is the engine run, so a caller that does not need the file
    list can still use it — ``fluid custom-scaffold`` does exactly that.
    """

    def __init__(self, message: str, *, run: "EngineRunResult") -> None:
        super().__init__(message)
        self.run = run
