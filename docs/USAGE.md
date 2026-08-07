# repo-overlays: functionalities and usage

Compose personal/team-authored Markdown guidance for AI agents (and humans reading the
same files) into project worktrees, without committing it upstream.

## 1. Functionalities

- **Per-project AI guidance** (`CLAUDE.md`, `AGENTS.md`, `.claude/commands/*.md`,
  subagents, skills) materialised into any worktree, excluded from git at the
  destination through `.git/info/exclude`, never through `.gitignore`.
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
- **New-repo bootstrap**: `repo-overlay init` instantiates each source's
  `_template/` skeleton into `<source>/<key>/` for a destination no key covers
  yet and applies it, so setting a repo up costs one command; `repo-overlay
  status --unmanaged` lists the git repos under the watched roots that no key
  covers at all (§5 `init`).
- **Multi-source composition**: stack several overlay repos (a private one plus a
  public one) so collaborators can contribute to one without seeing the
  others.

## 2. Concepts
- **Overlay source**: A directory (typically a Git repo) holding overlays identified by keys. You can have several souce repos; they are stacked in declared order.
- **Overlay key**: A top-level directory in a source. Two kinds: 
    - *Fixed target*: Key starts with `_`. Bound to an absolute destination in the source's `config.toml`. Layout mirrors the destination 1:1.
    - *Project overlay*: Key does not start with `_`. Bound to a worktree by testing three candidates for membership in the set of key directories that exist across the sources, in order: git remote slug (`owner_repo`), the destination directory basename, then the bare remote repo name (`repo`). First match wins; the third is what makes a linked worktree resolve to its repo's key whatever its own directory is called. When none match, the key falls back to the slug (else the basename) and nothing materialises, since no source provides that key. Uses `dot_X` → `.X` rewrite at materialisation.
- **Ignored key**: A top-level directory declared as NOT an overlay key for its source — via `ignore_keys = ["dir", …]` in the source's `config.toml` or an `.overlay-ignore` file at the source root (one name per line, `#` comments, trailing `/` allowed). Lets a source repo carry non-overlay content (docs, staging dirs) without the name accidentally matching a repo under a watched root. Per-source: another source may still provide the same key.
- **Partial**: `_shared/<name>.md` in any source. Referenced from templates as `{{>_shared/<name>.md}}`.
- **Template**: Any file in a source ending in `.mo`. Rendered to `_rendered/<key>/<path>` (extension stripped). Non-`.mo` files are symlinked verbatim. A template whose destination ends in `.json` must render to parseable JSON; an unparseable render is refused (status: `invalid-json`).
- **Data**: `<key>/data.toml` in any source. A key carrying one is *data-active*: its `.mo` templates render with full Mustache (variables `{{name}}`, sections `{{#list}}…{{/list}}`, inverted `{{^x}}`, raw `{{{x}}}`) against the parsed TOML. A key without one keeps the legacy contract: only `{{>partial}}` resolves and every other `{{…}}` passes through verbatim. The file resolves like a partial — first source in stack order wins, whole file, never merged, privacy-checked — and is never materialised. List-of-table values get `first`/`last` booleans injected (reserved keys) so templates can join items with commas. 
- **Skeleton**: `_template/` in any source. A key skeleton rather than an overlay key: `repo-overlay init` instantiates it into `<source>/<key>/` for a destination, substituting `{{key}}`, `{{slug}}`, `{{dest}}` and `{{remote}}` in paths and bodies and leaving every other tag for `apply`. Per source, and instantiated for *every* key `init` touches, so it holds only what that source would own for any repo (§5).
- **Live file**: The symlink at the destination that the agent reads/writes.
- **Drifted file**: A live file no longer matches a fresh render of its source. 

## 3. Overlay source layout

These are the files you may find in a source overlay repo:

