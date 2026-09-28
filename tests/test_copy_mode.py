"""Copy mode: files under an ``.overlay-copy`` marker materialise as regular files.

Contracts covered (USAGE.md "Copies instead of symlinks: `.overlay-copy`"):
- a copy is placed, excluded from git, and refreshed while nobody edits it;
- an edited copy is never overwritten: the source's version is proposed beside
  it, and the standing drift does not re-apply on every cd;
- template copies in two destinations of one key do not see each other's
  re-render as an edit (the shared ``_rendered/`` trap);
- a file the tool never wrote is adopted only when byte-identical;
- pruning removes an unedited copy and keeps an edited one;
- adding or removing the marker converts between link and copy.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from repo_overlays.apply import _already_applied, apply_one
from repo_overlays.cli import cmd_status
from repo_overlays.config import AppConfig, SourceConfig
from repo_overlays.manifest import read as read_manifest
from repo_overlays.sources import SourceStack
from tests.conftest import make_source, make_top_config


def _git_init(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)


def _project(tmp: Path, name: str = "myproject") -> tuple[Path, Path, AppConfig]:
    """A source whose key ``myproject`` copies ``js/`` and links everything else."""
    src_dir = make_source(tmp, "personal")
    overlay = src_dir / "myproject"
    (overlay / "js").mkdir(parents=True)
    (overlay / "js" / ".overlay-copy").write_text("")
    (overlay / "js" / "config.js").write_text('var penpotFlags = "enable-mcp";\n')
    (overlay / "AGENTS.md").write_text("guidance\n")
    project = tmp / name
    _git_init(project)
    config = AppConfig(sources=[SourceConfig(name="personal", path=src_dir, private=True)])
    return overlay, project, config


def test_copy_is_placed_excluded_and_refreshed(tmp: Path) -> None:
    overlay, project, config = _project(tmp)
    apply_one(project, config)

    live = project / "js" / "config.js"
    assert live.is_file() and not live.is_symlink()
    assert live.read_text() == 'var penpotFlags = "enable-mcp";\n'
    assert (project / "AGENTS.md").is_symlink()
    assert not (project / "js" / ".overlay-copy").exists()
    exclude = (project / ".git" / "info" / "exclude").read_text()
    assert "/js/config.js" in exclude

    (overlay / "js" / "config.js").write_text('var penpotFlags = "enable-mcp enable-branching";\n')
    apply_one(project, config)
    assert not live.is_symlink()
    assert live.read_text() == 'var penpotFlags = "enable-mcp enable-branching";\n'


def test_edited_copy_is_kept_and_the_source_proposed(tmp: Path) -> None:
    overlay, project, config = _project(tmp)
    apply_one(project, config)
    live = project / "js" / "config.js"

    live.write_text("local edit\n")
    (overlay / "js" / "config.js").write_text("source v2\n")
    apply_one(project, config)

    assert live.read_text() == "local edit\n"
    assert (project / "js" / "config.js.proposed").read_text() == "source v2\n"
    assert (project / "js" / ".divergent").exists()
    assert "js/config.js" in read_manifest(project).drift
    # Standing drift must not re-apply (and re-notify) on every cd.
    assert _already_applied(project, "myproject", False, SourceStack(config)) is True

    # promote's "accept": the proposal replaces the edit; one apply converges.
    (project / "js" / "config.js.proposed").replace(live)
    assert _already_applied(project, "myproject", False, SourceStack(config)) is False
    apply_one(project, config)
    assert read_manifest(project).drift == []
    assert _already_applied(project, "myproject", False, SourceStack(config)) is True


def test_template_copies_in_two_destinations_do_not_diverge(tmp: Path) -> None:
    """One ``_rendered/`` file serves every destination of a key, so a render-based
    drift check would call the second destination's (unedited) copy edited."""
    src_dir = make_source(tmp, "personal")
    (src_dir / "_shared" / "flags.txt").write_text("enable-mcp")
    overlay = src_dir / "myproject"
    overlay.mkdir()
    (overlay / ".overlay-copy").write_text("")
    (overlay / "config.js.mo").write_text('var penpotFlags = "{{>_shared/flags.txt}}";\n')
    config = AppConfig(sources=[SourceConfig(name="personal", path=src_dir, private=True)])
    first, second = tmp / "a" / "myproject", tmp / "b" / "myproject"
    for dest in (first, second):
        _git_init(dest)
        apply_one(dest, config)

    (src_dir / "_shared" / "flags.txt").write_text("enable-mcp enable-branching")
    apply_one(first, config)
    apply_one(second, config)

    for dest in (first, second):
        assert (dest / "config.js").read_text() == 'var penpotFlags = "enable-mcp enable-branching";\n'
        assert not (dest / "config.js.proposed").exists()
        assert read_manifest(dest).drift == []


