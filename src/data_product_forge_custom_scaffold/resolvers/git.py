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

git never prompts on the terminal for a credential (``GIT_TERMINAL_PROMPT=0``
unless the caller sets it), so a library call cannot hang waiting for input.

ssh never waits for an answer either. An ssh URL runs with
``GIT_SSH_COMMAND=ssh -o BatchMode=yes``, so a key passphrase or an unknown host key is
an error, not a hang. The default is left out when the caller has chosen an ssh command
(``GIT_SSH_COMMAND`` or ``GIT_SSH`` in the environment, or ``core.sshCommand`` in git
config): git gives ``GIT_SSH_COMMAND`` priority over the other two, so setting it
blindly would switch off a deploy key configured through either.

Cache layout::

    ~/.cache/fluid/custom-scaffold/git/<urlhash>/<ref>/

Subsequent fetches with the same ``url`` + ``ref`` reuse the cache. To
force a re-fetch, delete the cache dir or set
``FLUID_CUSTOM_SCAFFOLD_NOCACHE=1``.

Shells out to the ``git`` binary — no Python gitpython dependency.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
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

# What ssh prints when BatchMode turned a prompt into a failure.
_PROMPT_FAILURES = ("Host key verification failed", "Permission denied")
_BATCH_MODE_HINT = (
    "ssh ran without prompts (-o BatchMode=yes). Add the host to known_hosts or load the "
    "key into ssh-agent, or set GIT_SSH_COMMAND (for example to 'ssh') to choose how ssh runs."
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
    """True when the caller has picked the ssh command, by any of git's three routes.

    git uses ``GIT_SSH_COMMAND`` first, then ``core.sshCommand``, then ``GIT_SSH``
    (connect.c), so a default ``GIT_SSH_COMMAND`` would override the other two. The
    environment variables are checked by presence, as ``setdefault`` would;
    ``core.sshCommand`` is read from git's own configuration.
    """
    if "GIT_SSH_COMMAND" in env or "GIT_SSH" in env:
        return True
    try:
        probe = subprocess.run(
            ["git", "config", "--get", "core.sshCommand"],
            capture_output=True,
            text=True,
            env=dict(env),
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
            self._drop_stored_credentials(cache_dir, url)
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
        # Fresh clone — wipe any partial directory.
        if cache_dir.exists():
            shutil.rmtree(cache_dir)
        cache_dir.parent.mkdir(parents=True, exist_ok=True)

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

        try:
            if _looks_like_full_sha(ref) or clone_url != url:
                self._fetch_into_cache(clone_url, ref, cache_dir, url)
            else:
                self._clone_ref_into_cache(clone_url, ref, cache_dir, url)
        except BaseException:
            # An empty or half-made directory must not stay for a later run to reuse as if
            # it were a finished clone. This covers an interrupt (``KeyboardInterrupt``)
            # too, not only a failed git command.
            shutil.rmtree(cache_dir, ignore_errors=True)
            raise

    def _drop_stored_credentials(self, cache_dir: Path, url: str) -> None:
        """Take a token out of a cached clone's ``.git/config`` before the clone is reused.

        A clone made by an earlier version, or by a run that was killed while it
        transferred, can carry ``https://x-access-token:<token>@host/...`` as ``origin``.
        Put the plain URL back; if that fails, delete the clone so it is fetched again.
        Credentials that the source's own ``url`` carries are the contract author's
        choice and are left alone.
        """
        origin = _origin_url(cache_dir)
        if origin == url or not _has_credentials(origin):
            return
        try:
            self._scrub_token(cache_dir, origin, url)
        except ResolutionError:
            if cache_dir.exists():  # it could not even be deleted: do not reuse it
                raise

    def _scrub_token(self, cache_dir: Path, clone_url: str, url: str) -> None:
        """Reset a token clone's ``origin`` to the credential-free URL.

        ``git clone`` writes the URL it was given, token included, into
        ``.git/config``. Put the plain URL back; if that fails, delete the clone rather
        than leave the token in the cache.
        """
        try:
            self._git(
                ["-C", str(cache_dir), "remote", "set-url", "origin", url],
                clone_url=clone_url,
                url=url,
                what="reset the remote URL",
            )
        except ResolutionError:
            shutil.rmtree(cache_dir, ignore_errors=True)
            raise

    def _clone_ref_into_cache(self, clone_url: str, ref: str, cache_dir: Path, url: str) -> None:
        # Branch or tag: a shallow ``--branch`` clone is the cheapest path.
        self._git(
            ["clone", "--depth", "1", "--branch", ref, clone_url, str(cache_dir)],
            clone_url=clone_url,
            url=url,
            what=f"clone {_sanitise_url(url)}@{ref}",
        )

    def _fetch_into_cache(self, clone_url: str, ref: str, cache_dir: Path, url: str) -> None:
        # ``git init`` + ``git fetch <url>`` + ``git checkout``: for a commit id (``git clone
        # --branch`` rejects raw commit ids; the robherley/shallow-fetch-sha idiom) and for a
        # branch or tag that needs a token. ``git clone <url>`` records the URL, token
        # included, as ``origin`` in .git/config before it transfers anything, so a run
        # killed mid-transfer would leave the token on disk. ``git fetch <url>`` records no
        # remote: the token only ever lives in the arguments of this process. ``--`` keeps
        # a ref that looks like an option (``--upload-pack=...``) a ref.
        where = f"{_sanitise_url(url)}@{ref}"
        fetch = ["-C", str(cache_dir), "fetch", "--depth", "1", "--", clone_url, ref]
        self._git(["init", "-q", str(cache_dir)], clone_url=clone_url, url=url, what="git init")

        target = "FETCH_HEAD"
        if not _looks_like_full_sha(ref):
            self._git(fetch, clone_url=clone_url, url=url, what=f"fetch {where}")
        elif not self._git_ok(fetch, url=url):
            # A commit id: the shallow fetch of just that commit needs the server to allow
            # fetching a reachable sha (uploadpack.allowReachableSHA1InWant, which GitHub,
            # GitLab and Bitbucket do). Fallback for servers that do not (mirrors
            # cookiecutter's vcs.clone + copier's --vcs-ref): a full fetch, then check the
            # commit out locally. Still a URL fetch, so no token on disk.
            self._git(
                ["-C", str(cache_dir), "fetch", clone_url, "+refs/heads/*:refs/remotes/origin/*"],
                clone_url=clone_url,
                url=url,
                what=f"fetch {where}",
            )
            target = ref

        self._git(
            ["-C", str(cache_dir), "checkout", "-q", target],
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
        except FileNotFoundError as e:
            raise ResolutionError("git resolver requires the 'git' binary on PATH") from e
        except subprocess.CalledProcessError as e:
            # Hide the token from any error output before re-raising.
            detail = (e.stderr or "").replace(clone_url, _sanitise_url(url)).strip()
            if _batch_mode_blocked(detail, env):
                detail += "\n" + _BATCH_MODE_HINT
            raise ResolutionError(f"git {what} failed: {detail}") from None

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
    """Sanitise a ref name for use as a cache directory."""
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in ref)


_ORIGIN_SECTION = re.compile(r'\[\s*remote\s+"origin"\s*\]|\[\s*remote\.origin\s*\]', re.IGNORECASE)
_URL_LINE = re.compile(r"url\s*=\s*(.*)", re.IGNORECASE)
_USERINFO = re.compile(r"([a-z][a-z0-9+.-]*)://([^/?#@]*)@", re.IGNORECASE)


def _origin_url(cache_dir: Path) -> str:
    """The ``remote.origin.url`` in a cached clone's git config ("" when there is none).

    The file is read directly, so a cache that is reused without ever running git
    still works without the ``git`` binary.
    """
    try:
        text = (cache_dir / ".git" / "config").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    in_origin = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("["):
            in_origin = bool(_ORIGIN_SECTION.fullmatch(line))
        elif in_origin:
            match = _URL_LINE.fullmatch(line)
            if match:
                return match.group(1).strip().strip('"')
    return ""


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
