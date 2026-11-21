"""Builders shared by the engine and library-API tests."""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import yaml
from fluid_sdk import CustomScaffold

from data_product_forge_custom_scaffold import cli
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


class SdkScaffold(CustomScaffold):
    """A scaffold with a fixed plan whose ``apply()`` is the SDK's own (atomic, path-guarded)."""

    name = "sdk-scaffold"

    def __init__(self, actions: List[dict], *, output_root: Path) -> None:
        super().__init__(output_root=output_root)
        self._actions = actions

    def plan(self, contract: Mapping[str, Any]) -> List[dict]:
        return self._actions


def use_scaffold(monkeypatch: Any, scaffold: Any) -> None:
    """Make the engine use *scaffold* for every pattern (libraries still resolve)."""
    monkeypatch.setattr(Engine, "_instantiate_scaffold", lambda self, *a, **k: scaffold)


def parse_cli(*argv: str) -> argparse.Namespace:
    """Parse *argv* with the parser the host builds for the registered subcommand."""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    cli.make_register()(sub)
    return parser.parse_args(list(argv))


def git_source(
    url: str, ref: str = "v1", auth: Optional[Mapping[str, Any]] = None
) -> Dict[str, Any]:
    """A ``git`` library source, as a contract writes it."""
    source: Dict[str, Any] = {"kind": "git", "url": url, "ref": ref}
    if auth is not None:
        source["auth"] = dict(auth)
    return source


def patch_run(monkeypatch: Any, module: Any, respond: Callable[[Any, Dict[str, Any]], Any]):
    """Replace ``subprocess.run`` as *module* sees it. *respond(args, kwargs)* answers a call.

    Returns the list of ``(args, kwargs)`` every call was given, filled in as they happen.
    """
    calls: List[Tuple[Any, Dict[str, Any]]] = []

    def fake(args: Any, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        return respond(args, kwargs)

    monkeypatch.setattr(module.subprocess, "run", fake)
    return calls


def completed(args: Any, stdout: str = "") -> "subprocess.CompletedProcess[str]":
    return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")
