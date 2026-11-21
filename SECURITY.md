# Security policy

## Reporting a vulnerability

If you believe you've found a security issue in `data-product-forge-custom-scaffold`, **do not** open a public GitHub issue. Use GitHub's private vulnerability-reporting channel:

> **[Report a vulnerability →](https://github.com/Agenticstiger/data-product-forge-custom-scaffold/security/advisories/new)**

Include:

- A description of the issue and its impact.
- Steps to reproduce (a minimal contract + bundle is ideal).
- The version of the engine affected (`pip show data-product-forge-custom-scaffold | grep ^Version`).

You should hear back within 3 business days. Fix-and-CVE turnaround is 30 days for high-severity, best-effort for everything else.

## Threat model

This engine processes **two classes of input** with different trust:

| Input | Trust level | Source |
|---|---|---|
| **fluid contract** (`contract.fluid.yaml`) | Untrusted | The user's repo. May contain anything the user wrote. |
| **bundle** (`fluid-scaffold.yaml` + templates + static files) | Trusted | The bundle author. The user explicitly opted in by referencing the bundle in their contract. |

Bundles are *opt-in code* — installing a bundle is roughly equivalent to running its scripts. The engine does not sandbox bundles, and it shouldn't try to: Jinja templates and Python plugins exist specifically because users want full control over the output.

### What the engine defends against

These are real defenses the engine implements. Test pinning lives in `tests/unit/`:

- **Path traversal in destination paths** — `renderer._check_path_safety` rejects absolute paths and `..` segments before any write. Pinned by `test_renderer_symlink_safety.py` and `test_validation.py`.
- **Template source escape** — `renderer._render_one` resolves the source path against `bundle_root` and refuses if `relative_to` fails. A bundle can't render `/etc/passwd` by setting `from: ../../../../etc/passwd`.
- **Symlinks in `static/`** — `renderer._collect_static_files` refuses symlinks outright, with an explicit comment naming the threat (a bundle's `static/aws-creds` symlinked to `~/.aws/credentials` would otherwise leak the host's credentials into the output).
- **Git resolver URL schemes** — `resolvers/git.py::_ALLOWED_SCHEMES` allowlists `https/ssh/git+https/git+ssh`. `file://` and other dangerous schemes are rejected before any `git clone` invocation.
- **Git `ref` shape** — the JSON-Schema for `extensions.customScaffold.libraries[].source.ref` is `^[A-Za-z0-9._/-]+$` with `maxLength: 256`. A `ref` shaped like a git flag (`--upload-pack=evil`) or containing shell metacharacters is rejected before reaching the subprocess. Pinned by `test_dangerous_ref_values_rejected`.
- **Git subprocess safety** — `subprocess.run([...], shell=False)` list-form invocation; no shell metacharacter interpretation.
- **Credentials go only where the operator allows** — a contract may come from a pull request, so it only *asks* for a credential. `resolvers/git.py::_bind_auth` refuses a source unless its host and port (`host` means 443) are in the operator's `FLUID_CUSTOM_SCAFFOLD_AUTH_HOSTS` and its `secret_ref` starts with `FLUID_SCAFFOLD_` (or an operator-allowed prefix), before any environment value is read; `auth` goes only with a plain `https://host[:port]/path` url, whose host is read by the pattern that checks its shape, not by a url parser; a `url` that carries credentials or any user part, or a control character, is refused, so none reach the lock or the cache key. Pinned by `test_git_credential_binding.py`.
- **Token not on a command line, in the cache, or in a log** — the token reaches git as `http.<url>.extraHeader` in `GIT_CONFIG_*` environment pairs (git 2.31+), scoped to the source's url, and git is told not to follow a redirect from that url with `http.<url>.followRedirects=false`, scoped as well so that a setting the operator makes for the same url cannot turn redirects back on. `ps` (every user's view of a command line) shows none of it, `git`'s own children included; no variable a contract may name is passed to `git` or to the helpers it starts; nothing is written to `.git/config`, `FETCH_HEAD` or the lock; error text is redacted. A cached clone whose git config carries credentials, made by an earlier version, is cleaned before it is reused, and any other clone that carries them is deleted at the start of every run, but only one the engine made (inside `<cache>/git/<16 hex digits>/`, in a directory named for its own url); leftovers of a killed run are swept the same way; nothing else is looked at, and a `<cache>/git` that is a link is not swept; a cache entry that is a symbolic link is refused. The cache key names the host, the port and the variable (never the value). Pinned by `test_git_token_flow.py`, `test_git_cached_credentials.py` and `test_git_cache_sweep.py`; `tests/integration/test_git_https_credential.py` runs it against a local HTTPS git server (the header on every request, a rejected token, a refused redirect).
- **No prompts** — git runs with `GIT_TERMINAL_PROMPT=0`, and an ssh URL (as git will use it, after `url.<base>.insteadOf`) with `ssh -o BatchMode=yes` unless the caller could be choosing the ssh command (`GIT_SSH_COMMAND`, `GIT_SSH`, `core.sshCommand`, any git-config `include`, or a template directory), so a non-interactive job fails instead of waiting for an answer. Pinned by `test_git_hardening.py` and `test_git_ssh_default.py`.
- **Cache directories** — a clone is built beside its final place and renamed in when whole, so a killed run cannot leave a half-made directory that a later run reuses; a clone being replaced or removed is renamed aside first, so it is never left half-deleted (one that cannot be renamed is left whole and the run fails); a run that finds another run's finished clone already in place keeps it (it may be in use); a `ref` made only of dots is mapped to underscores so it cannot name the url's directory or its parent, and one that starts like a leftover (`.partial-`, `.old-`) cannot name a directory the sweep deletes (`_safe_ref`).
- **YAML loading** — `yaml.safe_load` everywhere. No custom constructors, no `Loader=FullLoader`.
- **Atomic writes** — file writes go through `os.replace` so a crashed run leaves either the old or new content, never a half-written file.
- **Deterministic output** — same contract + same bundle ⇒ same bytes, every time. Pinned by `tests/integration/test_round_trip.py::test_determinism_byte_identical`.

### What the engine deliberately does NOT defend against

- **Malicious bundles.** A bundle author can write a Jinja template that emits an executable shell script, or a Python plugin that calls `os.system`. The engine doesn't sandbox bundle code — that would defeat the purpose. **Treat bundles like any other Python dependency: install only what you trust.**
- **Jinja autoescaping.** Templates render with `autoescape=False` because the typical output is code (CI YAML, Dockerfiles, application source) where HTML-escaping would corrupt the output. Note that Jinja does **not** recursively re-evaluate context values as templates, so a contract field containing `{{ 7*7 }}` is rendered literally, not executed.
- **Resource limits.** A bundle's Jinja template can `{% for i in range(10**9) %}` and hang the CLI. The engine has no per-render timeout or memory cap.

## Supported versions

| Version | Supported          |
|---------|--------------------|
| 0.1.x   | ✅ Active           |

Security fixes are backported only to the latest minor while it remains active.
