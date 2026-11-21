"""Git resolver — clone a repo into the cache.

Source shape::

    {
      "kind": "git",
      "url": "https://github.com/example/my-bundle",
      "ref": "v1.2.0",                          # tag, branch, or sha
      "subdir": "bundles/my-scaffold",          # optional; default: repo root
      "auth": { "secret_ref": "GITHUB_TOKEN" }  # optional
    }

Auth: ``secret_ref`` names an environment variable holding a token. The contract only asks
for a credential; where it may go is decided by the operator, outside the contract:

* ``FLUID_CUSTOM_SCAFFOLD_AUTH_HOSTS`` lists the hosts (comma-separated, exact) a credential
  may be sent to. Unset or empty, no credential goes anywhere and a source with ``auth`` is
  refused.
* ``secret_ref`` must start with ``FLUID_SCAFFOLD_`` or with a prefix in
  ``FLUID_CUSTOM_SCAFFOLD_SECRET_PREFIXES`` (comma-separated), so a contract cannot name any
  other variable.
* ``auth`` goes only with an ``https://`` url, and a url that carries credentials
  (``https://user:token@host/...``) is refused: it would end up in the lock.

The token never appears on a command line. It is handed to git in the environment as an
``http.<url>.extraHeader`` (``GIT_CONFIG_COUNT``, git 2.31 or newer) scoped to the source's
url, with ``http.followRedirects=false`` so it cannot follow a redirect to another host. git
records it nowhere in the clone, and it is never logged. The cache key includes the host and
the variable name (never the value), so an unauthenticated source cannot reuse a private
clone. A cached clone that carries credentials, made by an earlier version, is cleaned before
it is reused.

git never prompts on the terminal for a credential (``GIT_TERMINAL_PROMPT=0``
unless the caller sets it), so a library call cannot hang waiting for input.

ssh never waits for an answer either. An ssh URL (as git will use it, after any
``url.<base>.insteadOf`` rewrite) runs with
``GIT_SSH_COMMAND=ssh -o BatchMode=yes``, so a key passphrase or an unknown host key is
an error, not a hang. The default is left out whenever the caller could be choosing the
ssh command: ``GIT_SSH_COMMAND`` or ``GIT_SSH`` in the environment, a ``core.sshCommand``
in git config, any ``include`` / ``includeIf`` directive (an include can set
``core.sshCommand`` for one host or one directory, and only git can say whether it
applies), or a template directory (its config is copied into every new repository). git
gives ``GIT_SSH_COMMAND`` priority over the other two, so setting it blindly would switch
off a deploy key configured through either. The configuration is read outside any
repository, because the commands run here never read the current directory's own config.

Cache layout::

    ~/.cache/fluid/custom-scaffold/git/<urlhash>/<ref>/

Subsequent fetches with the same ``url`` + ``ref`` reuse the cache. To
force a re-fetch, delete the cache dir or set
``FLUID_CUSTOM_SCAFFOLD_NOCACHE=1``.

A clone is built in a ``.partial-*`` directory beside its final place and renamed into it
when it is whole, so a directory under ``<urlhash>/<ref>`` is a finished clone even if the
run is killed outright (one made by an earlier version excepted). A killed run leaves only
the ``.partial-*`` directory, which holds no token and can be deleted. A refresh renames the
old clone aside and deletes it once the new one is in place; a first run never removes a
clone that another run published while it was fetching.

Shells out to the ``git`` binary — no Python gitpython dependency.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from ..manifest import MANIFEST_FILENAME
from .base import ResolutionError, ResolvedBundle, Resolver, cache_root

# Set to refetch a cached clone: it is replaced once the new one is whole.
_NOCACHE = "FLUID_CUSTOM_SCAFFOLD_NOCACHE"

# What the operator allows, in the environment of the process and never in the contract.
_AUTH_HOSTS = "FLUID_CUSTOM_SCAFFOLD_AUTH_HOSTS"  # hosts a credential may be sent to
_SECRET_PREFIXES = "FLUID_CUSTOM_SCAFFOLD_SECRET_PREFIXES"  # variable-name prefixes, besides:
_DEFAULT_SECRET_PREFIX = "FLUID_SCAFFOLD_"
_MIN_GIT = (2, 31)  # GIT_CONFIG_COUNT, which carries the credential without a command line

# A leftover of a killed run younger than this may be a live run's staging directory.
_LEFTOVER_AGE = 3600.0

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


def _git_env(url: str = "", credential: Optional["_Credential"] = None) -> Dict[str, str]:
    """The environment for git subprocesses: nothing may wait for a person to answer.

    Both settings are defaults, not overrides.

    * ``GIT_TERMINAL_PROMPT=0`` stops git's own credential prompt. A caller that has set
      it keeps its value.
    * For an ssh *url*, ``GIT_SSH_COMMAND=ssh -o BatchMode=yes`` stops ssh asking for a
      passphrase or a host-key confirmation. It is left out when the caller has chosen
      an ssh command (see :func:`_ssh_command_chosen`). The decision is about the URL as
      git will use it, so an https URL that ``url.<base>.insteadOf`` rewrites to ssh counts.

    A *credential* is added last, as config in the environment (never on a command line).
    """
    env = dict(os.environ)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    if _effective_url(url, env).startswith(_SSH_PREFIXES) and not _ssh_command_chosen(env):
        env["GIT_SSH_COMMAND"] = _SSH_BATCH_MODE
    if credential is not None:
        _add_git_config(env, credential.config())
    return env


def _effective_url(url: str, env: Mapping[str, str]) -> str:
    """*url* as git will use it, after any ``url.<base>.insteadOf`` rewrite.

    ``git ls-remote --get-url`` expands the URL and exits without talking to the remote. It
    runs outside any repository, like the ssh probe, and falls back to *url* if it cannot.
    """
    if not url:
        return url
    try:
        probe = subprocess.run(
            ["git", "ls-remote", "--get-url", url],
            capture_output=True,
            text=True,
            errors="replace",
            env=dict(env),
            cwd=tempfile.gettempdir(),
        )
    except OSError:  # no git binary: the command that needs it says so
        return url
    return probe.stdout.strip() if probe.returncode == 0 and probe.stdout.strip() else url


def _add_git_config(env: Dict[str, str], pairs: Sequence[Tuple[str, str]]) -> None:
    """Add config pairs to *env* after any ``GIT_CONFIG_COUNT`` pairs the caller already set."""
    try:
        count = int(env.get("GIT_CONFIG_COUNT") or 0)
    except ValueError:  # git rejects it too; ours then stand alone
        count = 0
    for index, (key, value) in enumerate(pairs, start=count):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value
    env["GIT_CONFIG_COUNT"] = str(count + len(pairs))


def _ssh_command_chosen(env: Mapping[str, str]) -> bool:
    """True when the caller could be choosing the ssh command, by any route git offers.

    git uses ``GIT_SSH_COMMAND`` first, then ``core.sshCommand``, then ``GIT_SSH``
    (connect.c), so a default ``GIT_SSH_COMMAND`` would override the other two. The
    environment variables count by presence, as ``setdefault`` would. In git's
    configuration a ``core.sshCommand`` counts, and so does any ``include`` or
    ``includeIf``: an include can set it for one host or one directory and only git can say
    whether it applies, so an include means "do not guess". So does a template directory
    (``init.templateDir``, ``GIT_TEMPLATE_DIR``): its ``config`` is copied into every repository
    ``git init`` makes, where it can set ``core.sshCommand``. The configuration is read from
    a directory outside any repository: the commands run here read the cache clone's own
    config, or none, never the current directory's.
    """
    if any(name in env for name in ("GIT_SSH_COMMAND", "GIT_SSH", "GIT_TEMPLATE_DIR")):
        return True
    try:
        probe = subprocess.run(
            [
                "git",
                "config",
                "--get-regexp",
                r"^(core\.sshcommand|include\.|includeif\.|init\.templatedir)",
            ],
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
        url, ref, subdir, binding = _validated(source)

        _sweep_leftovers()
        cache_dir = self._cache_dir_for(url, ref, binding)
        refetch = bool(os.environ.get(_NOCACHE))
        if os.path.lexists(cache_dir):
            _refuse_symlinks(cache_dir)
            if cache_dir.is_dir():  # also on a refresh: if the fetch fails the old clone stays
                _drop_stored_credentials(cache_dir, url)
        if not cache_dir.is_dir() or refetch:
            self._clone_into_cache(url, ref, cache_dir, source.get("auth") or {})

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
            resolved_version=sha or ref,
            mirror_url=url,
            extra={"ref": ref, "subdir": subdir},
        )

    # ── private helpers ─────────────────────────────────────────────

    @staticmethod
    def _cache_dir_for(url: str, ref: str, binding: Optional["_AuthBinding"] = None) -> Path:
        # The key names the credential (host and variable, never its value), so a source
        # without ``auth`` cannot reuse a clone that was fetched with one.
        seed = url if binding is None else f"{url}\0{binding.host}\0{binding.name}"
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
        return cache_root() / "git" / digest / _safe_ref(ref)

    def _clone_into_cache(
        self,
        url: str,
        ref: str,
        cache_dir: Path,
        auth: Mapping[str, Any],
    ) -> None:
        credential = _credential(url, auth)

        # Build the clone beside its final place and move it in when it is whole, so a
        # killed run leaves a ``.partial-*`` directory, never a half-made cache directory
        # for the next run to reuse. An older clone stays until the new one is ready.
        cache_dir.parent.mkdir(parents=True, exist_ok=True)
        staging = cache_dir.parent / f".partial-{secrets.token_hex(8)}"
        try:
            if _looks_like_full_sha(ref):
                self._fetch_into_cache(ref, staging, url, credential)
            else:
                self._clone_ref_into_cache(ref, staging, url, credential)
            _publish(staging, cache_dir, replace=bool(os.environ.get(_NOCACHE)))
        except BaseException:  # an interrupt too, not only a failed git command
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def _clone_ref_into_cache(
        self, ref: str, target: Path, url: str, credential: Optional["_Credential"]
    ) -> None:
        # Branch or tag: a shallow ``--branch`` clone is the cheapest path.
        self._git(
            ["clone", "--depth", "1", "--branch", ref, url, str(target)],
            url=url,
            what=f"clone {_sanitise_url(url)}@{ref}",
            credential=credential,
        )

    def _fetch_into_cache(
        self, ref: str, target: Path, url: str, credential: Optional["_Credential"]
    ) -> None:
        # ``git clone --branch`` rejects raw commit ids, so initialise an empty repo and
        # fetch the commit directly (the robherley/shallow-fetch-sha idiom). ``--`` keeps a
        # ref that looks like an option a ref (a commit id is hex, so this is a guard only).
        where = f"{_sanitise_url(url)}@{ref}"
        self._git(["init", "-q", str(target)], url=url, what="git init")

        checkout = "FETCH_HEAD"
        fetch = ["-C", str(target), "fetch", "--depth", "1", "--", url, ref]
        if not self._git_ok(fetch, url=url, credential=credential):
            # The shallow fetch of just that commit needs the server to allow fetching a
            # reachable sha (uploadpack.allowReachableSHA1InWant, which GitHub, GitLab and
            # Bitbucket do). Fallback for servers that do not (mirrors cookiecutter's
            # vcs.clone + copier's --vcs-ref): a full fetch, then check the commit out locally.
            self._git(
                ["-C", str(target), "fetch", url, "+refs/heads/*:refs/remotes/origin/*"],
                url=url,
                what=f"fetch {where}",
                credential=credential,
            )
            checkout = ref

        self._git(
            ["-C", str(target), "checkout", "-q", checkout],
            url=url,
            what=f"checkout {where}",
        )

    @staticmethod
    def _git(
        args: List[str], *, url: str, what: str, credential: Optional["_Credential"] = None
    ) -> None:
        """Run a git subcommand; the credential, if any, goes in the environment."""
        env = _git_env(url, credential)
        try:
            subprocess.run(["git", *args], check=True, capture_output=True, text=True, env=env)
            return
        except FileNotFoundError as e:
            raise ResolutionError("git resolver requires the 'git' binary on PATH") from e
        except subprocess.CalledProcessError as e:
            # The error is raised below, outside this handler, so the CalledProcessError is
            # not kept as the new error's __context__.
            detail = (e.stderr or "").strip()
            if credential is not None:
                detail = credential.redact(detail)
        if _batch_mode_blocked(detail, env):
            detail += "\n" + _BATCH_MODE_HINT
        raise ResolutionError(f"git {what} failed: {detail}")

    @staticmethod
    def _git_ok(
        args: List[str], *, url: str = "", credential: Optional["_Credential"] = None
    ) -> bool:
        """Best-effort probe: True on success, False on a non-zero git exit.

        Used only to decide whether the shallow fetch-by-sha fast path worked
        before falling back to a full fetch; a missing ``git`` binary surfaces
        as a real error from the preceding :meth:`_git` ``init`` call.
        """
        try:
            subprocess.run(
                ["git", *args],
                check=True,
                capture_output=True,
                text=True,
                env=_git_env(url, credential),
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


def _validated(source: Mapping[str, Any]) -> Tuple[str, str, str, Optional["_AuthBinding"]]:
    """The url, ref and subdir of a git source, and the credential it asks for, once everything
    the engine refuses has been refused (before anything is cached or fetched)."""
    url = source.get("url")
    ref = source.get("ref")
    if not url:
        raise ResolutionError("git source missing required 'url'")
    if not ref:
        raise ResolutionError("git source missing required 'ref'")
    if ":" in str(ref):
        raise ResolutionError(f"git source ref {str(ref)!r} is not a valid git ref name")
    if not isinstance(url, str) or not any(url.startswith(s) for s in _ALLOWED_SCHEMES):
        raise ResolutionError(
            f"git source url has disallowed scheme: {url!r} "
            f"(allowed: https/ssh/git+https/git+ssh)"
        )
    if _has_credentials(url):
        raise ResolutionError(
            "git source url must not carry credentials (they would reach the lock); "
            "name an environment variable in auth.secret_ref instead"
        )
    binding = _bind_auth(url, source.get("auth") or {})
    return url, str(ref), source.get("subdir") or "", binding


@dataclass(frozen=True)
class _AuthBinding:
    """What a source asks for: the variable that holds the token, and the host it goes to.

    This is all the cache key needs. It holds no value.
    """

    host: str
    name: str


@dataclass(frozen=True)
class _Credential:
    """A token, bound to one url. It reaches git as environment config and nowhere else."""

    url: str
    token: str = field(repr=False)

    @property
    def _basic(self) -> str:
        return base64.b64encode(f"x-access-token:{self.token}".encode("utf-8")).decode("ascii")

    def config(self) -> List[Tuple[str, str]]:
        """The git config that carries it: the header for this url only, and no redirects."""
        return [
            (f"http.{self.url}.extraHeader", f"Authorization: Basic {self._basic}"),
            ("http.followRedirects", "false"),
        ]

    def redact(self, text: str) -> str:
        return text.replace(self.token, "[redacted]").replace(self._basic, "[redacted]")


def _csv(value: str) -> List[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _bind_auth(url: str, auth: Mapping[str, Any]) -> Optional[_AuthBinding]:
    """Check what a source asks for against what the operator allows. Reads no token.

    A refusal names the setting that would allow it, never a value.
    """
    secret_ref = auth.get("secret_ref") if isinstance(auth, Mapping) else None
    if not secret_ref:
        return None
    if not isinstance(secret_ref, str) or not secret_ref.isidentifier():
        raise ResolutionError(f"auth.secret_ref must be an env-var-shaped name, got {secret_ref!r}")
    prefixes = (_DEFAULT_SECRET_PREFIX, *_csv(os.environ.get(_SECRET_PREFIXES, "")))
    if not secret_ref.startswith(prefixes):
        raise ResolutionError(
            f"auth.secret_ref {secret_ref!r} is not a variable a contract may name: it must start "
            f"with {' or '.join(prefixes)} (the operator can allow more prefixes in {_SECRET_PREFIXES})"
        )
    parts = urlsplit(url)
    if parts.scheme == "git+https":
        raise ResolutionError(
            "auth is not supported for git+https:// urls (git does not send a credential "
            "through that scheme); use an https:// url"
        )
    if parts.scheme != "https":
        raise ResolutionError(f"auth is only sent over https://, not {parts.scheme or 'this url'}")
    host = (parts.hostname or "").lower()
    if host not in {h.lower() for h in _csv(os.environ.get(_AUTH_HOSTS, ""))}:
        raise ResolutionError(
            f"auth.secret_ref: {host!r} is not a host this operator allows a credential to be sent "
            f"to (set {_AUTH_HOSTS}, comma-separated, outside the contract)"
        )
    return _AuthBinding(host=host, name=secret_ref)


def _credential(url: str, auth: Mapping[str, Any]) -> Optional[_Credential]:
    """The credential a source may use, with its token read, or None when it asks for none."""
    binding = _bind_auth(url, auth)
    if binding is None:
        return None
    token = os.environ.get(binding.name)
    if not token:
        raise ResolutionError(f"auth.secret_ref points to env var {binding.name!r} which is unset")
    found = _git_version()
    if found < _MIN_GIT:
        raise ResolutionError(
            f"git {_MIN_GIT[0]}.{_MIN_GIT[1]} or newer is needed to send a credential without "
            f"putting it on a command line (found {found[0]}.{found[1]})"
        )
    return _Credential(url=url, token=token)


def _git_version() -> Tuple[int, int]:
    """The installed git's (major, minor); a version that cannot be read counts as new enough."""
    try:
        out = subprocess.run(["git", "--version"], capture_output=True, text=True).stdout
    except OSError as e:
        raise ResolutionError("git resolver requires the 'git' binary on PATH") from e
    found = re.search(r"(\d+)\.(\d+)", out)
    return (int(found.group(1)), int(found.group(2))) if found else (99, 0)


