# Python-plugin bundle — full programmatic control

**Time:** 20 minutes | **Difficulty:** Intermediate | **Prerequisites:** [Build a YAML/Jinja bundle](build-a-yaml-bundle.md), familiarity with Python

When YAML + Jinja isn't enough — you need to call external APIs, inspect
contract shapes that Jinja can't reach, or run conditional logic that
gets tangled in template syntax — write a Python plugin instead. The
plugin author subclasses `fluid_sdk.CustomScaffold`, gets full Python at
their disposal, and is invoked by the engine through the same flow.

## What a Python-plugin bundle is

It's an installed Python package that:

1. Subclasses `fluid_sdk.CustomScaffold` (zero-dependency SDK — see the
   [SDK docs](https://github.com/Agenticstiger/forge-cli-sdk)).
2. Implements `plan(contract) → List[write_file actions]`.
3. Registers itself via a `pyproject.toml` entry-point in the
   `fluid_build.custom_scaffolds` group.

When a consumer's contract references the plugin by name, the engine
loads the class directly and calls its `plan()` method — no Jinja, no
manifest file, no `static/` directory.

## Step 1 — Author the plugin

```bash
mkdir my-org-scaffold && cd my-org-scaffold
mkdir -p src/my_org_scaffold tests
```

### `pyproject.toml`

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "my-org-scaffold"
version = "0.1.0"
description = "My org's standard data-product scaffold"
requires-python = ">=3.9"
dependencies = ["fluid-sdk>=0.1.0"]

[project.entry-points."fluid_build.custom_scaffolds"]
my-org = "my_org_scaffold.scaffold:MyOrgScaffold"

[tool.setuptools.packages.find]
where = ["src"]
```

The entry-point name (`my-org`) is what consumers reference in their
contract; the value points at the `CustomScaffold` subclass.

### `src/my_org_scaffold/__init__.py`

```python
"""Empty — just makes the directory a package."""
```

### `src/my_org_scaffold/scaffold.py`

```python
"""My org's custom scaffold plugin."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional

from fluid_sdk import (
    ContractHelper,
    CustomScaffold,
    PluginMetadata,
    write_file_action,
)


class MyOrgScaffold(CustomScaffold):
    """Generates a CDK app + GitLab CI from a fluid contract.

    Full Python at our disposal — no Jinja sandbox restrictions.
    """

    name = "my-org-scaffold"
    role = "custom_scaffold"

    def __init__(
        self,
        *,
        output_root: Optional[Any] = None,
        pattern_name: str = "default",
        variables: Optional[Mapping[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(output_root=output_root, **kwargs)
        self.pattern_name = pattern_name
        self.variables = dict(variables or {})

    @classmethod
    def get_plugin_info(cls) -> PluginMetadata:
        return PluginMetadata(
            name=cls.name,
            role=cls.role,
            display_name="My Org Scaffold",
            description="Generates a CDK app + GitLab CI",
            version="0.1.0",
            author="My Org Platform Team",
        )

    def plan(self, contract: Mapping[str, Any]) -> List[Dict[str, Any]]:
        c = ContractHelper(contract)

        # We have full Python here — anything Jinja can't express.
        # Example: only emit alerting config if the contract has it.
        actions: List[Dict[str, Any]] = []

        readme = self._render_readme(c)
        actions.append(write_file_action(
            path="README.md",
            content=readme.encode("utf-8"),
            description="Project README",
        ).to_dict())

        # Conditional emission — natural in Python, awkward in pure Jinja
        if c.environments:
            for env_name in sorted(c.environments):
                ts = self._render_env_config(c, env_name)
                actions.append(write_file_action(
                    path=f"config/{env_name}.ts",
                    content=ts.encode("utf-8"),
                    description=f"{env_name} env config",
                ).to_dict())

        return actions

    # ── Private rendering helpers (pure Python; no Jinja) ───────────

    def _render_readme(self, c: ContractHelper) -> str:
        envs = ", ".join(sorted(c.environments)) or "(none declared)"
        return (
            f"# {c.name or c.id}\n\n"
            f"{c.description or ''}\n\n"
            f"**Owner:** {c.owner.get('email', 'unknown')}\n"
            f"**Environments:** {envs}\n"
        )

    def _render_env_config(self, c: ContractHelper, env_name: str) -> str:
        env = c.environments[env_name]
        labels = env.get("metadata", {}).get("labels", {})
        account = labels.get("cloud.accountId", "MISSING")
        region = labels.get("cloud.region", "eu-central-1")
        return (
            "import { Config } from './config';\n\n"
            f"export const {env_name}Config: Config = {{\n"
            f"  environment: '{env_name}',\n"
            f"  accountId: '{account}',\n"
            f"  region: '{region}',\n"
            "};\n"
        )
```

`plan()` inherits `apply()` from `CustomScaffold` — the SDK's reference
implementation writes files atomically with path-traversal protection
and sha256 verification. You almost never override `apply()`.

## Step 2 — Test the plugin

The SDK ships a conformance test harness:

```python
# tests/test_scaffold.py
from fluid_sdk.testing import CustomScaffoldTestHarness, LOCAL_CONTRACT

from my_org_scaffold.scaffold import MyOrgScaffold


class TestMyOrgScaffold(CustomScaffoldTestHarness):
    plugin_class = MyOrgScaffold
    sample_contracts = [LOCAL_CONTRACT]
```

That's the whole test file. ~15 conformance tests run automatically —
determinism, idempotency, path-traversal safety, action shape, role
declaration, etc.

```bash
pip install -e .
pip install pytest
pytest -v
# ... 15+ tests pass ...
```

## Step 3 — Publish

```bash
pip install build twine
python -m build
twine upload dist/*
```

(Or push to an internal PyPI index. Or install from a private git URL.
The plugin just needs to be **pip-installable into the consumer's
environment.**)

## Step 4 — Consumer references the plugin

The consumer installs the plugin alongside the FLUID CLI and engine:

```bash
pip install data-product-forge data-product-forge-custom-scaffold my-org-scaffold
```

Then in their contract:

```yaml
extensions:
  customScaffold:
    libraries:
      - id: ci
        source:
          kind: entrypoint           # ← NEW source kind
          name: my-org               # ← matches the entry-point name
    patterns:
      - use: ci:default
        variables:
          someKey: someValue
```

Run:

```bash
fluid generate custom-scaffold
```

The engine:

1. Reads `extensions.customScaffold` from the contract.
2. Sees `kind: entrypoint`, resolves it via `importlib.metadata.entry_points(group="fluid_build.custom_scaffolds")`.
3. Finds the entry-point named `my-org`, loads `MyOrgScaffold`.
4. Instantiates it with `output_root=...`, `pattern_name="default"`, `variables={...}`.
5. Calls `plan(contract)` then `apply(actions)`.

Files appear on disk just like a YAML/Jinja bundle.

## When to use this vs YAML/Jinja

| Use YAML/Jinja when... | Use Python plugin when... |
|---|---|
| Output is mostly static text with substitutions | You need conditional file emission based on complex contract analysis |
| Per-env iteration is simple `{% for %}` | You need to call external APIs (catalog lookups, secret resolution) |
| Logic fits in a few `{% if %}` branches | You need to validate cross-references between contract sections |
| Authoring team is comfortable with templates | Authoring team prefers Python + IDE tooling |
| No external dependencies needed | You need libraries the engine doesn't ship (pyyaml-extras, jsonpath, etc.) |

You can mix — your org could ship multiple bundles, some YAML/Jinja
(simple ones) and some Python plugin (the complex ones). Consumers
choose by source kind. Everything else (validation flow, apply, the
contract shape) is identical.

## Limitations

- Plugin code runs in the same process as the FLUID CLI. It can do
  anything Python can do — read files, make HTTP calls, exec
  subprocesses. **Treat third-party plugins with the same trust level
  as third-party `pip install`s.**
- Plugins discovered via entry-points must be installed in the same
  Python environment as `data-product-forge-custom-scaffold`. They
  can't be fetched at runtime like git/path bundles.
- A Python plugin doesn't have a `static/` directory analogue. To ship
  binary fixtures alongside generated code, include them as Python
  package data and have `plan()` build appropriate `write_file` actions.

## What's next

- [Build a YAML/Jinja bundle](build-a-yaml-bundle.md) if you haven't already.
- [SDK reference](https://github.com/Agenticstiger/forge-cli-sdk/tree/main/docs) — every class + method on `CustomScaffold`, `ContractHelper`, etc.
- [Conformance test harness](https://github.com/Agenticstiger/forge-cli-sdk/blob/main/docs/reference/conformance-testing.md) — what the inherited tests actually check.
