# Changelog

All notable changes to `data-product-forge-custom-scaffold` are documented
here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
