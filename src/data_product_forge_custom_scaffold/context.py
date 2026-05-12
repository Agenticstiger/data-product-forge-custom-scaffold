"""Render-context builder.

The render context is the dict Jinja templates evaluate against. Built
from two layers (later wins):

1. **Contract** — full contract dict (key: ``fluid``) + ergonomic
   shortcuts (``metadata``, ``product_id``, ``owner``, ``consumes``,
   ``exposes``, ``builds``, ``environments``, ``observability``, ``labels``).
2. **Variables** — user-supplied overrides from
   ``contract.extensions.customScaffold.patterns[].variables``.

Templates access raw label keys directly:
``metadata.labels["governance.businessArea"]``.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping

from fluid_sdk import ContractHelper


def build_render_context(
    contract: Mapping[str, Any],
    *,
    variables: Mapping[str, Any] = None,
) -> Dict[str, Any]:
    """Construct the render context for a pattern invocation."""
    variables = dict(variables or {})
    c = ContractHelper(contract)

    ctx: Dict[str, Any] = {
        "fluid": dict(contract),
        # Ergonomic shortcuts
        "metadata": c.metadata,
        "product_id": c.id or "",
        "product_name": c.name or "",
        "product_type": c.product_type or "",
        "description": c.description or "",
        "domain": c.domain or "",
        "owner": c.owner,
        "tags": c.tags,
        "labels": c.labels,
        "exposes": [e.raw for e in c.exposes()],
        "consumes": [k.raw for k in c.consumes()],
        "builds": [b.raw for b in c.builds()],
        "environments": c.environments,
        "sovereignty": c.sovereignty,
        "security": c.security,
        "access_policy": c.access_policy,
        # Engine-supplied default
        "ci": {"system": "gitlab_ci"},
    }

    # User variables override.
    ctx.update(variables)
    return ctx
