# TODO

## Bug: `apply <path>` ignores source files added after the manifest was written

`_already_applied` (`src/repo_overlays/apply.py:398`) iterates `manifest.links` and the `info/exclude` block; it never compares them against `stack.iter_files_for_key(key)`. A source that gains a file after the destination's manifest exists is therefore invisible to `apply_one`: `repo-overlay apply <path>` exits 0, prints nothing and materialises nothing. Only a bare `repo-overlay apply` (the `apply_all` path) picks the file up.

Repro (hit live 2026-07-22): the watcher wrote a 2-link manifest for `~/Code/repo-overlays`; a third file was then added under `defaults/repo-overlays/`; two `repo-overlay apply ~/Code/repo-overlays` runs ignored it; `repo-overlay apply` (all) installed it. Affects the `cd`-hook path and `mise`/Emacs hooks, which all call the per-path form.

Fix shape: compare the manifest's link set against the enumerated source set inside `_already_applied` — the enumeration already runs there for the exclude-block comparison, so this is a set difference, not new I/O. Add a test: apply → add a source file → `apply_one` must install it.

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
