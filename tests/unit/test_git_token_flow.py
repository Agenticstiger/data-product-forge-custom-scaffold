"""A credential reaches git through the environment, and nowhere else.

The token goes to git as ``http.<url>.extraHeader`` in ``GIT_CONFIG_*`` environment pairs. It
is not on any command line (``ps`` shows a command line to every user), not in the clone's
config, and not in ``FETCH_HEAD``.

The tests run the real resolver and real git without a network: git rewrites the source's url to
a local directory (``url.<base>.insteadOf``), the way it would rewrite a mirror.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import List

import pytest

from data_product_forge_custom_scaffold.resolvers.base import ResolutionError
from data_product_forge_custom_scaffold.resolvers.git import GitResolver

TOKEN = "s3cret-token-value"
URL = "https://example.invalid/org/repo"
MANIFEST = "fluid-scaffold.yaml"
AUTH = {"secret_ref": "FLUID_SCAFFOLD_TEST_TOKEN"}

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary not on PATH")


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t.t", "-c", "user.name=t", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


@pytest.fixture
def served(tmp_path: Path, monkeypatch) -> SimpleNamespace:
    """A bundle repository that ``https://example.invalid/`` reaches, with an operator setup.

    ``main`` and an annotated tag ``v1`` sit on one commit. The operator allows the host and
    holds the token in ``FLUID_SCAFFOLD_TEST_TOKEN``.
    """
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q")
    _git(work, "symbolic-ref", "HEAD", "refs/heads/main")
    (work / MANIFEST).write_text("apiVersion: forge.scaffold/v1\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "c1")
    _git(work, "tag", "-a", "-m", "v1", "v1")  # annotated: FETCH_HEAD names the tag object
    server = tmp_path / "server" / "org"
    server.mkdir(parents=True)
    subprocess.run(
        ["git", "clone", "-q", "--bare", str(work), str(server / "repo.git")], check=True
    )

    config = tmp_path / "gitconfig"
    config.write_text(
        f'[url "file://{tmp_path / "server"}/"]\n\tinsteadOf = https://example.invalid/\n'
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("FLUID_CUSTOM_SCAFFOLD_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("FLUID_CUSTOM_SCAFFOLD_NOCACHE", raising=False)
    monkeypatch.setenv("FLUID_CUSTOM_SCAFFOLD_AUTH_HOSTS", "example.invalid")
    monkeypatch.setenv("FLUID_SCAFFOLD_TEST_TOKEN", TOKEN)
    return SimpleNamespace(sha=_git(work, "rev-parse", "HEAD"), root=tmp_path)


def _source(ref: str, *, auth: bool = True) -> dict:
    source = {"kind": "git", "url": URL, "ref": ref}
    return {**source, "auth": AUTH} if auth else source


def _text_under(root: Path) -> str:
    return "\n".join(p.read_text(errors="replace") for p in root.rglob("*") if p.is_file())


def _basic(token: str) -> str:
    import base64

    return base64.b64encode(f"x-access-token:{token}".encode()).decode()


@pytest.mark.parametrize("ref", ["main", "v1", "commit"])
def test_a_credentialed_fetch_leaves_neither_the_token_nor_its_header_in_the_cache(
    served: SimpleNamespace, ref: str
) -> None:
    ref = served.sha if ref == "commit" else ref

    bundle = GitResolver().resolve(_source(ref))

    assert (bundle.bundle_root / MANIFEST).is_file()
    assert bundle.resolved_version == served.sha
    on_disk = _text_under(served.root / "cache")
    assert TOKEN not in on_disk and _basic(TOKEN) not in on_disk


def test_the_clone_records_the_plain_url_as_origin(served: SimpleNamespace) -> None:
    bundle = GitResolver().resolve(_source("main"))

    assert _git(bundle.bundle_root, "config", "--get", "remote.origin.url") == URL


@pytest.mark.parametrize("ref", ["main", "commit"])
def test_no_git_step_leaves_the_token_on_disk(served: SimpleNamespace, monkeypatch, ref) -> None:
    # A kill can land after any step, so look after every one of them.
    ref = served.sha if ref == "commit" else ref
    cache_dir = served.root / "cache" / "clone"
    real_git, real_git_ok = GitResolver._git, GitResolver._git_ok
    after_each_step: List[str] = []

    def watching_git(args, *, url, what, credential=None):
        real_git(args, url=url, what=what, credential=credential)
        after_each_step.append(_text_under(cache_dir.parent))  # the clone is built beside it

    def watching_git_ok(args, *, url="", credential=None):
        ok = real_git_ok(args, url=url, credential=credential)
        after_each_step.append(_text_under(cache_dir.parent))
        return ok

    monkeypatch.setattr(GitResolver, "_git", staticmethod(watching_git))
    monkeypatch.setattr(GitResolver, "_git_ok", staticmethod(watching_git_ok))

    GitResolver()._clone_into_cache(URL, ref, cache_dir, AUTH)

    assert after_each_step
    assert all(TOKEN not in text and _basic(TOKEN) not in text for text in after_each_step)


def test_a_credentialed_source_gets_a_clone_of_its_own(served: SimpleNamespace) -> None:
    private = GitResolver().resolve(_source("main"))
    public = GitResolver().resolve(_source("main", auth=False))

    assert private.bundle_root != public.bundle_root  # the key names the credential
    assert (private.bundle_root / MANIFEST).is_file() and (public.bundle_root / MANIFEST).is_file()


def test_a_run_with_the_token_unset_is_refused_before_git_runs(
    served: SimpleNamespace, monkeypatch
) -> None:
    monkeypatch.delenv("FLUID_SCAFFOLD_TEST_TOKEN")

    with pytest.raises(ResolutionError, match="unset"):
        GitResolver().resolve(_source("main"))

    assert not (served.root / "cache" / "git").exists() or not any(
        (served.root / "cache" / "git").rglob("*.partial*")
    )


# ── what a process list shows ────────────────────────────────────────────────


@pytest.mark.skipif(os.name == "nt", reason="needs a POSIX shell and ps")
def test_the_token_is_in_the_environment_of_git_and_not_on_its_command_line(
    served: SimpleNamespace, monkeypatch
) -> None:
    # A stand-in `git` writes what `ps` shows for it and the config it was handed, then fails.
    real_git = shutil.which("git")
    bin_dir = served.root / "bin"
    bin_dir.mkdir()
    ps_log, env_log = served.root / "ps.log", served.root / "env.log"
    script = bin_dir / "git"
    script.write_text(
        "#!/bin/sh\n"
        f'case "$1" in --version|ls-remote) exec "{real_git}" "$@";; esac\n'
        f'ps -ww -o args= -p $$ >> "{ps_log}"\n'
        f"env | grep '^GIT_CONFIG_' | sort >> \"{env_log}\"\n"
        "exit 1\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    with pytest.raises(ResolutionError):
        GitResolver().resolve(_source("main"))

    shown = ps_log.read_text()
    assert "clone" in shown and URL in shown  # ps captured the command line, and it is clean
    assert TOKEN not in shown and _basic(TOKEN) not in shown
    handed = env_log.read_text()
    assert f"GIT_CONFIG_VALUE_0=Authorization: Basic {_basic(TOKEN)}" in handed
    assert f"GIT_CONFIG_KEY_0=http.{URL}.extraHeader" in handed
    assert "GIT_CONFIG_KEY_1=http.followRedirects" in handed
    assert "GIT_CONFIG_VALUE_1=false" in handed


@pytest.mark.skipif(os.name == "nt", reason="GIT_TRACE paths are POSIX here")
def test_no_process_git_starts_carries_the_token_on_its_command_line(
    served: SimpleNamespace, monkeypatch
) -> None:
    # git's own trace lists every process it starts, git-remote-https included: the ones
    # `ps` shows. The host is a closed port, so the fetch fails after the transport starts.
    trace = served.root / "trace.log"
    monkeypatch.setenv("GIT_TRACE", str(trace))
    monkeypatch.setenv("FLUID_CUSTOM_SCAFFOLD_AUTH_HOSTS", "127.0.0.1")
    source = {"kind": "git", "url": "https://127.0.0.1:1/org/repo", "ref": "v1", "auth": AUTH}

    with pytest.raises(ResolutionError):
        GitResolver().resolve(source)

    started = trace.read_text()
    assert "remote-https" in started  # the transport process was started, and traced
    assert TOKEN not in started and _basic(TOKEN) not in started
