# Contributing to data-product-forge-custom-scaffold

Thanks for taking the time. Before the commands, here's the mental model — it makes "is this in scope" decisions trivial.

## What this package is, and isn't

**It is:** the reference custom-scaffold engine. It resolves bundles (from a local path, a git repo, or a Python entry-point), renders Jinja templates against a fluid contract, copies static fixtures, and writes the output deterministically and atomically. Plus a JSON-Schema validator for the `extensions.customScaffold` block that the `data-product-forge` CLI calls into.

**It isn't:** an opinion on what your scaffold should produce. The bundles are the opinion; this is just the runtime that drives them. Adding a new bundle is a separate package; adding a new resolver kind belongs here.

If a change adds runtime opinions about what the user's project looks like, it probably belongs in a bundle, not in this engine.

## Repo layout

```
src/data_product_forge_custom_scaffold/
├── engine.py              # Orchestrator: contract -> resolved bundle -> rendered files
├── manifest.py            # Parser for fluid-scaffold.yaml
├── renderer.py            # Jinja rendering + static/ copying (security-sensitive)
├── validation.py          # JSON-Schema for extensions.customScaffold (validator hook)
├── templated.py           # TemplatedCustomScaffold — what most bundles plug into
├── context.py             # ContractContext — what Jinja gets to see
├── cli.py                 # `fluid generate custom-scaffold` subcommand registration
└── resolvers/
    ├── base.py            # Resolver ABC + ResolvedBundle dataclass
    ├── path.py            # Local-path resolver
    ├── git.py             # git clone resolver (security-sensitive)
    └── entrypoint.py      # importlib.metadata-based plugin loader
tests/unit/                # Unit tests, one file per source module
tests/integration/         # End-to-end round-trips
tests/fixtures/            # reference bundle used by integration tests
docs/                      # Walkthroughs + reference (in-repo only — no separate docs repo)
```

## Dev loop

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"          # pulls data-product-forge-sdk from PyPI

.venv/bin/pytest                           # 38 tests, ~0.6s
.venv/bin/ruff check src/ tests/
.venv/bin/black --check src/ tests/
.venv/bin/python -m build                  # sdist + wheel; run if you touched pyproject.toml
```

CI on a PR runs the same three gates on Python 3.10 / 3.11 / 3.12 / 3.13 / 3.14.

If you're working on the SDK and the scaffold in lockstep, override the
dependency line for local development:

```bash
.venv/bin/pip install -e /path/to/forge-cli-sdk    # before the scaffold install
.venv/bin/pip install -e ".[dev]" --no-deps         # pick up scaffold + dev extras without re-resolving the SDK
```

## Where high-quality PRs come from

- **Tests demonstrate the change.** The renderer has explicit symlink-safety tests and a determinism test — anything that touches those areas needs equivalent coverage.
- **Security-sensitive code gets an extra hop.** `renderer.py`, `resolvers/git.py`, `resolvers/entrypoint.py`, and `validation.py` all handle external input. Changes here need an explicit "this can't be exploited because…" line in the PR description.
- **One logical change per PR.** Conventional Commits format (`feat(resolvers): add pypi resolver`).
- **Determinism is a contract.** If your change can produce different bytes for the same contract on two runs, that's a bug.

## How to add a new resolver kind

A resolver materializes a "source" reference (in `extensions.customScaffold.libraries[].source`) into a local bundle. To add a new kind (say, `npm`):

1. Add `src/data_product_forge_custom_scaffold/resolvers/npm.py` subclassing `Resolver`. Set `kind = "npm"`. Implement `resolve(source, *, contract_dir)`.
2. Register it in `resolvers/__init__.py::DEFAULT_RESOLVERS`.
3. Add `"npm"` to the `kind` enum in `validation.py::CUSTOM_SCAFFOLD_SCHEMA`.
4. Add unit tests in `tests/unit/test_npm_resolver.py` covering: happy path, missing required field, malformed URL, network error.
5. Update `README.md` (the source-kinds table) and `docs/reference/manifest-format.md`.

If your resolver shells out to a binary (`git`, `npm`, etc.), follow the pattern in `resolvers/git.py`:
- List-form subprocess only (no shell).
- Allowlist URL schemes via regex.
- Redact tokens from any error stderr before re-raising.
- Confirm subdir stays under the cache root.

## How to extend the bundle manifest

`fluid-scaffold.yaml` schema lives in `manifest.py`. The `apiVersion` line pins the schema; bumping it (e.g. `fluid.dev/custom-scaffold.v2`) is a breaking change and needs a migration note in `CHANGELOG.md`.

For backwards-compatible additions (new optional fields), add them to the relevant dataclass with a sensible default and document in `docs/reference/manifest-format.md`.

## Reporting bugs and security issues

- **Bugs:** open a GitHub issue with the bug template. A minimal reproducer (contract + bundle) is worth ten paragraphs of description.
- **Security:** see [`SECURITY.md`](SECURITY.md). Do not file a public issue for vulnerabilities.

## License

By submitting a PR you agree your contribution is licensed under [Apache-2.0](LICENSE).
