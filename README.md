# data-product-forge-custom-scaffold

**The custom-scaffold engine for `data-product-forge`.** Install it alongside the CLI, plug in any scaffold bundle, and generate a complete project from your contract.

```bash
pip install data-product-forge data-product-forge-custom-scaffold
```

This pulls `data-product-forge-sdk` transitively (import path: `fluid_sdk`). Requires Python `>=3.10`.

Then in any fluid contract:

```yaml
extensions:
  customScaffold:
    libraries:
      - id: ci
        source: { kind: git, url: "https://github.com/example/my-bundle", ref: "v1.0" }
    patterns:
      - use: ci:basic
```

```bash
fluid custom-scaffold
# ✓ 3 files written, 0 failed
#   README.md
#   .gitlab-ci.yml
#   docs/runbook.md
```

Deterministic, idempotent, atomic.

---

## What this engine does

This is the **runtime** for `data-product-forge`'s custom-scaffold feature:

1. **Discovers** itself with the CLI via Python entry-points (just `pip install`).
2. **Resolves** bundles from `path` (local), `git` (clone into cache), or `entrypoint` (installed Python plugin).
3. **Validates** the `extensions.customScaffold` block in your contract.
4. **Renders** each pattern through Jinja2.
5. **Writes** the generated files atomically with path-traversal protection.
6. **Copies** the bundle's `static/` directory verbatim alongside rendered templates (for binary fixtures, sample data, pre-rendered files).

## What's in the contract

```yaml
extensions:
  customScaffold:
    libraries:
      - id: ci                                          # local alias
        source:
          kind: git                                     # path | git
          url: "https://github.com/example/ci-bundle"
          ref: "v1.0"
          subdir: "scaffold"                            # optional
          auth: { secret_ref: GITHUB_TOKEN }            # optional
    patterns:
      - use: ci:gitlab-ci                               # <library-id>:<pattern-name>
        variables:
          parentCiTemplateRef: "my-org/ci-templates@main"
```

## Source kinds

| Kind | What it does | When to use |
|---|---|---|
| `path` | Reads a local directory. Relative paths anchor to the contract's directory. | Bundle development; private monorepos that vendor bundles. |
| `git` | `git clone` into the cache. | Shared bundles distributed via a git repo. |
| `entrypoint` | Loads a Python `CustomScaffold` subclass registered via `fluid_build.custom_scaffolds` entry-point. | Bundles that need full programmatic control (external API calls, complex conditionals). |

Auth via `auth.secret_ref` (env-var name, never persisted).

> **Note:** explicit npm and pypi source kinds (auto-fetch from registry) are not in v0. For pip-installable Python plugins, use `kind: entrypoint` after `pip install`-ing the plugin package. Git covers most YAML/Jinja distribution today. File an issue if you need direct-from-registry npm/pypi fetch.

## Bundle authoring

Two paths — pick whichever fits:

### A. YAML + Jinja bundle (no Python)

Drop a directory like this:

```
my-bundle/
├── fluid-scaffold.yaml         ← manifest
├── templates/
│   ├── README.md.j2            ← Jinja templates
│   └── .gitlab-ci.yml.j2
└── static/                      ← optional — copied verbatim
    └── docs/
        └── runbook.md
```

The engine's built-in `TemplatedCustomScaffold` reads the manifest, renders the templates, and copies `static/` verbatim.

→ See **[`docs/walkthrough/build-a-yaml-bundle.md`](docs/walkthrough/build-a-yaml-bundle.md)** for the step-by-step.

### B. Python plugin bundle

Subclass `fluid_sdk.CustomScaffold` directly. Full programmatic control.

→ See the [SDK walkthrough](https://github.com/Agenticstiger/forge-cli-sdk/blob/main/docs/walkthrough/your-first-real-plugin.md).

## CLI surface

```bash
fluid custom-scaffold [OPTIONS]

  -c, --contract PATH    Path to contract.fluid.yaml (default: ./contract.fluid.yaml)
  -o, --output PATH      Output root (default: cwd)
      --dry-run          Plan only — print the file list, write nothing.
      --pattern USE      Restrict to specific patterns (repeatable)
      --lib ID           Restrict to specific library ids (repeatable)
      --pin              Reproducible re-run: resolve git sources to the commit
                         recorded in fluid-scaffold.lock (not the floating ref).
      --update           Update an existing output to the evolved template —
                         3-way merge that preserves your edits (see below).
      --target REF       With --update: the git ref/commit to update to.
      --json             Emit JSON instead of human output
```

Exit codes:

| Code | Meaning |
|---|---|
| `0` | success |
| `1` | bad CLI args / contract not found |
| `2` | engine error (resolution, plan, or apply failed) |
| `3` | at least one `apply()` action failed |
| `4` | `--update` completed with merge conflicts (markers written; resolve them) |

## Reproducibility & updates

Every successful generation writes a **`fluid-scaffold.lock`** at the output
root (commit it alongside the generated files). It records, per resolved
library, the exact commit it resolved to, plus the patterns and variables used —
the same model as copier's `.copier-answers.yml`.

```bash
# Generate — writes the output + fluid-scaffold.lock
fluid custom-scaffold -c contract.fluid.yaml -o ./my-project

# Reproducible re-run — resolve git sources to the LOCKED commit, not the
# moving ref. (npm-ci / poetry --frozen semantics; non-git sources can't be
# reproducibly pinned and the engine says so.)
fluid custom-scaffold -c contract.fluid.yaml -o ./my-project --pin

# Update — the template evolved? Re-render at the new ref and 3-way-merge it
# onto your working tree, preserving your edits. Non-overlapping changes merge
# cleanly; overlaps get Git-style conflict markers (exit 4).
fluid custom-scaffold -c contract.fluid.yaml -o ./my-project --update
```

`--update` renders the template at the **locked** commit (the baseline you
started from) and at the **new** ref, then merges with `git merge-file`
(base = old render, ours = your file, theirs = new render). On success the lock
advances. Full walkthrough: [`docs/walkthrough/reproducible-updates.md`](docs/walkthrough/reproducible-updates.md).

## Documentation

| Doc | What's inside |
|---|---|
| **[`docs/getting-started/`](docs/getting-started/README.md)** | 5-min: install, run against a fixture bundle, see the output |
| **[`docs/walkthrough/build-a-yaml-bundle.md`](docs/walkthrough/build-a-yaml-bundle.md)** | 15-min: author your own YAML/Jinja bundle from scratch |
| **[`docs/walkthrough/from-git-bundle.md`](docs/walkthrough/from-git-bundle.md)** | 5-min: consume a public bundle straight from a git repo |
| **[`docs/walkthrough/reproducible-updates.md`](docs/walkthrough/reproducible-updates.md)** | The lockfile, `--pin`, and `--update` (3-way merge) — reproducibility & updates |
| **[`docs/reference/manifest-format.md`](docs/reference/manifest-format.md)** | Full `fluid-scaffold.yaml` reference |

## License

Apache-2.0.
