# repo-overlays: functionalities and usage

Compose personal/team-authored Markdown guidance for AI agents (and humans reading the
same files) into project worktrees, without committing it upstream.

## 1. Functionalities

- **Per-project AI guidance** (`CLAUDE.md`, `AGENTS.md`, `.claude/commands/*.md`,
  subagents, skills) materialised into any worktree, gitignored at the destination.
- **Fixed-target guidance** (e.g. global `~/.config/claude/CLAUDE.md`, `~/AGENTS.md`)
  materialised into absolute paths.
- **Shared snippets** (`_shared/*.md`) composed into project overlays via Mustache
  partials, so common guidance lives once.
- **Drift detection**: when an agent edits a live file, the next apply refuses to
  overwrite, saves a `.proposed` render, and notifies you. Edits are reconciled with an
  interactive promote step.
- **Content-change detection**: a `*.json.mo` render is validated before it is
  written — unparseable JSON is refused, the last good render stays live, and
  `status` reports it as `invalid-json`. A per-link render hash makes
  `apply <path>` (the cd/editor hooks) re-render after a partial or template
  edit even though no path changed, and `status` reports a render that
  nothing re-applied (`stale:` — the watcher was down for that edit).
- **Data-driven templates**: a key carrying `data.toml` is *data-active* — its
  `.mo` templates render Mustache variables and sections (chevron) against the
  parsed TOML; every other key keeps the legacy partial-only contract,
  byte-identical. `status` lints variable refs that resolve against no data
  key (`unknown-data-ref:`).
- **Multi-source composition**: stack several overlay repos (private +
  public/Codeberg-wiki) so collaborators can contribute to one without seeing the
  others.

## 2. Concepts
- **Overlay source**: A directory (typically a Git repo) holding overlays identified by keys. You can have several souce repos; they are stacked in declared order.
- **Overlay key**: A top-level directory in a source. Two kinds: 
    - *Fixed target*: Key starts with `_`. Bound to an absolute destination in the source's `config.toml`. Layout mirrors the destination 1:1.
    - *Project overlay*: Key does not start with `_`. Bound to a worktree by git remote slug (`owner_repo`), falling back to the directory basename if the slug doesn't match any source key. Uses `dot_X` → `.X` rewrite at materialisation.
- **Ignored key**: A top-level directory declared as NOT an overlay key for its source — via `ignore_keys = ["dir", …]` in the source's `config.toml` or an `.overlay-ignore` file at the source root (one name per line, `#` comments, trailing `/` allowed). Lets a source repo carry non-overlay content (docs, staging dirs) without the name accidentally matching a repo under a watched root. Per-source: another source may still provide the same key.
- **Partial**: `_shared/<name>.md` in any source. Referenced from templates as `{{>_shared/<name>.md}}`.
- **Template**: Any file in a source ending in `.mo`. Rendered to `_rendered/<key>/<path>` (extension stripped). Non-`.mo` files are symlinked verbatim. A template whose destination ends in `.json` must render to parseable JSON; an unparseable render is refused (status: `invalid-json`).
- **Data**: `<key>/data.toml` in any source. A key carrying one is *data-active*: its `.mo` templates render with full Mustache (variables `{{name}}`, sections `{{#list}}…{{/list}}`, inverted `{{^x}}`, raw `{{{x}}}`) against the parsed TOML. A key without one keeps the legacy contract: only `{{>partial}}` resolves and every other `{{…}}` passes through verbatim. The file resolves like a partial — first source in stack order wins, whole file, never merged, privacy-checked — and is never materialised. List-of-table values get `first`/`last` booleans injected (reserved keys) so templates can join items with commas. 
- **Live file**: The symlink at the destination that the agent reads/writes.
- **Drifted file**: A live file no longer matches a fresh render of its source. 

## 3. Overlay source layout

These are the files you may find in a source overlay repo:

