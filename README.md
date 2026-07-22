# repo-overlays

Compose personal/team-authored Markdown guidance for AI agents into project
worktrees, without committing it upstream.

- `USAGE.md` — feature specification and user workflow
- `doc_overlays.graphml` — full composition diagram (open with yEd)

![Example multi-source multi-target overlay set](docs/example_overlays.svg)


## What it does

You maintain one or more **overlay source repos** containing Markdown files
(style guides, agent instructions, skills, slash commands). repo-overlays
**materialises** these into your project directories as symlinks, so the
agent finds them without the project repo knowing they exist.

### Example
You have a private `defaults` with your coding-style guidance and a
public `beadpot-docs` with domain knowledge. That makes two sources. Running `repo-overlay apply` produces at the target `~/Code/beadpot/`
```bash
~/Code/beadpot/
├── CLAUDE.md         #← defaults/_rendered/beadpot/CLAUDE.md (from template)
└── .claude/
    └── skills/
        └── test-feature.md  #← beadpot-docs/beadpot/dot_claude/skills/test-feature.md
```

Neither file is committed to `~/Code/beadpot/`. The agent reads them as if
they were native to the project. 

Benefits:
1. your personal guidance (i.e. `defaults`) stays out of upstream, and private
2. project notes can be git-shared 

## Where overlays land: harnesses

