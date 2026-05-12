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
- **Auth token redaction** — `resolvers/git.py::_inject_token` injects credentials via the URL but `_sanitise_url` strips them from every error message and log line.
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
