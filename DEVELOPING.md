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
├── resolve.py    # map a path → (key, dest_root, is_fixed) via fixed targets, then the
│                 # first of remote slug / basename / bare repo name that names an existing
│                 # key (else slug, else basename); iter_all_destinations() for apply_all
├── render.py     # render *.mo by recursive {{>ref}} substitution with _PartialLoader;
│                 # drift detection; writes _rendered/<source>/<key>/<rel>
├── apply.py      # walk stack for each key, call render or symlink verbatim, dot_rewrite
│                 # for project overlays; skip silently (_already_applied) when current;
│                 # catch missing partials and continue; prune manifest on re-apply
├── manifest.py   # read/write/prune <dest_root>/.repo-overlays.toml (tomllib/tomli_w)
├── bootstrap.py  # repo-overlay init: seed <source>/<key>/ from each source's _template/,
│                 # four init vars, then apply; unmanaged_destinations() → status --unmanaged
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

**Partial resolution** — `_PartialLoader` does not delegate partial resolution to a third-party library. `_resolve_partials()` (render.py) applies a compiled regex over `{{>ref}}` tags and calls `SourceStack.resolve_partial()` recursively. This is the only mechanism that can resolve partials across sources. For a data-active key (one carrying `data.toml`), chevron then renders variables and sections over the resolved text; partials are never handed to chevron.

**Drift detection** — compares sha256 of the *live file* against sha256 of the *prior render* (at `_rendered/`). If they differ, the agent edited the live file since the last render → diverged. The new render goes to `<live>.proposed`; the `.divergent` marker holds the live file's hash. `repo-overlay promote` clears both.

**Privacy** — `_check_privacy()` (sources.py): a non-private source requesting a partial from a private source raises `PermissionError` at apply time. Private requesting public is always allowed.

**Manifest pruning** — on every apply, `prune()` reads the old manifest, removes symlinks whose rel-paths are absent from the current run, then `write()` saves the new set. This handles source file deletions and source removal from config without leaving dangling links.

**Dot-rewrite** — `_dot_rewrite()` (apply.py) replaces `dot_` prefix on each path component with `.`. Applied only for project overlays (`is_fixed=False`). Fixed targets keep paths verbatim.

**Slug-fallback** — `_resolve_project_key()` (resolve.py) resolves a destination to an overlay key by trying three candidates in order: (1) git remote slug (`owner_repo`), (2) toplevel directory basename, (3) bare git remote repo name (`repo`). The first that names a source key wins; if none match it falls back to the slug, else the basename. Step 2 lets keys be named after the repo basename (e.g. `beadpot`) even when the remote is `owner/beadpot`; step 3 lets a linked worktree match by repo identity regardless of its directory name.

**Worktree resolution** — `_git_toplevel()` (resolve.py) handles both regular `.git` directories and linked-worktree `.git` files, resolving through the git common dir and origin remote URL. This is what makes the bare-remote-repo-name candidate (step 3 above) usable: a worktree checked out under an arbitrary directory name still resolves to the same overlay key as its main checkout.

**Bootstrap keys**: `bootstrap()` (bootstrap.py) resolves the destination with `resolve_key_dest`, whose owner-qualified-slug fallback is right for *lookup* and wrong for *creation*: minting the first `owner_repo` key dir would be a silent, permanent divergence from the hand-made bare names, so a key no source has yet defaults to the bare remote repo name, else the destination basename, with `--key` as the disambiguator. A `--key` the resolver could never reach for that destination (neither its remote slug, nor its basename, nor its bare repo name) is refused before anything is written, because nothing would ever look at that directory. The skeleton copy filters with `is_tool_junk`, not `_is_junk` (sources.py): the `.overlay-own` marker and a key-root `data.toml` never *materialise*, but this copy is source to source, so they are content a skeleton must be able to seed; only editor leftovers and tool caches are dropped. Backfilling an existing key is therefore safe, since every collision has a skip: `ok:` for a path already in the key dir, `have:` for a destination path another source already provides for that key or that the destination holds as a regular file (`apply` would refuse to overwrite it anyway). Exit codes split by mode: `init --write` follows `apply` (0 on success, 2 on error, never 1), the dry run follows `status` (0 nothing to create, 1 work pending).

