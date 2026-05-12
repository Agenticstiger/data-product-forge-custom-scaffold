# `fluid-scaffold.yaml` reference

Every YAML/Jinja bundle ships a `fluid-scaffold.yaml` at its root. This document is the complete reference.

## Top-level structure

```yaml
apiVersion: fluid.dev/custom-scaffold.v1
bundle:
  name:        string   # required, lowercase identifier
  version:     string   # semver recommended
  description: string   # optional, surfaces in fluid plugins list
  author:      string   # optional
  license:     string   # optional, SPDX id
  url:         string   # optional, project homepage
patterns:
  - <pattern entry>   # at least one
```

`apiVersion` must be `fluid.dev/custom-scaffold.v1`. Other values are rejected.

## Pattern entry

```yaml
patterns:
  - name:        string     # required, unique within bundle
    description: string     # optional
    supportedProductTypes: [SDP, ADP, CDP]   # informational filter hint
    supportedCISystems:    [gitlab_ci, github_actions]   # informational

    variables:               # optional JSON-Schema for `patterns[].variables` overrides
      $schema: http://json-schema.org/draft-07/schema#
      properties:
        myVar: {type: string, default: "x"}

    requiredContractFields:  # list of dotted paths the engine checks pre-render
      - metadata.owner.email
      - environments.*.metadata.labels["cloud.accountId"]

    templates:
      - <template entry>     # at least one
```

## Template entry

```yaml
templates:
  - from: string    # source path within bundle; Jinja-templated
    to:   string    # destination path; Jinja-templated; relative
```

Each template entry is a 1:1 mapping from a Jinja template to one output file. The `to` path may contain Jinja variables, so it can vary by contract content (e.g. `to: build/{{ product_id }}.json`).

## The `static/` directory

If your bundle has a top-level `static/` directory, the engine **copies every file under it byte-for-byte** into the output root, preserving relative paths.

```
my-bundle/
├── fluid-scaffold.yaml
├── templates/
│   └── README.md.j2
└── static/
    ├── docs/
    │   └── runbook.md         → copied to <output>/docs/runbook.md
    └── data/
        └── sample.csv         → copied to <output>/data/sample.csv
```

Use this for binary fixtures, sample data, pre-rendered files — anything that doesn't need Jinja substitution.

## Path templating

`to` paths are rendered through Jinja before content rendering. Absolute paths and `..` segments are rejected (path-traversal guard).

```yaml
- from: templates/build.json.j2
  to:   build/{{ product_id }}.json
```

## Iterating over contract collections

For per-environment / per-consume / per-expose iteration, **use Jinja's `{% for %}` inside a single template** that emits the full multi-section file:

```jinja
{# templates/.gitlab-ci.yml.j2 #}
stages:
  - deploy

{% for env in environments|sort %}
deploy:{{ env }}:
  stage: deploy
  script: fluid apply --env {{ env }}
{% endfor %}
```

The engine does not have `forEach:` in the manifest spec — `{% for %}` in Jinja gives you the same expressive power with no extra grammar to learn.

## Conditional output

Same — use Jinja's `{% if %}` inside the template:

```jinja
{% if fluid.observability is defined %}
alerts:
  enabled: true
{% endif %}
```

If you want a whole file to be absent, render to an empty string and the engine writes an empty file. (If you genuinely need "don't emit this file at all," write a Python `CustomScaffold` plugin instead.)

## The render context

When a template renders, it sees:

```python
{
    # Full contract
    "fluid": <full contract dict>,

    # Ergonomic shortcuts (from ContractHelper)
    "metadata":      contract["metadata"],
    "product_id":    contract["id"],
    "product_name":  contract["name"],
    "product_type":  contract["metadata"]["productType"],
    "description":   contract["description"],
    "domain":        contract["domain"],
    "owner":         contract["metadata"]["owner"],
    "tags":          contract["tags"] or [],
    "labels":        contract["labels"] or {},
    "exposes":       [parsed expose dicts...],
    "consumes":      [parsed consume dicts...],
    "builds":        [parsed build dicts...],
    "environments":  contract["environments"] or {},
    "sovereignty":   contract["sovereignty"] or {},
    "security":      contract["security"] or {},

    # Engine defaults (overridable via variables)
    "ci":            {"system": "gitlab_ci"},

    # User variables (last to win)
    "myVar":         "...",
}
```

## `requiredContractFields` syntax

Dotted paths with `labels["key"]`, `*` (wildcard), and `[]` (non-empty array) support:

```yaml
requiredContractFields:
  - metadata.owner.email
  - metadata.labels["principal.steward.id"]
  - environments.*.metadata.labels["cloud.accountId"]
  - observability.alert.channels[]
```

- `*` requires at least one matching key.
- `[]` requires a non-empty list.
- Strings / lists / dicts must be non-empty to count as "present."

The engine checks these before the first byte is rendered. Missing → clear error, no files written.

## Full minimal example

```yaml
apiVersion: fluid.dev/custom-scaffold.v1
bundle:
  name: minimal-bundle
  version: 0.1.0

patterns:
  - name: just-readme
    templates:
      - from: templates/README.md.j2
        to:   README.md
```

With `templates/README.md.j2`:

```jinja
# {{ product_name }}

{{ description }}
```

That's a complete, working bundle.