```
<overlay>/
├── config.toml                   # how to resolve fixed & project (see below)
├── README.md                     # (optional) entry point for human readers
├── _shared/                      # (optional) re-usable snippets for templates
│   └── python-style.md           
├── _rendered/                    # (optional) rendered .mo templates; in the source's own .gitignore
├── _template/                    # (optional) skeleton `repo-overlay init` copies into <key>/
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
    - (except for `_shared`, `_rendered` and `_template`, which are reserved for internal use)
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

Every `apply`, automatic or manual (see above), compares these external changes with a
current render of their sources.

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

A Markdown link in an overlay file is read in (up to) three places: the source repo, the target repo, or the source repo's web view on whatever forge hosts it. The link is resolved differently:

- source `<overlay>/<key>/…`: human author in their editor
- target `<target>/…` — agent reading the live symlink
- web `<forge>/<user>/<overlay>/…/<key>/…`: collaborator on web

repo-overlays mirrors `<overlay>/<key>/<rel>` to `<target>/<rel>` (modulo `dot_X` → `.X`). The *prefix* differs by context but the *suffix inside the key* is identical, so most relative links resolve to the right file in all three views with no extra effort. There is no `{{base_path}}` template variable — the value would have to differ per destination, and a template is rendered only once, shared by every destination of the key. Data-driven values are key-scoped (`data.toml`) for exactly that reason: the key is the shared scope.


#### Pattern 1 — same-key references (just works)

From `<overlay>/beadpot/CLAUDE.md.mo` linking to `<overlay>/beadpot/local/docs/architecture.md`:

```markdown
See [architecture](local/docs/architecture.md).
```

Resolves to `<overlay>/beadpot/local/docs/architecture.md` in source, `<target>/local/docs/architecture.md` in target, and stays within the source repo on the web. The same pattern works for any depth, as long as both endpoints live under the same key.

#### Pattern 2 — shared snippets are inlined, not linked

For content reused across keys, use partial inclusion rather than a link:

```markdown
{{>_shared/python-style.md}}
```

The reader of the rendered file sees the content directly — no link to break. If a navigational "see also" link to a partial is useful for the human author, write `[python style](../_shared/python-style.md)`: it works in source and on the web but is broken in target (partials are not materialised as separate files).

#### Pattern 3 — links to target-only files

To link from an overlay file to a file that lives only in the target (e.g. project source code outside the overlay):

```markdown
See [the page module](common/src/app/common/types/page.cljc).
```

This resolves in the target. It is broken in source and on the web because the file is not in the overlay repo. That is acceptable: only the agent navigates these links at runtime.

#### Pattern 4 — cross-source references (full URLs)

To link from one overlay source to a file in a *different* overlay source, use a full URL to that source's web view:

```markdown
[python style](https://<forge>/<user>/beadpot-docs/.../_shared/python-style.md)
```

These open the browser in every context. They require network and bind the link to a particular branch (and to a particular forge's URL shape) — use them sparingly.

#### Templates and web rendering

A forge's source view renders `.md` files as Markdown, with working relative links, but shows `.mo` templates as plain text. If a file needs both partial composition *and* readable rendering on the web, keep it as plain `.md` and use a separate orchestrating `.mo` template that includes it via `{{>…}}`. The `.md` reads cleanly in the browser; the `.mo` produces the final composed output in the target.

## 5. Commands

Update dependencies via `uv sync`

Make `repo-overlay` available in PATH, e.g. by symlinking it from `~/.local/bin`:
```sh
ln -sf $PWD/.venv/bin/repo-overlay ~/.local/bin/`
```

These commands are available

```
repo-overlay init [<path>]       # bootstrap a repo's key from _template/ (dry-run; --write, --key)
repo-overlay apply [<path>]      # materialise; default = apply everything
repo-overlay promote <key>       # reconcile drift interactively
repo-overlay render <src> <dst>  # (internal) render one template
repo-overlay watch [--once]      # inotify daemon; --once runs apply_all and exits
repo-overlay list                # list every live symlink ($HOME-relative), one per line
repo-overlay config              # print effective sources, targets, watched_roots
repo-overlay status [<path>]     # reports drifts / broken links / missing partials / stale renders / invalid JSON
repo-overlay status --unmanaged  # ...and git repos under the watched roots that no key covers
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
repo-overlay init <path> --key beadpot --write   # force the key name
```

- **`_template/`** is a reserved source directory (like `_shared`/`_rendered`,
  never an overlay key). Its layout mirrors `<source>/<key>/`, using the same
  `dot_` names. Each source owns its own skeleton — `memory-bus` the memory
  wiring, `defaults` the guidance — so `init` walks the stack and writes each
  source's template into that source's tree only. A skeleton is instantiated
  for **every** key `init` touches, not only the keys that source already
  serves, so it must hold what that source would own for any repo; project
  text belongs in the key directory instead.
- **Variables**, substituted in file paths and bodies: `{{key}}` (resolved
  overlay key), `{{slug}}` (`--slug`, else the kebab-cased key, so a worktree or
  a differently named clone does not seed its directory name), `{{dest}}`
  (absolute destination), `{{remote}}` (origin `owner_repo`, else empty). Only
  these four are touched; `{{>_shared/…}}` partials and any other `{{…}}` are
  left for `apply` to render — a `.mo` in the skeleton stays a live template.
  Those four names are **reserved inside `_template/`**: `init` substitutes them
  before `apply` ever sees the file, so a data-active key (one carrying
  `data.toml`, § 2 *Concepts*) cannot receive them from the renderer.
- **Key naming.** An explicit `--key NAME` wins verbatim. A fixed-target
  destination (one that matched `[targets]`, e.g. the `_claude` key) keeps its
  key unchanged, never rewritten. Otherwise: if the resolved key already exists
  as a key directory in some source, it is used as is; if no source has it, the
  key is **new** and defaults to the bare remote repo name (`repo` out of
  `owner/repo`), falling back to the destination directory's basename when the
  repo has no remote. A run that will create a new key prints one extra line
  naming it, so the dry-run is the place to catch a wrong guess:
  `new key: NLP-beta (no source has this key yet; override with --key)`.
- **Why not the resolver's own fallback.** `resolve_key_dest` falls back to the
  owner-qualified slug (`owner_repo`) when nothing matches. That is right for
  *lookup* and wrong for *creation*: on a normal setup every existing key is a
  bare name, so writing the first `owner_repo`-named key directory is a silent,
  permanent naming divergence that only shows up later, as a key that no other
  clone of the repo resolves to. A worktree's directory basename is wrong for
  the same reason (`beadpot-userlibs` for a worktree of `beadpot`). Two repos
  that share a bare name are disambiguated with `--key`, once, at creation.
- **Lookup order**, unchanged, used to find an *existing* key: remote slug
  (`owner_repo`), then destination basename, then bare remote repo name, each
  tested for membership in the set of key directories that exist across the
  sources; when none of the three match, the slug (else the basename) is
  returned. `--slug` plays no part in this: it is the memory/project slug
  substituted into `{{slug}}`, unrelated to the key.
- **Dry-run by default**, and a dry-run never writes. `--write` creates only
  absent files and then *always* applies the destination, even when every
  template file already existed. So `init --write` is also the repair command
  for a key directory that was hand-made and never applied, and re-running
  after adding a template file backfills exactly that file. Three kinds of file
  are left alone: one that already exists in the key directory (`ok:`, so a
  hand-edited file is never clobbered), one whose destination path another
  source already provides for that key, and one the destination itself holds as
  a regular file, upstream's own `AGENTS.md` for instance (both `have:`). Two
  sources on one path is a whole-file override, and `apply` refuses to replace a
  real file with a link on every run, so in either case the skeleton's copy would
  shadow real content or become a permanent warning. Those two rules are what
  make shipping a new `_template/` file and sweeping it across existing keys safe.
- **Worktrees.** The destination is resolved exactly as `apply` resolves it
  (`resolve_key_dest`), so a linked worktree bootstraps its *repo's* key once,
  not one per worktree.

**Exit codes.** Dry-run keeps the `status` contract; `--write` is a mutating
command and behaves like `apply`.

| Mode | Code | Meaning | Typical cause |
|---|---|---|---|
| dry-run | 0 | nothing to create | the key is already fully bootstrapped |
| dry-run | 1 | work pending | at least one template file is missing |
| `--write` | 0 | success | files created, or none needed; the destination applied cleanly |
| either | 2 | error | destination unresolvable (not a git repo, no target match), or the trailing apply failed |

`--write` **never** exits 1, so `repo-overlay init X --write && <next step>`
chains work: a successful write is a success, exactly as with `apply`. Dry-run
exit 1 means "work pending", the same convention `status` uses, so a non-zero
dry-run is a to-do list rather than a failure.

**Discovery.** `repo-overlay status --unmanaged` additionally lists git repos
under the watched roots that no key covers, each with the `init` command to fix
it. It is **off by default on purpose**: the scheduled drift digest consumes
`status` and treats any line as an issue, so surfacing every bare repo
unconditionally would turn the daily signal into a permanent nag. Run it
on demand; the digest does not pass the flag.

**Fresh-clone recipe.** For a repo that no source covers yet:

1. Clone (or create) the overlay source repo that will hold the key, e.g.
   `~/Overlays/beadpot-docs`.
2. Register it by hand: append a `[[sources]]` block to
   `~/.config/repo-overlays/config.toml` (§ 6.1). There is no command for this.
3. Create `_template/` in that source, holding the skeleton every new key of
   that source should get.
4. Run `repo-overlay init <clone>` as a dry-run. Read the plan, check the
   `new key:` line for the name it picked, then re-run with `--write`.
5. Confirm: `repo-overlay status --unmanaged` no longer lists the clone.
6. Commit the new key directory in the overlay source.

What is deliberately not automated: registering the source (step 2), cloning
anything (the source in step 1, and the target repo itself), and pushing the
bootstrapped key directory back to its remote (step 6 commits locally; the push
is yours).

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
2026-07-21T14:18:28+02:00	drift	/home/alvar/Code/beadpot/docs.local/findings/guide.md
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

Scope: a source is watched **whole**, to any depth, plus its parent directory
non-recursively, so renaming a source directory itself is caught.
`watched_roots` are watched at their top level, so renaming a repo inside one
is caught, but moves deeper inside a repo are not. A move across filesystems
arrives as an unpaired delete + create and is not a rename at all — inotify
gives no cookie.

The asymmetry between the two is deliberate, and it is the whole bound on the
watch set. A source is small and hand-authored, and working notes are filed
deep inside a key (`<key>/wip.local/done/<yyyy-mm>-<set>/demo/`), so a depth cap there is a
silent hole: the edit fires no event and the file simply never materialises. A
`watched_root` is the opposite — large, machine-generated in places, and its
children are the *destinations*. A watched destination would make each apply's
own manifest write the trigger for the next apply, so the daemon would never
return to idle. `node_modules` and `.venv` are skipped outright.

A directory created after startup is watched as it appears, together with
every subdirectory it already contains. The catch-up matters because
`mkdir -p a/b/c`, `git clone` and `cp -r` all outrun inotify: the `CREATE` of
`a` is delivered after `b` and `c` exist, so their own events went to watches
that did not exist yet. Watching `a` alone would leave the tree permanently
half-seen.

On a successful apply you'll see output like:
```
Overlay beadpot (defaults, beadpot-docs) → ~/Code/beadpot
Overlay _claude (defaults) → ~/.config/claude
```

* Source names in parentheses show which repos contribute to the key.
* Paths abbreviate `$HOME` as `~` for readability.
* Missing partials are reported but don't block other overlay keys.
* A destination already materialised is skipped and prints nothing, so a
  sweep with no work to do is silent and writes nothing at all. `repo-overlay
  apply` over a current tree therefore produces no output, the same way
  re-entering an already-applied repo does.

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
  they share) benefit from being public, versioned, and editable by collaborators.

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
path = "~/Overlays/beadpot-docs"    # local clone of a public docs repo
remote = "git@<forge>:<user>/beadpot-docs.git"
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

