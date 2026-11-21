# Library API — `generate()`

`generate()` is the engine as a function. It runs exactly what `fluid custom-scaffold`
runs — resolve each library, render each pattern, write the files, record
`fluid-scaffold.lock` — and returns every file it wrote to the output directory as
`(relative path, sha256, size)`. A host or tool that needs those rows (to record them
in a manifest, say) calls it directly instead of running the command and parsing
`--json`.

It never writes to stdout, never exits, and never reads `sys.argv`. Non-fatal
conditions go through the `logging` module, and the SDK's default `apply()` logs
failed files to stderr; configure logging in the host to route or silence them. The
command is a thin wrapper over `generate()`, so the two cannot drift.

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
| `contract` | The loaded contract (a mapping). Validate it first — `fluid validate`, or `make_validator`: beyond being a mapping it is not checked, so a malformed `extensions.customScaffold` block raises the underlying exception. A contract with no such block is a no-op: no files. A non-mapping raises `TypeError`. |
| `out_dir` | The output root. Created on demand; a dry run does not create it. |
| `contract_dir` | The directory a relative `path:` source is resolved against. Defaults to the current working directory, so pass the contract file's directory for a result that does not depend on where the process runs. |
| `dry_run` | Plan and check every path, write nothing under `out_dir`. `files` is then what a real run would write. A dry run still resolves the libraries (a `git` source is cloned into the per-user cache) but writes no file into `out_dir`, so it cannot predict an I/O failure a real run would hit (an unwritable directory, a directory where a file is planned). |
| `pattern_filter` | Only run patterns whose `use:` value is listed (`None` or empty: all). A sequence of strings; a bare string raises `TypeError`. |
| `library_filter` | Only resolve libraries whose id is listed (`None` or empty: all). A pattern whose library is filtered out is skipped. A sequence of strings; a bare string raises `TypeError`. |
| `pin` | Resolve `git` sources to the commit recorded in an existing `fluid-scaffold.lock` rather than following the contract's `ref`. See [reproducible updates](../walkthrough/reproducible-updates.md). |
| `dialect` | A white-label `ScaffoldDialect`. The default is the built-in fluid dialect. |

All parameters after `out_dir` are keyword-only.

## Result

`generate()` returns a `GenerationResult`:

| Field | Type | Meaning |
|---|---|---|
| `files` | `tuple[GeneratedFile, ...]` | Every file the run wrote to `out_dir`, sorted by `path`. |
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

- **The whole generated output.** Scaffold files plus the reproducibility lock
  (`fluid-scaffold.lock`), so for a fresh `out_dir` a verifier that walks it finds no
  file the list does not account for. (Files that were already in `out_dir` and that
  the run did not write are not listed.) Filter on the lock's name if you only want
  the scaffold files.
- **The lock is best-effort.** The engine never fails a run over it. If the lock cannot
  be written, a warning is logged, `result.run.lockfile` is `None`, and a real run
  omits the row — a dry run still lists it, because it cannot know.
- **Deterministic order.** Sorted by `path` in code-point order, independent of the
  order patterns run in and of filesystem order.
- **One row per path.** When two patterns write the same path, the later pattern's
  bytes win on disk and the path is listed once, describing them. Paths are compared
  as written: on a case-insensitive or Unicode-normalising filesystem (the macOS
  default, for one), two spellings of one file are listed as two rows.
- **Digests of what is on disk.** After a real run each row is computed from the file
  as written, not from the plan. A planned file that is missing after `apply()`
  (a plugin whose own `apply()` keeps its output elsewhere) raises `OutputError`.

### Determinism

The same contract and bundles give the same `files` — same paths, same digests —
whatever the output directory, and re-running into the same directory changes
nothing. A bundle's own templates are expected to be deterministic too; the engine
adds no timestamps or absolute paths to the output, and writes the lock with LF
newlines on every platform.

## Dry run

`dry_run=True` resolves the libraries, plans every pattern and checks every path, but
writes nothing into `out_dir` and does not create it. (Resolving a `git` library still
fetches it into the per-user cache. git runs with `GIT_TERMINAL_PROMPT=0` unless you set it,
and an ssh URL with `ssh -o BatchMode=yes` unless you chose an ssh command (`GIT_SSH_COMMAND`,
`GIT_SSH` or `core.sshCommand`), so neither a credential prompt nor an ssh passphrase or
host-key question can hang a non-interactive job.) `files` is what a real run would write,
including the lock, with digests of the planned bytes — so when the lock is written, a
dry run and a real run of the same contract return equal `files`.

A dry run describes a planned action the way the SDK's default `apply()` writes it: a
`write_file` action with no `content_b64` is an empty file. An action whose path is
unusable or whose content cannot be decoded raises `OutputError` (a real run reports
the same action as a failed file).

