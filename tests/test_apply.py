"""Integration tests: multi-source × multi-target apply scenarios.

Features covered (USAGE.md §3, §4, §5, §6, §7):
- apply_one materialises symlinks for a project overlay (dot_ rewrite).
- apply_one materialises symlinks for a fixed target (no dot_ rewrite).
- Multi-source: later source overrides earlier for same file in shared key.
- Multi-target: same partial consumed by two different destinations.
- Manifest written and pruned when source file removed.
- Stale links removed after source is dropped from config.
- Regular files at destination are never overwritten.
- apply_all covers every destination (fixed + project repos).
"""

from __future__ import annotations

import json
import os
import subprocess
import tomllib
from pathlib import Path

import pytest

from repo_overlays.apply import apply_all, apply_one
from repo_overlays.config import AppConfig, SourceConfig
from repo_overlays.manifest import MANIFEST_FILENAME, read as read_manifest
from tests.conftest import make_source, make_top_config


# ── helpers ────────────────────────────────────────────────────────────────


def _git_init(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)


def _config(*sources: SourceConfig) -> AppConfig:
    return AppConfig(sources=list(sources))


# ── project overlay (dot_ rewrite) ────────────────────────────────────────


def test_project_overlay_symlinks_with_dot_rewrite(tmp: Path) -> None:
    """apply_one creates symlinks under a git project, applying dot_<name> → .<name>.

    Feature: project overlays, dot_X path rewrite (USAGE.md §2, §3).
    """
    src_dir = make_source(tmp, "personal")
    project = tmp / "myproject"
    _git_init(project)

    overlay_dir = src_dir / "myproject"
    overlay_dir.mkdir()
    (overlay_dir / "CLAUDE.md").write_text("agent guidance")
    (overlay_dir / "dot_claude").mkdir()
    (overlay_dir / "dot_claude" / "commands" / "release.md").parent.mkdir(parents=True)
    (overlay_dir / "dot_claude" / "commands" / "release.md").write_text("release cmd")

    config = _config(SourceConfig(name="personal", path=src_dir, private=True))
    apply_one(project, config)

    assert (project / "CLAUDE.md").is_symlink()
    assert (project / ".claude" / "commands" / "release.md").is_symlink()
    assert (project / "CLAUDE.md").read_text() == "agent guidance"


def test_project_overlay_with_template(tmp: Path) -> None:
    """apply_one renders .mo templates into _rendered/ and symlinks result.

    Feature: template rendering, symlink materialisation (USAGE.md §2, §4.1).
    """
    src_dir = make_source(tmp, "personal")
    (src_dir / "_shared" / "style.md").write_text("## Python Style\nUse type hints.\n")

    project = tmp / "myproject"
    _git_init(project)

    overlay_dir = src_dir / "myproject"
    overlay_dir.mkdir()
    template = overlay_dir / "CLAUDE.md.mo"
    template.write_text("# Guide\n{{>_shared/style.md}}")

    config = _config(SourceConfig(name="personal", path=src_dir, private=True))
    apply_one(project, config)

    live = project / "CLAUDE.md"
    assert live.is_symlink()
    content = live.read_text()
    assert "## Python Style" in content
    assert "Use type hints." in content


# ── fixed target (no dot_ rewrite) ────────────────────────────────────────


def test_fixed_target_symlinks_verbatim(tmp: Path) -> None:
    """apply_one for a fixed target uses 1:1 path mapping without dot_ rewrite.

    Feature: fixed-target overlays (USAGE.md §2, §3).
    """
    fixed_dest = tmp / "config" / "claude"
    fixed_dest.mkdir(parents=True)

    src_dir = make_source(tmp, "personal", targets={"_claude": str(fixed_dest)})
    claude_dir = src_dir / "_claude"
    claude_dir.mkdir()
    (claude_dir / "CLAUDE.md").write_text("global guidance")
    (claude_dir / "commands").mkdir()
    (claude_dir / "commands" / "commit-msg.md").write_text("commit cmd")

    config = _config(SourceConfig(name="personal", path=src_dir, private=True, targets={"_claude": fixed_dest}))
    apply_one(fixed_dest, config)

    assert (fixed_dest / "CLAUDE.md").is_symlink()
    assert (fixed_dest / "commands" / "commit-msg.md").is_symlink()
    # No dot_ rewrite on fixed targets.
    assert not (fixed_dest / ".CLAUDE.md").exists()


# ── multi-source composition ───────────────────────────────────────────────


def test_multisource_later_overrides_earlier(tmp: Path) -> None:
    """When two sources provide the same file under a key, the later source wins.

    Feature: key-level file merging, conflict warning (USAGE.md §6.3).
    """
    s1 = make_source(tmp, "base")
    s2 = make_source(tmp, "override")
    project = tmp / "proj"
    _git_init(project)

    (s1 / "proj").mkdir()
    (s1 / "proj" / "AGENTS.md").write_text("base content")
    (s2 / "proj").mkdir()
    (s2 / "proj" / "AGENTS.md").write_text("override content")

    config = _config(
        SourceConfig(name="base", path=s1),
        SourceConfig(name="override", path=s2),
    )
    apply_one(project, config)

    assert (project / "AGENTS.md").read_text() == "override content"


def test_multisource_shared_partial_consumed_by_two_destinations(tmp: Path) -> None:
    """A partial from one source is composed into two separate destination projects.

    Feature: shared partials, multi-target (USAGE.md §6.3, mermaid diagram).
    The penpot-data-model.md partial is consumed by both penpot/ and beadpot/ keys.
    """
    personal = make_source(tmp, "personal")
    wiki = make_source(tmp, "wiki")

    (wiki / "_shared" / "penpot-data-model.md").write_text(
        "## Penpot Data Model\nShapes, tokens.\n"
    )

    penpot_proj = tmp / "penpot"
    beadpot_proj = tmp / "beadpot"
    _git_init(penpot_proj)
    _git_init(beadpot_proj)

    (personal / "penpot").mkdir()
    (personal / "penpot" / "CLAUDE.md.mo").write_text(
        "Penpot guide:\n{{>_shared/penpot-data-model.md}}"
    )
    (personal / "beadpot").mkdir()
    (personal / "beadpot" / "CLAUDE.md.mo").write_text(
        "Beadpot guide:\n{{>_shared/penpot-data-model.md}}"
    )

    config = _config(
        SourceConfig(name="personal", path=personal, private=True),
        SourceConfig(name="wiki", path=wiki),
    )

    apply_one(penpot_proj, config)
    apply_one(beadpot_proj, config)

    penpot_content = (penpot_proj / "CLAUDE.md").read_text()
    beadpot_content = (beadpot_proj / "CLAUDE.md").read_text()

    assert "Penpot Data Model" in penpot_content
    assert "Penpot Data Model" in beadpot_content
    assert "Penpot guide" in penpot_content
    assert "Beadpot guide" in beadpot_content