```
<overlay>/
├── config.toml                   # how to resolve fixed & project (see below)
├── README.md                     # (optional) index page for wiki-like navigation
├── _shared/                      # (optional) re-usable snippets for templates
│   └── python-style.md           
├── _rendered/                    # (optional) rendered .mo templates; gitignored
├── _claude/                      # fixed target → ~/.config/claude
│   ├── CLAUDE.md.mo
│   └── commands/commit-msg.md
└── beadpot/                      # project overlay target → repo/worktree "beadpot"
    ├── CLAUDE.md.mo
    └── dot_claude/commands/release.md
```

### Overlaying your first project
Example minimal overlay

```
beadpot_agent_overlay/
├── config.toml                   # `watched_roots=["~/Code"]` says where to find ...
└── beadpot/                      # ... the target for this dir → ~/Code/beadpot
    ├── AGENTS.md                 # literal global AGENTS file, git-tracked in overlay
    └── tests/
      └── AGENTS.md
```

### Finding key targets with `<overlay>/config.toml`

The file `config.toml` specifies how to map overlay directories to targets
- directories prefixed `_` point to “fixed” targets declared in `[targets]`
    - (except for `_shared` and `_rendered`, which are reserved for internal use)
- any other directories are treated as repo/worktree keys to be resolved by looking into the listed `watched_roots` in order.

```toml
watched_roots = ["~/Code", "~/work"]

[targets]
_home   = "~"
_claude = "~/.config/claude"
```

## 4. Day-to-day workflow

The files in an overlay repo `<source>/<key>` are the authoritative sources for human
reading and editing. Live destinations are symlinks pointing to
`<overlay>/_rendered/<key>`. Agents read and edit the overlay via these symlinks, unaware of their origin. To facilitate inspection, the structure within mirrors the
source structure one level lower at `<source>/<key>`, but is machine-generated.

### 4.1 Human authoring guidance

When you want to document your target project with local notes -- for yourself or for an agent, you can:

1. Edit a regular or template source file:
    ```sh
    $EDITOR <overlay>/<key>/AGENTS.md
    ```

2. Edit a partial (snippet). Snippets can be included from multiple `<key>` templates
   (`.mo`). For example, by placing language-specific agent instructions or
   CONTRIBUTING.md rules in `_shared`, a single source of truth can be shared across
   target projects 
    ```sh
    $EDITOR <overlay>/_shared/python-style.md
    ```

3. Edit a project-specific (e.g. `<key>=beadpot`) template, typically to assemble it
   from snippets in `_shared`:
    ```sh
    $EDITOR <overlay>/beadpot/CLAUDE.md.mo
    ```

After any of these interventions, you materialise rendered overlay sources to the `beadpot` key target (e.g. `~/Code/beadpot`) via `repo-overlay apply` 

`apply` is also triggered automatically by:

- a systemd user service (`repo-overlay.service`), whenever files change under any
  overlay source or watched root;
- a `mise enter` hook when you `cd` into a watched project (the first entry prints a confirmation line; re-entering an already-applied repo or any of its subdirectories is silent);
- an Emacs `find-file-hook`, plus `:after` advice on `project-switch-project` (memoised per root per session; note there is no `project-switch-hook` in `project.el`, despite what earlier revisions of the README suggested)

See below for installation notes for systemd/mise/emacs.

See below for `repo-overlay` subcommands other than `apply`.

### 4.2 LLM authoring guidance

An agent may edit a file in the target repo via the symbolic link there ("live file"). For example, it may edit its own instructions, `~/Code/beadpot/CLAUDE.md`, or update local project documentation after some changes, e.g. `~/Code/beadpot/local/docs/pydantic_models.md`.

These external changes are handled at `apply`-time (whether automatically or manually triggered, see above).compares the files with a current render of its sources.

If the live file differs from fresh render, the fresh render is written to `<live>.proposed`; a `.divergent` marker is set and a desktop notification fires. The live file is **not** erased.

