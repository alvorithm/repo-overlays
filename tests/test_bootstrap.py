"""`repo-overlay init` — bootstrap a repo's overlay key from _template/.

Covers DESIGN.md §5 test plan: _template/ is reserved and never materialises;
the four init variables substitute in bodies and paths while partials are left
for apply; idempotent no-clobber creation with backfill; --write always applies
and exits 0; multi-source and --source restriction; worktree resolves to the
repo's key; a new key defaults to the bare remote repo name and --key overrides
it while a fixed target keeps its own and an unreachable one is refused; the
skeleton can seed the .overlay-own and data.toml declarations; an opted-out
destination keeps its key dir but no links; unresolvable target errors; and
`status --unmanaged` surfaces bare repos without touching the default output.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from repo_overlays.apply import apply_one
from repo_overlays.bootstrap import bootstrap, unmanaged_destinations
from repo_overlays.config import AppConfig, SourceConfig
from repo_overlays.manifest import SKIP_FILENAME, read as read_manifest
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
    assert rc == 0  # --write is a mutating command: 0 on success

    # No source has a key for this repo yet, so the new key is the bare remote
    # repo name, not the owner-qualified slug; {{remote}} keeps the slug and
    # {{slug}} follows the key.
    out = src / "myproject" / "dot_claude" / "myproject.md"
    assert out.is_file()  # path variable {{slug}} applied to the filename
    body = out.read_text()
    assert "key=myproject" in body
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

    assert bootstrap(project, config, write=True) == 0
    keyfile = src / "myproject" / "AGENTS.md"
    keyfile.write_text("hand-edited\n")  # user changed it afterwards

    # Second run: nothing left to create, still exit 0, edit preserved.
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
    assert bootstrap(project, config, write=True) == 0

    new = src / "myproject" / "dot_claude" / "settings.json"
    assert new.read_text() == '{"slug": "myproject"}\n'


def test_write_applies_even_when_every_key_file_already_exists(tmp: Path) -> None:
    """--write is a mutating command: it applies whether or not it created files."""
    src = make_source(tmp, "personal")
    _template(src, "AGENTS.md", "for {{key}}\n")
    project = tmp / "myproject"
    _git_init(project)
    config = _config(SourceConfig(name="personal", path=src, private=True))

    assert bootstrap(project, config, write=True) == 0
    live = project / "AGENTS.md"
    assert live.is_symlink()

    # Nothing left to create, and the destination lost its link: the run must
    # still apply, not short-circuit on "no missing files".
    live.unlink()
    assert bootstrap(project, config, write=True) == 0
    assert live.is_symlink()


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

    # Restrict to one source: only its tree is written. --write exits 0.
    assert bootstrap(project, config, only_sources=["memory"], write=True) == 0
    assert (b / "myproject" / "dot_claude" / "settings.json").exists()
    assert not (a / "myproject" / "AGENTS.md").exists()

    # Full run backfills the other source, also exit 0.
    assert bootstrap(project, config, write=True) == 0
    assert (a / "myproject" / "AGENTS.md").exists()


def test_init_leaves_a_path_another_source_already_provides(tmp: Path) -> None:
    """The skeleton never duplicates a file another source contributes for the key.

    Two sources on one destination path is a whole-file override, so a stub
    copied next to real content would shadow it or lose silently. This is what
    makes backfilling an existing key with a newly shipped template safe.
    """
    a = make_source(tmp, "guidance")
    b = make_source(tmp, "wiki")
    _template(a, "AGENTS.md.mo", "stub for {{key}}\n")
    (b / "myproject").mkdir()
    (b / "myproject" / "AGENTS.md.mo").write_text("real guidance\n")

    project = tmp / "myproject"
    _git_init(project)
    config = _config(
        SourceConfig(name="guidance", path=a),
        SourceConfig(name="wiki", path=b),
    )

    assert bootstrap(project, config) == 0  # dry run: nothing left to create
    assert bootstrap(project, config, write=True) == 0
    assert not (a / "myproject").exists()
    assert (project / "AGENTS.md").read_text() == "real guidance\n"


def test_init_leaves_a_path_the_destination_tracks_itself(tmp: Path) -> None:
    """A repo that ships its own file at that path keeps it; apply would refuse anyway."""
    src = make_source(tmp, "guidance")
    _template(src, "AGENTS.md.mo", "stub for {{key}}\n")
    _template(src, "dot_claude/skills/x/SKILL.md.mo", "skill for {{key}}\n")

    project = tmp / "myproject"
    _git_init(project)
    (project / "AGENTS.md").write_text("upstream's own guidance\n")  # tracked, not a link
    config = _config(SourceConfig(name="guidance", path=src, private=True))

    assert bootstrap(project, config, write=True) == 0
    assert not (src / "myproject" / "AGENTS.md.mo").exists()
    assert (project / "AGENTS.md").read_text() == "upstream's own guidance\n"
    # The rest of the skeleton still lands, including the dot_ rewrite.
    assert (src / "myproject" / "dot_claude" / "skills" / "x" / "SKILL.md.mo").is_file()
    assert (project / ".claude" / "skills" / "x" / "SKILL.md").is_symlink()


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
    assert bootstrap(worktree, config, write=True) == 0

    # Written into the repo's key dir, named for the repo — once, not per worktree.
    assert (src / "myproject" / "AGENTS.md").read_text() == "for myproject\n"


# ── key naming ──────────────────────────────────────────────────────────────


def test_new_key_defaults_to_the_bare_remote_repo_name(tmp: Path) -> None:
    """A key no source has yet is named for the repo, not owner_repo or basename.

    resolve_key_dest falls back to the owner-qualified slug, which would mint a
    key shaped unlike every hand-made one; the directory basename is wrong too
    when a clone or worktree is named after something else.
    """
    src = make_source(tmp, "personal")
    _template(src, "AGENTS.md", "for {{key}}\n")
    _template(src, "dot_hindsight/config.json", '{"projectName": "{{slug}}"}\n')

    project = tmp / "myproject-clone"
    _git_init(project)
    _git(project, "remote", "add", "origin", "git@github.com:acme/myproject.git")
    config = _config(SourceConfig(name="personal", path=src, private=True))

    assert bootstrap(project, config, write=True) == 0
    assert (src / "myproject" / "AGENTS.md").read_text() == "for myproject\n"
    assert not (src / "acme_myproject").exists()
    assert not (src / "myproject-clone").exists()
    # The memory slug follows the key too: a directory-name slug is exactly the
    # drift init exists to prevent (project:<worktree-dir> for a worktree).
    assert (src / "myproject" / "dot_hindsight" / "config.json").read_text() == (
        '{"projectName": "myproject"}\n'
    )


def test_explicit_key_overrides_the_default_the_resolver_would_pick(tmp: Path) -> None:
    """--key selects the remote slug, the disambiguator for a shared bare name."""
    src = make_source(tmp, "personal")
    _template(src, "AGENTS.md", "for {{key}}\n")

    project = tmp / "myproject"
    _git_init(project)
    _git(project, "remote", "add", "origin", "git@github.com:acme/myproject.git")
    config = _config(SourceConfig(name="personal", path=src, private=True))

    # The slug is lookup candidate 1, so the key dir stays reachable.
    assert bootstrap(project, config, key="acme_myproject", write=True) == 0
    assert (src / "acme_myproject" / "AGENTS.md").read_text() == "for acme_myproject\n"
    assert not (src / "myproject").exists()


def test_fixed_target_keeps_its_underscore_key(tmp: Path) -> None:
    """A fixed target's key is declared, never rewritten to a directory basename."""
    fixed_dest = tmp / "cfg"
    fixed_dest.mkdir()
    src = make_source(tmp, "personal", targets={"_claude": str(fixed_dest)})
    _template(src, "AGENTS.md", "for {{key}}\n")
    config = _config(SourceConfig(
        name="personal", path=src, private=True, targets={"_claude": fixed_dest}
    ))

    assert bootstrap(fixed_dest, config, write=True) == 0
    assert (src / "_claude" / "AGENTS.md").read_text() == "for _claude\n"
    assert not (src / "cfg").exists()