# ── manifest ───────────────────────────────────────────────────────────────


def test_manifest_written_after_apply(tmp: Path) -> None:
    """apply_one writes .repo-overlays.toml recording installed links.

    Feature: destination hygiene, manifest (USAGE.md §7).
    """
    src_dir = make_source(tmp, "src1")
    project = tmp / "proj"
    _git_init(project)

    (src_dir / "proj").mkdir()
    (src_dir / "proj" / "AGENTS.md").write_text("guidance")

    config = _config(SourceConfig(name="src1", path=src_dir))
    apply_one(project, config)

    manifest = read_manifest(project)
    paths = {lr.path for lr in manifest.links}
    assert "AGENTS.md" in paths


def test_manifest_prunes_removed_file(tmp: Path) -> None:
    """Removing a file from the overlay and re-applying prunes the old symlink.

    Feature: stale link cleanup via manifest (USAGE.md §7).
    """
    src_dir = make_source(tmp, "src1")
    project = tmp / "proj"
    _git_init(project)

    overlay_dir = src_dir / "proj"
    overlay_dir.mkdir()
    agent_file = overlay_dir / "AGENTS.md"
    agent_file.write_text("guidance")

    config = _config(SourceConfig(name="src1", path=src_dir))
    apply_one(project, config)
    assert (project / "AGENTS.md").is_symlink()

    # Remove the file from the source.
    agent_file.unlink()
    apply_one(project, config)

    assert not (project / "AGENTS.md").exists(), "Stale symlink must be pruned"


def test_regular_file_at_dest_not_overwritten(tmp: Path) -> None:
    """A regular (non-symlink) file at the destination is left alone, not overwritten.

    Feature: safety guard against overwriting user files (PORT.md §5).
    """
    src_dir = make_source(tmp, "src1")
    project = tmp / "proj"
    _git_init(project)

    overlay_dir = src_dir / "proj"
    overlay_dir.mkdir()
    (overlay_dir / "AGENTS.md").write_text("overlay content")

    # Pre-create a real file at the destination.
    (project / "AGENTS.md").write_text("user content")

    config = _config(SourceConfig(name="src1", path=src_dir))
    apply_one(project, config)

    # Must not be overwritten.
    assert (project / "AGENTS.md").read_text() == "user content"
    assert not (project / "AGENTS.md").is_symlink()


# ── apply_all ─────────────────────────────────────────────────────────────


def test_apply_all_covers_fixed_and_project_destinations(tmp: Path) -> None:
    """apply_all materialises both fixed targets and project repos under watched_roots.

    Feature: apply_all, watched_roots auto-discovery (USAGE.md §4.1, §5).
    """
    fixed_dest = tmp / "config_claude"
    fixed_dest.mkdir()

    watched = tmp / "Code"
    project = watched / "myproject"
    _git_init(project)

    src_dir = make_source(
        tmp,
        "personal",
        targets={"_claude": str(fixed_dest)},
        watched_roots=[str(watched)],
    )
    (src_dir / "_claude").mkdir()
    (src_dir / "_claude" / "CLAUDE.md").write_text("global guidance")

    (src_dir / "myproject").mkdir()
    (src_dir / "myproject" / "AGENTS.md").write_text("project guidance")

    # Load config via make_top_config to exercise the full loading path.
    cfg_file = make_top_config(
        tmp,
        [{"name": "personal", "path": str(src_dir), "private": True}],
    )
    from repo_overlays.config import load_config
    config = load_config(cfg_file)

    apply_all(config)

    assert (fixed_dest / "CLAUDE.md").is_symlink()
    assert (project / "AGENTS.md").is_symlink()


# ── remote slug vs basename fallback ────────────────────────────────────


def test_project_key_falls_back_to_basename_when_slug_does_not_match(tmp: Path) -> None:
    """When the git remote slug (owner_repo) differs from the overlay key,
    apply_one falls back to the toplevel basename.

    Feature: remote-slug vs basename resolution (USAGE.md §2).
    """
    src_dir = make_source(tmp, "personal")
    projects = tmp / "projects"
    projects.mkdir()
    dest = projects / "myproject"
    _git_init(dest)

    # Give the repo a remote whose slug does NOT match the basename.
    subprocess.run(
        ["git", "-C", str(dest), "remote", "add", "origin",
         "git@github.com:some-owner/myproject.git"],
        check=True, capture_output=True,
    )

    # Overlay key is the basename "myproject", not the slug "some-owner_myproject".
    overlay_dir = src_dir / "myproject"
    overlay_dir.mkdir()
    (overlay_dir / "AGENTS.md").write_text("project guidance")

    config = _config(SourceConfig(name="personal", path=src_dir, private=True))
    ok = apply_one(dest, config)

    assert ok, "apply_one should succeed with basename fallback"
    assert (dest / "AGENTS.md").is_symlink()
    assert (dest / "AGENTS.md").read_text() == "project guidance"


def test_project_key_uses_remote_slug_when_it_matches(tmp: Path) -> None:
    """When the overlay key matches the remote slug exactly, that key is used
    (in preference over a potentially matching basename).

    Feature: remote-slug takes priority over basename (USAGE.md §2).
    """
    src_dir = make_source(tmp, "personal")
    projects = tmp / "projects"
    projects.mkdir()
    dest = projects / "myrepo"
    _git_init(dest)

    # Remote slug will be "someone_mylib".
    subprocess.run(
        ["git", "-C", str(dest), "remote", "add", "origin",
         "git@github.com:someone/mylib.git"],
        check=True, capture_output=True,
    )

    # Create overlay key matching the remote slug.
    (src_dir / "someone_mylib").mkdir()
    (src_dir / "someone_mylib" / "README.md").write_text("slug-match")

    # Also create a key matching the basename (should NOT be used).
    (src_dir / "myrepo").mkdir()
    (src_dir / "myrepo" / "README.md").write_text("basename-match")

    config = _config(SourceConfig(name="personal", path=src_dir, private=True))
    ok = apply_one(dest, config)

    assert ok
    # The source files from someone_mylib/ should win.
    assert (dest / "README.md").is_symlink()
    assert (dest / "README.md").read_text() == "slug-match"