## Path confinement

Every planned file path is checked between planning and writing, for every bundle
kind (YAML/Jinja and Python plugin alike) and on a dry run too. The destination is
resolved — following any symlink that already exists — and must land strictly inside
`out_dir`. A path that does not raises `UnsafePathError` before that pattern is
applied:

- an absolute path outside `out_dir`;
- a `..` segment that leaves `out_dir`;
- a path that goes through a symlink leading out of `out_dir`;
- an empty path, or one that names `out_dir` itself.

Patterns run in order, each planned, checked and applied in turn. A refusal in a later
pattern therefore leaves the earlier patterns' files in `out_dir` (as a failing
`plan()` always has): discard a failed output directory.

`fluid-scaffold.lock` is the engine's own file and is held to a stricter rule: it is never
written or read through a symlink. A symlink at that name — pointing anywhere, inside
`out_dir` or out of it — raises `UnsafePathError` before any pattern is applied, on a dry
run too, and the write itself refuses a link that appears in the meantime. A new lock is
created owner-only (mode `0o600`); an existing lock keeps its mode when it is rewritten.

A harmless `a/../b.txt` that stays inside is accepted and reported as `b.txt`. The
check sees the tree as it is when `generate()` runs; it cannot stop another process
swapping a directory for a symlink in the instant before a write.

Templated bundles keep their stricter, earlier check (no absolute path and no `..`
in a rendered destination), which raises the same error. Symlinks inside a bundle's
`static/` directory are refused outright, as before.

## Errors

Everything the engine attributes to a contract, a bundle, a plugin or the output is an
`EngineError`, so one `except` is enough; the subclasses let you branch.

| Error | Raised when |
|---|---|
| `ContractError` | A pattern names a library the `customScaffold` block never declared. |
| `BundleError` | A library does not resolve (`ResolutionError`), its manifest is invalid (`ManifestError`), it has no such pattern, or its plugin class fails to initialise. |
| `PlanError` | A pattern's `plan()` failed. |
| `UnsafePathError` | A planned path would land outside `out_dir`, or `fluid-scaffold.lock` is a symlink (a `RenderError`). |
| `ApplyError` | `apply()` raised, or reported failed files. |
| `OutputError` | The run finished, but a planned file cannot be described: it is missing from `out_dir` after `apply()`, or (dry run) its action has no usable path or content. |

`ResolutionError`, `ManifestError` and `RenderError` keep their old import paths and
their old bases; they are now also `EngineError`s.

**Redaction.** A failure while a pattern's plugin class is constructed, or its `plan()`
or `apply()` runs, is reported by exception *type* only, because an arbitrary
exception's text can carry a credential — and a template can interpolate contract values
into one. That includes a failed template render: it is a `PlanError` with the
`RenderError` chained as `__cause__`. The SDK's user-actionable `PluginError` and
`UnsafePathError` keep their message. Errors raised while resolving a bundle or parsing
a manifest are engine-authored and keep theirs. `ApplyError` for failed files lists the
failed paths, not the per-action error text.

**Inputs that are not validated.** `generate()` does not validate what it is given
beyond `contract` being a mapping and the filters not being a bare string. A malformed
`customScaffold` block (an entry missing `id` or `use`), a corrupt lock file with
`pin=True`, or an `out_dir` the operating system cannot resolve raises the underlying
exception, not an `EngineError`.

**`ApplyError.run` and `OutputError.run`.** When `apply()` *reported* failed files
(rather than raised), `error.run` is the engine run, so the per-action results are still
reachable; an `OutputError` carries the run too:

```python
try:
    generate(contract, out_dir)
except ApplyError as exc:
    if exc.run is not None:
        for item in exc.run.apply_result.results:
            if item["status"] == "failed":
                ...
```

**After a failure.** Files that earlier patterns already wrote stay in `out_dir`. The
lock is written unless `plan()` or `apply()` raised, so it is still there when `apply()`
*reported* failed files. An `ApplyError` raised by `apply()` carries no run. A host that
records files should record them only from a successful `generate()` and discard the
directory otherwise.

## Relationship to the command

`fluid custom-scaffold` calls `generate()` and renders the engine run, so its behaviour
follows from the above. It does not use the file list, so it carries on when `generate()`
raises `OutputError` (a plugin that keeps its output elsewhere still prints its summary
and exits `0`):

| Outcome | Exit code |
|---|---|
| success, or `OutputError` | `0` |
| contract file not found (a CLI-only check) | `1` |
| `ApplyError` with `run` set (some files failed) | `3` |
| any other `EngineError` | `2` |

`--update` is not part of the library API yet; it still goes through
`Engine.update()`.
