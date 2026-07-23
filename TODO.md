# TODO

## Feature: new-repo bootstrap from a template overlay

**Design draft**: `work/wf-now/init-bootstrap/DESIGN.md` (overlay-managed via `defaults`, not committed here) — evidence inventory, `_template/` per source, `init` semantics, the `status --unmanaged` gating question, and a test plan.

Problem: a freshly cloned/created repo under a watched root gets **zero** overlay coverage until a key dir is hand-crafted in the overlay source (per-repo `dot_claude/settings.json`, `dot_hindsight/config.json`, …). There is no reminder anywhere in the flow, so this is forgotten essentially every time, and the repo runs without memory routing/agent guidance until someone notices.

Requirements:
- It must be possible to set up a repo **from the get-go** with a minimal overlay (e.g. from `defaults`), one command or less.
- Discovery, not memory: unkeyed repos under watched roots should be *surfaced*, not silently skipped.

Suggested design (adjust freely):
- A `_template/` dir per overlay source holding a minimal key skeleton with placeholders (`{{key}}`, `{{slug}}` = kebab-cased basename by default) — rendered by the same Mustache machinery already used for `.mo` files.
- `repo-overlay init <repo> [--source defaults]`: creates `<source>/<key>/` from the template, then applies. Idempotent; refuses to clobber an existing key.
- `repo-overlay status` (and the watch service log) lists repos under watched roots with **no matching key** as `unmanaged`, so the gap is visible without running init.
- Optional: `watch` auto-inits from the template when a new repo appears (opt-in flag; default should stay surface-only to keep the human in the loop for slug choice — cwd basename is not always the right slug, cf. memory-bus I2).