def test_project_key_matches_remote_repo_name_regardless_of_dir(tmp: Path) -> None:
    """A linked worktree/clone whose directory is NOT named after the repo still
    resolves to the bare-repo-name overlay key via the remote (owner/repo -> repo).

    Feature: worktree-friendly key resolution (USAGE.md §2).
    """
    src_dir = make_source(tmp, "personal")
    worktrees = tmp / "worktrees"
    worktrees.mkdir()
    dest = worktrees / "penpot-feature-x"  # dir name != repo name
    _git_init(dest)
    subprocess.run(
        ["git", "-C", str(dest), "remote", "add", "origin",
         "git@github.com:penpot/penpot.git"],
        check=True, capture_output=True,
    )

    # Overlay key is the bare repo name "penpot" (not the slug, not the dir name).
    overlay_dir = src_dir / "penpot"
    overlay_dir.mkdir()
    (overlay_dir / "CLAUDE.md").write_text("penpot guidance")

    config = _config(SourceConfig(name="personal", path=src_dir, private=True))
    ok = apply_one(dest, config)

    assert ok, "apply_one should resolve via the remote repo name"
    assert (dest / "CLAUDE.md").is_symlink()
    assert (dest / "CLAUDE.md").read_text() == "penpot guidance"


def test_apply_all_falls_back_to_basename(tmp: Path) -> None:
    """apply_all discovers repos under watched_roots and falls back to basename
    when the remote slug doesn't match any overlay key.
    """
    watched = tmp / "Code"
    project = watched / "beadpot"
    _git_init(project)
    subprocess.run(
        ["git", "-C", str(project), "remote", "add", "origin",
         "git@github.com:penpot/beadpot.git"],
        check=True, capture_output=True,
    )

    src_dir = make_source(
        tmp,
        "personal",
        watched_roots=[str(watched)],
    )
    (src_dir / "beadpot").mkdir()
    (src_dir / "beadpot" / "AGENTS.md").write_text("beadpot guidance")

    cfg_file = make_top_config(
        tmp,
        [{"name": "personal", "path": str(src_dir), "private": True}],
    )
    from repo_overlays.config import load_config
    config = load_config(cfg_file)

    apply_all(config)

    assert (project / "AGENTS.md").is_symlink()
    assert (project / "AGENTS.md").read_text() == "beadpot guidance"


# ── list subcommand ──────────────────────────────────────────────────────


def test_list_outputs_home_relative_paths(tmp: Path, capsys: pytest.CaptureFixture) -> None:
    """repo-overlay list prints every overlay-managed file as $HOME-relative path."""
    fixed_dest = tmp / "config" / "myapp"
    fixed_dest.mkdir(parents=True)

    watched = tmp / "Code"
    project = watched / "myproj"
    _git_init(project)

    src_dir = make_source(
        tmp,
        "personal",
        targets={"_myapp": str(fixed_dest)},
        watched_roots=[str(watched)],
    )
    (src_dir / "_myapp").mkdir()
    (src_dir / "_myapp" / "CONFIG.md").write_text("app config")
    (src_dir / "_myapp" / "dot_secret" / "key.md").parent.mkdir(parents=True)
    (src_dir / "_myapp" / "dot_secret" / "key.md").write_text("secret")

    (src_dir / "myproj").mkdir()
    (src_dir / "myproj" / "AGENTS.md").write_text("project guidance")

    cfg_file = make_top_config(
        tmp,
        [{"name": "personal", "path": str(src_dir), "private": True}],
    )

    from repo_overlays.config import load_config
    from repo_overlays.apply import apply_all
    config = load_config(cfg_file)
    apply_all(config)

    # Verify manifests were written and symlinks exist before testing list.
    assert (fixed_dest / "CONFIG.md").is_symlink()
    assert (project / "AGENTS.md").is_symlink()

    from repo_overlays.cli import cmd_list
    import argparse

    ns = argparse.Namespace(config=str(cfg_file))
    ret = cmd_list(ns)
    assert ret == 0

    out = capsys.readouterr().out
    lines = [line.strip() for line in out.split("\n") if line.strip()]

    # Spot-check known entries. Paths outside $HOME are printed absolute.
    assert f"{fixed_dest}/CONFIG.md" in lines, f"missing CONFIG.md in {lines}"
    assert f"{project}/AGENTS.md" in lines, f"missing AGENTS.md in {lines}"

    # Fixed targets keep dot_ prefix verbatim (no dot_ rewrite).
    assert f"{fixed_dest}/dot_secret/key.md" in lines,\
        f"missing dot_secret/key.md in {lines}"


# ── git-dir destinations (dot_git/…) and linked worktrees ────────────────


