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
- **Multi-source composition**: stack several overlay repos (private +
  public/Codeberg-wiki) so collaborators can contribute to one without seeing the
  others.

## 2. Concepts
- **Overlay source**: A directory (typically a Git repo) holding overlays identified by keys. You can have several souce repos; they are stacked in declared order.
- **Overlay key**: A top-level directory in a source. Two kinds: 
    - *Fixed target*: Key starts with `_`. Bound to an absolute destination in the source's `config.toml`. Layout mirrors the destination 1:1.
    - *Project overlay*: Key does not start with `_`. Bound to a worktree by git remote slug (`owner_repo`), falling back to the directory basename if the slug doesn't match any source key. Uses `dot_X` → `.X` rewrite at materialisation.
- **Partial**: `_shared/<name>.md` in any source. Referenced from templates as `{{>_shared/<name>.md}}`.
- **Template**: Any file in a source ending in `.mo`. Rendered to `_rendered/<key>/<path>` (extension stripped). Non-`.mo` files are symlinked verbatim. 
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
- an Emacs `find-file-hook` / `project-switch-hook` 

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

repo-overlays mirrors `<overlay>/<key>/<rel>` to `<target>/<rel>` (modulo `dot_X` → `.X`). The *prefix* differs by context but the *suffix inside the key* is identical, so most relative links resolve to the right file in all three views with no extra effort. There is no `{{base_path}}` template variable — and no need for one — because the value would have to differ per context, but a template is rendered only once.


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
repo-overlay apply [<path>]      # materialise; default = apply everything
repo-overlay promote <key>       # reconcile drift interactively
repo-overlay render <src> <dst>  # (internal) render one template
repo-overlay watch [--once]      # inotify daemon; --once runs apply_all and exits
repo-overlay list                # list every live symlink ($HOME-relative), one per line
repo-overlay config              # print effective sources, targets, watched_roots
repo-overlay status              # reports drifts / broken links / missing partials
```

On a successful apply you'll see output like:
```
Overlay beadpot (personal, beadpot-docs) → ~/Code/beadpot
Overlay _claude (personal) → ~/.config/claude
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
path = "~/Code/beadpot-docs.wiki"    # local clone of a Codeberg wiki repo
remote = "git@codeberg.org:<user>/beadpot-docs.wiki.git"
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

### 9.3 Setting up a new overlayed repo

1. Add the overlay key directory in your overlay source (e.g.
   `~/Code/my-overlay/new-repo/CLAUDE.md`).
2. Ensure the repo is under a `watched_roots` path in your source's
   `config.toml`.
3. Run `repo-overlay apply`.  The manifest, symlinks, and
   `.git/info/exclude` entries are all created automatically.

If the repo is not a git repository (e.g. `~/Ask`), the exclude step is
skipped — there is no `.git` to exclude from.
