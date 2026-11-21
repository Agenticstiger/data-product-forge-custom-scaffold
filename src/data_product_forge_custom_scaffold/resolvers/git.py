"""Git resolver — clone a repo into the cache.

Source shape::

    {
      "kind": "git",
      "url": "https://github.com/example/my-bundle",
      "ref": "v1.2.0",                          # tag, branch, or sha
      "subdir": "bundles/my-scaffold",          # optional; default: repo root
      "auth": { "secret_ref": "GITHUB_TOKEN" }  # optional
    }

Auth: ``secret_ref`` names an environment variable holding the token. It goes into
the URL ``https://x-access-token:<token>@host/repo`` that is handed to ``git fetch``,
never to ``git clone``: ``clone`` records the URL it was given as ``origin`` in
``.git/config`` before it transfers anything, so a run killed mid-transfer would leave
the token in the cache. ``fetch <url>`` records no remote, so the token is not written
to the cache (git strips it from FETCH_HEAD) and it is never logged. A cached clone
that does carry credentials, made by an earlier version, is cleaned before it is reused.
A branch or tag is looked up the way ``git clone --branch`` looks it up, with or without
a token: ``refs/heads/<ref>`` first, then ``refs/tags/<ref>``.

git never prompts on the terminal for a credential (``GIT_TERMINAL_PROMPT=0``
unless the caller sets it), so a library call cannot hang waiting for input.

ssh never waits for an answer either. An ssh URL runs with
``GIT_SSH_COMMAND=ssh -o BatchMode=yes``, so a key passphrase or an unknown host key is
an error, not a hang. The default is left out whenever the caller could be choosing the
ssh command: ``GIT_SSH_COMMAND`` or ``GIT_SSH`` in the environment, a ``core.sshCommand``
in git config, or any ``include`` / ``includeIf`` directive (an include can set
``core.sshCommand`` for one host or one directory, and only git can say whether it
applies). git gives ``GIT_SSH_COMMAND`` priority over the other two, so setting it
blindly would switch off a deploy key configured through either. The configuration is
read outside any repository, because the commands run here never read the current
directory's own config.

Cache layout::

    ~/.cache/fluid/custom-scaffold/git/<urlhash>/<ref>/

Subsequent fetches with the same ``url`` + ``ref`` reuse the cache. To
force a re-fetch, delete the cache dir or set
``FLUID_CUSTOM_SCAFFOLD_NOCACHE=1``.

A clone is built in a ``.partial-*`` directory beside its final place and renamed into it
when it is whole, so a directory under ``<urlhash>/<ref>`` is a finished clone even if the
run is killed outright. A killed run leaves only the ``.partial-*`` directory, which holds
no token and can be deleted.

Shells out to the ``git`` binary — no Python gitpython dependency.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from ..manifest import MANIFEST_FILENAME
from .base import ResolutionError, ResolvedBundle, Resolver, cache_root

# Allowed URL schemes — any other scheme is rejected outright.
_ALLOWED_SCHEMES = ("https://", "ssh://", "git@", "git+https://", "git+ssh://")

# A full-length commit object name: 40 hex (SHA-1) or 64 hex (SHA-256). ``git
# clone --branch`` rejects raw commit ids ("Remote branch <sha> not found"), so
# a ref of this shape is fetched + checked out instead of branch-cloned.
_FULL_SHA_RE = re.compile(r"\A(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


# ssh must not wait for a person: no passphrase prompt, no "trust this host key?" question.
_SSH_BATCH_MODE = "ssh -o BatchMode=yes"
_SSH_PREFIXES = ("ssh://", "git+ssh://", "git@")

# What ssh prints when BatchMode turned a prompt into a failure. ssh writes "Permission
# denied (publickey)."; a file-system error says "Permission denied" without the bracket.
_PROMPT_FAILURES = ("Host key verification failed", "Permission denied (")
_BATCH_MODE_HINT = (
    "ssh ran without prompts (-o BatchMode=yes), so it cannot ask for a passphrase or "
    "confirm a host key. Add the host to known_hosts or load the key into ssh-agent, or set "
    "GIT_SSH_COMMAND (for example to 'ssh') to choose how ssh runs."
)


def _git_env(url: str = "") -> Dict[str, str]:
    """The environment for git subprocesses: nothing may wait for a person to answer.

    Both settings are defaults, not overrides.

    * ``GIT_TERMINAL_PROMPT=0`` stops git's own credential prompt. A caller that has set
      it keeps its value.
    * For an ssh *url*, ``GIT_SSH_COMMAND=ssh -o BatchMode=yes`` stops ssh asking for a
      passphrase or a host-key confirmation. It is left out when the caller has chosen
      an ssh command (see :func:`_ssh_command_chosen`).
    """
    env = dict(os.environ)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    if url.startswith(_SSH_PREFIXES) and not _ssh_command_chosen(env):
        env["GIT_SSH_COMMAND"] = _SSH_BATCH_MODE
    return env


def _ssh_command_chosen(env: Mapping[str, str]) -> bool:
    """True when the caller could be choosing the ssh command, by any route git offers.

    git uses ``GIT_SSH_COMMAND`` first, then ``core.sshCommand``, then ``GIT_SSH``
    (connect.c), so a default ``GIT_SSH_COMMAND`` would override the other two. The
    environment variables count by presence, as ``setdefault`` would. In git's
    configuration a ``core.sshCommand`` counts, and so does any ``include`` or
    ``includeIf``: an include can set it for one host or one directory and only git can say
    whether it applies, so an include means "do not guess". The configuration is read from
    a directory outside any repository: the commands run here read the cache clone's own
    config, or none, never the current directory's.
    """
    if "GIT_SSH_COMMAND" in env or "GIT_SSH" in env:
        return True
    try:
        probe = subprocess.run(
            ["git", "config", "--get-regexp", r"^(core\.sshcommand|include\.|includeif\.)"],
            capture_output=True,
            text=True,
            errors="replace",
            env=dict(env),
            cwd=tempfile.gettempdir(),
        )
    except OSError:  # no git binary: the command that needs it says so
        return False
    return probe.returncode == 0 and bool(probe.stdout.strip())


def _batch_mode_blocked(detail: str, env: Mapping[str, str]) -> bool:
    """True when *detail* is ssh refusing to prompt under the default :func:`_git_env` set."""
    return (
        env.get("GIT_SSH_COMMAND") == _SSH_BATCH_MODE
        and os.environ.get("GIT_SSH_COMMAND") != _SSH_BATCH_MODE
        and any(marker in detail for marker in _PROMPT_FAILURES)
    )


def _looks_like_full_sha(ref: str) -> bool:
    """True for a full-length commit id (the only reproducible git pin)."""
    return bool(_FULL_SHA_RE.match(ref))


class GitResolver(Resolver):
    kind = "git"

    def resolve(
        self,
        source: Mapping[str, Any],
        *,
        contract_dir: Optional[Path] = None,
    ) -> ResolvedBundle:
        # contract_dir is unused for git sources — URLs are absolute and
        # cache locations are user-scoped. Accepted for signature parity
        # with other resolvers.
        del contract_dir
        url = source.get("url")
        ref = source.get("ref")
        subdir = source.get("subdir") or ""

        if not url:
            raise ResolutionError("git source missing required 'url'")
        if not ref:
            raise ResolutionError("git source missing required 'ref'")
        if not isinstance(url, str) or not any(url.startswith(s) for s in _ALLOWED_SCHEMES):
            raise ResolutionError(
                f"git source url has disallowed scheme: {url!r} "
                f"(allowed: https/ssh/git+https/git+ssh)"
            )

        cache_dir = self._cache_dir_for(url, str(ref))
        refetch = bool(os.environ.get("FLUID_CUSTOM_SCAFFOLD_NOCACHE"))
        if cache_dir.is_dir() and not refetch:
            _drop_stored_credentials(cache_dir, url)
        if not cache_dir.is_dir() or refetch:
            self._clone_into_cache(url, str(ref), cache_dir, source.get("auth") or {})

        bundle_root = (cache_dir / subdir).resolve() if subdir else cache_dir
        # Confine: subdir must stay under cache_dir.
        try:
            bundle_root.relative_to(cache_dir)
        except ValueError as e:
            raise ResolutionError(f"git source subdir {subdir!r} escapes the clone root") from e

        if not (bundle_root / MANIFEST_FILENAME).is_file():
            raise ResolutionError(
                f"git source {url}@{ref} (subdir={subdir!r}) is missing {MANIFEST_FILENAME}"
            )

        # Resolve the actual commit sha for the lockfile.
        sha = self._head_sha(cache_dir)

        return ResolvedBundle(
            kind=self.kind,
            bundle_root=bundle_root,
            resolved_version=sha or str(ref),
            mirror_url=url,
            extra={"ref": str(ref), "subdir": subdir},
        )

    # ── private helpers ─────────────────────────────────────────────

    @staticmethod
    def _cache_dir_for(url: str, ref: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        return cache_root() / "git" / digest / _safe_ref(ref)

    @staticmethod
    def _inject_token(url: str, token: str) -> str:
        """Inject a token into an HTTPS URL: https://x-access-token:<TOKEN>@host/path."""
        if not url.startswith("https://"):
            return url
        rest = url[len("https://") :]
        return f"https://x-access-token:{token}@{rest}"

    def _clone_into_cache(
        self,
        url: str,
        ref: str,
        cache_dir: Path,
        auth: Mapping[str, Any],
    ) -> None:
        clone_url = url
        secret_ref = auth.get("secret_ref") if isinstance(auth, Mapping) else None
        if secret_ref:
            if not isinstance(secret_ref, str) or not secret_ref.isidentifier():
                raise ResolutionError(
                    f"auth.secret_ref must be an env-var-shaped name, got {secret_ref!r}"
                )
            token = os.environ.get(secret_ref)
            if not token:
                raise ResolutionError(
                    f"auth.secret_ref points to env var {secret_ref!r} which is unset"
                )
            clone_url = self._inject_token(url, token)

        # Build the clone beside its final place and move it in when it is whole, so a
        # killed run leaves a ``.partial-*`` directory, never a half-made cache directory
        # for the next run to reuse. An older clone stays until the new one is ready.
        cache_dir.parent.mkdir(parents=True, exist_ok=True)
        staging = cache_dir.parent / f".partial-{secrets.token_hex(8)}"
        try:
            if _looks_like_full_sha(ref) or clone_url != url:
                self._fetch_into_cache(clone_url, ref, staging, url)
            else:
                self._clone_ref_into_cache(clone_url, ref, staging, url)
            _publish(staging, cache_dir)
        except BaseException:  # an interrupt too, not only a failed git command
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def _clone_ref_into_cache(self, clone_url: str, ref: str, target: Path, url: str) -> None:
        # Branch or tag: a shallow ``--branch`` clone is the cheapest path.
        self._git(
            ["clone", "--depth", "1", "--branch", ref, clone_url, str(target)],
            clone_url=clone_url,
            url=url,
            what=f"clone {_sanitise_url(url)}@{ref}",
        )

    def _fetch_into_cache(self, clone_url: str, ref: str, target: Path, url: str) -> None:
        # ``git init`` + ``git fetch <url>`` + ``git checkout``: for a commit id (``git clone
        # --branch`` rejects raw commit ids; the robherley/shallow-fetch-sha idiom) and for a
        # branch or tag that needs a token. ``git clone <url>`` records the URL, token
        # included, as ``origin`` in .git/config before it transfers anything, so a run
        # killed mid-transfer would leave the token on disk. ``git fetch <url>`` records no
        # remote: the token only ever lives in the arguments of this process. ``--`` keeps
        # a ref that looks like an option (``--upload-pack=...``) a ref.
        where = f"{_sanitise_url(url)}@{ref}"
        self._git(["init", "-q", str(target)], clone_url=clone_url, url=url, what="git init")

        def fetch(name: str) -> List[str]:
            return ["-C", str(target), "fetch", "--depth", "1", "--", clone_url, name]

        checkout = "FETCH_HEAD"
        if _looks_like_full_sha(ref):
            if not self._git_ok(fetch(ref), url=url):
                # The shallow fetch of just that commit needs the server to allow fetching
                # a reachable sha (uploadpack.allowReachableSHA1InWant, which GitHub,
                # GitLab and Bitbucket do). Fallback for servers that do not (mirrors
                # cookiecutter's vcs.clone + copier's --vcs-ref): a full fetch, then check
                # the commit out locally. Still a URL fetch, so no token on disk.
                self._git(
                    ["-C", str(target), "fetch", clone_url, "+refs/heads/*:refs/remotes/origin/*"],
                    clone_url=clone_url,
                    url=url,
                    what=f"fetch {where}",
                )
                checkout = ref
        elif not self._git_ok(fetch(f"refs/heads/{ref}"), url=url):
            # The lookup ``git clone --branch`` does: a branch first, then a tag. If the
            # first fetch failed for another reason (auth, network) the second says so.
            self._git(
                fetch(f"refs/tags/{ref}"),
                clone_url=clone_url,
                url=url,
                what=f"fetch {where} (as a branch, then as a tag)",
            )

        self._git(
            ["-C", str(target), "checkout", "-q", checkout],
            clone_url=clone_url,
            url=url,
            what=f"checkout {where}",
        )

    @staticmethod
    def _git(args: List[str], *, clone_url: str, url: str, what: str) -> None:
        """Run a git subcommand, redacting any embedded token from errors."""
        env = _git_env(url)
        try:
            subprocess.run(["git", *args], check=True, capture_output=True, text=True, env=env)
            return
        except FileNotFoundError as e:
            raise ResolutionError("git resolver requires the 'git' binary on PATH") from e
        except subprocess.CalledProcessError as e:
            # Hide the token from any error output. The error is raised below, outside this
            # handler, so the CalledProcessError (its argv holds the token) is not kept as
            # the new error's __context__.
            detail = (e.stderr or "").replace(clone_url, _sanitise_url(url)).strip()
        if _batch_mode_blocked(detail, env):
            detail += "\n" + _BATCH_MODE_HINT
        raise ResolutionError(f"git {what} failed: {detail}")

    @staticmethod
    def _git_ok(args: List[str], *, url: str = "") -> bool:
        """Best-effort probe: True on success, False on a non-zero git exit.

        Used only to decide whether the shallow fetch-by-sha fast path worked
        before falling back to a full fetch; a missing ``git`` binary surfaces
        as a real error from the preceding :meth:`_git` ``init`` call.
        """
        try:
            subprocess.run(
                ["git", *args], check=True, capture_output=True, text=True, env=_git_env(url)
            )
            return True
        except (FileNotFoundError, subprocess.CalledProcessError):
            return False

    @staticmethod
    def _head_sha(repo_dir: Path) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
                env=_git_env(),
            )
            return result.stdout.strip()
        except Exception:
            return ""


