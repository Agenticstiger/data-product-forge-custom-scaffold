# Changelog

All notable changes to `data-product-forge-custom-scaffold` are documented
here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] — 2026-06-27

### Fixed

- **`fluid custom-scaffold` no longer crashes on first invocation.** The FLUID
  CLI dispatches a command as `func(args, logger)` (two positionals), but the
  command was registered with a one-arg handler — so the primary CLI entry point
  raised `TypeError: takes 1 positional argument but 2 were given` for every user
  who installed the engine alongside `data-product-forge`. The handler now
  accepts (and ignores) extra positionals. Found by end-to-end testing through
  the real `fluid` binary.

### Added

- **Reproducibility: `fluid-scaffold.lock` + `--pin`** (copier-style answers
  model). A successful generation now writes a deterministic, credential-free
  `fluid-scaffold.lock` at the output root recording, per resolved library, the
  exact resolved commit (alongside the contract ref) plus the patterns and
  variables used. Re-running with `--pin` resolves git sources to the **locked
  commit** instead of following a (possibly moved) ref — a byte-reproducible
  re-run (`npm ci` / `poetry --frozen` semantics; opt-in, default behaviour
  unchanged). The lock travels with the generated project; commit it.

## [0.1.2] — 2026-06-27

### Fixed

- **Git resolver now supports SHA pinning** (was broken). `git clone --branch
  <ref>` rejects a raw commit SHA, so a contract pinned to a full commit id —
  the only truly reproducible git ref — hard-failed. A full-length SHA
  (40-hex / 64-hex) is now fetched and checked out (`git init` +
  `git fetch --depth 1 <url> <sha>` + `git checkout FETCH_HEAD`, with a
  full-fetch fallback for servers that disallow fetch-by-SHA). Tags and branches
  keep the cheap shallow `--branch` clone. The token-bearing URL is passed inline
  to `git fetch`, so it is no longer persisted to `.git/config`.
- **Docs:** corrected the clone URLs (the `github.com/fluid-build/…` org 404s;
  the canonical org is `Agenticstiger`) and the Python prerequisite (`3.9+` →
  `3.10+`, matching `requires-python`).

### Changed

- **Honest schema for unenforced fields.** `patterns[].when` and
  `patterns[].environments` (contract side) and the bundle manifest's
  `variables` / `supportedProductTypes` / `supportedCISystems` are accepted but
  not acted on by the engine; they are now documented as **reserved** /
  **advisory** so authors get no silent no-op surprise. `requiredContractFields`
  and `templates` remain enforced.

### Security

- **Engine isolates and redacts plugin failures.** `plan()` / `apply()` now run
  under per-call isolation; unexpected (non-`PluginError`) exceptions are logged
  by type only, so plugin-supplied text never propagates to the caller.
- **Operator allow/block governs custom-scaffold loading.** The
  `custom_scaffolds` entry-point resolver gates each plugin through the same
  `FLUID_PLUGINS_ALLOWLIST` / `FLUID_PLUGINS_BLOCKLIST` policy before load.

## [0.1.1] — 2026-06-01

### Added

- **Shipped JSON-Schema artifact** `schemas/custom-scaffold.v1.json`, loaded via
  `importlib.resources` as the single source of truth for the
  `extensions.customScaffold` validator (was inline-only; the declared
  `schemas/*.json` package-data glob now resolves and ships in the wheel).
- **Native `fluid forge` copilot support** — `get_extension_schema()` registered
  under the new `fluid_build.extension_schemas` entry-point group, so the copilot
  can natively generate and validate `extensions.customScaffold` blocks.
- **White-label spec dialects** — `ScaffoldDialect` plus `make_validator()` /
  `make_register()` factories let a third party reuse the engine under their own
  manifest `apiVersion`, `extensions.<key>`, and CLI subcommand without forking.
  Runnable `examples/acme-scaffold/` reference included.

## [0.1.0] — 2026-05-12

First public release.

### Added

- Custom-scaffold engine that runs as a plugin to the `data-product-forge`
  CLI. Registered via two entry-points: `fluid_build.commands` (CLI
  subcommand) and `fluid_build.extension_validators` (contract validator).
- Three bundle resolvers:
  - **`path`** — local-filesystem bundle (`resolvers/path.py`).
  - **`git`** — clone-into-cache from `https/ssh/git+https/git+ssh` URLs
    (`resolvers/git.py`). Supports `auth.secret_ref` env-var token
    injection with token redaction in error stderr.
  - **`entrypoint`** — load a Python `CustomScaffold` subclass from any
    installed package's `fluid_build.custom_scaffolds` entry-point
    (`resolvers/entrypoint.py`).
- Jinja-template renderer with deterministic output, atomic writes, and
  defensive guardrails against path traversal and static/ symlinks
  (`renderer.py`).
- Bundle manifest parser for `fluid-scaffold.yaml`
  (`apiVersion: fluid.dev/custom-scaffold.v1`).
- JSON-Schema validator for `extensions.customScaffold` block in fluid
  contracts (`validation.py`).
- Reference bundle fixture under `tests/fixtures/reference_bundle/` and
  round-trip integration test pinning determinism.

### Dependencies

- `data-product-forge-sdk >= 0.9, < 1` (import path: `fluid_sdk`).
- `Jinja2 >= 3.1`
- `PyYAML >= 6.0`
- `jsonschema >= 4.17`

### Security

- Path traversal protection in `renderer.py` (rejects absolute paths and
  `..` segments in destination paths).
- Template source confinement (bundle templates cannot escape
  `bundle_root` via `..` in their `from:` path).
- Symlink refusal in bundle `static/` directories (explicit threat model:
  prevents bundles from exfiltrating host files like `~/.aws/credentials`).
- Git URL scheme allowlist (`https/ssh/git+https/git+ssh`); `file://` and
  other schemes rejected outright.
- Git `ref` regex `^[A-Za-z0-9._/-]+$` with `maxLength: 256` in the JSON
  schema, with explicit negative-test coverage for flag-shaped values,
  whitespace, and shell metacharacters.
- Token redaction in git subprocess error output.
- `yaml.safe_load` everywhere; no custom loaders.

### Notes

- Requires Python `>=3.10` (matches the `data-product-forge` CLI floor).
- Depends on `data-product-forge-sdk` (import path `fluid_sdk`), resolved
  transitively from PyPI on install.

[Unreleased]: https://github.com/Agenticstiger/data-product-forge-custom-scaffold/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Agenticstiger/data-product-forge-custom-scaffold/releases/tag/v0.1.0
