# Pull a bundle straight from git

**Time:** 5 minutes | **Difficulty:** Beginner | **Prerequisites:** [Getting Started](../getting-started/README.md)

The fastest way to share a bundle within an organisation is **just push it to a git repo**. No PyPI release, no npm publish — the engine clones it on demand into a per-user cache.

## Step 1 — Find or publish a bundle repo

Any git repo with a `fluid-scaffold.yaml` at the top level (or under a subdirectory) qualifies. For this walkthrough, we'll use the engine's reference bundle:

```
https://github.com/Agenticstiger/data-product-forge-custom-scaffold
  └─ tests/fixtures/reference_bundle/
       ├── fluid-scaffold.yaml
       ├── templates/...
       └── static/docs/...
```

## Step 2 — Reference it in your contract

```yaml
fluidVersion: "0.7.4"
id: my-product
name: My Product
description: Demo product.
metadata: {owner: {team: x, email: x@example.com}}

environments:
  dev:  {metadata: {labels: {"cloud.accountId": "111"}}}
  prod: {metadata: {labels: {"cloud.accountId": "333"}}}

extensions:
  customScaffold:
    libraries:
      - id: ref
        source:
          kind: git
          url: "https://github.com/Agenticstiger/data-product-forge-custom-scaffold"
          ref: "main"
          subdir: "tests/fixtures/reference_bundle"
    patterns:
      - use: ref:basic
```

## Step 3 — Generate

```bash
fluid custom-scaffold
```

```
Resolved libraries:
  ref  (git)  version=<commit-sha>

✓ 3 files written, 0 failed
  README.md
  .gitlab-ci.yml
  docs/runbook.md
```

Files appear in cwd.

## Where the bundle lives on disk

The engine cached it here:

```
~/.cache/fluid/custom-scaffold/git/<urlhash>/<ref>/
```

Re-runs reuse the cache. To force a fresh clone, delete that directory or set:

```bash
FLUID_CUSTOM_SCAFFOLD_NOCACHE=1 fluid custom-scaffold
```

## Private git repos

For private repos, declare an auth ref pointing at an env var holding a token:

```yaml
source:
  kind: git
  url: "https://github.com/my-org/private-bundle"
  ref: "v1.2.0"
  auth:
    secret_ref: GITHUB_TOKEN          # env-var name, never the value
```

```bash
export GITHUB_TOKEN=ghp_xxx
fluid custom-scaffold
```

The token is injected into the clone URL at fetch time, never written to disk, never logged.

## Pin a specific version

For production reproducibility, pin a tag or full sha:

```yaml
source:
  kind: git
  url: "https://github.com/example/my-bundle"
  ref: "v2.3.1"            # any tag, branch, or full sha
```

Tags can be force-pushed — if you want guaranteed reproducibility, pin a 40-char commit sha instead.

## Trade-offs vs other channels

| Distribution | Pros | Cons |
|---|---|---|
| **git** | No release workflow needed; tag = version; works for private repos | Requires git on PATH; somewhat slower first-fetch |
| **path** | Best for development | Doesn't share across users |

The engine treats both uniformly — same render pipeline, same `requiredContractFields` checks, same determinism guarantee.

## What's next?

- [Build your own YAML bundle](build-a-yaml-bundle.md) and publish it to git.
- [Manifest reference](../reference/manifest-format.md) — every field explained.
