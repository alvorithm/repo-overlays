"""Tests for the manifest read/write/prune API.

Features covered (USAGE.md §7, PORT.md §6):
- Manifest round-trips correctly (read after write).
- prune removes symlinks and updates the manifest.
- Missing manifest returns empty Manifest gracefully.
- schema/updated fields are written.
"""

from __future__ import annotations

from pathlib import Path

from repo_overlays.manifest import (
    MANIFEST_FILENAME,
    LinkRecord,
    Manifest,
    prune,
    read,
    write,
)


def test_write_and_read_roundtrip(tmp: Path) -> None:
    """Manifest written with write() can be read back with read()."""
    dest = tmp / "dest"
    dest.mkdir()
    links = [
        LinkRecord(
            path="CLAUDE.md",
            source="personal",
            key="beadpot",
            target=str(tmp / "_rendered" / "beadpot" / "CLAUDE.md"),
        ),
        LinkRecord(
            path=".claude/commands/release.md",
            source="beadpot-docs",
            key="beadpot",
            target=str(tmp / "beadpot-docs" / "beadpot" / "dot_claude" / "commands" / "release.md"),
        ),
    ]
    write(dest, links)

    manifest_file = dest / MANIFEST_FILENAME
    assert manifest_file.exists()

    restored = read(dest)
    assert len(restored.links) == 2
    by_path = restored.by_path()
    assert "CLAUDE.md" in by_path
    assert by_path["CLAUDE.md"].source == "personal"
    assert by_path["CLAUDE.md"].key == "beadpot"
    assert ".claude/commands/release.md" in by_path


def test_read_missing_returns_empty(tmp: Path) -> None:
    """read() on a directory without a manifest returns an empty Manifest."""
    dest = tmp / "dest"
    dest.mkdir()
    manifest = read(dest)
    assert manifest.links == []


def test_prune_removes_stale_symlinks(tmp: Path) -> None:
    """prune() removes symlinks no longer in current_paths and updates the manifest."""
    dest = tmp / "dest"
    dest.mkdir()

    # Create two symlinks.
    target1 = tmp / "t1"
    target1.write_text("t1")
    target2 = tmp / "t2"
    target2.write_text("t2")
    link1 = dest / "FILE1.md"
    link2 = dest / "FILE2.md"
    link1.symlink_to(target1)
    link2.symlink_to(target2)

    links = [
        LinkRecord(path="FILE1.md", source="src", key="k", target=str(target1)),
        LinkRecord(path="FILE2.md", source="src", key="k", target=str(target2)),
    ]
    write(dest, links)

    # Keep only FILE1; FILE2 should be pruned.
    pruned = prune(dest, current_paths={"FILE1.md"})

    assert "FILE2.md" in pruned
    assert not link2.exists(), "Pruned symlink must be removed"
    assert link1.is_symlink(), "Kept symlink must remain"

    # Manifest must be updated.
    restored = read(dest)
    paths = {lr.path for lr in restored.links}
    assert "FILE1.md" in paths
    assert "FILE2.md" not in paths


def test_manifest_contains_schema_and_updated(tmp: Path) -> None:
    """Written manifest contains 'schema' and 'updated' fields."""
    dest = tmp / "dest"
    dest.mkdir()
    write(dest, [])
    content = (dest / MANIFEST_FILENAME).read_text()
    assert "schema" in content
    assert "updated" in content
