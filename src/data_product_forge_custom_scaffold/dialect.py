"""ScaffoldDialect — the white-label / "custom spec dialect" knobs.

A *dialect* bundles every namespacing decision the engine makes, so a third
party can re-use this engine under their own branding — their own
``contract.extensions.<key>`` sub-key, their own bundle-manifest ``apiVersion``,
their own ``fluid generate <command>`` subcommand — **without forking**.

The engine ships exactly one dialect, :data:`DEFAULT`, equal to the historical
hardcoded fluid values. Every public default (the module-level
``validate`` / ``register`` / ``get_extension_schema`` callables, and manifest
parsing) is bound to :data:`DEFAULT`, so behaviour is byte-for-byte unchanged
for existing users. White-label authors construct their own dialect and bind the
``make_validator`` / ``make_register`` / ``make_schema_provider`` factories to it,
then register those callables under the same ``fluid_build.*`` entry-point groups
with their own entry *names*.

The per-key ``extension_key`` namespacing mirrors the OpenAPI vendor-extension
convention (``x-<vendor>-*``) and the per-tool keying used by
``validate-pyproject``'s ``tool_schema`` plugins.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from .version import MANIFEST_API_VERSION


@dataclass(frozen=True)
class ScaffoldDialect:
    """Immutable namespacing config for one scaffold "spec dialect".

    Fields default to the built-in fluid values, so a white-label author only
    overrides what differs (typically ``extension_key``, ``command_name`` and
    ``manifest_api_versions``).

    Attributes:
        extension_key: the ``contract.extensions`` sub-key this dialect claims
            (e.g. ``"customScaffold"``). This is also the entry-point *name* a
            white-label package registers under ``fluid_build.extension_validators``
            and ``fluid_build.extension_schemas`` (the forge CLI keys validators
            and schemas by entry-point name == extension sub-key).
        manifest_api_versions: accepted bundle-manifest ``apiVersion`` values.
            A tuple so a dialect may accept several versions while still
            rejecting unknown ones; the first entry is the recommended/primary.
        manifest_filename: on-disk bundle-manifest filename.
        command_name: the ``fluid generate <command_name>`` subcommand name.
        contract_default_path: default ``--contract`` path for the subcommand.
        display_name: human label used in CLI help / summaries.
        error_prefix: prefix for validator diagnostics that name this package.
        aggregate_plugin_name: ``ExecutionResult.plugin`` label for an engine
            run under this dialect.
    """

    extension_key: str = "customScaffold"
    manifest_api_versions: Tuple[str, ...] = (MANIFEST_API_VERSION,)
    manifest_filename: str = "fluid-scaffold.yaml"
    command_name: str = "custom-scaffold"
    contract_default_path: str = "contract.fluid.yaml"
    display_name: str = "custom scaffold"
    error_prefix: str = "data-product-forge-custom-scaffold"
    aggregate_plugin_name: str = "custom-scaffold-engine"

    @property
    def primary_api_version(self) -> str:
        """The ``apiVersion`` this dialect writes / recommends (first accepted)."""
        return self.manifest_api_versions[0]

    @property
    def schema_title(self) -> str:
        """JSON-Schema ``title`` for this dialect's extension block."""
        return f"extensions.{self.extension_key}"


# The built-in fluid dialect. Equal to the historical hardcoded values, so all
# module-level defaults preserve current behaviour exactly.
DEFAULT = ScaffoldDialect()