def test_foreign_file_is_adopted_only_when_identical(tmp: Path) -> None:
    overlay, project, config = _project(tmp)
    (overlay / "js" / "other.js").write_text("source\n")
    (project / "js").mkdir()
    (project / "js" / "config.js").write_text('var penpotFlags = "enable-mcp";\n')
    (project / "js" / "other.js").write_text("somebody else's file\n")

    apply_one(project, config)

    recorded = read_manifest(project).by_path()
    assert recorded["js/config.js"].is_copy
    assert "js/other.js" not in recorded
    assert (project / "js" / "other.js").read_text() == "somebody else's file\n"


def test_prune_removes_unedited_copy_and_keeps_edited_one(tmp: Path) -> None:
    overlay, project, config = _project(tmp)
    (overlay / "js" / "edited.js").write_text("source\n")
    apply_one(project, config)
    (project / "js" / "edited.js").write_text("local edit\n")

    (overlay / "js" / "config.js").unlink()
    (overlay / "js" / "edited.js").unlink()
    apply_one(project, config)

    assert not (project / "js" / "config.js").exists()
    assert (project / "js" / "edited.js").read_text() == "local edit\n"
    assert "js/edited.js" not in read_manifest(project).by_path()


def test_marker_converts_between_link_and_copy(tmp: Path) -> None:
    overlay, project, config = _project(tmp)
    live = project / "js" / "config.js"
    (overlay / "js" / ".overlay-copy").unlink()
    apply_one(project, config)
    assert live.is_symlink()

    (overlay / "js" / ".overlay-copy").write_text("")
    apply_one(project, config)
    assert live.is_file() and not live.is_symlink()

    (overlay / "js" / ".overlay-copy").unlink()
    apply_one(project, config)
    assert live.is_symlink()
    assert live.read_text() == 'var penpotFlags = "enable-mcp";\n'


def test_status_reports_edited_and_missing_copies(tmp: Path, capsys) -> None:
    dest = tmp / "cfg"
    src_dir = make_source(tmp, "personal", targets={"_cfg": str(dest)})
    (src_dir / "_cfg").mkdir(exist_ok=True)
    (src_dir / "_cfg" / ".overlay-copy").write_text("")
    (src_dir / "_cfg" / "a.json").write_text("{}\n")
    (src_dir / "_cfg" / "b.json").write_text("[]\n")
    top = make_top_config(tmp, [{"name": "personal", "path": str(src_dir)}])
    config = AppConfig(sources=[SourceConfig(
        name="personal", path=src_dir, private=True, targets={"_cfg": dest}
    )])
    apply_one(dest, config)

    (dest / "a.json").write_text('{"edited": true}\n')
    (dest / "b.json").unlink()
    rc = cmd_status(argparse.Namespace(config=str(top), path=None))
    out = capsys.readouterr().out

    assert rc == 1
    assert f"edited-copy: {dest / 'a.json'}" in out
    assert f"missing: {dest / 'b.json'}" in out