To reconcile diverged lives and your template sources, run:

```sh
repo-overlay promote <key> 
```

This is an interactive command. You can diff, edit the affected sources (literal,
template or partial), accept the agent-proposed, or keep current.

### 4.3 Cross-referencing files

> [!WARNING]
> This section is unnecessarily verbose, trim

A Markdown link in an overlay file is read in (up to) three places: the source repo, the target repo or on the web, in Codeberg. The link is resolved differently:

- source `<overlay>/<key>/…`: human author in their editor
- target `<target>/…` — agent reading the live symlink
- Codeberg `https://codeberg.org/<user>/<overlay>/src/branch/main/<key>/…`: collaborator on web

repo-overlays mirrors `<overlay>/<key>/<rel>` to `<target>/<rel>` (modulo `dot_X` → `.X`). The *prefix* differs by context but the *suffix inside the key* is identical, so most relative links resolve to the right file in all three views with no extra effort. There is no `{{base_path}}` template variable — the value would have to differ per destination, and a template is rendered only once, shared by every destination of the key. Data-driven values are key-scoped (`data.toml`) for exactly that reason: the key is the shared scope.


#### Pattern 1 — same-key references (just works)

From `<overlay>/beadpot/CLAUDE.md.mo` linking to `<overlay>/beadpot/local/docs/architecture.md`:

```markdown
See [architecture](local/docs/architecture.md).
```

Resolves to `<overlay>/beadpot/local/docs/architecture.md` in source, `<target>/local/docs/architecture.md` in target, and stays within the source repo on Codeberg. The same pattern works for any depth, as long as both endpoints live under the same key.

#### Pattern 2 — shared snippets are inlined, not linked

For content reused across keys, use partial inclusion rather than a link:

```markdown
{{>_shared/python-style.md}}
```

The reader of the rendered file sees the content directly — no link to break. If a navigational "see also" link to a partial is useful for the human author, write `[python style](../_shared/python-style.md)`: it works in source and Codeberg but is broken in target (partials are not materialised as separate files).

#### Pattern 3 — links to target-only files

To link from an overlay file to a file that lives only in the target (e.g. project source code outside the overlay):

```markdown
See [the page module](common/src/app/common/types/page.cljc).
```

This resolves in the target. It is broken in source and Codeberg because the file is not in the overlay repo. That is acceptable: only the agent navigates these links at runtime.

#### Pattern 4 — cross-source references (Codeberg URLs)

To link from one overlay source to a file in a *different* overlay source, use a full URL:

```markdown
[python style](https://codeberg.org/alvorithm/beadpot-docs/src/branch/main/_shared/python-style.md)
```

These open the browser in every context. They require network and bind the link to a particular branch — use them sparingly.

#### Templates and Codeberg rendering

Codeberg's source view renders `.md` files as Markdown (with working relative links) but shows `.mo` templates as plain text. If a file needs both partial composition *and* Codeberg-friendly rendering, keep it as plain `.md` and use a separate orchestrating `.mo` template that includes it via `{{>…}}`. The `.md` reads cleanly on Codeberg; the `.mo` produces the final composed output in the target.

## 5. Commands

Update dependencies via `uv sync`