# ── skeleton declarations ───────────────────────────────────────────────────


def test_template_seeds_the_overlay_own_marker(tmp: Path) -> None:
    """`.overlay-own` is content for a source-to-source copy, junk only for apply."""
    src = make_source(tmp, "personal")
    _template(src, "docs.local/.overlay-own", "")
    _template(src, "docs.local/reference/note.md", "note for {{key}}\n")

    project = tmp / "myproject"
    _git_init(project)
    config = _config(SourceConfig(name="personal", path=src, private=True))

    assert bootstrap(project, config, write=True) == 0
    assert (src / "myproject" / "docs.local" / ".overlay-own").is_file()

    # The marker did its job at the destination: files materialise, it does not,
    # and git excludes the directory instead of each file under it.
    assert (project / "docs.local" / "reference" / "note.md").is_symlink()
    assert not (project / "docs.local" / ".overlay-own").exists()
    exclude = (project / ".git" / "info" / "exclude").read_text()
    assert "/docs.local/\n" in exclude
    assert "/docs.local/reference/note.md" not in exclude


def test_template_seeds_a_data_toml_so_the_new_key_is_data_active(tmp: Path) -> None:
    """A key-root data.toml is a declaration the skeleton must be able to seed."""
    src = make_source(tmp, "personal")
    _template(src, "data.toml", 'name = "{{slug}}"\nkey = "{{key}}"\n')

    project = tmp / "myproject"
    _git_init(project)
    config = _config(SourceConfig(name="personal", path=src, private=True))

    assert bootstrap(project, config, write=True) == 0
    assert (src / "myproject" / "data.toml").read_text() == (
        'name = "myproject"\nkey = "myproject"\n'
    )
    assert not (project / "data.toml").exists()  # a declaration never materialises


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


def test_init_refuses_a_key_the_resolver_cannot_reach(tmp: Path, capsys) -> None:
    """An arbitrary --key would write a key dir no apply ever resolves to."""
    src = make_source(tmp, "personal")
    _template(src, "AGENTS.md", "for {{key}}\n")

    project = tmp / "myproject"
    _git_init(project)
    config = _config(SourceConfig(name="personal", path=src, private=True))

    assert bootstrap(project, config, key="chosen", write=True) == 2
    assert not (src / "chosen").exists()  # refused before writing anything
    err = capsys.readouterr().err
    assert "unreachable" in err
    assert "myproject" in err  # names the candidates that would have worked


def test_init_seeds_an_opted_out_destination_without_materialising(tmp: Path) -> None:
    """`.repo-overlays-skip` is deliberate: seed the key dir, skip apply, exit 0."""
    src = make_source(tmp, "personal")
    _template(src, "AGENTS.md", "for {{key}}\n")

    project = tmp / "myproject"
    _git_init(project)
    (project / SKIP_FILENAME).touch()
    config = _config(SourceConfig(name="personal", path=src, private=True))

    assert bootstrap(project, config, write=True) == 0
    assert (src / "myproject" / "AGENTS.md").is_file()
    assert not (project / "AGENTS.md").exists()


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
