# Pull a bundle straight from git

**Time:** 5 minutes | **Difficulty:** Beginner | **Prerequisites:** [Getting Started](../getting-started/README.md)

The fastest way to share a bundle within an organisation is **just push it to a git repo**. No PyPI release, no npm publish — the engine clones it on demand into a per-user cache.

## Step 1 — Find or publish a bundle repo

Any git repo with a `fluid-scaffold.yaml` at the top level (or under a subdirectory) qualifies. For this walkthrough, we'll use the engine's reference bundle:

```
https://github.com/Agenticstiger/data-product-forge-custom-scaffold
  └─ tests/fixtures/reference_bundle/
       ├── fluid-scaffold.yaml
       ├── templates/...
       └── static/docs/...
```

## Step 2 — Reference it in your contract

```yaml
fluidVersion: "0.7.4"
id: my-product
name: My Product
description: Demo product.
metadata: {owner: {team: x, email: x@example.com}}

environments:
  dev:  {metadata: {labels: {"cloud.accountId": "111"}}}
  prod: {metadata: {labels: {"cloud.accountId": "333"}}}

extensions:
  customScaffold:
    libraries:
      - id: ref
        source:
          kind: git
          url: "https://github.com/Agenticstiger/data-product-forge-custom-scaffold"
          ref: "main"
          subdir: "tests/fixtures/reference_bundle"
    patterns:
      - use: ref:basic
```

## Step 3 — Generate

```bash
fluid custom-scaffold
```

```
Resolved libraries:
  ref  (git)  version=<commit-sha>

✓ 3 files written, 0 failed
  README.md
  .gitlab-ci.yml
  docs/runbook.md
```

Files appear in cwd.

## Where the bundle lives on disk

The engine cached it here:

```
~/.cache/fluid/custom-scaffold/git/<urlhash>/<ref>/
```

Re-runs reuse the cache. To force a fresh clone, delete that directory or set:

```bash
FLUID_CUSTOM_SCAFFOLD_NOCACHE=1 fluid custom-scaffold
```

## Private git repos

For private repos, the contract names an environment variable that holds a token. The
operator, who runs the engine, decides which hosts a token may go to; a contract cannot.

```yaml
source:
  kind: git
  url: "https://github.com/my-org/private-bundle"
  ref: "v1.2.0"
  auth:
    secret_ref: FLUID_SCAFFOLD_GITHUB_TOKEN   # an env-var name, never the value
```

```bash
export FLUID_CUSTOM_SCAFFOLD_AUTH_HOSTS=github.com      # hosts a token may be sent to
export FLUID_SCAFFOLD_GITHUB_TOKEN=ghp_xxx
fluid custom-scaffold
```

The rules, all enforced before git runs:

- The host in `url` must be listed in `FLUID_CUSTOM_SCAFFOLD_AUTH_HOSTS` (comma-separated, exact:
  `host`, or `host:port` for a port other than 443). Unset, a source with `auth` is refused and
  no token is sent anywhere.
- `secret_ref` must start with `FLUID_SCAFFOLD_`. To allow other names, list prefixes in
  `FLUID_CUSTOM_SCAFFOLD_SECRET_PREFIXES` (comma-separated).
- `auth` goes only with an `https://host[:port]/path` url (a plain ASCII host name; no user,
  brackets, escapes, backslashes, query or fragment). A `url` that carries credentials, or any
  user part (`https://alice@host/...`, not only `https://user:token@host/...`), is refused: a
  user part can be the token, and the lock would record it. Drop the user from the url.

The token is handed to git in the environment, as an `http.<url>.extraHeader` for that url
only (git 2.31 or newer), and redirects are refused for the fetch. It is on no command line,
so `ps` does not show it, and it is not written to the cache or the lock or logged. A clone in the cache that an earlier
version made with the token in its url is deleted the next time any source is resolved (only
that: nothing else in the cache directory is touched). The cache entry is keyed by the host, the port and the variable name, so a source
without `auth` never reuses a private clone.

### SSH sources

An `ssh://` or `git@host:org/repo` URL runs ssh without prompts (`-o BatchMode=yes`), so a
job cannot hang on a key passphrase or an unknown-host question: add the host to
`known_hosts` and load the key into `ssh-agent` first. If you choose the ssh command
yourself the default is left out: set `GIT_SSH_COMMAND` or `GIT_SSH`, or configure
`core.sshCommand` (an `include` or `includeIf` in your git config, or a template directory, also leaves it
out, since either can set `core.sshCommand` for one host or directory). To be prompted again, set
`GIT_SSH_COMMAND=ssh`.

## Pin a specific version

For production reproducibility, pin a tag or full sha:

```yaml
source:
  kind: git
  url: "https://github.com/example/my-bundle"
  ref: "v2.3.1"            # any tag, branch, or full sha
```

Tags can be force-pushed — if you want guaranteed reproducibility, pin a 40-char commit sha instead.

## Trade-offs vs other channels

| Distribution | Pros | Cons |
|---|---|---|
| **git** | No release workflow needed; tag = version; works for private repos | Requires git on PATH; somewhat slower first-fetch |
| **path** | Best for development | Doesn't share across users |

The engine treats both uniformly — same render pipeline, same `requiredContractFields` checks, same determinism guarantee.

## What's next?

- [Build your own YAML bundle](build-a-yaml-bundle.md) and publish it to git.
- [Manifest reference](../reference/manifest-format.md) — every field explained.