def _commit_empty(path: Path) -> None:
    subprocess.run(["git", "-C", str(path), "commit", "-q", "--allow-empty", "-m", "init"],
                   check=True, capture_output=True,
                   env={**os.environ,
                        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


def test_git_dir_overlay_lands_in_hooks(tmp: Path) -> None:
    """dot_git/hooks/<hook> materialises into the real .git/hooks of a normal checkout."""
    src_dir = make_source(tmp, "personal")
    project = tmp / "myproject"
    _git_init(project)

    hook_src = src_dir / "myproject" / "dot_git" / "hooks" / "post-merge"
    hook_src.parent.mkdir(parents=True)
    hook_src.write_text("#!/bin/sh\nexit 0\n")

    config = _config(SourceConfig(name="personal", path=src_dir, private=True))
    apply_one(project, config)

    hook = project / ".git" / "hooks" / "post-merge"
    assert hook.is_symlink()
    assert hook.resolve() == hook_src.resolve()

    # Git-dir paths are never tracked, so they must not reach info/exclude.
    exclude = (project / ".git" / "info" / "exclude").read_text()
    assert "hooks/post-merge" not in exclude


def test_git_dir_overlay_in_linked_worktree(tmp: Path) -> None:
    """In a linked worktree (.git is a file) hooks resolve to the shared common dir.

    Regression: `dest_root/".git"/…` raised NotADirectoryError and aborted apply
    for every remaining key.
    """
    src_dir = make_source(tmp, "personal")
    project = tmp / "myproject"
    _git_init(project)
    subprocess.run(
        ["git", "-C", str(project), "remote", "add", "origin",
         "git@github.com:acme/myproject.git"],
        check=True, capture_output=True,
    )
    _commit_empty(project)

    # Directory name ≠ repo name: resolution goes through the shared origin.
    worktree = tmp / "Worktrees" / "myproject-feature"
    subprocess.run(["git", "-C", str(project), "worktree", "add", "-q",
                    "-b", "feature", str(worktree)],
                   check=True, capture_output=True)
    assert (worktree / ".git").is_file(), "expected a linked worktree"

    overlay = src_dir / "myproject"
    (overlay / "dot_git" / "hooks").mkdir(parents=True)
    (overlay / "dot_git" / "hooks" / "post-merge").write_text("#!/bin/sh\nexit 0\n")
    (overlay / "AGENTS.md").write_text("project guidance")

    config = _config(SourceConfig(name="personal", path=src_dir, private=True))
    assert apply_one(worktree, config) is True

    # Ordinary files still land in the worktree …
    assert (worktree / "AGENTS.md").is_symlink()
    # … and the hook lands in the shared git dir, not under the .git file.
    hook = project / ".git" / "hooks" / "post-merge"
    assert hook.is_symlink()
    assert hook.resolve() == (overlay / "dot_git" / "hooks" / "post-merge").resolve()

    # Manifest records the out-of-worktree link absolutely, and re-apply is a no-op.
    manifest = read_manifest(worktree)
    paths = {lr.path for lr in manifest.links}
    assert str(hook) in paths, paths
    assert apply_one(worktree, config) is True


def test_worktree_and_main_keep_separate_exclude_blocks(tmp: Path) -> None:
    """Worktree and main checkout share info/exclude; each owns a labelled block.

    Git resolves ``info/`` to the common dir, so an unlabelled block would be
    overwritten by whichever destination applied last.
    """
    src_dir = make_source(tmp, "personal")
    project = tmp / "myproject"
    _git_init(project)
    subprocess.run(
        ["git", "-C", str(project), "remote", "add", "origin",
         "git@github.com:acme/myproject.git"],
        check=True, capture_output=True,
    )
    _commit_empty(project)
    worktree = tmp / "Worktrees" / "myproject-feature"
    subprocess.run(["git", "-C", str(project), "worktree", "add", "-q",
                    "-b", "feature", str(worktree)],
                   check=True, capture_output=True)

    overlay = src_dir / "myproject"
    overlay.mkdir()
    (overlay / "AGENTS.md").write_text("project guidance")

    config = _config(SourceConfig(name="personal", path=src_dir, private=True))
    apply_one(project, config)
    apply_one(worktree, config)

    exclude = (project / ".git" / "info" / "exclude").read_text()
    assert f"[{project}]" in exclude
    assert f"[{worktree}]" in exclude
    assert exclude.count("/AGENTS.md") == 2


def test_git_dir_overlay_skipped_outside_git_repo(tmp: Path) -> None:
    """A dot_git/ destination in a non-git target is skipped, other files still apply."""
    fixed_dest = tmp / "config" / "myapp"
    fixed_dest.mkdir(parents=True)
    src_dir = make_source(tmp, "personal", targets={"_myapp": str(fixed_dest)})
    (src_dir / "_myapp" / ".git" / "hooks").mkdir(parents=True)
    (src_dir / "_myapp" / ".git" / "hooks" / "post-merge").write_text("x")
    (src_dir / "_myapp" / "CONFIG.md").write_text("app config")

    config = _config(SourceConfig(
        name="personal", path=src_dir, private=True,
        targets={"_myapp": fixed_dest},
    ))
    apply_one(fixed_dest, config)

    assert (fixed_dest / "CONFIG.md").is_symlink()
    assert not (fixed_dest / ".git" / "hooks" / "post-merge").exists()


def test_exclude_drops_blocks_of_vanished_destinations(tmp: Path) -> None:
    """A moved or deleted worktree's block is garbage-collected on next apply.

    info/exclude is shared across worktrees, so nothing else would ever remove
    the entries of a destination that no longer exists.
    """
    from repo_overlays.apply import _merge_exclude_blocks, _MARKER_END, _marker_start

    live = tmp / "live"
    live.mkdir()
    gone = tmp / "gone"
    other = tmp / "other"
    other.mkdir()

    def block(dest: Path, entry: str) -> str:
        return f"{_marker_start(dest)}\n/{entry}\n{_MARKER_END}\n"

    existing = "# handwritten\n" + block(gone, "GONE.md") + block(other, "OTHER.md")
    merged = _merge_exclude_blocks(existing, live, block(live, "LIVE.md"))

    assert "# handwritten" in merged
    assert "/OTHER.md" in merged, "live destination's block must survive"
    assert "/GONE.md" not in merged, "vanished destination's block must be dropped"
    assert "/LIVE.md" in merged
    # Re-running is a fixed point.
    assert _merge_exclude_blocks(merged, live, block(live, "LIVE.md")) == merged


def test_exclude_adopts_unlabelled_legacy_block(tmp: Path) -> None:
    """A block written before per-destination labelling is replaced, not duplicated."""
    from repo_overlays.apply import _merge_exclude_blocks, _MARKER_END, _MARKER_PREFIX, _marker_start

    dest = tmp / "dest"
    dest.mkdir()
    legacy = f"{_MARKER_PREFIX} ──\n/OLD.md\n{_MARKER_END}\n"
    new = f"{_marker_start(dest)}\n/NEW.md\n{_MARKER_END}\n"

    merged = _merge_exclude_blocks(legacy, dest, new)
    assert "/OLD.md" not in merged
    assert merged.count("/NEW.md") == 1


# ── per-destination opt-out ──────────────────────────────────────────────


def test_skip_marker_excludes_a_single_destination(tmp: Path) -> None:
    """A destination carrying .repo-overlays-skip gets no overlay.

    Worktrees receive their repo's overlay by default; the marker is the
    per-destination opt-out (a worktree is too short-lived for a config entry).
    """
    from repo_overlays.manifest import SKIP_FILENAME

    src_dir = make_source(tmp, "personal")
    project = tmp / "myproject"
    _git_init(project)
    (src_dir / "myproject").mkdir()
    (src_dir / "myproject" / "AGENTS.md").write_text("guidance")

    (project / SKIP_FILENAME).touch()
    config = _config(SourceConfig(name="personal", path=src_dir, private=True))

    assert apply_one(project, config) is False
    assert not (project / "AGENTS.md").exists()


def test_skip_marker_withdraws_previously_applied_links(tmp: Path) -> None:
    """Marking an already-applied destination removes what was installed."""
    from repo_overlays.manifest import SKIP_FILENAME

    src_dir = make_source(tmp, "personal")
    project = tmp / "myproject"
    _git_init(project)
    (src_dir / "myproject").mkdir()
    (src_dir / "myproject" / "AGENTS.md").write_text("guidance")

    config = _config(SourceConfig(name="personal", path=src_dir, private=True))
    apply_one(project, config)
    assert (project / "AGENTS.md").is_symlink()

    (project / SKIP_FILENAME).touch()
    assert apply_one(project, config) is False

    assert not (project / "AGENTS.md").exists()
    assert not (project / MANIFEST_FILENAME).exists()
    exclude = (project / ".git" / "info" / "exclude").read_text()
    assert "AGENTS.md" not in exclude, "exclude block must go too"


def test_manifest_holds_only_current_links(tmp: Path) -> None:
    """Re-applying after a source file is removed leaves no record behind.

    Regression: the manifest was rewritten as (previous ∪ current), so pruned
    links kept their record forever and manifests accumulated entries pointing
    at files that no longer existed.
    """
    src_dir = make_source(tmp, "personal")
    project = tmp / "myproject"
    _git_init(project)
    overlay = src_dir / "myproject"
    overlay.mkdir()
    (overlay / "AGENTS.md").write_text("guidance")
    (overlay / "OLD.md").write_text("obsolete")

    config = _config(SourceConfig(name="personal", path=src_dir, private=True))
    apply_one(project, config)
    assert {lr.path for lr in read_manifest(project).links} == {"AGENTS.md", "OLD.md"}

    (overlay / "OLD.md").unlink()
    apply_one(project, config)

    paths = {lr.path for lr in read_manifest(project).links}
    assert paths == {"AGENTS.md"}, paths
    assert not (project / "OLD.md").exists()


def test_dangling_link_is_repointed_not_dropped(tmp: Path) -> None:
    """A link whose target moved is repointed, not pruned.

    Regression: the write branch keyed on exists(), which is False for a
    dangling symlink, so apply raised FileExistsError, skipped the file, and
    the next prune deleted the link — silently withdrawing overlays from every
    destination after a source directory was moved.
    """
    src_dir = make_source(tmp, "personal")
    project = tmp / "myproject"
    _git_init(project)
    overlay = src_dir / "myproject"
    overlay.mkdir()
    (overlay / "AGENTS.md").write_text("guidance")

    config = _config(SourceConfig(name="personal", path=src_dir, private=True))
    apply_one(project, config)
    link = project / "AGENTS.md"
    assert link.is_symlink()

    # Simulate the source having moved: same link, target now absent.
    link.unlink()
    link.symlink_to(tmp / "gone" / "AGENTS.md")
    assert link.is_symlink() and not link.exists()

    apply_one(project, config)

    assert link.is_symlink(), "link must survive"
    assert link.resolve() == (overlay / "AGENTS.md").resolve()
    assert link.read_text() == "guidance"
    assert {lr.path for lr in read_manifest(project).links} == {"AGENTS.md"}


def test_drift_in_subdirectory_is_reported_by_status(tmp: Path) -> None:
    """A diverged file deep in the tree stays visible to status.

    Regression: drift markers were located from the manifest's link records,
    but a diverged file is not a link — apply refuses to overwrite it — so the
    marker in a subdirectory holding no other overlay file was invisible.
    The notification fired and `status` still reported everything clean.
    """
    from repo_overlays.manifest import divergent_markers, read as read_m

    src_dir = make_source(tmp, "personal")
    project = tmp / "myproject"
    _git_init(project)
    overlay = src_dir / "myproject"
    (overlay / "work" / "notes").mkdir(parents=True)
    (overlay / "work" / "notes" / "guide.md.mo").write_text("v1\n")

    config = _config(SourceConfig(name="personal", path=src_dir, private=True))
    apply_one(project, config)

    live = project / "work" / "notes" / "guide.md"
    live.unlink()
    live.write_text("the agent's own version\n")
    (overlay / "work" / "notes" / "guide.md.mo").write_text("v2\n")
    apply_one(project, config)

    assert (project / "work" / "notes" / ".divergent").exists()
    assert read_m(project).drift == ["work/notes/guide.md"]
    found = {str(p.relative_to(project)) for p in divergent_markers(project)}
    assert found == {"work/notes/.divergent"}, found


# ── directory-level excludes (.overlay-own) ────────────────────────────────


def _exclude_of(project: Path) -> str:
    return (project / ".git" / "info" / "exclude").read_text()


def _own_overlay(tmp: Path) -> tuple[Path, Path, AppConfig]:
    """Source with `myproject/work/` marked overlay-owned, plus a root file."""
    src_dir = make_source(tmp, "personal")
    project = tmp / "myproject"
    _git_init(project)
    overlay = src_dir / "myproject"
    (overlay / "work" / "wf-now").mkdir(parents=True)
    (overlay / "work" / ".overlay-own").write_text("")
    (overlay / "work" / "wf-now" / "plan.md").write_text("plan\n")
    (overlay / "work" / "wf-now" / "notes.md").write_text("notes\n")
    (overlay / "AGENTS.md").write_text("guidance\n")
    config = _config(SourceConfig(name="personal", path=src_dir, private=True))
    return project, overlay, config


def test_own_marker_excludes_the_directory_not_its_files(tmp: Path) -> None:
    """One `/work/` entry replaces the per-file lines; files outside stay per-file."""
    project, _overlay, config = _own_overlay(tmp)
    apply_one(project, config)

    exclude = _exclude_of(project)
    assert "/work/\n" in exclude
    assert "/work/wf-now/plan.md" not in exclude
    assert "/work/wf-now/notes.md" not in exclude
    assert "/AGENTS.md" in exclude

    # The links themselves are unaffected: only the exclude granularity changed.
    assert (project / "work" / "wf-now" / "plan.md").is_symlink()
    assert {lr.path for lr in read_manifest(project).links} == {
        "AGENTS.md", "work/wf-now/plan.md", "work/wf-now/notes.md",
    }


def test_own_marker_hides_files_that_are_not_overlay_managed(tmp: Path) -> None:
    """The point of the coarse entry: git never sees anything under an owned dir.

    A file an agent writes directly into `work/` (no overlay source, so no
    exclude line of its own) must not show as untracked — that visibility is
    what got overlay symlinks staged into a project by a stray `git add -A`.
    """
    project, _overlay, config = _own_overlay(tmp)
    apply_one(project, config)

    (project / "work" / "wf-now" / "scratch.md").write_text("agent scratch\n")
    out = subprocess.run(
        ["git", "-C", str(project), "status", "--porcelain", "-uall"],
        check=True, capture_output=True, text=True,
    ).stdout
    assert "work/" not in out, out


def test_own_marker_declines_to_collapse_a_tracked_directory(tmp: Path) -> None:
    """Git already tracks something under the dir → keep per-file entries and warn.

    Collapsing there would hide the tracked file's untracked neighbours while
    the tracked file itself stays tracked, so the marker cannot deliver what it
    promises; saying so beats silently half-applying it.
    """
    project, _overlay, config = _own_overlay(tmp)
    (project / "work").mkdir(exist_ok=True)
    (project / "work" / "real.md").write_text("a file the project tracks\n")
    subprocess.run(["git", "-C", str(project), "add", "-f", "work/real.md"],
                   check=True, capture_output=True)

    apply_one(project, config)

    exclude = _exclude_of(project)
    assert "/work/\n" not in exclude
    assert "/work/wf-now/plan.md" in exclude
    assert "/AGENTS.md" in exclude


def test_own_marker_at_key_root_is_refused(tmp: Path) -> None:
    """A marker at the key root would emit `/`, excluding the whole project."""
    src_dir = make_source(tmp, "personal")
    project = tmp / "myproject"
    _git_init(project)
    overlay = src_dir / "myproject"
    overlay.mkdir(parents=True)
    (overlay / ".overlay-own").write_text("")
    (overlay / "AGENTS.md").write_text("guidance\n")

    config = _config(SourceConfig(name="personal", path=src_dir, private=True))
    apply_one(project, config)

    exclude = _exclude_of(project)
    assert "\n/\n" not in exclude
    assert "/AGENTS.md" in exclude


def test_own_marker_never_materialises(tmp: Path) -> None:
    """The marker is a declaration; it must not appear at the destination."""
    project, _overlay, config = _own_overlay(tmp)
    apply_one(project, config)

    assert not (project / "work" / ".overlay-own").exists()
    assert not (project / "work" / ".overlay-own").is_symlink()
    assert all(".overlay-own" not in lr.path for lr in read_manifest(project).links)


def test_tracked_links_reports_a_staged_overlay_symlink(tmp: Path) -> None:
    """`info/exclude` cannot hide a tracked path, so status must flag it.

    Live case: two overlay symlinks were staged in a beadpot worktree, one
    pointing at a source path that had since been renamed away.
    """
    from repo_overlays.apply import tracked_links

    project, _overlay, config = _own_overlay(tmp)
    apply_one(project, config)
    subprocess.run(["git", "-C", str(project), "add", "-f", "AGENTS.md"],
                   check=True, capture_output=True)

    paths = [lr.path for lr in read_manifest(project).links]
    assert tracked_links(project, paths) == ["AGENTS.md"]

    subprocess.run(["git", "-C", str(project), "rm", "--cached", "-q", "AGENTS.md"],
                   check=True, capture_output=True)
    assert tracked_links(project, paths) == []


def test_marker_added_later_rewrites_a_stale_exclude_block(tmp: Path) -> None:
    """Exclusion policy can change with no change to any link.

    Adding `.overlay-own` to an already-applied destination alters no symlink,
    so the "already applied" short-circuit used to return early and the block
    stayed per-file until something else forced a re-apply.
    """
    project, overlay, config = _own_overlay(tmp)
    (overlay / "work" / ".overlay-own").unlink()
    apply_one(project, config)
    assert "/work/wf-now/plan.md" in _exclude_of(project)

    (overlay / "work" / ".overlay-own").write_text("")
    apply_one(project, config)

    exclude = _exclude_of(project)
    assert "/work/\n" in exclude
    assert "/work/wf-now/plan.md" not in exclude


# ── _already_applied: source changes since last apply ──────────────────────


def test_apply_one_materialises_a_file_added_after_first_apply(tmp: Path) -> None:
    """The FIXES.md bug: a source file added post-apply was never placed.

    `_already_applied` validated only recorded links, so a new source file —
    absent from the manifest — left the check reporting "applied" while the
    file was still missing at the destination. `repo-overlay apply <path>`
    (mise/emacs hooks, and the CLI with an argument) then skipped it.
    """
    src_dir = make_source(tmp, "personal")
    project = tmp / "myproject"
    _git_init(project)
    overlay = src_dir / "myproject"
    overlay.mkdir(parents=True)
    (overlay / "AGENTS.md").write_text("guidance\n")

    config = _config(SourceConfig(name="personal", path=src_dir, private=True))
    assert apply_one(project, config) is True
    assert {lr.path for lr in read_manifest(project).links} == {"AGENTS.md"}

    # Add a second file to the source, then re-apply the same path.
    (overlay / "CLAUDE.md").write_text("more guidance\n")
    assert apply_one(project, config) is True

    assert (project / "CLAUDE.md").is_symlink()
    assert (project / "CLAUDE.md").resolve() == (overlay / "CLAUDE.md").resolve()
    assert {lr.path for lr in read_manifest(project).links} == {"AGENTS.md", "CLAUDE.md"}


def test_apply_one_prunes_a_file_removed_after_first_apply(tmp: Path) -> None:
    """Symmetric case: a source file deleted post-apply is pruned on re-apply."""
    src_dir = make_source(tmp, "personal")
    project = tmp / "myproject"
    _git_init(project)
    overlay = src_dir / "myproject"
    overlay.mkdir(parents=True)
    (overlay / "AGENTS.md").write_text("guidance\n")
    (overlay / "CLAUDE.md").write_text("more\n")

    config = _config(SourceConfig(name="personal", path=src_dir, private=True))
    apply_one(project, config)
    assert (project / "CLAUDE.md").is_symlink()

    (overlay / "CLAUDE.md").unlink()
    assert apply_one(project, config) is True

    assert not (project / "CLAUDE.md").exists()
    assert {lr.path for lr in read_manifest(project).links} == {"AGENTS.md"}


def test_already_applied_is_true_when_nothing_changed(tmp: Path) -> None:
    """The fast path still fires: no source change → no re-apply, no churn."""
    from repo_overlays.apply import _already_applied
    from repo_overlays.sources import SourceStack

    src_dir = make_source(tmp, "personal")
    project = tmp / "myproject"
    _git_init(project)
    overlay = src_dir / "myproject"
    overlay.mkdir(parents=True)
    (overlay / "AGENTS.md").write_text("guidance\n")

    config = _config(SourceConfig(name="personal", path=src_dir, private=True))
    apply_one(project, config)

    assert _already_applied(project, "myproject", False, SourceStack(config)) is True


def test_already_applied_ignores_standing_drift(tmp: Path) -> None:
    """A destination with recorded drift must not re-apply on every cd.

    The diverged template yields no link, only a manifest.drift entry; the
    presence check treats that as accounted for, so it stays on the fast path.
    """
    from repo_overlays.apply import _already_applied
    from repo_overlays.sources import SourceStack

    src_dir = make_source(tmp, "personal")
    project = tmp / "myproject"
    _git_init(project)
    overlay = src_dir / "myproject"
    (overlay / "work").mkdir(parents=True)
    (overlay / "work" / "guide.md.mo").write_text("v1\n")

    config = _config(SourceConfig(name="personal", path=src_dir, private=True))
    apply_one(project, config)

    # Diverge the live file and bump the template, then apply to record drift.
    live = project / "work" / "guide.md"
    live.unlink()
    live.write_text("the agent's own version\n")
    (overlay / "work" / "guide.md.mo").write_text("v2\n")
    apply_one(project, config)
    assert read_manifest(project).drift == ["work/guide.md"]

    # With drift standing and no source change, the fast path holds.
    assert _already_applied(project, "myproject", False, SourceStack(config)) is True


def test_already_applied_false_after_partial_edit(tmp: Path) -> None:
    """The content-hash gap: a partial edit changes no path, so the old
    path-set check reported "applied" and `apply <path>` (mise/emacs hooks)
    never re-rendered. The recorded render hash must catch it: the fast path
    goes False and a full apply re-renders the live file.
    """
    from repo_overlays.apply import _already_applied
    from repo_overlays.sources import SourceStack

    src_dir = make_source(tmp, "personal")
    project = tmp / "myproject"
    _git_init(project)
    overlay = src_dir / "myproject"
    overlay.mkdir(parents=True)
    (src_dir / "_shared" / "voice.md").write_text("v1\n")
    (overlay / "CLAUDE.md.mo").write_text("{{>_shared/voice.md}}")

    config = _config(SourceConfig(name="personal", path=src_dir, private=True))
    apply_one(project, config)
    assert (project / "CLAUDE.md").read_text() == "v1\n"

    # Edit the partial only: same paths, different content.
    (src_dir / "_shared" / "voice.md").write_text("v2\n")
    assert _already_applied(project, "myproject", False, SourceStack(config)) is False

    assert apply_one(project, config) is True
    assert (project / "CLAUDE.md").read_text() == "v2\n"


def test_old_manifest_without_render_hash_reapplies_once(tmp: Path) -> None:
    """Manifests written before the hash field existed carry render_hash=None;
    the first apply after the upgrade must re-render and backfill the hash.
    """
    from repo_overlays.apply import _already_applied
    from repo_overlays.manifest import LinkRecord, write as write_manifest
    from repo_overlays.sources import SourceStack

    src_dir = make_source(tmp, "personal")
    project = tmp / "myproject"
    _git_init(project)
    overlay = src_dir / "myproject"
    overlay.mkdir(parents=True)
    (overlay / "CLAUDE.md.mo").write_text("v1\n")

    config = _config(SourceConfig(name="personal", path=src_dir, private=True))
    apply_one(project, config)

    # Simulate a pre-hash manifest: drop the field from every record.
    data = tomllib.loads((project / MANIFEST_FILENAME).read_text())
    records = [LinkRecord(**{k: v for k, v in lr.items() if k != "render_hash"})
               for lr in data["link"]]
    write_manifest(project, records, data["drift"])

    assert _already_applied(project, "myproject", False, SourceStack(config)) is False
    assert apply_one(project, config) is True
    assert all(lr.render_hash is not None for lr in read_manifest(project).links)


def test_invalid_json_render_keeps_last_good_link(tmp: Path) -> None:
    """A *.json.mo that stops rendering valid JSON must not break the live
    file: the last good render stays in service, the link is re-recorded with
    the fresh hash so the fast path keeps holding, and fixing the source
    re-renders.
    """
    from repo_overlays.apply import _already_applied
    from repo_overlays.sources import SourceStack

    src_dir = make_source(tmp, "personal")
    project = tmp / "myproject"
    _git_init(project)
    overlay = src_dir / "myproject"
    overlay.mkdir(parents=True)
    (src_dir / "_shared" / "servers.json").write_text('"penpot": {"port": 4401}\n')
    (overlay / "mcp.json.mo").write_text(
        '{\n  "mcpServers": {\n{{>_shared/servers.json}}\n  }\n}\n'
    )

    config = _config(SourceConfig(name="personal", path=src_dir, private=True))
    apply_one(project, config)
    live = project / "mcp.json"
    assert json.loads(live.read_text())["mcpServers"]["penpot"]["port"] == 4401

    # Break the fragment: the render now has a dangling comma → invalid JSON.
    (src_dir / "_shared" / "servers.json").write_text('"penpot": {"port": 4401},\n')
    assert apply_one(project, config) is True

    # Last-good content survives; the record is re-recorded with the new hash.
    assert live.is_symlink()
    assert json.loads(live.read_text())["mcpServers"]["penpot"]["port"] == 4401
    assert _already_applied(project, "myproject", False, SourceStack(config)) is True

    # Fixing the source re-renders and restores.
    (src_dir / "_shared" / "servers.json").write_text('"penpot": {"port": 4402}\n')
    assert apply_one(project, config) is True
    assert json.loads(live.read_text())["mcpServers"]["penpot"]["port"] == 4402


def test_status_reports_stale_render(tmp: Path, capsys) -> None:
    """status flags a live link whose render is no longer current: the source
    changed after the last apply and nothing re-rendered (watcher down)."""
    import argparse
    from repo_overlays.cli import cmd_status

    dest = tmp / "cfg"
    src_dir = make_source(tmp, "personal", targets={"_cfg": str(dest)})
    (src_dir / "_cfg").mkdir(exist_ok=True)
    (src_dir / "_shared" / "servers.json").write_text('"penpot": {"port": 4401}\n')
    (src_dir / "_cfg" / "mcp.json.mo").write_text(
        '{\n  "mcpServers": {\n{{>_shared/servers.json}}\n  }\n}\n'
    )
    top = make_top_config(tmp, [{"name": "personal", "path": str(src_dir)}])

    config = _config(SourceConfig(
        name="personal", path=src_dir, private=True, targets={"_cfg": dest}
    ))
    apply_one(dest, config)

    # Edit the partial, do not re-apply: the live render is now stale.
    (src_dir / "_shared" / "servers.json").write_text('"penpot": {"port": 4402}\n')
    rc = cmd_status(argparse.Namespace(config=str(top), path=None))
    out = capsys.readouterr().out
    assert rc == 1
    assert "stale:" in out
    assert "All overlays clean." not in out


def test_status_reports_invalid_json_template(tmp: Path, capsys) -> None:
    """status flags a *.json.mo whose render does not parse, which apply
    refused to write — the issue is only visible here and in the event log."""
    import argparse
    from repo_overlays.cli import cmd_status

    dest = tmp / "cfg"
    dest.mkdir(parents=True)
    src_dir = make_source(tmp, "personal", targets={"_cfg": str(dest)})
    (src_dir / "_cfg").mkdir(exist_ok=True)
    (src_dir / "_shared" / "servers.json").write_text('"penpot": {"port": 4401},\n')
    (src_dir / "_cfg" / "mcp.json.mo").write_text(
        '{\n  "mcpServers": {\n{{>_shared/servers.json}}\n  }\n}\n'
    )
    top = make_top_config(tmp, [{"name": "personal", "path": str(src_dir)}])

    rc = cmd_status(argparse.Namespace(config=str(top), path=None))
    out = capsys.readouterr().out
    assert rc == 1
    assert "invalid-json:" in out


def test_data_toml_never_materialises(tmp: Path) -> None:
    """data.toml is a declaration like .overlay-own: it gates rendering but
    must never appear at the destination or in the manifest."""
    src_dir = make_source(tmp, "personal")
    project = tmp / "myproject"
    _git_init(project)
    overlay = src_dir / "myproject"
    overlay.mkdir(parents=True)
    (overlay / "data.toml").write_text("name = 'x'\n")
    (overlay / "CLAUDE.md.mo").write_text("{{name}}\n")

    config = _config(SourceConfig(name="personal", path=src_dir, private=True))
    apply_one(project, config)

    assert not (project / "data.toml").exists()
    assert {lr.path for lr in read_manifest(project).links} == {"CLAUDE.md"}
    assert (project / "CLAUDE.md").read_text() == "x\n"


def test_data_edit_reapplies_via_render_hash(tmp: Path) -> None:
    """Editing data.toml changes no path; the render hash must re-arm
    apply <path> so the cd/editor hooks re-render."""
    from repo_overlays.apply import _already_applied
    from repo_overlays.sources import SourceStack

    src_dir = make_source(tmp, "personal")
    project = tmp / "myproject"
    _git_init(project)
    overlay = src_dir / "myproject"
    overlay.mkdir(parents=True)
    (overlay / "data.toml").write_text("name = 'v1'\n")
    (overlay / "CLAUDE.md.mo").write_text("{{name}}\n")

    config = _config(SourceConfig(name="personal", path=src_dir, private=True))
    apply_one(project, config)
    assert (project / "CLAUDE.md").read_text() == "v1\n"

    (overlay / "data.toml").write_text("name = 'v2'\n")
    assert _already_applied(project, "myproject", False, SourceStack(config)) is False
    apply_one(project, config)
    assert (project / "CLAUDE.md").read_text() == "v2\n"


def test_data_driven_invalid_json_keeps_last_good(tmp: Path) -> None:
    """A data edit that breaks the rendered JSON is refused; the last good
    render stays live and the link is re-recorded (fast path keeps holding)."""
    from repo_overlays.apply import _already_applied
    from repo_overlays.sources import SourceStack

    src_dir = make_source(tmp, "personal")
    project = tmp / "myproject"
    _git_init(project)
    overlay = src_dir / "myproject"
    overlay.mkdir(parents=True)
    (overlay / "data.toml").write_text('args = \'["ok"]\'\n')
    (overlay / "mcp.json.mo").write_text('{\n  "args": {{{args}}}\n}\n')

    config = _config(SourceConfig(name="personal", path=src_dir, private=True))
    apply_one(project, config)
    live = project / "mcp.json"
    assert json.loads(live.read_text())["args"] == ["ok"]

    (overlay / "data.toml").write_text('args = \'["broken\'\n')
    assert apply_one(project, config) is True
    assert json.loads(live.read_text())["args"] == ["ok"], "last good must survive"
    assert _already_applied(project, "myproject", False, SourceStack(config)) is True

    (overlay / "data.toml").write_text('args = \'["fixed"]\'\n')
    assert apply_one(project, config) is True
    assert json.loads(live.read_text())["args"] == ["fixed"]


def test_status_lints_unknown_data_ref(tmp: Path, capsys) -> None:
    """status flags a plain {{ref}} that resolves against no data key; refs
    inside sections and known names stay silent."""
    import argparse
    from repo_overlays.cli import cmd_status

    dest = tmp / "cfg"
    dest.mkdir(parents=True)
    src_dir = make_source(tmp, "personal", targets={"_cfg": str(dest)})
    (src_dir / "_cfg").mkdir(exist_ok=True)
    (src_dir / "_cfg" / "data.toml").write_text(
        "name = 'penpot'\n"
        "[[servers]]\nname = 'penpot'\n"
    )
    (src_dir / "_cfg" / "CLAUDE.md.mo").write_text(
        "{{name}} {{typo}} {{#servers}}{{name}}{{/servers}}\n"
    )
    top = make_top_config(tmp, [{"name": "personal", "path": str(src_dir)}])

    rc = cmd_status(argparse.Namespace(config=str(top), path=None))
    out = capsys.readouterr().out
    assert rc == 1
    refs = [line.rsplit(": ", 1)[1] for line in out.splitlines()
            if line.startswith("unknown-data-ref:")]
    assert refs == ["typo"], refs


def test_status_reports_no_destinations_instead_of_clean(tmp: Path, capsys) -> None:
    """A config that resolves zero destinations is a failure, not a clean run.

    Previously `status` printed "All overlays clean." and exited 0 when the
    config never loaded — the drift digest then reported all-clear on a broken
    setup. It must exit non-zero with a distinct marker instead.
    """
    import argparse
    from repo_overlays.cli import cmd_status

    missing = tmp / "does-not-exist.toml"
    rc = cmd_status(argparse.Namespace(config=str(missing), path=None))

    out = capsys.readouterr().out
    assert rc == 1
    assert out.startswith("no-destinations:")
    assert "All overlays clean." not in out