Make `repo-overlay` available in PATH, e.g. by symlinking it from `~/.local/bin`:
```sh
ln -sf $PWD/.venv/bin/repo-overlay ~/.local/bin/`
```

These commands are available

```
repo-overlay init [<path>]       # bootstrap a repo's key from _template/ (dry-run; --write)
repo-overlay apply [<path>]      # materialise; default = apply everything
repo-overlay promote <key>       # reconcile drift interactively
repo-overlay render <src> <dst>  # (internal) render one template
repo-overlay watch [--once]      # inotify daemon; --once runs apply_all and exits
repo-overlay list                # list every live symlink ($HOME-relative), one per line
repo-overlay config              # print effective sources, targets, watched_roots
repo-overlay status [<path>]     # reports drifts / broken links / missing partials / stale renders / invalid JSON
```

`status` without an argument sweeps every destination (~0.2 s here). With a
path it checks only the destination containing it and skips the template
partial sweep — a manifest read plus one stat per link, tens of milliseconds,
and silent when clean. That form is meant for a directory-enter hook, next to
`repo-overlay apply`, the way `chezmoi status` is used.

### `init` — bootstrap a new repo's overlay

A repo cloned under a watched root gets no overlay coverage until a key
directory exists for it, and nothing in the flow reminds you — so it is
forgotten essentially every time. `init` instantiates each source's
`_template/` skeleton into `<source>/<key>/` for the resolved destination, then
applies it.

```sh
repo-overlay init                # dry-run for the cwd's repo: lists what it would create
repo-overlay init <path> --write # create the missing files, then apply the destination
repo-overlay init --source memory-bus --slug my-repo --write
```

- **`_template/`** is a reserved source directory (like `_shared`/`_rendered`,
  never an overlay key). Its layout mirrors `<source>/<key>/`, using the same
  `dot_` names. Each source owns its own skeleton — `memory-bus` the memory
  wiring, `defaults` the guidance — so `init` walks the stack and writes each
  source's template into that source's tree only.
- **Variables**, substituted in file paths and bodies: `{{key}}` (resolved
  overlay key), `{{slug}}` (`--slug`, else kebab-cased basename), `{{dest}}`
  (absolute destination), `{{remote}}` (origin `owner_repo`, else empty). Only
  these four are touched; `{{>_shared/…}}` partials and any other `{{…}}` are
  left for `apply` to render — a `.mo` in the skeleton stays a live template.
- **Dry-run by default**; `--write` creates only absent files (never clobbers a
  hand-edited one) and ends by applying the destination. Exit codes follow
  `status`: 0 = nothing to do, 1 = files missing/created, ≥2 = error. Re-running
  after adding a template file therefore backfills exactly that file.
- The key is resolved exactly as `apply` resolves it (`resolve_key_dest`), so a
  linked worktree bootstraps its *repo's* key once, not one per worktree.

**Discovery.** `repo-overlay status --unmanaged` additionally lists git repos
under the watched roots that no key covers, each with the `init` command to fix
it. It is **off by default on purpose**: the scheduled drift digest consumes
`status` and treats any line as an issue, so surfacing every bare repo
unconditionally would turn the daily signal into a permanent nag. Run it
on demand; the digest does not pass the flag.

### Scheduled drift digest

The watcher notifies about drift *when an apply hits it*. Issues that no apply
touches — a broken link after a source move, a `.mo` whose partial went away,
a `diverged:` marker left unresolved for weeks — surface only if something
runs `status`. On this machine a systemd user timer does that daily:

| Piece | Path |
|---|---|
| script | `~/.local/bin/config-drift-notify` (chezmoi source: `dot_local/bin/executable_config-drift-notify`) |
| units | `config-drift.{service,timer}` under `~/.config/systemd/user/` |
| schedule | 18:30 daily, `Persistent=true`, 15 min jitter |

It runs `chezmoi status` and `repo-overlay status`, and sends one
`notify-send` listing at most 8 paths per section; both clean ⇒ no
notification. Everything lives in the dotfiles repo (`~/.local/share/chezmoi`,
README § Config-drift notifier), not here — `repo-overlay status` is the
stable interface it depends on: **line-per-issue on stdout, exit 1 when any
issue was found, `All overlays clean.` and exit 0 otherwise.** The digest
filters that sentinel line out by exact match.

Deliberately not in shell startup: agent harnesses (pi, omp, Claude Code)
capture shell stderr into their tool results, so a per-shell reminder banner
pollutes every agent transcript.

### The event log

Two things happen once and matter later, so both are appended to
`$XDG_STATE_HOME/repo-overlays/events.log` (tab-separated, append-only):

```
2026-07-21T14:18:28+02:00	drift	/home/alvar/Code/beadpot/work/notes/guide.md
2026-07-21T18:08:23+02:00	rename	/home/alvar/Overlays/ai-overlay	/home/alvar/Overlays/defaults
```

- **`drift`** — every drift notification. A desktop popup vanishes, and the
  apply that raised it may have run in a terminal nobody was watching; "what
  was that notification?" has to stay answerable.
- **`rename`** — every *directory* move the watcher observes. inotify reports a
  rename as `IN_MOVED_FROM` + `IN_MOVED_TO` sharing a cookie, so the pair is
  exact rather than inferred. File moves are ignored (editors rename
  constantly); a directory moved out of the watched set is ignored too, having
  no destination to record.

The log has no reader of its own, deliberately. A directory move invalidates
stored paths well outside this tool — documentation, notes, a memory system's
records — and each of those consumers knows its own substrate. Ours records the
fact; they decide what it means. The memory system, for instance, consumes
`rename` lines with `curate.py paths --from-log`.

Scope: sources are watched recursively (depth 3) and their parent directory
non-recursively, so renaming a source directory itself is caught; `watched_roots`
are watched at their top level, so renaming a repo inside one is caught, but
moves deeper inside a repo are not. A move across filesystems arrives as an
unpaired delete + create and is not a rename at all — inotify gives no cookie.

On a successful apply you'll see output like:
```
Overlay beadpot (defaults, beadpot-docs) → ~/Code/beadpot
Overlay _claude (defaults) → ~/.config/claude
```

* Source names in parentheses show which repos contribute to the key.
* Paths abbreviate `$HOME` as `~` for readability.
* Missing partials are reported but don't block other overlay keys.

## Debugging

### What files does repo-overlays manage?

```sh
repo-overlay list
```

Outputs one `$HOME`-relative path per line for every symlink that an overlay has
placed.  Useful for feeding into `.chezmoiignore` so that dotfile management does
not collide with overlay-managed files:

```sh
repo-overlay list >> ~/.local/share/chezmoi/.chezmoiignore
```

### Re-applying an overlay with visible output

The systemd watcher keeps overlays up to date as source files change.  When
you later `cd` into a watched repo the mise hook finds nothing to do and stays
silent (see `_already_applied()`).  To force a re-apply and see the full
message:

```sh
rm ~/Code/beadpot/.repo-overlays.toml
cd ~/Code/beadpot
```

The next `cd` triggers the mise hook, which re-materialises the overlay
and prints the status line.

### Missing partials

If a template references a `{{>_shared/…}}` that doesn't exist in any source,
`apply` reports the error but continues with other overlay keys.  Check with:

```sh
repo-overlay status
```

### After changing repo-overlay code

A one-shot `repo-overlay apply` (or any manual CLI invocation) always runs the
current code. The long-lived watcher does not: after updating repo-overlay
itself, restart the daemon so it picks up the new code:

```sh
systemctl --user restart repo-overlay.service
```

## 6. Managing multiple overlay sources

A target repo may receive overlays with different audiences and lifecycles:

- Personal global guidance and personal-project overlays should stay on your machine (or
  a private remote).
- Domain or open-source-project guidance (e.g. `beadpot/`, `penpot/` and the partials
  they share) benefit from being public, versioned, and editable by collaborators —
  including via a Git-backed wiki UI.

`repo-overlays` composes any number of sources into one destination set.

### 6.1 Declaring stacked sources for a target

A single, user-level config (i.e. living in XDG-compliant `~/.config`) declares how sources stack. Sources declared later override earlier ones.

```toml
# ~/.config/repo-overlays/config.toml
[[sources]]
name = "personal"
path = "~/Code/repo-overlays"
private = true                       # never referenced from public output

