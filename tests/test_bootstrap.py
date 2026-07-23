"""`repo-overlay init` — bootstrap a repo's overlay key from _template/.

Covers DESIGN.md §5 test plan: _template/ is reserved and never materialises;
the four init variables substitute in bodies and paths while partials are left
for apply; idempotent no-clobber creation with backfill; multi-source and
--source restriction; worktree resolves to the repo's key; unresolvable target
errors; and `status --unmanaged` surfaces bare repos without touching the
default output.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from repo_overlays.apply import apply_one
from repo_overlays.bootstrap import bootstrap, unmanaged_destinations
from repo_overlays.config import AppConfig, SourceConfig
from repo_overlays.manifest import read as read_manifest
from repo_overlays.sources import SourceStack
from tests.conftest import make_source, make_top_config


# ── helpers ────────────────────────────────────────────────────────────────


def _git_init(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)


def _git(path: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    }
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True, env=env
    ).stdout


def _config(*sources: SourceConfig) -> AppConfig:
    return AppConfig(sources=list(sources))


def _template(src: Path, rel: str, body: str) -> Path:
    p = src / "_template" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


# ── _template/ is reserved ─────────────────────────────────────────────────


def test_template_dir_is_never_a_key_or_materialised(tmp: Path) -> None:
    src = make_source(tmp, "personal")
    _template(src, "AGENTS.md", "skeleton for {{key}}\n")
    (src / "myproject").mkdir()
    (src / "myproject" / "CLAUDE.md").write_text("real guidance\n")

    project = tmp / "myproject"
    _git_init(project)
    config = _config(SourceConfig(name="personal", path=src, private=True))

    assert "_template" not in set(SourceStack(config).iter_keys())

    apply_one(project, config)
    # The real key materialised; nothing from _template/ leaked in.
    assert (project / "CLAUDE.md").is_symlink()
    assert not (project / "AGENTS.md").exists()
    assert all("_template" not in lr.target for lr in read_manifest(project).links)


# ── variable substitution ──────────────────────────────────────────────────


def test_init_substitutes_the_four_vars_in_body_and_path(tmp: Path) -> None:
    src = make_source(tmp, "personal")
    _template(
        src,
        "dot_claude/{{slug}}.md",
        "key={{key}} slug={{slug}} dest={{dest}} remote={{remote}}\n",
    )
    project = tmp / "myproject"
    _git_init(project)
    _git(project, "remote", "add", "origin", "git@github.com:acme/myproject.git")
    config = _config(SourceConfig(name="personal", path=src, private=True))

    rc = bootstrap(project, config, write=True)
    assert rc == 1  # something was missing (created) — status-style exit

    # A repo with an origin resolves to the remote-slug key (what apply uses),
    # so init targets that key dir; slug stays the kebab-cased basename.
    out = src / "acme_myproject" / "dot_claude" / "myproject.md"
    assert out.is_file()  # path variable {{slug}} applied to the filename
    body = out.read_text()
    assert "key=acme_myproject" in body
    assert "slug=myproject" in body
    assert f"dest={project}" in body
    assert "remote=acme_myproject" in body


def test_init_leaves_partials_and_unknown_vars_for_apply(tmp: Path) -> None:
    """init substitutes only its four vars; {{>partial}} stays for apply."""
    src = make_source(tmp, "personal")
    (src / "_shared" / "voice.md").write_text("Be terse.\n")
    _template(src, "AGENTS.md.mo", "# {{key}}\n{{>_shared/voice.md}}\n{{unknown}}\n")

    project = tmp / "myproject"
    _git_init(project)
    config = _config(SourceConfig(name="personal", path=src, private=True))

    bootstrap(project, config, write=True)
    keyfile = src / "myproject" / "AGENTS.md.mo"
    raw = keyfile.read_text()
    assert "# myproject" in raw           # init var substituted
    assert "{{>_shared/voice.md}}" in raw  # partial left intact
    assert "{{unknown}}" in raw            # unknown var left intact

    # apply then renders the .mo, resolving the partial.
    apply_one(project, config)
    live = (project / "AGENTS.md").read_text()
    assert "Be terse." in live
    assert "# myproject" in live


# ── idempotency, no clobber, backfill ──────────────────────────────────────


def test_init_dry_run_creates_nothing_and_exits_1(tmp: Path) -> None:
    src = make_source(tmp, "personal")
    _template(src, "AGENTS.md", "for {{key}}\n")
    project = tmp / "myproject"
    _git_init(project)
    config = _config(SourceConfig(name="personal", path=src, private=True))

    rc = bootstrap(project, config, write=False)
    assert rc == 1
    assert not (src / "myproject" / "AGENTS.md").exists()


def test_init_is_idempotent_and_never_clobbers(tmp: Path) -> None:
    src = make_source(tmp, "personal")
    _template(src, "AGENTS.md", "for {{key}}\n")
    project = tmp / "myproject"
    _git_init(project)
    config = _config(SourceConfig(name="personal", path=src, private=True))

    assert bootstrap(project, config, write=True) == 1
    keyfile = src / "myproject" / "AGENTS.md"
    keyfile.write_text("hand-edited\n")  # user changed it afterwards

    # Second run: nothing to do, exit 0, edit preserved.
    assert bootstrap(project, config, write=True) == 0
    assert keyfile.read_text() == "hand-edited\n"


def test_init_backfills_only_the_newly_added_template_file(tmp: Path) -> None:
    src = make_source(tmp, "personal")
    _template(src, "AGENTS.md", "for {{key}}\n")
    project = tmp / "myproject"
    _git_init(project)
    config = _config(SourceConfig(name="personal", path=src, private=True))

    bootstrap(project, config, write=True)
    # Add a second template file, re-run: exactly that one appears.
    _template(src, "dot_claude/settings.json", '{"slug": "{{slug}}"}\n')
    assert bootstrap(project, config, write=True) == 1

    new = src / "myproject" / "dot_claude" / "settings.json"
    assert new.read_text() == '{"slug": "myproject"}\n'


# ── multi-source ────────────────────────────────────────────────────────────


def test_init_writes_into_each_sources_own_tree_and_source_filter(tmp: Path) -> None:
    a = make_source(tmp, "guidance")
    b = make_source(tmp, "memory")
    _template(a, "AGENTS.md", "guidance for {{key}}\n")
    _template(b, "dot_claude/settings.json", '{"key": "{{key}}"}\n')

    project = tmp / "myproject"
    _git_init(project)
    config = _config(
        SourceConfig(name="guidance", path=a),
        SourceConfig(name="memory", path=b),
    )

    # Restrict to one source: only its tree is written.
    assert bootstrap(project, config, only_sources=["memory"], write=True) == 1
    assert (b / "myproject" / "dot_claude" / "settings.json").exists()
    assert not (a / "myproject" / "AGENTS.md").exists()

    # Full run backfills the other source.
    assert bootstrap(project, config, write=True) == 1
    assert (a / "myproject" / "AGENTS.md").exists()


# ── worktree ────────────────────────────────────────────────────────────────


def test_init_in_worktree_resolves_repo_key_and_writes_once(tmp: Path) -> None:
    src = make_source(tmp, "personal")
    _template(src, "AGENTS.md", "for {{key}}\n")

    project = tmp / "myproject"
    _git_init(project)
    _git(project, "remote", "add", "origin", "git@github.com:acme/myproject.git")
    _git(project, "commit", "-q", "--allow-empty", "-m", "init")
    # Give the source a myproject key so resolution matches by name, not slug.
    (src / "myproject").mkdir()

    worktree = tmp / "Worktrees" / "myproject-feature"
    _git(project, "worktree", "add", "-q", "-b", "feature", str(worktree))
    assert (worktree / ".git").is_file(), "expected a linked worktree"

    config = _config(SourceConfig(name="personal", path=src, private=True))
    assert bootstrap(worktree, config, write=True) == 1

    # Written into the repo's key dir, named for the repo — once, not per worktree.
    assert (src / "myproject" / "AGENTS.md").read_text() == "for myproject\n"


# ── refusals ────────────────────────────────────────────────────────────────


def test_init_on_unresolvable_path_exits_2(tmp: Path, capsys) -> None:
    src = make_source(tmp, "personal", watched_roots=[str(tmp / "Code")])
    _template(src, "AGENTS.md", "x\n")
    plain = tmp / "not-a-repo"
    plain.mkdir()
    config = _config(SourceConfig(name="personal", path=src, watched_roots=[tmp / "Code"]))

    rc = bootstrap(plain, config, write=True)
    assert rc == 2
    err = capsys.readouterr().err
    assert "resolves to no overlay key" in err
    assert str(tmp / "Code") in err  # names the watched roots searched


# ── status --unmanaged ──────────────────────────────────────────────────────


def test_status_unmanaged_lists_bare_repos_only_behind_the_flag(tmp: Path, capsys) -> None:
    import argparse
    from repo_overlays.cli import cmd_status

    watched = tmp / "Code"
    src = make_source(tmp, "personal", watched_roots=[str(watched)])
    (src / "keyed").mkdir()  # a source key
    (src / "keyed" / "AGENTS.md").write_text("g\n")
    _git_init(watched / "keyed")   # has a matching source key
    _git_init(watched / "orphan")  # no source key → unmanaged

    top_cfg = make_top_config(tmp, [{"name": "personal", "path": str(src)}])

    # Default: the unmanaged repo is not surfaced; the digest contract holds.
    rc = cmd_status(argparse.Namespace(config=str(top_cfg), path=None, unmanaged=False))
    out = capsys.readouterr().out
    assert "unmanaged:" not in out
    assert rc == 0

    # --unmanaged: the bare repo is listed (and only it), exit 1.
    rc_flag = cmd_status(argparse.Namespace(config=str(top_cfg), path=None, unmanaged=True))
    out = capsys.readouterr().out
    assert f"unmanaged: {watched / 'orphan'}" in out
    assert "keyed" not in out
    assert rc_flag == 1


def test_unmanaged_destinations_direct(tmp: Path) -> None:
    """Focused check without the CLI's real-config coupling."""
    watched = tmp / "Code"
    src = make_source(tmp, "personal", watched_roots=[str(watched)])
    (src / "keyed").mkdir()
    (src / "keyed" / "AGENTS.md").write_text("g\n")
    _git_init(watched / "keyed")
    _git_init(watched / "orphan")

    config = _config(SourceConfig(name="personal", path=src, watched_roots=[watched]))
    names = {p.name for p in unmanaged_destinations(config)}
    assert "orphan" in names
    assert "keyed" not in names