Just share the source as a regular git repo on any forge. A source is an ordinary directory tree, so nothing here depends on a forge feature beyond browsing and editing files: `_shared/` and every key directory keep their nesting on the web exactly as on disk.

Use a `README.md` file to:
- explain what the project is, how to contribute,
- list main entry points for human readers

Like any other files at the overlay root, README is *not* materialized anywhere as agent
guidance.

The file tree and search find any content, but it is good practice to cross-reference related files. See §4.3 for link patterns that work simultaneously in the source repo, the target worktree, and the web view.

### 6.4 Contributor workflows

A collaborator who only needs to edit the public docs may just edit files directly in the forge's web editor. Alternatively, they can edit via command line: 
```sh
git clone git@<forge>:you/beadpot-docs.git
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

The resolver tests three candidates against the keys that exist across the
sources: the **git remote slug** (`owner_repo`), the **toplevel directory
basename**, then the **bare remote repo name** (`repo`). First match wins.

| Scenario | Slug | Basename | Bare name | Result |
|---|---|---|---|---|
| Main checkout | `penpot_beadpot` → no | `beadpot` → yes | not reached | `beadpot` key |
| Worktree with its own key | `alvorithm_penpot` → no | `penpot-feature` → yes | not reached | `penpot-feature` key |
| Worktree named for a branch | `alvorithm_beadpot` → no | `beadpot-userlibs` → no | `beadpot` → yes | `beadpot` key, same overlay as the main checkout |
| Nothing matches | no | no | no | Nothing materialised; `status --unmanaged` lists the repo, `init` bootstraps it |

### Creating a worktree-specific overlay

```sh
git worktree add ~/Code/penpot-feature my-branch
```

Add a worktree-specific overlay key named after the worktree directory (e.g.
`penpot-feature`) in your private source, with a template that overrides specific
sections. The basename fallback picks it up.

### Worktrees without their own overlay

A worktree needs no key of its own: the bare-remote-repo-name candidate resolves
it to its repo's key, so it materialises the same overlay as the main checkout.
Per-feature folders inside that shared key (e.g. `wip.local/<branch>/`) cover
branch-specific docs without per-worktree overlays, which is the normal
arrangement. Only a checkout whose *repo* has no key anywhere gets nothing; that
is what `repo-overlay init` (§5) is for.

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
original instructions are completely replaced. Keep the symlink out of commits with the
clone-local exclude file, not with `.gitignore`:

```
# .git/info/exclude (local to the clone, never committed)
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
~/Overlays/beadpot-docs/beadpot/docs.local/.overlay-own
```

The destination then gets one directory entry instead of one line per file:

```
/docs.local/                     ← replaces 44 per-file entries
/AGENTS.md
/src/beadpot/graph/AGENTS.md
```

Scope: `.overlay-own` marks a directory overlay-owned for the purposes of the
git exclude block **only**. It does not stop the source's files under that
directory from materialising, and it does not make the destination's own files
disappear. The source files under an own-marked directory are materialised
exactly as anywhere else; the one thing that changes is the exclude block,
which gets a single `/dir/` entry instead of one entry per file.

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
tracked: ~/Code/beadpot/docs.local/…/REPORT.md -> git -C ~/Code/beadpot rm --cached docs.local/…/REPORT.md
```

Nothing else surfaces this: a staged overlay symlink looks like any other
staged addition in `git status`, and it carries an absolute path into your home
directory that no other clone can resolve.

### 9.3 Setting up a new overlayed repo

One command, for any source that ships a `_template/` skeleton:

```sh
repo-overlay status --unmanaged        # which repos have no key at all
repo-overlay init ~/Code/new-repo      # dry run: what each skeleton would create
repo-overlay init ~/Code/new-repo --write
```

`--write` creates the key directory and applies it, so the manifest, symlinks and
`.git/info/exclude` entries all follow. See §5 for the exit codes and how the key
is named.

By hand, when no source has a skeleton for what this repo needs:

1. Add the overlay key directory in your overlay source (e.g.
   `~/Overlays/my-overlay/new-repo/AGENTS.md`).
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
