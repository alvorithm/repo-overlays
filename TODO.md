# TODO

## Bug: `apply` crashes on dangling destination symlinks

`src/repo_overlays/apply.py:95-104` — existence checks use `Path.exists()`, which **follows symlinks**. A destination that is a *dangling* symlink (target deleted) returns `False` from both checks, so the code takes the bare `symlink_to()` branch and dies with `FileExistsError` instead of replacing the link.

Repro (hit live 2026-07-09): rename an overlay key dir (`ai-overlay/Work` → `ai-overlay/General`) while its rendered symlinks exist in the target repo — every old symlink now dangles; next `repo-overlay apply` traceback:

```
File ".../apply.py", line 102, in _apply_key
    final_dest.symlink_to(link_target) if not final_dest.exists() else (
FileExistsError: [Errno 17] File exists: '.../General/dot_claude/settings.json' -> '/home/alvar/Ask/General/.claude/settings.json'
```

Fix shape: treat "is a symlink" as replaceable regardless of whether its target resolves — e.g. `if final_dest.is_symlink(): final_dest.unlink()` before the exists-guard, or `exists(follow_symlinks=False)` (3.12+). The regular-file guard at line 95 is unaffected (`not is_symlink()` already excludes links). Add a test: apply → delete/rename source → apply again must converge, not crash. Workaround until fixed: `find <repo> -xtype l -delete` then re-apply.

## Feature: new-repo bootstrap from a template overlay

Problem: a freshly cloned/created repo under a watched root gets **zero** overlay coverage until a key dir is hand-crafted in the overlay source (per-repo `dot_claude/settings.json`, `dot_hindsight/config.json`, …). There is no reminder anywhere in the flow, so this is forgotten essentially every time, and the repo runs without memory routing/agent guidance until someone notices.

Requirements:
- It must be possible to set up a repo **from the get-go** with a minimal overlay (e.g. from `ai-overlay`), one command or less.
- Discovery, not memory: unkeyed repos under watched roots should be *surfaced*, not silently skipped.

Suggested design (adjust freely):
- A `_template/` dir per overlay source holding a minimal key skeleton with placeholders (`{{key}}`, `{{slug}}` = kebab-cased basename by default) — rendered by the same Mustache machinery already used for `.mo` files.
- `repo-overlay init <repo> [--source ai-overlay]`: creates `<source>/<key>/` from the template, then applies. Idempotent; refuses to clobber an existing key.
- `repo-overlay status` (and the watch service log) lists repos under watched roots with **no matching key** as `unmanaged`, so the gap is visible without running init.
- Optional: `watch` auto-inits from the template when a new repo appears (opt-in flag; default should stay surface-only to keep the human in the loop for slug choice — cwd basename is not always the right slug, cf. memory-bus I2).
