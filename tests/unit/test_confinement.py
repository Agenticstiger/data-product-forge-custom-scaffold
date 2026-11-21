"""``confine`` — the output-path check shared by the engine and the library API.

A planned path is accepted when it resolves to a file strictly inside the output
root (following symlinks that already exist) and is returned normalised; anything
else is an ``UnsafePathError``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from data_product_forge_custom_scaffold.confinement import confine, confine_actions
from data_product_forge_custom_scaffold.errors import EngineError, UnsafePathError


@pytest.fixture
def root(tmp_path: Path) -> Path:
    out = tmp_path / "out"
    out.mkdir()
    return out.resolve()


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("README.md", "README.md"),
        ("docs/runbook.md", "docs/runbook.md"),
        ("./docs/runbook.md", "docs/runbook.md"),
        ("a/../b.txt", "b.txt"),  # harmless dot-dot is normalised, as the SDK's apply accepts it
        ("a/b/../../c.txt", "c.txt"),
    ],
)
def test_relative_paths_are_normalised(root: Path, raw: str, expected: str) -> None:
    assert confine(root, raw) == expected


def test_absolute_path_inside_the_root_is_accepted_and_made_relative(root: Path) -> None:
    assert confine(root, str(root / "sub" / "x.txt")) == "sub/x.txt"


@pytest.mark.parametrize(
    "raw",
    [
        "../escape.txt",
        "a/../../escape.txt",
        "../out-sibling/x.txt",
        "/etc/passwd",
        "",
        ".",
        "a/..",
        "bad\0name",
    ],
)
def test_paths_that_leave_the_root_or_name_nothing_are_refused(root: Path, raw: str) -> None:
    with pytest.raises(UnsafePathError):
        confine(root, raw)


@pytest.mark.parametrize("raw", [None, 5, b"x.txt", ["x.txt"]])
def test_non_string_paths_are_refused(root: Path, raw: object) -> None:
    with pytest.raises(UnsafePathError, match="not a usable relative file path"):
        confine(root, raw)


def test_a_symlink_that_leads_out_of_the_root_is_refused(root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(UnsafePathError, match="outside the output directory"):
        confine(root, "link/x.txt")
    assert list(outside.iterdir()) == []


def test_a_symlink_that_stays_inside_the_root_reports_the_physical_path(root: Path) -> None:
    (root / "real").mkdir()
    (root / "alias").symlink_to(root / "real", target_is_directory=True)

    # Two spellings of one file get one key, so a manifest never lists it twice.
    assert confine(root, "alias/x.txt") == confine(root, "real/x.txt") == "real/x.txt"


def test_refusals_name_the_offending_path_and_are_engine_errors(root: Path) -> None:
    with pytest.raises(UnsafePathError) as ei:
        confine(root, "../escape.txt")

    assert "'../escape.txt'" in str(ei.value)
    assert isinstance(ei.value, EngineError)
    # The refusal does not drag the absolute location of the output directory in.
    assert str(root) not in str(ei.value)


# ── confine_actions: the plan-level check the engine runs before apply ─────────


def _write(path: object) -> dict:
    return {"op": "write_file", "params": {"path": path}}


def test_confine_actions_accepts_a_plan_that_stays_inside(root: Path) -> None:
    confine_actions(root, [_write("a.txt"), _write("sub/b.txt"), _write("c/../d.txt")])


def test_confine_actions_refuses_the_first_unsafe_path(root: Path) -> None:
    with pytest.raises(UnsafePathError, match="'../evil.txt'"):
        confine_actions(root, [_write("ok.txt"), _write("../evil.txt"), _write("/etc/passwd")])


def test_confine_actions_leaves_non_files_and_unusable_paths_to_apply(root: Path) -> None:
    # apply() reports these as skipped/failed actions; refusing them here would
    # change what a run with a sloppy plugin does.
    confine_actions(
        root,
        [
            {"op": "noop", "params": {"path": "../x"}},
            _write(""),
            _write(None),
            _write(7),
            {"op": "write_file"},
            {"op": "write_file", "params": None},
        ],
    )
