# acme-scaffold — a white-label "custom spec dialect"

This example shows how a third party reuses the
[`data-product-forge-custom-scaffold`](../../) engine under **their own brand**
without forking it — what we call a *custom spec dialect*.

ACME ships a tiny package that defines one [`ScaffoldDialect`](src/acme_scaffold/dialect.py)
and binds the engine's factories to it:

| Aspect | Built-in (fluid) | ACME dialect |
|---|---|---|
| Contract extension key | `extensions.customScaffold` | `extensions.acmeScaffold` |
| Bundle manifest `apiVersion` | `fluid.dev/custom-scaffold.v1` | `acme.dev/scaffold.v1` |
| CLI subcommand | `fluid generate custom-scaffold` | `fluid generate acme-scaffold` |
| Default contract | `contract.fluid.yaml` | `contract.acme.yaml` |

The dialect's callables are registered under the **same** `fluid_build.*`
entry-point *groups* the forge CLI already walks, with ACME *names* (see
[`pyproject.toml`](pyproject.toml)). That single registration makes the dialect:

- a CLI subcommand (`fluid_build.commands`),
- a contract validator for `extensions.acmeScaffold` (`fluid_build.extension_validators`), and
- **natively understood by the `fluid forge` copilot** — because it also advertises
  its JSON-Schema via `fluid_build.extension_schemas`, the copilot grounds
  generation on it and validates the emitted block, exactly like a core field.

> The entry-point *name* under `extension_validators` / `extension_schemas`
> MUST equal the extension sub-key (`acmeScaffold`): the forge CLI keys both by
> entry-point name == `contract.extensions.<key>`.

## Try it

```bash
pip install -e ../..            # the engine
pip install -e .                # this dialect

# Print the ACME extension schema (e.g. to prime the copilot):
fluid generate acme-scaffold --print-schema

# Generate from the sample contract (dry-run, nothing written):
fluid generate acme-scaffold -c sample/contract.acme.yaml --dry-run
```

Both dialects coexist: installing this package alongside the engine gives you
`fluid generate custom-scaffold` **and** `fluid generate acme-scaffold`,
validating `extensions.customScaffold` **and** `extensions.acmeScaffold`
independently.

## Notes

- For v1 the on-disk bundle manifest filename stays `fluid-scaffold.yaml`
  (the resolvers presence-check that name); the dialect's `apiVersion` is what
  brands the bundle. Everything else — extension key, subcommand, contract
  default, output branding — is yours.
- Bundle templates still reference the contract via `{{ fluid.* }}` / the
  ergonomic shortcuts (`{{ product_name }}`, `{{ owner.email }}`, …) regardless
  of dialect; those are template-author-facing, not branding.
