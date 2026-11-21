"""Builders shared by the engine and library-API tests."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import yaml

from data_product_forge_custom_scaffold.engine import Engine

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SECRET = "sk-live-supersecret-do-not-leak"


def write_bundle(
    root: Path, patterns: Mapping[str, Mapping[str, str]], *, name: str = "bundle"
) -> Path:
    """Write a YAML/Jinja bundle under *root*.

    *patterns* maps a pattern name to ``{destination: file text}``; each file text is
    stored as a template (``templates/<pattern>-<n>.j2``), so plain text renders as
    itself and a destination may be a Jinja expression.
    """
    bundle = root / name
    (bundle / "templates").mkdir(parents=True)
    declared = []
    for pattern, files in patterns.items():
        entries = []
        for i, (dest, text) in enumerate(files.items()):
            template = f"templates/{pattern}-{i}.j2"
            (bundle / template).write_text(text)
            entries.append({"from": template, "to": dest})
        declared.append({"name": pattern, "templates": entries})
    (bundle / "fluid-scaffold.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "fluid.dev/custom-scaffold.v1",
                "bundle": {"name": name, "version": "1.0.0"},
                "patterns": declared,
            }
        )
    )
    return bundle


def path_contract(
    bundle: Path,
    uses: Sequence[str],
    *,
    lib: str = "lib",
    source: Optional[Mapping[str, Any]] = None,
    variables: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """A contract that declares *bundle* as library *lib* and runs *uses*.

    A *uses* entry is a pattern name (taken to belong to *lib*) or a full
    ``library:pattern`` value, so a test can also name a library that was never
    declared. *variables*, if given, are set on every pattern.
    """
    patterns: List[Dict[str, Any]] = []
    for use in uses:
        pattern: Dict[str, Any] = {"use": use if ":" in use else f"{lib}:{use}"}
        if variables is not None:
            pattern["variables"] = dict(variables)
        patterns.append(pattern)
    return {
        "id": "helper-product",
        "extensions": {
            "customScaffold": {
                "libraries": [
                    {"id": lib, "source": dict(source or {"kind": "path", "path": str(bundle)})}
                ],
                "patterns": patterns,
            }
        },
    }


def reference_bundle(root: Path) -> Path:
    """Copy the reference fixture bundle under *root* (once) and return it."""
    bundle = root / "reference_bundle"
    if not bundle.exists():
        shutil.copytree(FIXTURES / "reference_bundle", bundle)
    return bundle


def reference_contract(bundle: Path) -> Dict[str, Any]:
    """A contract that satisfies the reference bundle's required fields."""
    contract = path_contract(bundle, ["basic"], lib="ref", variables={"teamName": "platform"})
    contract.update(
        {
            "fluidVersion": "0.7.4",
            "kind": "DataProduct",
            "name": "Helper Product",
            "metadata": {"owner": {"team": "t", "email": "t@example.com"}},
            "environments": {"dev": {"metadata": {"labels": {"cloud.region": "us-east-1"}}}},
        }
    )
    return contract


def tree_digests(root: Path) -> Dict[str, Tuple[str, int]]:
    """Independent oracle: ``{relative path: (sha256, size)}`` read straight from disk."""
    out: Dict[str, Tuple[str, int]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            data = path.read_bytes()
            out[path.relative_to(root).as_posix()] = (hashlib.sha256(data).hexdigest(), len(data))
    return out


class ScriptedScaffold:
    """Duck-typed scaffold: a fixed plan, a scriptable ``apply()``, and a call counter.

    With no *apply* script, ``apply()`` fails the test — for tests whose plan must be
    refused before anything runs.
    """

    def __init__(
        self, actions: List[dict], apply: Optional[Callable[[List[dict]], Any]] = None
    ) -> None:
        self._actions = actions
        self._apply = apply
        self.apply_calls = 0

    def plan(self, contract: Mapping[str, Any]) -> List[dict]:
        return self._actions

    def apply(self, actions: List[dict]) -> Any:
        self.apply_calls += 1
        if self._apply is None:
            raise AssertionError("apply() must not run for a refused plan")
        return self._apply(actions)


def use_scaffold(monkeypatch: Any, scaffold: Any) -> None:
    """Make the engine use *scaffold* for every pattern (libraries still resolve)."""
    monkeypatch.setattr(Engine, "_instantiate_scaffold", lambda self, *a, **k: scaffold)
