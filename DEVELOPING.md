# Developing repo-overlays

## Architecture

The tool is a pipeline: **load config → build source stack → resolve destinations → render templates → place symlinks → write manifest**.

Each `apply` run re-derives everything from scratch. There is no persistent process state beyond the per-destination `.repo-overlays.toml` manifest and the `_rendered/` cache inside each source. The watcher daemon is a loop that calls `apply_all` on debounced inotify events.

### Module map

```
src/repo_overlays/
├── cli.py        # argparse entry point; no logic, delegates to modules below
├── config.py     # load ~/.config/repo-overlays/config.toml + per-source config.toml;
│                 # produces frozen AppConfig (SourceConfig list + unified targets)
├── sources.py    # SourceStack: iterate keys, merge per-key file lists (later src wins),
│                 # resolve_partial() with @name/ addressing and privacy enforcement
├── resolve.py    # map a path → (key, dest_root, is_fixed) via fixed targets, then git
│                 # remote slug (falling back to basename if slug matches no source key);
│                 # iter_all_destinations() for apply_all
├── render.py     # render *.mo by recursive {{>ref}} substitution with _PartialLoader;
│                 # drift detection; writes _rendered/<source>/<key>/<rel>
├── apply.py      # walk stack for each key, call render or symlink verbatim, dot_rewrite
│                 # for project overlays; skip silently (_already_applied) on re-entry;
│                 # catch missing partials and continue; prune manifest on re-apply
├── manifest.py   # read/write/prune <dest_root>/.repo-overlays.toml (tomllib/tomli_w)
├── promote.py    # interactive reconciliation of .proposed / .divergent files
└── watch.py      # inotify_simple loop with 200 ms debounce; calls apply_all on events
```

### Data flow: `apply_all`

```
load_config()
  └─ per-source config.toml → SourceConfig (watched_roots, targets)
iter_all_destinations(config)
  ├─ unified targets (fixed, e.g. _claude → ~/.config/claude)
  └─ rglob(".git") under watched_roots → git remote slug or basename
for each (key, dest_root, is_fixed):
  SourceStack.iter_files_for_key(key)   # merged, later source wins
  for each file:
    *.mo  → render_template()
              _PartialLoader.get(ref)   # calls resolve_partial() cross-source
              drift check: sha256(live) vs sha256(prior render)
              write _rendered/<src>/<key>/<rel-no-mo>
              symlink dest → _rendered/…
    other → symlink dest → source file directly
  prune(dest_root, current_paths)       # remove symlinks not in this run
  write(dest_root, links)               # update .repo-overlays.toml
```

### Key invariants

**Partial resolution** — `_PartialLoader` does not delegate to a third-party Mustache library. `_resolve_partials()` (render.py) applies a compiled regex over `{{>ref}}` tags and calls `SourceStack.resolve_partial()` recursively. This is the only mechanism that can resolve partials across sources.

**Drift detection** — compares sha256 of the *live file* against sha256 of the *prior render* (at `_rendered/`). If they differ, the agent edited the live file since the last render → diverged. The new render goes to `<live>.proposed`; the `.divergent` marker holds the live file's hash. `repo-overlay promote` clears both.

**Privacy** — `_check_privacy()` (sources.py): a non-private source requesting a partial from a private source raises `PermissionError` at apply time. Private requesting public is always allowed.

**Manifest pruning** — on every apply, `prune()` reads the old manifest, removes symlinks whose rel-paths are absent from the current run, then `write()` saves the new set. This handles source file deletions and source removal from config without leaving dangling links.

**Dot-rewrite** — `_dot_rewrite()` (apply.py) replaces `dot_` prefix on each path component with `.`. Applied only for project overlays (`is_fixed=False`). Fixed targets keep paths verbatim.

**Slug-fallback** — `_resolve_project_key()` (resolve.py) tries the git remote slug first, then falls back to the toplevel basename if no source has the slug-named key. Lets overlay keys be named after the repo basename (e.g. `beadpot`) even when the remote URL is `owner/beadpot`.

**Skip on re-entry** — `_already_applied()` (apply.py) checks the manifest before printing. If the destination already has a valid manifest with all symlinks in place, `apply_one` returns silently. Prevents noisy output on every `cd` into an already-applied repo.

**Regular-file guard** — `_apply_key()` skips any destination path that is a regular (non-symlink) file with a warning. It will never silently overwrite a committed project file.

**Missing partials** — `_apply_key()` catches `FileNotFoundError` from unresolved `{{>…}}` refs. The error is reported but the program continues to the next overlay key rather than aborting entirely.

## Development workflow

```sh
cd ~/Code/repo-overlays
uv sync                         # install deps + dev extras (.venv/)
uv run pytest                   # all tests (temp files under /tmp)
uv run pytest -x -q             # fail-fast, quiet
uv run repo-overlay config      # smoke-test against real config
uv run repo-overlay apply --config /tmp/test-config.toml  # isolated run
```

Tests are fully hermetic: `conftest.py` helpers build synthetic sources under `/tmp` via `tmp_path`. No real source repos or destinations are touched.

### Testing a change end-to-end

```sh
uv run repo-overlay apply        # apply against ~/.config/repo-overlays/config.toml
uv run repo-overlay status       # check for drift, broken links, missing partials
```

Stop the watcher service before testing to avoid races:

```sh
systemctl --user stop repo-overlay.service
# make and test changes
systemctl --user start repo-overlay.service
```

## Known issues

**Destination depth cap** — `iter_all_destinations` (resolve.py:91) caps git repo discovery at 5 path components relative to a `watched_root`. Worktree layouts deeper than this are silently skipped. Increase the cap or add the destination explicitly as a fixed target in `config.toml`.

**Divergence false positives** — `status` detects diverged destinations by scanning `dest_root.rglob(".divergent")`. If two destination roots share a parent (e.g. one is a subdirectory of the other), a marker from the inner root will appear in the outer root's scan.

**No Mustache variables** — templates support `{{>partial}}` inclusion only. `{{variable}}` and `{{#section}}` tags are passed through verbatim. Add variable substitution to `_resolve_partials` if needed.