def _safe_ref(ref: str) -> str:
    """Sanitise a ref name for use as a cache directory name.

    Dots stay (``v1.2.0``), but a name made only of dots (``.``, ``..``) would name the
    url's directory or its parent instead of a directory inside it, so it becomes
    underscores.
    """
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in ref)
    return safe if safe.strip(".") else "_" * max(len(safe), 1)


def _publish(staging: Path, cache_dir: Path) -> None:
    """Move a finished clone into place; an older clone is replaced only now."""
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
    try:
        os.replace(staging, cache_dir)
    except OSError:
        if not cache_dir.is_dir():
            raise
        # Another run published the same clone first: keep theirs.
        shutil.rmtree(staging, ignore_errors=True)


def _drop_stored_credentials(cache_dir: Path, url: str) -> None:
    """Make sure a cached clone holds no credentials before it is reused.

    A clone made by an earlier version, or by a run killed mid-transfer, can carry
    ``https://x-access-token:<token>@host/...`` as ``origin``. A clone that finished is
    kept, with the plain URL put back. One that did not (it has no commit), or that still
    holds credentials afterwards, is deleted so it is fetched again. Credentials that the
    source's own ``url`` carries are the contract author's choice and are left alone.
    """
    tainted = _stored_credentials(cache_dir, url)
    if not tainted:
        return
    if GitResolver._git_ok(["-C", str(cache_dir), "rev-parse", "--verify", "-q", "HEAD"]):
        try:
            _scrub_origin(cache_dir, tainted[0], url)
        except ResolutionError:
            pass  # the clone was deleted, or is checked again below
        if not _stored_credentials(cache_dir, url):
            return
    shutil.rmtree(cache_dir, ignore_errors=True)
    if cache_dir.exists():
        raise ResolutionError(f"cached clone {cache_dir} holds credentials and cannot be removed")


