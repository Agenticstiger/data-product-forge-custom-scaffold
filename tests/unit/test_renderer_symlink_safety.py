"""Regression: refuse symlinks in bundle ``static/`` directories.

If a malicious bundle commits a symlink under ``static/`` (e.g.
``static/aws-creds`` → ``/home/victim/.aws/credentials``), the
renderer must refuse to follow it rather than read the symlink target
and emit its contents into the user's workspace.

The companion ``_render_one()`` path defends via ``resolve()`` +
``relative_to()``; ``_collect_static_files()`` now refuses symlinks
outright (a stronger and simpler stance — symlinks have no legitimate
use case in a bundle's static directory).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from data_product_forge_custom_scaffold.manifest import BundleManifest
from data_product_forge_custom_scaffold.renderer import Renderer, RenderError


def _make_bundle(root: Path) -> None:
    """Build a minimal valid bundle: manifest + one template + static/."""
    (root / "templates").mkdir()
    (root / "templates" / "noop.j2").write_text("just text\n")
    (root / "fluid-scaffold.yaml").write_text(
        "apiVersion: fluid.dev/custom-scaffold.v1\n"
        "bundle:\n"
        "  name: symlink-test-bundle\n"
        "  version: 1.0.0\n"
        "patterns:\n"
        "  - name: basic\n"
        "    templates:\n"
        "      - {from: templates/noop.j2, to: noop.txt}\n"
    )


def test_symlink_in_static_dir_is_rejected(tmp_path: Path) -> None:
    """A symlink under ``static/`` must trigger ``RenderError`` — even when
    its target is a valid file the engine could otherwise legitimately read."""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _make_bundle(bundle)

    # Create a sensitive file outside the bundle to act as the symlink target.
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("SUPER SECRET — would have been exfiltrated")

    # Plant the malicious symlink under static/.
    static = bundle / "static"
    static.mkdir()
    symlink_path = static / "innocent-name.txt"
    symlink_path.symlink_to(secret_file)

    # Sanity: the symlink resolves to the secret content (i.e. the read
    # primitive would work if the engine followed it).
    assert symlink_path.read_text() == "SUPER SECRET — would have been exfiltrated"

    manifest = BundleManifest.from_path(bundle)
    pattern = manifest.get_pattern("basic")
    renderer = Renderer(bundle)

    with pytest.raises(RenderError, match="symlinks are not permitted"):
        renderer.render_pattern(pattern, {"product_id": "x"})


def test_symlink_inside_subdir_of_static_also_rejected(tmp_path: Path) -> None:
    """Symlinks nested deeper under static/ must also be rejected."""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _make_bundle(bundle)

    nested = bundle / "static" / "configs"
    nested.mkdir(parents=True)
    target = tmp_path / "target.txt"
    target.write_text("escape attempt")
    (nested / "link").symlink_to(target)

    manifest = BundleManifest.from_path(bundle)
    pattern = manifest.get_pattern("basic")
    renderer = Renderer(bundle)

    with pytest.raises(RenderError, match="symlinks are not permitted"):
        renderer.render_pattern(pattern, {"product_id": "x"})


def test_regular_files_in_static_still_work(tmp_path: Path) -> None:
    """The fix must not break the legitimate copy-verbatim path."""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _make_bundle(bundle)

    static = bundle / "static"
    static.mkdir()
    (static / "regular.txt").write_text("hello\n")
    nested = static / "docs"
    nested.mkdir()
    (nested / "runbook.md").write_text("# Runbook\n")

    manifest = BundleManifest.from_path(bundle)
    pattern = manifest.get_pattern("basic")
    renderer = Renderer(bundle)

    rendered = renderer.render_pattern(pattern, {"product_id": "x"})
    paths = sorted(r.path for r in rendered)
    # 1 Jinja template (noop.txt) + 2 static files
    assert paths == ["docs/runbook.md", "noop.txt", "regular.txt"]

    by_path = {r.path: r.content for r in rendered}
    assert by_path["regular.txt"] == b"hello\n"
    assert by_path["docs/runbook.md"] == b"# Runbook\n"
