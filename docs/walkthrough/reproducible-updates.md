# Reproducibility & updates — the lockfile, `--pin`, and `--update`

**Time:** 10 minutes | **Prerequisites:** a contract that scaffolds from a **git**
bundle (the lockfile records git commits; path / entry-point sources can't be
reproducibly pinned).

Scaffolding from a template raises two questions every team eventually hits:

1. **Reproducibility** — can I regenerate the *exact same* output later, even
   though the template's `main` branch has moved on?
2. **Updates** — the template improved; how do I pull those changes into a
   project I generated months ago **without losing the edits I made**?

This engine answers both with a single artifact — `fluid-scaffold.lock` — and two
flags: `--pin` and `--update`. The model is borrowed from
[copier](https://copier.readthedocs.io/)'s `.copier-answers.yml` + `copier update`.

---

## 1. The lockfile

Every **successful** (non-`--dry-run`) generation writes a `fluid-scaffold.lock`
at the output root:

```bash
fluid custom-scaffold -c contract.fluid.yaml -o ./my-project
```

```yaml
# fluid-scaffold.lock — records what generated this output tree, for
# reproducible re-runs. Managed by data-product-forge-custom-scaffold;
# commit it alongside the generated files. Do not edit by hand.
engineVersion: 0.3.0
lockfileVersion: 1
libraries:
  acme:
    kind: git
    src: https://github.com/acme/scaffold-bundles
    ref: main                  # the floating ref the contract asked for
    commit: 9f3c1a…            # the EXACT commit it resolved to (the pin)
    subdir: bundles/dbt
patterns:
  - use: acme:dbt-project
    variables: {warehouse: snowflake}
```

It is **deterministic** (sorted keys) and **credential-free** (only the source
URL, ref, commit, and subdir — never your `auth` block or any token). **Commit
it** alongside the generated files; it travels with the project.

> The lock is the source of truth for "what produced this output". `git diff` on
> it tells you exactly which template commit a project is on.

---

## 2. `--pin` — reproducible re-runs

A contract usually points at a moving ref (`ref: main`). Re-running normally
**follows** that ref and re-resolves to whatever it points at *now* (and the lock
advances). When you instead want the *exact* commit you generated from:

```bash
fluid custom-scaffold -c contract.fluid.yaml -o ./my-project --pin
```

`--pin` resolves each **git** library to the `commit` recorded in the lock rather
than the floating ref — `npm ci` / `poetry --frozen` semantics. Use it in CI to
guarantee byte-identical regeneration.

> Only **git** sources carry a reproducible commit. `path:` and entry-point
> (`pypi:`) sources resolve to whatever is on disk / installed *now*, so `--pin`
> can't freeze them — the engine **warns** rather than imply a false pin.

---

## 3. `--update` — pull template changes, keep your edits

The template evolved and you want the improvements, but you've hand-edited the
generated files. `--update` does a **3-way merge**:

```bash
fluid custom-scaffold -c contract.fluid.yaml -o ./my-project --update
# or update to a specific tag/commit instead of the contract's ref:
fluid custom-scaffold -c contract.fluid.yaml -o ./my-project --update --target v2.0.0
```

Under the hood, for every file the engine renders the template **twice in
memory** and merges with `git merge-file`:

| Side | What it is |
|---|---|
| **base** | the template rendered at the **locked** commit — the baseline you started from |
| **ours** | the file currently on disk — **your edits** |
| **theirs** | the template rendered at the **new** ref — the upstream improvement |

- **Non-overlapping** template and user changes merge **cleanly**.
- **Overlapping** changes produce standard Git conflict markers:

  ```
  <<<<<<< current (your edits)
  region = "us-west-2"
  =======
  region = "us-east-1"
  >>>>>>> new (updated template)
  ```

  Resolve them like any merge conflict, then commit. `--update` exits **`4`** when
  any file has conflicts, so CI can detect "merged, needs attention".

On success the lock advances to the new commit. The per-file outcome is reported:

```text
Updated ./my-project:
  merged           dbt_project.yml
  added            models/staging/_sources.yml
  unchanged        README.md
  conflict         profiles.yml

⚠ 1 file(s) have conflict markers (<<<<<<< / >>>>>>>) — resolve them, then commit.
```

### A typical loop

```bash
fluid custom-scaffold -c contract.fluid.yaml -o ./proj   # generate (writes the lock)
git -C proj init && git -C proj add -A && git -C proj commit -m "scaffold"
# … hand-edit some files, commit your changes …

# later, the template ships improvements:
fluid custom-scaffold -c contract.fluid.yaml -o ./proj --update
git -C proj diff           # review the merge; resolve any conflict markers
git -C proj add -A && git -C proj commit -m "update scaffold to latest template"
```

---

## Notes & current limits

- **Deleted-upstream files are kept**, not removed: if the new template no longer
  emits a file you have, `--update` leaves your copy in place and reports it as
  `removed-upstream` (it never deletes your work).
- **`variables` are taken from the contract**, not re-prompted, and there is no
  migrations hook yet — both are planned follow-ups toward full copier parity.
- `--update` requires an existing `fluid-scaffold.lock` (generate first).
