"""ACME Scaffold — a white-label "custom spec dialect" built on the
``data-product-forge-custom-scaffold`` engine.

It reuses the engine wholesale, but under ACME's own branding:

* ``contract.extensions.acmeScaffold``      (not ``customScaffold``)
* bundle-manifest ``apiVersion: acme.dev/scaffold.v1``
* ``fluid generate acme-scaffold``           (not ``custom-scaffold``)

The three module-level callables below are bound to :data:`ACME_DIALECT` and
registered under the standard ``fluid_build.*`` entry-point groups (with ACME
*names*) in ``pyproject.toml``. The forge CLI therefore discovers them natively,
and the ``fluid forge`` copilot can generate + validate ``extensions.acmeScaffold``
blocks just like any built-in contract field — no forge-cli changes required.
"""

from __future__ import annotations

from data_product_forge_custom_scaffold import (
    ScaffoldDialect,
    make_register,
    make_schema_provider,
    make_validator,
)

ACME_DIALECT = ScaffoldDialect(
    extension_key="acmeScaffold",
    manifest_api_versions=("acme.dev/scaffold.v1",),
    # v1: the on-disk bundle manifest filename stays the engine default.
    manifest_filename="fluid-scaffold.yaml",
    command_name="acme-scaffold",
    contract_default_path="contract.acme.yaml",
    display_name="ACME scaffold",
    error_prefix="acme-scaffold",
    aggregate_plugin_name="acme-scaffold-engine",
)

# CLI-facing callables bound to the ACME dialect. Each is registered under a
# standard fluid_build.* GROUP with an ACME entry-point NAME (see pyproject.toml).
register = make_register(ACME_DIALECT)  # fluid_build.commands
validate = make_validator(ACME_DIALECT)  # fluid_build.extension_validators
get_extension_schema = make_schema_provider(ACME_DIALECT)  # fluid_build.extension_schemas
