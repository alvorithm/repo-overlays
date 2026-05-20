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
You have a private `ai-overlay` with your coding-style guidance and a
public `beadpot-docs.wiki` with domain knowledge. That makes two sources. Running `repo-overlay apply` produces at the target `~/Code/beadpot/`
```bash
~/Code/beadpot/
├── CLAUDE.md         #← ai-overlay/_rendered/beadpot/CLAUDE.md (from template)
└── .claude/
    └── skills/
        └── test-feature.md  #← beadpot-docs.wiki/beadpot/dot_claude/skills/test-feature.md
```

Neither file is committed to `~/Code/beadpot/`. The agent reads them as if
they were native to the project. 

Benefits:
1. your personal guidance (i.e. `ai-overlay`) stays out of upstream, and private
2. project notes can be git-shared 

## Glossary

| Term | Meaning |
|------|---------|
| **source** | A directory (git repo) holding overlay files. You can stack several; they are consulted in declared order. |
| **source stack** | The ordered list of sources. Later sources override earlier ones on per-file conflicts; the first source that has a partial wins for partial lookup. |
| **overlay key** | A top-level directory in a source, matched to a destination. Keys starting with `_` are *fixed targets* (bound to an absolute path); others are *project overlays* (matched to a git repo by remote slug or basename). |
| **partial** | A `_shared/<name>.md` file in any source. Included into templates with `{{>_shared/<name>.md}}`. |
| **template** | A `*.mo` file. Rendered via Mustache (partials resolved across the whole source stack) into `_rendered/<key>/<path>`. |
| **materialise** | The act of writing `_rendered/` output and placing a symlink at the destination. |
| **live file** | The symlink at the destination that the agent reads or writes. |
| **drift** | A live file whose content no longer matches a fresh render of its template — i.e. an agent has edited it since the last apply. |
| **reconcile** | The interactive step (`repo-overlay promote`) that resolves drift: diff, accept the new render, keep the agent's edit, or edit the source. |
| **watched_roots** | Parent directories whose git-repo children are auto-discovered as destinations and re-applied when any source changes. |

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
| ai-overlay | `~/Code/ai-overlay` | private | Personal voice/style/language partials; orchestrating templates |
| penpot-docs.wiki | `~/Code/penpot-docs.wiki` | public | Penpot data model and implementation |
| beadpot-docs.wiki | `~/Code/beadpot-docs.wiki` | public | beadpot model schemas, graph ingestion pipeline, skills |
