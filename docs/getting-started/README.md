# Getting Started

**Time:** 5 minutes | **Difficulty:** Beginner | **Prerequisites:** Python 3.10+, `pip`

You're going to install the engine, point it at a tiny example bundle, and watch it produce a working project from a fluid contract.

## Step 1 — Install

```bash
pip install data-product-forge data-product-forge-custom-scaffold
```

Verify the engine is discovered:

```bash
fluid generate --help
```

`custom-scaffold` should appear under available subcommands.

## Step 2 — Get a bundle (locally)

Copy the reference fixture bundle that ships with the engine:

```bash
git clone https://github.com/Agenticstiger/data-product-forge-custom-scaffold.git
cp -r data-product-forge-custom-scaffold/tests/fixtures/reference_bundle ./my-bundle
```

The bundle contains:

```
my-bundle/
├── fluid-scaffold.yaml          ← manifest
├── templates/
│   ├── README.md.j2             ← Jinja template
│   └── .gitlab-ci.yml.j2        ← Jinja template
└── static/
    └── docs/
        └── runbook.md           ← copied verbatim, no rendering
```

## Step 3 — Write a contract

```bash
mkdir my-project && cd my-project
cat > contract.fluid.yaml <<'YAML'
fluidVersion: "0.7.4"
kind: DataProduct
id: my-test-product
name: My Test Product
description: A 5-minute demo product.
domain: platform
metadata:
  owner: {team: platform, email: platform@example.com}

environments:
  dev:
    metadata:
      labels: {"cloud.accountId": "111", "cloud.region": "us-east-1"}
  prod:
    metadata:
      labels: {"cloud.accountId": "333", "cloud.region": "us-east-1"}

extensions:
  customScaffold:
    libraries:
      - id: ref
        source: { kind: path, path: "../my-bundle" }
    patterns:
      - use: ref:basic
        variables:
          teamName: "data-platform"
YAML
```

## Step 4 — Generate

```bash
fluid generate custom-scaffold
```

```
Resolved libraries:
  ref  (path)  version=local

✓ 3 files written, 0 failed
  README.md
  .gitlab-ci.yml
  docs/runbook.md
```

## Step 5 — Inspect

```
README.md                          ← header derived from contract.name/description
.gitlab-ci.yml                     ← deploy:dev + deploy:prod (prod gated `when: manual`)
docs/runbook.md                    ← copied verbatim from the bundle's static/
```

The `.gitlab-ci.yml`:

```yaml
# Auto-generated CI for my-test-product
stages:
  - validate
  - deploy

validate:
  stage: validate
  script:
    - fluid validate

deploy:dev:
  stage: deploy
  script:
    - fluid apply --env dev
  only:
    - main

deploy:prod:
  stage: deploy
  script:
    - fluid apply --env prod
  when: manual
  only:
    - main
```

**Every byte is derived from the contract.** Change `environments` → re-run → output adapts.

## Step 6 — Determinism check

```bash
fluid generate custom-scaffold --dry-run
```

You'll see the same 3 file paths listed — no files touched. The action list is byte-for-byte identical to what `apply()` would produce.

## What just happened?

```
contract.fluid.yaml
        │
        ▼  fluid generate custom-scaffold
FLUID CLI: loads + validates, dispatches to engine
        │
        ▼
Engine: reads extensions.customScaffold, resolves library, plans + applies
        │
        ▼
TemplatedCustomScaffold: validates required fields, renders templates,
                          copies static/, writes files atomically.
```

## What's next?

| Want to... | Go to |
|---|---|
| Build your own YAML/Jinja bundle | [`walkthrough/build-a-yaml-bundle.md`](../walkthrough/build-a-yaml-bundle.md) |
| Pull a bundle straight from git | [`walkthrough/from-git-bundle.md`](../walkthrough/from-git-bundle.md) |
| Understand the manifest format | [`reference/manifest-format.md`](../reference/manifest-format.md) |
| Write a Python-plugin bundle | [SDK walkthrough](https://github.com/Agenticstiger/forge-cli-sdk/blob/main/docs/walkthrough/your-first-real-plugin.md) |