def _safe_ref(ref: str) -> str:
    """Sanitise a ref name for use as a cache directory name.

    Dots stay (``v1.2.0``), but a name made only of dots (``.``, ``..``) would name the
    url's directory or its parent instead of a directory inside it, so it becomes
    underscores.
    """
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in ref)
    return safe if safe.strip(".") else "_" * max(len(safe), 1)


def _publish(staging: Path, cache_dir: Path, *, replace: bool) -> None:
    """Move a finished clone into place.

    A refresh (*replace*) renames the older clone out of the way first, so ``cache_dir`` is
    always whole or absent, never half-deleted, and deletes it only once the new one is in
    place. A first run never removes a clone: if another run published the same one while
    this one was fetching, theirs stays (it may be in use) and this one is discarded.
    """
    previous = None
    if replace and cache_dir.exists():
        previous = cache_dir.with_name(f".old-{secrets.token_hex(8)}")
        try:
            os.replace(cache_dir, previous)
        except FileNotFoundError:  # another refresh moved it first
            previous = None
    try:
        os.replace(staging, cache_dir)
    except OSError:
        if cache_dir.is_dir():
            shutil.rmtree(staging, ignore_errors=True)
        else:
            if previous is not None:
                os.replace(previous, cache_dir)  # put the older clone back
            raise
    if previous is not None:
        shutil.rmtree(previous, ignore_errors=True)