**Git-dir destinations** — `_resolve_dest()` / `_git_path()` (apply.py) route any destination whose first component is `.git` through `git rev-parse --git-path`, never a plain join: in a linked worktree `.git` is a *file*, so joining raised `NotADirectoryError`, and git itself splits the git dir between the per-worktree gitdir (`HEAD`, `index`) and the shared common dir (`hooks/`, `info/`, `config`). Links resolving outside `dest_root` are recorded absolute in the manifest — `dest_root / <absolute>` returns the absolute path, so manifest consumers are unaffected. `info/exclude` is shared for the same reason, hence the `[<dest_root>]`-labelled block per destination.

**Failure containment** — `_apply_key()` reports and skips per-file `OSError` (source → destination, error type); `_apply_and_record()` wraps the whole per-destination apply in the same net. One unwritable or malformed destination never aborts a sweep over the others.

**Skip when current** — `_already_applied()` (apply.py) checks the manifest before printing. If the destination already has a valid manifest with all symlinks in place, both `apply_one` and `apply_all` return silently. For `apply_one` this prevents noisy output on every `cd`; for `apply_all` it is load-bearing, because `_apply_and_record` rewrites a manifest and an `info/exclude` block per destination, and a watched destination turns that write into the next sweep's trigger. A planned path the destination holds as a *regular file* counts as accounted for (`_blocks_a_link()`): `_apply_key` refuses to overwrite one, so a fresh apply is genuinely a no-op there, and treating it as a missing link left every repo with its own bundled `AGENTS.md` permanently "not applied".

**Bounded watch growth** — `_watch_created_dir()` (watch.py) watches a directory created after startup, but only within the descent budget its parent watch carries (`_collect_watch_paths` hands out `_SOURCE_DEPTH` for source trees and `_TOP_LEVEL_ONLY` for `watched_roots` and source parents). Unbounded, the set had no ceiling: each new directory granted the right to watch its own children, so one clone under a watched root pulled a whole checkout in (measured: 166 → 1141 watches, most of it `node_modules`), and any destination caught that way closed a feedback loop with the manifest write above. `_should_ignore()` filters this tool's own artifacts (manifest, `.proposed`, `.divergent`) as a second line of defence; `.repo-overlays-skip` is deliberately *not* filtered, since creating one must trigger the withdrawal.

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

**Destination depth cap**: `_iter_repo_roots` (resolve.py:140) caps git repo discovery at `_MAX_SEARCH_DEPTH` = 4 path components below a `watched_root`, and stops descending at the first repo it finds. A checkout deeper than the cap is silently skipped; raise the cap or add the destination explicitly as a fixed target in `config.toml`. Linked worktrees are exempt since f1c568a: they come from `git worktree list` (`_linked_worktrees`), so they are picked up wherever they live, however deep.

**Variables are key-gated** — templates render `{{>partial}}` only unless the key carries `data.toml`; then chevron renders the partial-resolved text against the parsed TOML (variables, sections, inverted sections, `{{{raw}}}`). No-data keys stay byte-identical to the legacy regex path (regression-tested). The data file resolves like a partial — first source in stack order wins, whole file, never merged, privacy-checked — and never materialises (it is excluded in `_is_junk`). List-of-table values get `first`/`last` injected (reserved keys). `status` lints plain `{{var}}` refs against the data (`unknown-data-ref`), tracking section context like chevron's context stack; sections over absent data legitimately render nothing.

## Python coding conventions

- **Docstrings** — Google style. Start with `Return …` (or describe the side effect for procedures), omit type annotations from the prose (they belong on the signature), and use single backticks for inline code, never double.
- **Certifying tests** — must be functional or end-to-end. No setter/getter tests, no mocks. Tests build synthetic sources under `/tmp` (see `conftest.py`) and exercise the real pipeline.
- **Dynamic imports** — `sys.path.insert` at runtime confuses the type checker; silence the resulting error with `# ty: ignore[unresolved-import]`. A trailing `# ty:` comment can trip ruff `PLC0415` (import-outside-top-level); make sure it does not conflict with an existing `# noqa` suppression on the same line.
- **argparse** — avoid `argparse.FileType` (deprecated since Python 3.14). Accept path strings and open them after `parse_args()`, preserving the `-` convention for stdin/stdout.

## Commit discipline

- **One concern per commit** — keep unrelated changes in separate commits with their own documented messages, so history stays bisectable and reviewable.
- **Always commit; `docs(<area>):` convention** — the docs-authoring skill mandates an always-commit policy and a `docs(<area>): …` subject line for documentation changes.
