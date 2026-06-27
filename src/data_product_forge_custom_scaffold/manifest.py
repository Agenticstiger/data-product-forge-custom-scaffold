"""Parser for ``fluid-scaffold.yaml`` bundle manifests.

Every YAML/Jinja bundle ships a ``fluid-scaffold.yaml`` at its root.
Minimal v1 schema::

    apiVersion: fluid.dev/custom-scaffold.v1
    bundle:
      name: my-bundle
      version: 1.0.0
    patterns:
      - name: basic
        description: GitLab CI + README from a fluid contract
        supportedProductTypes: [SDP, ADP, CDP]
        variables:                       # optional JSON-Schema for overrides
          $schema: http://json-schema.org/draft-07/schema#
          properties:
            parentCiTemplateRef: { type: string }
        requiredContractFields:          # cheap presence guard
          - metadata.owner.email
          - environments
        templates:
          - from: templates/.gitlab-ci.yml.j2
            to: .gitlab-ci.yml
          - from: templates/README.md.j2
            to: README.md

Bundle authors who need per-environment or per-consume files use Jinja
``{% for %}`` loops inside a single template that renders the whole
multi-section file. For static binary fixtures, drop them in a top-level
``static/`` directory in the bundle — the engine copies that directory
to the output root verbatim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Mapping, Optional

import yaml

from .dialect import DEFAULT as DEFAULT_DIALECT
from .dialect import ScaffoldDialect
from .version import MANIFEST_API_VERSION

MANIFEST_FILENAME = "fluid-scaffold.yaml"
STATIC_DIRNAME = "static"


class ManifestError(ValueError):
    """Raised when ``fluid-scaffold.yaml`` is malformed or unparseable."""


# ---------------------------------------------------------------------------
# Template entry — one file emission
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TemplateEntry:
    """One file-emission directive from ``patterns[].templates[]``.

    Attributes:
        from_path: Path to the Jinja template within the bundle.
        to_path: Destination path relative to the output root. May
            itself contain Jinja variables.
    """

    from_path: str
    to_path: str
    raw: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "TemplateEntry":
        if not isinstance(d, Mapping):
            raise ManifestError(f"template entry must be a mapping, got {type(d).__name__}")
        if not d.get("from"):
            raise ManifestError("template entry missing required 'from'")
        if not d.get("to"):
            raise ManifestError(f"template entry missing required 'to' (from={d.get('from')!r})")
        return cls(
            from_path=str(d["from"]),
            to_path=str(d["to"]),
            raw=dict(d),
        )


# ---------------------------------------------------------------------------
# Pattern entry — what the bundle does
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PatternEntry:
    """One pattern declaration from a bundle's ``fluid-scaffold.yaml``.

    Field status — what the engine actually does with each:

    * ``name`` / ``description`` — pattern identity.
    * ``required_contract_fields`` — **enforced**: a fail-fast presence check on
      the contract before rendering (see ``TemplatedCustomScaffold``).
    * ``templates`` — **enforced**: the file-emission directives.
    * ``variables_schema`` (the ``variables`` block) — **reserved**: parsed, but
      NOT yet validated against the user-supplied variables. Declaring it has no
      effect today; per-variable validation is a planned feature.
    * ``supported_product_types`` / ``supported_ci_systems`` — **advisory**
      declarative metadata for catalogs and tooling; NOT enforced by the engine
      (it receives no target product-type / CI-system to gate against).
    """

    name: str
    description: str = ""
    # Advisory metadata — not enforced by the engine (see class docstring).
    supported_product_types: List[str] = field(default_factory=list)
    supported_ci_systems: List[str] = field(default_factory=list)
    # Reserved — parsed but not yet validated against user variables.
    variables_schema: Mapping[str, Any] = field(default_factory=dict)
    required_contract_fields: List[str] = field(default_factory=list)
    templates: List[TemplateEntry] = field(default_factory=list)
    raw: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "PatternEntry":
        if not isinstance(d, Mapping):
            raise ManifestError(f"pattern entry must be a mapping, got {type(d).__name__}")
        if not d.get("name"):
            raise ManifestError("pattern entry missing required 'name'")
        return cls(
            name=str(d["name"]),
            description=str(d.get("description", "")),
            supported_product_types=list(d.get("supportedProductTypes") or []),
            supported_ci_systems=list(d.get("supportedCISystems") or []),
            variables_schema=dict(d.get("variables") or {}),
            required_contract_fields=list(d.get("requiredContractFields") or []),
            templates=[
                TemplateEntry.from_dict(t)
                for t in (d.get("templates") or [])
                if isinstance(t, Mapping)
            ],
            raw=dict(d),
        )


# ---------------------------------------------------------------------------
# Bundle identity
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BundleIdentity:
    """``bundle:`` block."""

    name: str = ""
    version: str = "0.0.0"
    description: str = ""
    author: str = ""
    license: Optional[str] = None
    url: Optional[str] = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "BundleIdentity":
        d = d or {}
        return cls(
            name=str(d.get("name", "")),
            version=str(d.get("version", "0.0.0")),
            description=str(d.get("description", "")),
            author=str(d.get("author", "")),
            license=d.get("license"),
            url=d.get("url"),
            raw=dict(d),
        )


# ---------------------------------------------------------------------------
# Full manifest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BundleManifest:
    """Parsed ``fluid-scaffold.yaml`` — typed, read-only."""

    api_version: str = MANIFEST_API_VERSION
    bundle: BundleIdentity = field(default_factory=BundleIdentity)
    patterns: List[PatternEntry] = field(default_factory=list)
    bundle_root: Optional[Path] = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(
        cls,
        d: Mapping[str, Any],
        *,
        bundle_root: Optional[Path] = None,
        dialect: ScaffoldDialect = DEFAULT_DIALECT,
    ) -> "BundleManifest":
        if not isinstance(d, Mapping):
            raise ManifestError(f"top-level manifest must be a mapping, got {type(d).__name__}")
        api_version = str(d.get("apiVersion", dialect.primary_api_version))
        if api_version not in dialect.manifest_api_versions:
            accepted = ", ".join(repr(v) for v in dialect.manifest_api_versions)
            raise ManifestError(
                f"unsupported apiVersion {api_version!r} (this dialect understands {accepted})"
            )
        bundle = BundleIdentity.from_dict(d.get("bundle") or {})
        if not bundle.name:
            raise ManifestError("bundle.name is required")

        patterns_raw = d.get("patterns") or []
        if not isinstance(patterns_raw, list):
            raise ManifestError(f"patterns must be a list, got {type(patterns_raw).__name__}")
        if not patterns_raw:
            raise ManifestError("manifest must declare at least one pattern")

        patterns = [PatternEntry.from_dict(p) for p in patterns_raw if isinstance(p, Mapping)]
        names = [p.name for p in patterns]
        if len(names) != len(set(names)):
            raise ManifestError(f"pattern names must be unique within a bundle: {names}")

        return cls(
            api_version=api_version,
            bundle=bundle,
            patterns=patterns,
            bundle_root=bundle_root,
            raw=dict(d),
        )

    @classmethod
    def from_path(
        cls, bundle_root: Path, *, dialect: ScaffoldDialect = DEFAULT_DIALECT
    ) -> "BundleManifest":
        bundle_root = Path(bundle_root).resolve()
        manifest_path = bundle_root / dialect.manifest_filename
        if not manifest_path.is_file():
            raise ManifestError(f"no {dialect.manifest_filename} found in {bundle_root}")
        with manifest_path.open("rb") as fh:
            data = yaml.safe_load(fh)
        return cls.from_dict(data or {}, bundle_root=bundle_root, dialect=dialect)

    def get_pattern(self, name: str) -> Optional[PatternEntry]:
        for p in self.patterns:
            if p.name == name:
                return p
        return None

    def pattern_names(self) -> List[str]:
        return [p.name for p in self.patterns]