def _discard(path: Path) -> Optional[Path]:
    """Remove a directory so that it is never seen half-deleted; what is left of it, if any.

    It is renamed away first (atomic) and deleted after: a process killed in between leaves
    an ``.old-*`` directory, not a damaged *path*. A directory that cannot be renamed is left
    whole, not deleted in place. Returns where whatever could not be deleted now is (*path*
    itself when it could not be renamed), or None when nothing is left.
    """
    graveyard = path.with_name(f".old-{secrets.token_hex(8)}")
    try:
        os.replace(path, graveyard)
    except OSError:
        return path
    shutil.rmtree(graveyard, ignore_errors=True)
    return graveyard if graveyard.exists() else None


def _drop_stored_credentials(cache_dir: Path, url: str) -> None:
    """Make sure a cached clone holds no credentials before it is reused.

    A clone made by an earlier version, or by a run killed mid-transfer, can carry
    ``https://x-access-token:<token>@host/...`` as ``origin``. A clone that finished is
    kept, with the plain URL put back. One that did not (it has no commit), or that still
    holds credentials afterwards, is deleted so it is fetched again; if a credential cannot
    be deleted that is an error, never silence.
    """
    tainted = _stored_credentials(cache_dir)
    if not tainted:
        return
    git_dir = str(cache_dir / ".git")  # not -C: a broken .git would fall through to a parent repo
    if GitResolver._git_ok(["--git-dir", git_dir, "rev-parse", "--verify", "-q", "HEAD"]):
        try:
            _scrub_origin(git_dir, tainted[0], url)
        except ResolutionError:
            pass  # deleted below
        if not _stored_credentials(cache_dir):
            return
    left = _discard(cache_dir)
    if left is not None and _stored_credentials(left):
        raise ResolutionError(
            f"a cached clone that holds credentials could not be removed: delete {left} by hand"
        )


