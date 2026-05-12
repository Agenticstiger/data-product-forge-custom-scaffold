<!--
Thanks for the contribution. A few things that make review fast:

1. One logical change per PR. Refactor + bug fix + feature in one branch
   makes review slower for everyone.
2. CI must be green. Lint, tests, and the build smoke must pass on every
   supported Python (3.10–3.14).
3. New behavior needs a test. Bug fix? A failing test that the fix turns
   green. New feature? A test that pins the contract.
4. Security-sensitive paths (renderer, git resolver, entrypoint loader)
   need an explicit "this can't be exploited because…" note in the PR
   body.
-->

## Summary

<!-- One paragraph: what changes and why. -->

## What this is, what it isn't

<!-- Two short lists. Helps reviewers focus and avoid scope drift. -->

- ✅ This PR does …
- ❌ This PR does NOT …

## Test plan

<!-- Bulleted checklist of what you verified locally. Reviewers will trust it. -->

- [ ] `pytest tests/` — all green on Python 3.12
- [ ] `ruff check src/ tests/`
- [ ] `black --check src/ tests/`
- [ ] `python -m build && twine check dist/*`
- [ ] Manually exercised: <!-- describe the user-flow you ran end-to-end, if applicable -->

## Security considerations

<!-- If you touched renderer.py, resolvers/, validation.py, or anything that handles untrusted input, explain the trust boundaries. Otherwise: "N/A — pure refactor / docs / no untrusted-input handling." -->

## Breaking changes

<!-- If yes: describe migration. CHANGELOG entry. Otherwise: "None." -->
