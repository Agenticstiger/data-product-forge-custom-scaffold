"""The lock is the engine's own file: it is never written or read through a symlink.

``fluid-scaffold.lock`` records contract variables verbatim. A symlink planted at its
name in the output directory (a checked-out repository can carry one) used to make
the run overwrite the link's target — a file outside the output directory — with that
YAML, then report the lock as a confined output.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from data_product_forge_custom_scaffold import Engine, UnsafePathError, generate
from data_product_forge_custom_scaffold import lockfile as lockfile_mod
from data_product_forge_custom_scaffold.lockfile import LOCKFILE_NAME, read_lock, write_lock
from tests import helpers
from tests.helpers import parse_cli, reference_bundle, reference_contract

VICTIM_TEXT = "export SAFE=1\n"


def _victim(tmp_path: Path) -> Path:
    victim = tmp_path / "victim.rc"
    victim.write_text(VICTIM_TEXT)
    return victim


def _link_lock(out: Path, target: object) -> Path:
    out.mkdir(exist_ok=True)
    link = out / LOCKFILE_NAME
    link.symlink_to(target)
    return link


def _contract(tmp_path: Path) -> dict:
    contract = reference_contract(reference_bundle(tmp_path))
    contract["extensions"]["customScaffold"]["patterns"][0]["variables"] = {
        "teamName": "k: $(echo PWNED)"
    }
    return contract


@pytest.mark.parametrize("where", ["outside", "inside", "dangling"])
def test_write_lock_refuses_a_planted_symlink_and_leaves_its_target_alone(
    tmp_path: Path, where: str
) -> None:
    out = tmp_path / "out"
    victim = _victim(tmp_path)
    inside = out / "other.txt"
    out.mkdir()
    inside.write_text(VICTIM_TEXT)
    target = {"outside": victim, "inside": inside, "dangling": tmp_path / "nowhere"}[where]
    _link_lock(out, target)

    with pytest.raises(UnsafePathError, match="is a symlink"):
        write_lock(out, {"lockfileVersion": 1})

    assert victim.read_text() == VICTIM_TEXT and inside.read_text() == VICTIM_TEXT
    assert not (tmp_path / "nowhere").exists()


def test_read_lock_refuses_a_planted_symlink(tmp_path: Path) -> None:
    victim = _victim(tmp_path)
    _link_lock(tmp_path / "out", victim)

    with pytest.raises(UnsafePathError, match="is a symlink"):
        read_lock(tmp_path / "out")


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="needs O_NOFOLLOW")
def test_write_lock_does_not_follow_a_link_that_appears_after_the_check(
    tmp_path: Path, monkeypatch
) -> None:
    # The path check and the open are two steps; the open itself must also refuse a link
    # an attacker plants in between.
    out = tmp_path / "out"
    out.mkdir()
    victim = _victim(tmp_path)
    real_lock_path = lockfile_mod.lock_path

    def plant_after_the_check(root: Path) -> Path:
        path = real_lock_path(root)  # no link yet: the check passes
        path.symlink_to(victim)  # the attacker wins the race
        return path

    monkeypatch.setattr(lockfile_mod, "lock_path", plant_after_the_check)
    with pytest.raises(UnsafePathError, match="is a symlink"):
        write_lock(out, {"lockfileVersion": 1})

    assert victim.read_text() == VICTIM_TEXT


@pytest.mark.parametrize("dry_run", [False, True])
def test_generate_refuses_the_linked_lock_before_writing_anything(
    tmp_path: Path, dry_run: bool
) -> None:
    out = tmp_path / "out"
    victim = _victim(tmp_path)
    _link_lock(out, "../victim.rc")

    with pytest.raises(UnsafePathError, match="is a symlink"):
        generate(_contract(tmp_path), out, dry_run=dry_run)

    assert victim.read_text() == VICTIM_TEXT
    assert "PWNED" not in victim.read_text()
    # No pattern was applied either: the link is the only thing in the directory.
    assert [p.name for p in out.iterdir()] == [LOCKFILE_NAME]


def test_generate_reads_the_lock_back_through_the_same_guard(tmp_path: Path, monkeypatch) -> None:
    # Swap the lock for a link after the run wrote it: the read-back must not hash the
    # link's target.
    out = tmp_path / "out"
    victim = _victim(tmp_path)
    real_run = Engine.run

    def run_then_swap(self, *args, **kwargs):
        result = real_run(self, *args, **kwargs)
        assert result.lockfile is not None
        result.lockfile.unlink()
        result.lockfile.symlink_to(victim)
        return result

    monkeypatch.setattr(Engine, "run", run_then_swap)
    with pytest.raises(UnsafePathError, match="is a symlink"):
        generate(_contract(tmp_path), out)

    assert victim.read_text() == VICTIM_TEXT


def test_the_command_refuses_a_linked_lock_with_exit_2(tmp_path: Path, capsys) -> None:
    contract_file = tmp_path / "contract.fluid.yaml"
    contract_file.write_text(
        "fluidVersion: '0.7.4'\nkind: DataProduct\nid: x\nname: X\n"
        "metadata: {owner: {team: t, email: t@example.com}}\n"
        "environments: {dev: {metadata: {labels: {cloud.region: us-east-1}}}}\n"
        "extensions:\n  customScaffold:\n    libraries:\n"
        f"      - id: ref\n        source: {{kind: path, path: '{reference_bundle(tmp_path)}'}}\n"
        "    patterns:\n      - use: ref:basic\n        variables: {teamName: t}\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    victim = _victim(tmp_path)
    _link_lock(out, victim)

    ns = parse_cli("custom-scaffold", "-c", str(contract_file), "-o", str(out))
    rc = ns.func(ns)
    captured = capsys.readouterr()

    assert rc == 2 and captured.out == ""
    assert captured.err == (
        f"error: {LOCKFILE_NAME!r} in the output directory is a symlink; refusing to use it\n"
    )
    assert victim.read_text() == VICTIM_TEXT


def test_a_regular_lock_is_still_written_and_replaced(tmp_path: Path) -> None:
    # The guard must not get in the way of the normal case, a first run or a re-run.
    out = tmp_path / "out"
    first = generate(_contract(tmp_path), out)
    second = generate(_contract(tmp_path), out)

    assert (out / LOCKFILE_NAME).is_file() and not (out / LOCKFILE_NAME).is_symlink()
    assert first.files == second.files
    assert helpers.tree_digests(out)[LOCKFILE_NAME][1] > 0
