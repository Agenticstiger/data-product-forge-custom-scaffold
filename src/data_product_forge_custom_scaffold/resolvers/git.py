"""Git resolver — clone a repo into the cache.

Source shape::

    {
      "kind": "git",
      "url": "https://github.com/example/my-bundle",
      "ref": "v1.2.0",                          # tag, branch, or sha
      "subdir": "bundles/my-scaffold",          # optional; default: repo root
      "auth": { "secret_ref": "GITHUB_TOKEN" }  # optional
    }

Auth: ``secret_ref`` names an environment variable holding the token.
The token is injected into the clone URL as
``https://<token>@host/repo``. The token is never written to disk and
never logged.

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
import shutil
import subprocess
from pathlib import Path
from typing import Any, Mapping, Optional

from ..manifest import MANIFEST_FILENAME
from .base import ResolutionError, ResolvedBundle, Resolver, cache_root

# Allowed URL schemes — any other scheme is rejected outright.
_ALLOWED_SCHEMES = ("https://", "ssh://", "git@", "git+https://", "git+ssh://")


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
        if not cache_dir.is_dir() or os.environ.get("FLUID_CUSTOM_SCAFFOLD_NOCACHE"):
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

        # Shallow clone to keep cache small. Allow no-checkout for ref/sha switches.
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", "--branch", ref, clone_url, str(cache_dir)],
                check=True,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as e:
            raise ResolutionError("git resolver requires the 'git' binary on PATH") from e
        except subprocess.CalledProcessError as e:
            # Hide the token from any error output before re-raising.
            stderr = (e.stderr or "").replace(clone_url, _sanitise_url(url))
            raise ResolutionError(
                f"git clone failed for {_sanitise_url(url)}@{ref}: {stderr.strip()}"
            ) from None

    @staticmethod
    def _head_sha(repo_dir: Path) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            )
            return result.stdout.strip()
        except Exception:
            return ""


def _safe_ref(ref: str) -> str:
    """Sanitise a ref name for use as a cache directory."""
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in ref)


def _sanitise_url(url: str) -> str:
    """Strip any embedded credentials from a URL for safe logging."""
    if "@" in url and url.startswith("https://"):
        head, _, tail = url.partition("@")
        return f"https://[redacted]@{tail}"
    return url