[[sources]]
name = "beadpot-docs"
path = "~/Overlays/beadpot-docs"    # local clone of a Codeberg wiki repo
remote = "git@codeberg.org:<user>/beadpot-docs.git"
```

Each source's own `config.toml` still declares its `[targets]` and `watched_roots`; the
top-level file only enumerates and orders sources.

### 6.2 Composition rules

- **Partials** (`_shared/<name>.md`): looked up in stack order; the first source that
  defines a partial wins. A template can request a specific source's partial with
  `{{>@beadpot-docs/_shared/<name>.md}}`.
- **Keys**: if two sources define the same key (`beadpot/`), files are merged by
  relative path; later sources override earlier on conflict. A warning is printed for
  every override.
- **Fixed targets**: if two sources declare the same `[targets]` key with different
  paths, apply refuses and exits non-zero. (Same key → same path, always.)
- **Privacy boundary**: a source flagged `private = true` may *use* partials from
  non-private sources, but its own partials and templates are never referenced from a
  non-private source's rendered output. Violations are flagged by `repo-overlay status`.

### 6.3 Editing a public source via the web

Just share on Codeberg as a regular git repo. While it would be useful to leverage wiki-specific functionality (e.g. `Home.md` and `_Sidebar.md` for navigation), wikis in Codeberg are flat: all `.md` files live on the repo root, which makes e.g. `_shared` invisible for editing and viewing. 

Use a `README.md` file to:
- explain what the project is, how to contribute,
- list main entry points for human readers

Like any other files at the overlay root, README is *not* materialized anywhere as agent
guidance.

The file tree widget and search allow to find any content, but it is good practice to cross-reference related files wiki-style. See §4.3 for link patterns that work simultaneously in the source repo, the target worktree, and the Codeberg source view.

### 6.4 Contributor workflows

A collaborator who only needs to edit the public docs may just edit pages directly in the Codeberg UI. Alternatively, they can edit via command line: 
```sh
git clone git@codeberg.org:you/beadpot-docs.git
$EDITOR beadpot/CLAUDE.md.mo _shared/penpot-data-model.md
git commit -am "..."
git push
```

They never see other sources (e.g. general agent user preferences).

Your local workflow against the same public source:

```sh
cd ~/Code/beadpot-docs
git pull                          # fetch contributor changes
repo-overlay apply                # or the watcher picks up these automatically
```

### 6.5 Adding / removing sources

Clone or symlink a new source, then register it:

```sh
$EDITOR ~/.config/repo-overlays/config.toml
repo-overlay apply
```

Removing a source from `config.toml` and running `apply` removes all symlinks that
source owned (tracked via the per-destination applied-links manifest, §7).

## 7. Worktree-specific overlays

`git worktree` checkouts are discovered automatically: the tool finds both regular
`.git` directories and linked-worktree `.git` files.

### Key resolution

The resolver tries the **git remote slug** first, then the **toplevel directory
basename**. This gives three scenarios:

| Scenario | Slug matches a key? | Basename matches a key? | Result |
|---|---|---|---|
| Main checkout | `penpot_beadpot` → no | `beadpot` → yes | Uses `beadpot` key |
| Worktree with custom key | `penpot_penpot` → no | `penpot-feature` → yes | Uses `penpot-feature` key |
| Worktree, no matching key | `penpot_beadpot` → no | `scoping` → no | No overlay materialised |

### Creating a worktree-specific overlay

```sh
git worktree add ~/Code/penpot-feature my-branch
```

Add a worktree-specific overlay key named after the worktree directory (e.g.
`penpot-feature`) in your private source, with a template that overrides specific
sections. The basename fallback picks it up.

### Worktrees without their own overlay

When no key matches, the worktree gets no materialised symlinks. Read docs from the
main checkout's already-materialised locations, and edit at the overlay source.
Per-feature folders inside a shared overlay key (e.g. `work/wf-now/feature-scoping/`)
cover branch-specific docs without per-worktree overlays.

### Excluding one destination

An empty `.repo-overlays-skip` file at a destination's root opts it out. On the
next apply, anything already installed there is withdrawn — recorded links
removed, manifest deleted, `info/exclude` block dropped — and the destination is
skipped from then on. The decision lives next to the checkout it applies to,
which is what a worktree needs; `ignore_keys` / `.overlay-ignore` are the
source-side counterpart and exclude a *key*, not a destination.

### Git-dir destinations (`dot_git/…`)

An overlay key may carry files destined for the repository's git dir — in practice
git hooks: `<key>/dot_git/hooks/post-merge` → `<repo>/.git/hooks/post-merge`.

These paths are **not** joined onto the destination; they are resolved with
`git rev-parse --git-path`, because git splits the git dir in a linked worktree:

| Path | Where it lives in a worktree |
|---|---|
| `hooks/`, `info/`, `config` | shared **common dir** (main checkout's `.git/`) |
| `HEAD`, `index` | per-worktree gitdir (`<main>/.git/worktrees/<name>/`) |

Consequences, all intentional:

- a hook applied from a worktree installs into the main checkout's `.git/hooks`,
  which is the only place git looks for it — one hook per repository, not per worktree;
- the manifest records such a link by absolute path, since it lies outside the
  destination tree;
- `.git/info/exclude` is shared too, so every destination writes its own
  `[<dest_root>]`-labelled block into it (a worktree-local `info/exclude` is ignored
  by git);
- applying into a directory that is not a git repo skips the `dot_git/` files with a
  message and materialises everything else.

## 8. Overriding or supplementing target-repo bundled files
A project like Penpot ships its own `AGENTS.md` (or `CLAUDE.md`) in the repository root.
When you have a worktree (`git worktree add …`) you may want to:

1. **Supplement**: add personal/team context on top of the repo's instructions.
2. **Cancel a section**: remove a directive the repo ships that conflicts with your workflow.

`repo-overlays` does not parse the repo's bundled instructions — it only manages the
files it installs. The strategies below work at the agent-reading level.

### 8.1 Naive approach: replace the repo file with a symlink

If you *replace* `AGENTS.md` with a symlink to your overlay-rendered file, the repo's
original instructions are completely replaced. Use `.gitignore` to keep the symlink out
of commits:

```
# .gitignore (local, via .git/info/exclude — never commit this)
AGENTS.md
```

This approach is invasive and breaks if someone git-resets or checks out a new branch
that restores the original file.

### 8.2 Recommended approach: supplement via a second file

> [!WARNING]
> Claude-based example. Adapt the instructions below to your agent harness

Claude Code reads `CLAUDE.md` at the project root AND any `CLAUDE.md` files found in
parent directories up to `~/.config/claude/CLAUDE.md`. It also reads
`.claude/commands/*.md` as slash-command definitions.

Use the overlay to add extra files the agent reads alongside the repo's own:

```
~/Code/penpot/
├── AGENTS.md          ← repo-owned, not touched by repo-overlays
├── CLAUDE.md          ← installed by repo-overlays (your overlay)
└── .claude/
    └── commands/      ← additional slash commands from overlay
```

The agent sees both `AGENTS.md` and `CLAUDE.md`; your overlay content takes effect
without touching the repo's file.

#### To cancel specific directives in `AGENTS.md`
Place an override note in the supplement, `~/Code/penpot/CLAUDE.md`:

```markdown
## Local overrides

The repo's `AGENTS.md` asks you to <X>. For this worktree, ignore that: do <Y> instead.
Reason: [explain].
```

Agents treat later instructions as higher-priority when there is ambiguity.
> [!WARNING]
> Consult the context-loading sequence for your harness to understand where the override will land.


## 9. Destination hygiene

### 9.1 Manifest file

For every materialised destination, repo-overlays writes a manifest:

```
<dest_root>/.repo-overlays.toml
```

It records which links were installed and which source owns them, so
`apply` can prune stale links cleanly and `status` can detect external
tampering.

The manifest is per-machine (it records absolute paths to your overlay
sources) and must never be committed.  Add it to your **global git
exclude** so every repo is covered:

```sh
# ~/.config/git/gitignore-global  (or wherever core.excludesFile points)
.repo-overlays.toml
```

### 9.2 Live overlay paths

`apply` automatically writes every overlay symlink it installs into the
target repo's ``.git/info/exclude``, inside a marked section:

```
# ── repo-overlays (auto-managed, do not edit between markers) ──
/AGENTS.md
/CLAUDE.md
/.claude/settings.json
# ── end repo-overlays ──
```

This is:

- **Idempotent**: repeated `apply` runs replace the block, never duplicate entries.
- **Per-repo**: each destination's ``.git/info/exclude`` only lists its own paths.
- **Non-invasive**: ``.git/info/exclude`` is local to the clone, never committed.

No manual `.gitignore` edits are needed for overlay-managed files.

#### Whole directories: `.overlay-own`

Per-file entries leak when an overlay key owns a *tree*. A file materialised —
or written straight into the tree by an agent — is visible to git until its
exclude line exists, and a single `git add -A` in that window tracks it
**forever**: `info/exclude` only suppresses *untracked* paths.

Drop an empty `.overlay-own` file in a source directory to declare that tree
wholly overlay-owned:

```
~/Overlays/beadpot-docs/beadpot/work/.overlay-own
```

The destination then gets one directory entry instead of one line per file:

```
/work/                     ← replaces 44 per-file entries
/AGENTS.md
/src/beadpot/graph/AGENTS.md
```

- Git does not descend into the directory at all, so *anything* appearing
  there is silent — overlay-managed or not. That is the point: the tree is
  scratch space for agent working notes, not repo content.
- To track one file from such a tree anyway, `git add -f <path>`.
- The marker is a declaration, never materialised, and never inferred from the
  destination's contents — otherwise the granularity would flip back to
  per-file the moment an agent dropped a file in, which is exactly when the
  coarse form is needed.
- A directory git **already** tracks something under is left per-file, with a
  warning: blanket-excluding it would hide the tracked file's untracked
  neighbours while the tracked file stays tracked regardless.
- A marker at the key root is refused — it would exclude the whole project.

`repo-overlay status` reports the trap directly, per destination:

```
tracked: ~/Code/beadpot/work/…/REPORT.md — git -C ~/Code/beadpot rm --cached work/…/REPORT.md
```

Nothing else surfaces this: a staged overlay symlink looks like any other
staged addition in `git status`, and it carries an absolute path into your home
directory that no other clone can resolve.

### 9.3 Setting up a new overlayed repo

1. Add the overlay key directory in your overlay source (e.g.
   `~/Code/my-overlay/new-repo/CLAUDE.md`).
2. Ensure the repo is under a `watched_roots` path in your source's
   `config.toml`.
3. Run `repo-overlay apply`.  The manifest, symlinks, and
   `.git/info/exclude` entries are all created automatically.

If the repo is not a git repository (e.g. `~/Ask`), the exclude step is
skipped — there is no `.git` to exclude from.

### 9.4 Harness state that must never be versioned

Agent-harness runtime state, telemetry, session logs, and credential stores
live outside the overlay system, but they share the same rule as the manifest:
exclude them **globally**, never rely on a per-repo `.gitignore` (which is
fragile and drifts between repos). Keep these unversioned and out of dotfile
management:

```
~/.claude.json            # runtime config / MCP state
~/.claude/projects/       # session state
~/.claude/todos/          # session state
~/.claude/history.json    # session history
~/.claude/statsig/        # telemetry
~/.codex/auth.json        # credential store
~/.codex/threads/         # session state
~/.codex/history.json     # session history
```

A global git exclude (§9.1) plus the auto-managed per-repo `.git/info/exclude`
(§9.2) covers overlay artifacts consistently; extend the global exclude with the
paths above so harness state is never accidentally committed.
