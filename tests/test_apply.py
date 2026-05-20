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

import subprocess
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
