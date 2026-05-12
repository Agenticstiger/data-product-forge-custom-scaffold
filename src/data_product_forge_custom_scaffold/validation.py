"""Validator for ``contract.extensions.customScaffold``.

Registered as ``fluid_build.extension_validators / customScaffold``. The
FLUID CLI calls this with the contract's ``extensions`` block; we check
the ``customScaffold`` sub-key against our own JSON-Schema fragment and
append any errors to the caller-supplied list.

The fluid core schema treats ``extensions:`` as additionalProperties=true
— it doesn't know what shape the customScaffold sub-block has. This
validator fills that gap.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping

try:
    from jsonschema import Draft7Validator
except ImportError:
    Draft7Validator = None  # type: ignore[assignment]


# The JSON-Schema for `contract.extensions.customScaffold`. Kept inline
# (rather than as a separate .json file) so the validator is self-contained.
CUSTOM_SCAFFOLD_SCHEMA: Dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "extensions.customScaffold",
    "type": "object",
    "additionalProperties": False,
    "required": ["libraries", "patterns"],
    "properties": {
        "libraries": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "source"],
                "properties": {
                    "id": {
                        "type": "string",
                        "pattern": "^[a-zA-Z][a-zA-Z0-9_-]*$",
                        "description": "Local alias for this library, referenced by patterns[].use",
                    },
                    "source": {
                        "type": "object",
                        "required": ["kind"],
                        "properties": {
                            "kind": {
                                "type": "string",
                                "enum": ["path", "git", "entrypoint"],
                            },
                            # Common optional keys (kind-specific shapes live
                            # in the resolver implementations, not in core
                            # schema — we want the schema to stay flexible).
                            "path": {"type": "string"},
                            "url": {"type": "string"},
                            "ref": {"type": "string"},
                            "name": {"type": "string"},
                            "package": {"type": "string"},
                            "version": {"type": "string"},
                            "registry": {"type": "string"},
                            "index_url": {"type": "string"},
                            "subdir": {"type": "string"},
                            "auth": {
                                "type": "object",
                                "properties": {
                                    "secret_ref": {
                                        "type": "string",
                                        "pattern": "^[A-Za-z_][A-Za-z0-9_]*$",
                                    },
                                    "mode": {"type": "string"},
                                },
                                "additionalProperties": True,
                            },
                        },
                        "additionalProperties": True,
                    },
                },
            },
        },
        "patterns": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["use"],
                "properties": {
                    "use": {
                        "type": "string",
                        "pattern": r"^[a-zA-Z][a-zA-Z0-9_-]*:[a-zA-Z][a-zA-Z0-9_-]*$",
                        "description": "<library-id>:<pattern-name>",
                    },
                    "output": {"type": "string"},
                    "variables": {"type": "object", "additionalProperties": True},
                    "when": {"type": "object", "additionalProperties": True},
                    "environments": {"type": "object", "additionalProperties": True},
                },
            },
        },
        "policy": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "onMissingLib": {"type": "string", "enum": ["error", "warn", "skip"]},
                "allowNetworkFetch": {"type": "boolean"},
            },
        },
    },
}


def validate(extensions: Mapping[str, Any], errors: List[str]) -> None:
    """Validate ``extensions.customScaffold`` and append errors to *errors*.

    Called by the FLUID CLI's contract-validation orchestrator when our
    entry-point is loaded.

    No-op when the customScaffold sub-block is absent (the engine is
    opt-in).
    """
    if not isinstance(extensions, Mapping):
        return
    block = extensions.get("customScaffold")
    if block is None:
        return  # not opted in

    if Draft7Validator is None:
        errors.append(
            "data-product-forge-custom-scaffold: validation requires the "
            "'jsonschema' package; install via `pip install jsonschema`."
        )
        return

    validator = Draft7Validator(CUSTOM_SCAFFOLD_SCHEMA)
    for err in sorted(validator.iter_errors(block), key=lambda e: e.path):
        path = ".".join(str(p) for p in err.path) or "<root>"
        errors.append(f"extensions.customScaffold.{path}: {err.message}")

    # Cross-reference checks: every pattern's `use` must resolve to a
    # declared library id.
    libraries_by_id = {
        lib["id"]
        for lib in (block.get("libraries") or [])
        if isinstance(lib, Mapping) and isinstance(lib.get("id"), str)
    }
    for i, pat in enumerate(block.get("patterns") or []):
        if not isinstance(pat, Mapping):
            continue
        use = pat.get("use", "")
        if ":" not in use:
            continue
        lib_id, _ = use.split(":", 1)
        if lib_id not in libraries_by_id:
            errors.append(
                f"extensions.customScaffold.patterns[{i}].use references "
                f"unknown library id {lib_id!r} "
                f"(known: {sorted(libraries_by_id) or '(none declared)'})"
            )