**Separation of concerns.** [chezmoi](https://www.chezmoi.io/) manages files that *applications* read (settings, keybindings). repo-overlays manages files that *LLM agents* read (`CLAUDE.md`, `AGENTS.md`, skills, slash commands). The two never fight over the same file.

**Consuming harnesses.** Fixed-target keys deploy agent guidance into the config dirs of the harnesses in use — `pi`, `omp` (oh-my-pi), and Claude Code, with [Zed](https://zed.dev/) as an ACP front-end:

| Fixed key | Destination | Read by |
|-----------|-------------|---------|
| `_claude` | `~/.config/claude` | Claude Code, omp |
| `_pi`     | `~/.config/pi`     | pi |
| `_omp`    | `~/.config/omp`    | omp |

The watcher discovers project destinations under `~/Code` and `~/Ask`.

Notes on the harnesses (context for why the targets look the way they do):

- **Shared core** — pi and omp share the agent core and a JSONL session store; override the location with `--session-dir` (omp) or `PI_CODING_AGENT_SESSION_DIR` (pi).
- **Skill discovery** — Claude Code and omp auto-discover skills from `~/.config/claude/skills/`; pi requires an explicit `skills` entry in its `settings.json`.
- **Claude Code XDG** — Claude Code hard-codes `~/.claude`; a chezmoi-managed symlink `~/.claude → ~/.config/claude` keeps it XDG-compliant.

## Glossary

| Term | Meaning |
|------|---------|
| **source** | A directory (git repo) holding overlay files. You can stack several; they are consulted in declared order. |
| **source stack** | The ordered list of sources. Later sources override earlier ones on per-file conflicts; the first source that has a partial wins for partial lookup. |
| **overlay key** | A top-level directory in a source, matched to a destination. Keys starting with `_` are *fixed targets* (bound to an absolute path); others are *project overlays* matched to a git repo by, in order: remote slug (`owner_repo`), directory basename, then bare remote repo name (`repo`). The last step lets linked worktrees match by repo identity regardless of their directory name — see [Worktrees](#worktrees). |
| **partial** | A `_shared/<name>.md` file in any source. Included into templates with `{{>_shared/<name>.md}}`. |
| **template** | A `*.mo` file. Rendered via Mustache (partials resolved across the whole source stack) into `_rendered/<key>/<path>`. |
| **materialise** | The act of writing `_rendered/` output and placing a symlink at the destination. |
| **live file** | The symlink at the destination that the agent reads or writes. |
| **owned directory** | A source directory carrying an empty `.overlay-own` marker: wholly overlay-owned, so the destination excludes the *directory* (`/work/`) instead of each file under it. Keeps new files in the tree from ever being visible to git — see [USAGE.md §9.2](docs/USAGE.md). |
| **drift** | A live file whose content no longer matches a fresh render of its template — i.e. an agent has edited it since the last apply. |
| **reconcile** | The interactive step (`repo-overlay promote`) that resolves drift: diff, accept the new render, keep the agent's edit, or edit the source. |
| **watched_roots** | Parent directories whose git-repo children are auto-discovered as destinations and re-applied when any source changes. |

## Worktrees

A linked git worktree shares its `origin` with the main checkout, so overlays
resolve by the repo's **bare remote repo name** — not the worktree's directory
name. You can therefore name and place worktrees freely; all of these resolve to
the `penpot` overlay key:

```
~/Code/worktrees/penpot-feature-x       → key `penpot` (via origin)
~/Code/worktrees/penpot-bugfix-123      → key `penpot`
~/Code/penpot/.claude/worktrees/foo     → key `penpot` (a Claude Code worktree)
```

Recommended convention: a flat `~/Worktrees/<repo>-<branch>`, added to
`watched_roots`. Location is a convention, not a constraint: linked worktrees
are enumerated with `git worktree list`, so they are found wherever they are
checked out, as long as the **main** checkout sits under a watched_root.
Overlays also apply on `cd` / file-open via the hooks, from any path.

A worktree receives its repo's overlay like any other checkout. To exclude one,
drop an empty **`.repo-overlays-skip`** file at its root: the next apply removes
whatever it had installed there (links, manifest, `info/exclude` block) and
leaves it alone from then on. Per-destination, so it suits a short-lived
worktree better than a config entry would.

Tools create worktrees in their own places unless told otherwise — Claude Code
in `<repo>/.claude/worktrees/`, omp in `~/.omp/wt`. Steering them to one tree is
per-tool: omp has a `worktree.base` setting (`OMP_WORKTREE_DIR` overrides);
Claude Code has no base-directory setting and needs a `WorktreeCreate` hook,
which replaces its git logic and returns the directory to use.

## Installation

**Prerequisites** — Python ≥ 3.11 and [uv](https://docs.astral.sh/uv/).

**Install the CLI:**

```sh
cd ~/Code/repo-overlays
uv sync
ln -sf "$PWD/.venv/bin/repo-overlay" ~/.local/bin/repo-overlay
```

### File watcher service

Save the following as `~/.config/systemd/user/repo-overlay.service`:

```ini
[Unit]
Description=Materialise repo overlays on filesystem changes

[Service]
Type=simple
ExecStart=%h/Code/repo-overlays/.venv/bin/repo-overlay watch
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
```

Then enable and start it:

```sh
systemctl --user daemon-reload
systemctl --user enable --now repo-overlay.service
systemctl --user status repo-overlay.service   # verify running
```

### Optional: mise cd hook

Fires `repo-overlay apply` whenever you `cd` into any directory. For directories under a `watched_root`, the overlay materialises immediately on entry.

Add to `~/.config/mise/config.toml`:

```toml
[settings]
experimental = true   # required for hooks

[hooks]
enter = 'repo-overlay apply "$PWD" || true'
```

The `|| true` absorbs the exit 1 that occurs when the directory has no matching overlay key, preventing shell prompt disruption.

On entry, you'll see a brief output line:
```
Overlay beadpot (defaults, beadpot-docs) → ~/Code/beadpot
```
Re-entering an already-applied repo (or cd'ing into one of its subdirectories)
is silent — the overlay is already in place once materialised.

### Optional: Emacs integration

Fires `repo-overlay apply` for the current project whenever you open a file or switch projects. Runs asynchronously so it never blocks Emacs.

```elisp
(defun my/repo-overlay-apply ()
  "Materialise repo-overlays for the current project root."
  (when-let* ((root (or (and (fboundp 'project-current)
                             (project-current)
                             (project-root (project-current)))
                        (locate-dominating-file
                         (or buffer-file-name default-directory) ".git"))))
    (call-process-shell-command
     (concat "repo-overlay apply "
             (shell-quote-argument (expand-file-name root))
             " &")
     nil 0)))

(add-hook 'find-file-hook      #'my/repo-overlay-apply)
(add-hook 'project-switch-hook #'my/repo-overlay-apply)
```

Add this to your `init.el` or `early-init.el`. Requires `repo-overlay` on `$PATH` (i.e. `~/.local/bin/` in `exec-path`).


## Configuration

- Top-level source stack: `~/.config/repo-overlays/config.toml`
- Per-source targets and watched_roots: `<source>/config.toml`

## Quick start

```sh
repo-overlay config     # show effective sources and targets
repo-overlay apply      # materialise all overlays now
repo-overlay status     # check for drift, broken links, missing partials
repo-overlay promote    # interactive: reconcile agent-edited files
```

## Overlay repos in use

| Repo | Path | Visibility | Purpose |
|------|------|-----------|---------|
| defaults | `~/Overlays/defaults` | private | Personal voice/style/language partials; orchestrating templates |
| penpot-docs | `~/Overlays/penpot-docs` | public | Penpot data model and implementation |
| beadpot-docs | `~/Overlays/beadpot-docs` | public | beadpot model schemas, graph ingestion pipeline, skills |
