# Library API — `generate()`

`generate()` is the engine as a function. It runs exactly what `fluid custom-scaffold`
runs — resolve each library, render each pattern, write the files, record
`fluid-scaffold.lock` — and returns every file it left in the output directory as
`(relative path, sha256, size)`. A host or tool that needs those rows (to record them
in a manifest, say) calls it directly instead of running the command and parsing
`--json`.

It never prints, never exits, and never reads `sys.argv`. The command is a thin
wrapper over it, so the two cannot drift.

## Signature

```python
def generate(
    contract: Mapping[str, Any],
    out_dir: str | os.PathLike[str],
    *,
    contract_dir: str | os.PathLike[str] | None = None,
    dry_run: bool = False,
    pattern_filter: Sequence[str] | None = None,
    library_filter: Sequence[str] | None = None,
    pin: bool = False,
    dialect: ScaffoldDialect = DEFAULT_DIALECT,
) -> GenerationResult
```

(The source spells the unions with `Optional` / `Union`; the meaning is the same on
every supported Python.)

```python
from pathlib import Path

import yaml

from data_product_forge_custom_scaffold import EngineError, generate

contract_path = Path("contract.fluid.yaml")
contract = yaml.safe_load(contract_path.read_text(encoding="utf-8"))

try:
    result = generate(contract, "build/scaffold", contract_dir=contract_path.parent)
except EngineError as exc:
    raise SystemExit(f"scaffold failed: {exc}")

for f in result.files:
    print(f"{f.sha256}  {f.size:>8}  {f.path}")
```

## Parameters

| Parameter | Meaning |
|---|---|
| `contract` | The loaded contract (a mapping). Validate it first — `fluid validate`, or `make_validator`. A contract with no `extensions.customScaffold` block is a no-op: no files. A non-mapping raises `TypeError`. |
| `out_dir` | The output root. Created on demand; a dry run does not create it. |
| `contract_dir` | The directory a relative `path:` source is resolved against. Defaults to the current working directory, so pass the contract file's directory for a result that does not depend on where the process runs. |
| `dry_run` | Plan and check, write nothing. `files` is then what a real run would write. |
| `pattern_filter` | Only run patterns whose `use:` value is listed (`None` or empty: all). |
| `library_filter` | Only resolve libraries whose id is listed (`None` or empty: all). A pattern whose library is filtered out is skipped. |
| `pin` | Resolve `git` sources to the commit recorded in an existing `fluid-scaffold.lock` rather than following the contract's `ref`. See [reproducible updates](../walkthrough/reproducible-updates.md). |
| `dialect` | A white-label `ScaffoldDialect`. The default is the built-in fluid dialect. |

All parameters after `out_dir` are keyword-only.

## Result

`generate()` returns a `GenerationResult`:

| Field | Type | Meaning |
|---|---|---|
| `files` | `tuple[GeneratedFile, ...]` | Every file the run left in `out_dir`, sorted by `path`. |
| `dry_run` | `bool` | Whether nothing was written. |
| `run` | `EngineRunResult` | The raw engine run — planned actions, the SDK's apply summary, the resolved libraries and their versions — for a caller that needs per-action detail. Not part of equality or `repr`. |

and each `GeneratedFile` is a row:

| Field | Type | Meaning |
|---|---|---|
| `path` | `str` | Location relative to `out_dir`, with `/` separators. |
| `sha256` | `str` | Lowercase hex SHA-256 of the file's bytes. |
| `size` | `int` | Length of the file in bytes. |

Both are immutable dataclasses.

```python
manifest = [{"path": f.path, "sha256": f.sha256, "size": f.size} for f in result.files]
```

### What `files` contains

- **The whole generated tree.** Scaffold files plus the reproducibility lock
  (`fluid-scaffold.lock`), so a verifier that walks `out_dir` finds no file the list
  does not account for. Filter on the lock's name if you only want scaffold files.
- **Deterministic order.** Sorted by `path` in code-point order, independent of the
  order patterns run in, filesystem order, and platform.
- **One row per path.** When two patterns write the same path, the later pattern's
  bytes win on disk and the path is listed once, describing them.
- **Digests of what is on disk.** After a real run each row is computed from the file
  as written, not from the plan. A planned file that is missing after `apply()`
  raises `ApplyError`.

### Determinism

The same contract and bundles give the same `files` — same paths, same digests —
whatever the output directory, and re-running into the same directory changes
nothing. A bundle's own templates are expected to be deterministic too; the engine
adds no timestamps or absolute paths to the output.

## Dry run

`dry_run=True` resolves the libraries, plans every pattern and checks every path, but
writes nothing and does not create `out_dir`. `files` is what a real run would write,
including the lock, with digests of the planned bytes — so a dry run and a real run of
the same contract return equal `files`.

## Path confinement

Every planned file path is checked between planning and writing, for every bundle
kind (YAML/Jinja and Python plugin alike) and on a dry run too. The destination is
resolved — following any symlink that already exists — and must land strictly inside
`out_dir`. A path that does not raises `UnsafePathError` before anything is written:

- an absolute path outside `out_dir`;
- a `..` segment that leaves `out_dir`;
- a path that goes through a symlink leading out of `out_dir`;
- an empty path, or one that names `out_dir` itself.

A harmless `a/../b.txt` that stays inside is accepted and reported as `b.txt`. The
check sees the tree as it is when `generate()` runs; it cannot stop another process
swapping a directory for a symlink in the instant before a write.

Templated bundles keep their stricter, earlier check (no absolute path and no `..`
in a rendered destination), which raises the same error. Symlinks inside a bundle's
`static/` directory are refused outright, as before.

## Errors

Everything the engine can attribute is an `EngineError`, so one `except` is enough;
the subclasses let you branch.

| Error | Raised when |
|---|---|
| `ContractError` | A pattern names a library the `customScaffold` block never declared. |
| `BundleError` | A library does not resolve (`ResolutionError`), its manifest is invalid (`ManifestError`), it has no such pattern, or its plugin class fails to initialise. |
| `PlanError` | A pattern's `plan()` failed. |
| `UnsafePathError` | A planned path would land outside `out_dir` (a `RenderError`). |
| `ApplyError` | `apply()` raised, or reported failed files, or a planned file is missing afterwards. |

`ResolutionError`, `ManifestError` and `RenderError` keep their old import paths and
their old bases; they are now also `EngineError`s.

**Redaction.** A failure inside third-party plugin code is reported by exception *type*
only, because an arbitrary exception's text can carry a credential; the original is
chained as `__cause__`. Engine-authored errors and the SDK's user-actionable
`PluginError` keep their message. `ApplyError` for failed files lists the failed
paths, not the per-action error text.

**`ApplyError.run`.** When `apply()` *reported* failed files (rather than raised),
`error.run` is the engine run, so the per-action results are still reachable:

```python
try:
    generate(contract, out_dir)
except ApplyError as exc:
    if exc.run is not None:
        for item in exc.run.apply_result.results:
            if item["status"] == "failed":
                ...
```

## Relationship to the command

`fluid custom-scaffold` calls `generate()` and renders the result, so its behaviour
follows from the above:

| Outcome | Exit code |
|---|---|
| success | `0` |
| contract file not found (a CLI-only check) | `1` |
| any `EngineError` except failed files | `2` |
| `ApplyError` with `run` set (some files failed) | `3` |

`--update` is not part of the library API yet; it still goes through
`Engine.update()`.
