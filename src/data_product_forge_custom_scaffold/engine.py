"""The top-level :class:`Engine` orchestrator.

End-to-end flow when a user runs ``fluid generate custom-scaffold``:

  1. Load + validate contract.fluid.yaml (done by the FLUID CLI before us).
  2. Read ``contract.extensions.customScaffold``.
  3. For each declared library: resolve the source kind. Relative ``path``
     sources anchor to the contract's directory.
  4. For each declared pattern:
       a. Parse ``use: <lib-id>:<pattern-name>``.
       b. Instantiate the right scaffold class:
          * YAML/Jinja bundle → :class:`TemplatedCustomScaffold` pointed
            at the bundle directory.
          * Python-plugin bundle → the plugin's own
            :class:`fluid_sdk.CustomScaffold` subclass.
       c. Call ``plan(contract)`` → list of write_file actions.
       d. Call ``apply(actions)`` (or skip if --dry-run).

This module is provider-agnostic — it knows nothing about gitlab-ci,
data products, etc. It just dispatches between the contract's
``extensions.customScaffold`` block and the resolver / scaffold class.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from fluid_sdk import CustomScaffold, ExecutionResult, PluginError

from .confinement import confine_actions
from .dialect import DEFAULT as DEFAULT_DIALECT
from .dialect import ScaffoldDialect
from .errors import (
    ApplyError,
    BundleError,
    ContractError,
    EngineError,  # moved to .errors with the hierarchy; callers still import it from here
    PlanError,
    UnsafePathError,
)
from .lockfile import LOCKFILE_NAME, build_lock, dump_lock, pin_source, read_lock, write_lock
from .resolvers import ResolvedBundle, resolve_bundle
from .templated import TemplatedCustomScaffold
from .update import UpdateError, UpdateResult, actions_to_render, merge_renders
from .version import ENGINE_VERSION

LOG = logging.getLogger(__name__)


@dataclass
class EngineRunResult:
    """What an :meth:`Engine.run` invocation produced."""

    actions: List[Dict[str, Any]] = field(default_factory=list)
    apply_result: Optional[ExecutionResult] = None
    resolved_libraries: Dict[str, ResolvedBundle] = field(default_factory=dict)
    # Path to the fluid-scaffold.lock written for this run (None on dry-run or
    # when nothing was resolved).
    lockfile: Optional[Path] = None
    # The exact text of the lock for this run — set on a dry run too (where nothing
    # is written), so a caller can preview the full output. None when no library
    # was resolved.
    lock_text: Optional[str] = None


class Engine:
    """Orchestrates a single ``fluid generate custom-scaffold`` invocation."""

    def __init__(
        self,
        *,
        output_root: Path,
        contract_dir: Optional[Path] = None,
        dialect: ScaffoldDialect = DEFAULT_DIALECT,
    ) -> None:
        self.output_root = Path(output_root).resolve()
        self.contract_dir = (contract_dir or Path.cwd()).resolve()
        self.dialect = dialect

    def run(
        self,
        contract: Mapping[str, Any],
        *,
        dry_run: bool = False,
        pattern_filter: Optional[List[str]] = None,
        library_filter: Optional[List[str]] = None,
        pin: bool = False,
    ) -> EngineRunResult:
        """Generate scaffolds from *contract*.

        When ``pin`` is set, each git library is resolved to the exact commit
        recorded in an existing ``fluid-scaffold.lock`` at the output root rather
        than following the (possibly moved) contract ``ref`` — a reproducible
        re-run. Without ``pin`` (the default) the contract ref is followed and
        the lock is refreshed to whatever it now resolves to.
        """
        block = self._extract_block(contract)
        if block is None:
            return EngineRunResult()

        # 1. Resolve libraries. With pin, re-pin git sources to the locked commit.
        locked_libs = read_lock(self.output_root).get("libraries", {}) if pin else {}
        resolved: Dict[str, ResolvedBundle] = {}
        for lib in block.get("libraries", []):
            lib_id = lib["id"]
            if library_filter and lib_id not in library_filter:
                continue
            source = lib["source"]
            if pin:
                if source.get("kind") != "git":
                    # Only git sources carry a reproducible commit. Path /
                    # entry-point sources resolve to whatever is on disk / installed
                    # now, so --pin cannot freeze them — say so rather than imply a
                    # false guarantee.
                    LOG.warning(
                        "library %r is a %r source — --pin cannot reproducibly lock it "
                        "(only git sources pin to a commit)",
                        lib_id,
                        source.get("kind", "?"),
                    )
                source = pin_source(source, locked_libs.get(lib_id) or {})
            # Thread contract_dir so relative `path:` sources anchor to
            # the contract's directory, not the invoking process cwd.
            resolved[lib_id] = resolve_bundle(source, contract_dir=self.contract_dir)

        # 2. Plan + apply each pattern.
        all_actions: List[Dict[str, Any]] = []
        apply_results: List[ExecutionResult] = []

        for pattern_decl in block.get("patterns", []):
            use = pattern_decl["use"]
            if pattern_filter and use not in pattern_filter:
                continue

            lib_id, pattern_name = use.split(":", 1)
            if lib_id not in resolved:
                # If the library was filtered out via --lib, silently
                # skip the pattern with a warning rather than raising.
                # (Hard-fail only when the contract is internally
                # inconsistent — i.e. no filter is active.)
                if library_filter and lib_id not in library_filter:
                    LOG.warning(
                        "skipping pattern %r — library %r excluded by filter",
                        use,
                        lib_id,
                    )
                    continue
                raise ContractError(
                    f"pattern {use!r} references library {lib_id!r}, "
                    f"which was not declared (known: {sorted(resolved.keys())})"
                )

            scaffold = _build_scaffold(
                self, resolved[lib_id], use, pattern_name, pattern_decl.get("variables") or {}
            )
            actions = _plan_confined(use, scaffold, contract, self.output_root)
            all_actions.extend(actions)

            if not dry_run:
                apply_results.append(_apply_isolated(use, scaffold, actions))

        lockfile_path, lock_text = _record_lock(
            self.output_root, resolved, block.get("patterns", []), dry_run=dry_run
        )

        return EngineRunResult(
            actions=all_actions,
            apply_result=(
                _aggregate(apply_results, plugin_name=self.dialect.aggregate_plugin_name)
                if apply_results
                else None
            ),
            resolved_libraries=resolved,
            lockfile=lockfile_path,
            lock_text=lock_text,
        )

    def update(
        self,
        contract: Mapping[str, Any],
        *,
        target_ref: Optional[str] = None,
        dry_run: bool = False,
    ) -> UpdateResult:
        """Update an already-generated output to an evolved template.

        Renders each library's template TWICE — at the **locked** commit (from
        ``fluid-scaffold.lock``) and at the **new** target (``target_ref`` for
        git sources, else the contract's current ref) — and 3-way-merges the new
        render onto the working tree, preserving the user's edits and writing
        Git-style conflict markers where they overlap. On success the lock is
        refreshed to the new resolution. Raises :class:`UpdateError` if there is
        no lockfile to update from.
        """
        block = self._extract_block(contract)
        if block is None:
            raise UpdateError("contract has no extensions customScaffold block to update")

        lock = read_lock(self.output_root)
        locked_libs = lock.get("libraries") or {}
        if not locked_libs:
            raise UpdateError(
                f"no {LOCKFILE_NAME} found under {self.output_root} — generate first, then update"
            )

        # Resolve OLD (locked commit) and NEW (target ref) for each library.
        old_resolved: Dict[str, ResolvedBundle] = {}
        new_resolved: Dict[str, ResolvedBundle] = {}
        for lib in block.get("libraries", []):
            lib_id = lib["id"]
            source = lib["source"]
            old_source = pin_source(source, locked_libs.get(lib_id) or {})
            new_source = dict(source)
            if target_ref and new_source.get("kind") == "git":
                new_source["ref"] = target_ref
            old_resolved[lib_id] = resolve_bundle(old_source, contract_dir=self.contract_dir)
            new_resolved[lib_id] = resolve_bundle(new_source, contract_dir=self.contract_dir)

        # Render both versions in memory (plan emits the rendered content).
        old_render: Dict[str, bytes] = {}
        new_render: Dict[str, bytes] = {}
        for pattern_decl in block.get("patterns", []):
            use = pattern_decl["use"]
            lib_id, pattern_name = use.split(":", 1)
            variables = pattern_decl.get("variables") or {}
            if lib_id in old_resolved:
                old_scaffold = self._instantiate_scaffold(
                    old_resolved[lib_id], pattern_name=pattern_name, variables=variables
                )
                old_render.update(actions_to_render(old_scaffold.plan(contract)))
            if lib_id in new_resolved:
                new_scaffold = self._instantiate_scaffold(
                    new_resolved[lib_id], pattern_name=pattern_name, variables=variables
                )
                new_render.update(actions_to_render(new_scaffold.plan(contract)))

        result = merge_renders(
            old=old_render,
            new=new_render,
            output_root=self.output_root,
            dry_run=dry_run,
        )

        # Refresh the lock to the new resolution (unless dry-run).
        if not dry_run and new_resolved:
            try:
                write_lock(
                    self.output_root,
                    build_lock(
                        engine_version=ENGINE_VERSION,
                        resolved=new_resolved,
                        patterns=block.get("patterns", []),
                    ),
                )
            except OSError as e:
                LOG.warning("could not refresh lockfile: %s", type(e).__name__)

        return result

    # ── Internal helpers ────────────────────────────────────────────

    def _instantiate_scaffold(
        self,
        resolved: ResolvedBundle,
        *,
        pattern_name: str,
        variables: Mapping[str, Any],
    ) -> CustomScaffold:
        """Instantiate the right scaffold class for the resolved bundle.

        * For Python-plugin bundles (``resolved.plugin_class`` set), call
          the plugin's constructor with ``output_root``, ``pattern_name``,
          and ``variables``. Plugin authors can accept these explicitly
          or fall back to ``**kwargs`` / ``self.extra``.
        * For YAML/Jinja bundles (``resolved.bundle_root`` set), use the
          built-in :class:`TemplatedCustomScaffold`.
        """
        if resolved.plugin_class is not None:
            cls = resolved.plugin_class
            try:
                # Python plugins receive pattern_name + variables as
                # named kwargs. The SDK's CustomScaffold base accepts
                # arbitrary **kwargs and stores them in self.extra, so
                # this is forwards-compatible with plugins that don't
                # explicitly accept these parameters.
                return cls(
                    output_root=self.output_root,
                    pattern_name=pattern_name,
                    variables=dict(variables),
                )
            except TypeError:
                # Plugin's __init__ may not accept pattern_name/variables
                # explicitly. Retry without them — they'll be visible via
                # the plugin's contract dict if needed.
                return cls(output_root=self.output_root)

        if resolved.bundle_root is None:
            raise BundleError(
                f"resolved bundle for kind={resolved.kind!r} has neither "
                f"bundle_root nor plugin_class — resolver bug"
            )

        return TemplatedCustomScaffold(
            bundle_root=resolved.bundle_root,
            pattern_name=pattern_name,
            variables=dict(variables),
            output_root=self.output_root,
            dialect=self.dialect,
        )

    def _extract_block(self, contract: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
        ext = contract.get("extensions")
        if not isinstance(ext, Mapping):
            return None
        block = ext.get(self.dialect.extension_key)
        if not isinstance(block, Mapping):
            return None
        return block


def _build_scaffold(
    engine: Engine,
    bundle: ResolvedBundle,
    use: str,
    pattern_name: str,
    variables: Mapping[str, Any],
) -> CustomScaffold:
    """Instantiate a pattern's scaffold; every failure becomes a typed ``BundleError``."""
    try:
        return engine._instantiate_scaffold(bundle, pattern_name=pattern_name, variables=variables)
    except EngineError:
        raise  # ManifestError / RenderError: already typed, engine-authored text
    except PluginError as e:
        # E.g. the bundle has no such pattern — user-actionable, surface it.
        raise BundleError(f"pattern {use!r}: {e}") from e
    except Exception as e:
        # A plugin class's own constructor failed: type only, as for plan().
        raise BundleError(
            f"pattern {use!r}: its scaffold failed to initialise: {type(e).__name__}"
        ) from e


def _plan_confined(
    use: str, scaffold: CustomScaffold, contract: Mapping[str, Any], output_root: Path
) -> List[Dict[str, Any]]:
    """Run ``plan()`` in isolation, then confine every planned path before apply."""
    try:
        # One list for confinement, the recorded plan and apply(): a one-shot iterable
        # (a generator) would otherwise be consumed by the first of them.
        actions = list(scaffold.plan(contract))
        # Confine BEFORE apply() — for every bundle kind, and on a dry run too, so a
        # plan that escapes the output directory is refused rather than listed as
        # "would write".
        confine_actions(output_root, actions)
    except UnsafePathError as e:
        raise UnsafePathError(f"plan failed for pattern {use!r}: {e}") from e
    except PluginError as e:
        # PluginError is user-actionable by the SDK contract — surface it.
        raise PlanError(f"plan failed for pattern {use!r}: {e}") from e
    except Exception as e:
        # Unexpected: isolate and surface the exception TYPE only. An arbitrary
        # exception's text may carry secrets (a credential in a message, a value
        # from a stack); never interpolate it. The full traceback is still chained
        # via ``from e``.
        raise PlanError(f"plan failed for pattern {use!r}: {type(e).__name__}") from e
    return actions


def _apply_isolated(
    use: str, scaffold: CustomScaffold, actions: List[Dict[str, Any]]
) -> ExecutionResult:
    """Run ``apply()`` in isolation.

    apply() runs plugin code that writes to disk. Isolate it the same way as plan():
    a failing apply must not crash the run with a raw, possibly secret-bearing
    traceback.
    """
    try:
        return scaffold.apply(actions)
    except PluginError as e:
        raise ApplyError(f"apply failed for pattern {use!r}: {e}") from e
    except Exception as e:
        raise ApplyError(f"apply failed for pattern {use!r}: {type(e).__name__}") from e


def _record_lock(
    output_root: Path,
    resolved: Mapping[str, ResolvedBundle],
    patterns: List[Mapping[str, Any]],
    *,
    dry_run: bool,
) -> Tuple[Optional[Path], Optional[str]]:
    """Build the lock for this run and, unless it is a dry run, write it.

    Records what generated this output, for reproducible re-runs (copier's
    answers-file model). Returns ``(path written or None, lock text or None)``; the
    text is returned on a dry run too. Best-effort: a lock-write failure must not
    fail an otherwise-successful generation.
    """
    if not resolved:
        return None, None
    lock = build_lock(engine_version=ENGINE_VERSION, resolved=resolved, patterns=patterns)
    path: Optional[Path] = None
    if not dry_run:
        try:
            path = write_lock(output_root, lock)
        except OSError as e:
            LOG.warning("could not write lockfile: %s", type(e).__name__)
    return path, dump_lock(lock)


def _aggregate(
    results: List[ExecutionResult], *, plugin_name: str = "custom-scaffold-engine"
) -> ExecutionResult:
    """Combine multiple ExecutionResults into one summary."""
    if not results:
        raise ValueError("cannot aggregate empty list")
    first = results[0]
    return ExecutionResult(
        plugin=plugin_name,
        role="custom_scaffold",
        applied=sum(r.applied for r in results),
        failed=sum(r.failed for r in results),
        duration_sec=round(sum(r.duration_sec for r in results), 4),
        timestamp=first.timestamp,
        results=[item for r in results for item in r.results],
        artifacts=[item for r in results for item in r.artifacts],
        warnings=[item for r in results for item in r.warnings],
    )