def _scrub_origin(cache_dir: Path, tainted_url: str, url: str) -> None:
    """Reset a cached clone's ``origin`` to the credential-free *url*.

    ``git clone`` writes the URL it was given, token included, into ``.git/config``. Put
    the plain URL back; if that fails, delete the clone rather than leave the token in
    the cache.
    """
    try:
        GitResolver._git(
            ["-C", str(cache_dir), "remote", "set-url", "origin", url],
            clone_url=tainted_url,
            url=url,
            what="reset the remote URL",
        )
    except ResolutionError:
        shutil.rmtree(cache_dir, ignore_errors=True)
        raise


_CONFIG_URL = re.compile(r"[a-z][a-z0-9+.-]*://[^\s\"']+", re.IGNORECASE)
_USERINFO = re.compile(r"([a-z][a-z0-9+.-]*)://([^/?#@]*)@", re.IGNORECASE)


def _stored_credentials(cache_dir: Path, url: str) -> List[str]:
    """The URLs in a cached clone's git config that carry credentials *url* does not.

    The config is scanned as text for anything shaped like a URL, whatever section or
    spelling holds it (``origin``, a second ``url`` line, a ``pushurl``, a comment on the
    header line), so a form that git does not write itself cannot hide a token. It is read
    as a plain file, so a cache hit still works without the ``git`` binary.
    """
    try:
        text = (cache_dir / ".git" / "config").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [u for u in _CONFIG_URL.findall(text) if u != url and _has_credentials(u)]


def _has_credentials(url: str) -> bool:
    """True when *url* embeds a secret: any user part on http(s), a password elsewhere.

    ``ssh://git@host/...`` and ``git@host:path`` only name the account; that is not one.
    """
    match = _USERINFO.match(url)
    if not match:
        return False
    return match.group(1).lower() in ("http", "https") or ":" in match.group(2)


def _sanitise_url(url: str) -> str:
    """Strip any embedded credentials from a URL for safe logging."""
    if "@" in url and url.startswith("https://"):
        head, _, tail = url.partition("@")
        return f"https://[redacted]@{tail}"
    return url