def _scrub_origin(git_dir: str, tainted_url: str, url: str) -> None:
    """Reset a cached clone's ``origin`` to the credential-free *url*.

    ``git clone`` of an earlier version wrote the URL it was given, token included, into
    ``.git/config``. Raises :class:`ResolutionError` if git cannot.
    """
    GitResolver._git(
        ["--git-dir", git_dir, "remote", "set-url", "origin", url],
        url=url,
        what="reset the remote URL",
    )


def _refuse_symlinks(cache_dir: Path) -> None:
    """A cache entry is never a symbolic link, nor is its ``.git`` or ``.git/config``.

    ``git remote set-url`` would write through a linked config, and a linked directory would
    be reused as if it were ours; the engine follows no link in its own cache.
    """
    for path in (cache_dir, cache_dir / ".git", cache_dir / ".git" / "config"):
        if path.is_symlink():
            raise ResolutionError(
                f"cache entry {path} is a symbolic link; remove it (the engine does not "
                f"follow links in its cache)"
            )


def _sweep_leftovers() -> None:
    """Delete the ``.partial-*`` and ``.old-*`` directories that a killed run left behind.

    One that holds a credential (a clone from an earlier version that was being deleted) goes
    at once. The others go when they are older than an hour, which no live run's staging
    directory is. A link is removed, never followed.
    """
    git_cache = cache_root() / "git"
    try:
        leftovers = [*git_cache.glob("*/.partial-*"), *git_cache.glob("*/.old-*")]
    except OSError:
        return
    now = time.time()
    for path in leftovers:
        try:
            if path.is_symlink():
                path.unlink()
            elif now - path.stat().st_mtime > _LEFTOVER_AGE or _stored_credentials(path):
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            continue


_CONFIG_URL = re.compile(r"[a-z][a-z0-9+.-]*://[^\s\"']+", re.IGNORECASE)
_USERINFO = re.compile(r"([a-z][a-z0-9+.-]*)://([^/?#@]*)@", re.IGNORECASE)


def _stored_credentials(cache_dir: Path) -> List[str]:
    """The URLs in a cached clone's git config that carry credentials.

    The config is scanned as text for anything shaped like a URL, whatever section or
    spelling holds it (``origin``, a second ``url`` line, a ``pushurl``, a comment on the
    header line), so a form that git does not write itself cannot hide a token. It is read
    as a plain file, so a cache hit still works without the ``git`` binary.
    """
    try:
        text = (cache_dir / ".git" / "config").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    text = text.replace("\\\r\n", "").replace("\\\n", "")  # git joins a line ending in a backslash
    return [u for u in _CONFIG_URL.findall(text) if _has_credentials(u)]


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
