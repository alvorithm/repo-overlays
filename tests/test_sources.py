"""Tests for SourceStack: key iteration, partial lookup, privacy enforcement.

Features covered (USAGE.md §6.3, PORT.md §2):
- iter_keys() yields keys from all sources without duplicates.
- iter_files_for_key() returns merged file list; later source wins on conflict.
- resolve_partial() finds partial in first source that has it (stack order).
- @name/... syntax targets a specific named source.
- Privacy: public source requesting private partial raises PermissionError.
- Privacy: private source requesting public partial is allowed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repo_overlays.config import AppConfig, SourceConfig
from repo_overlays.sources import SourceStack
from tests.conftest import make_source


def _config(*sources: SourceConfig) -> AppConfig:
    return AppConfig(sources=list(sources))


def test_iter_keys_deduplicates(tmp: Path) -> None:
    """iter_keys() yields each key once even when multiple sources have it."""
    s1 = make_source(tmp, "s1")
    s2 = make_source(tmp, "s2")
    (s1 / "myproject").mkdir()
    (s2 / "myproject").mkdir()
    (s1 / "unique_to_s1").mkdir()

    config = _config(
        SourceConfig(name="s1", path=s1),
        SourceConfig(name="s2", path=s2),
    )
    stack = SourceStack(config)
    keys = list(stack.iter_keys())
    assert keys.count("myproject") == 1
    assert "unique_to_s1" in keys


def test_iter_files_later_source_wins(tmp: Path) -> None:
    """iter_files_for_key() returns the later source's file on path conflict."""
    s1 = make_source(tmp, "s1")
    s2 = make_source(tmp, "s2")
    (s1 / "proj").mkdir()
    (s1 / "proj" / "CLAUDE.md").write_text("from s1")
    (s2 / "proj").mkdir()
    (s2 / "proj" / "CLAUDE.md").write_text("from s2")

    config = _config(
        SourceConfig(name="s1", path=s1),
        SourceConfig(name="s2", path=s2),
    )
    stack = SourceStack(config)
    files = {abs_path.name: abs_path.read_text() for abs_path, _ in stack.iter_files_for_key("proj")}
    assert files["CLAUDE.md"] == "from s2"


def test_resolve_partial_stack_order(tmp: Path) -> None:
    """resolve_partial() picks the first source (in stack order) that has the partial."""
    s1 = make_source(tmp, "s1")
    s2 = make_source(tmp, "s2")
    (s1 / "_shared" / "snippet.md").write_text("from s1")
    (s2 / "_shared" / "snippet.md").write_text("from s2")

    config = _config(
        SourceConfig(name="s1", path=s1),
        SourceConfig(name="s2", path=s2),
    )
    stack = SourceStack(config)
    result = stack.resolve_partial("_shared/snippet.md")
    assert result.read_text() == "from s1"


def test_resolve_partial_named_source(tmp: Path) -> None:
    """@name/_shared/file.md resolves against that specific source."""
    s1 = make_source(tmp, "s1")
    s2 = make_source(tmp, "s2")
    (s1 / "_shared" / "snippet.md").write_text("from s1")
    (s2 / "_shared" / "snippet.md").write_text("from s2")

    config = _config(
        SourceConfig(name="s1", path=s1),
        SourceConfig(name="s2", path=s2),
    )
    stack = SourceStack(config)
    result = stack.resolve_partial("@s2/_shared/snippet.md")
    assert result.read_text() == "from s2"


def test_resolve_partial_not_found_raises(tmp: Path) -> None:
    """resolve_partial() raises FileNotFoundError when no source has the partial."""
    s1 = make_source(tmp, "s1")
    config = _config(SourceConfig(name="s1", path=s1))
    stack = SourceStack(config)
    with pytest.raises(FileNotFoundError):
        stack.resolve_partial("_shared/nonexistent.md")


def test_privacy_public_requesting_private_raises(tmp: Path) -> None:
    """Public source requesting a private-only partial raises PermissionError."""
    private_src = make_source(tmp, "personal")
    public_src = make_source(tmp, "wiki")
    (private_src / "_shared" / "secret.md").write_text("secret")

    config = _config(
        SourceConfig(name="personal", path=private_src, private=True),
        SourceConfig(name="wiki", path=public_src, private=False),
    )
    stack = SourceStack(config)
    requesting = config.sources[1]  # wiki (public)
    with pytest.raises(PermissionError, match="private"):
        stack.resolve_partial("_shared/secret.md", requesting_source=requesting)


def test_privacy_private_requesting_public_is_allowed(tmp: Path) -> None:
    """Private source requesting a public partial is permitted."""
    private_src = make_source(tmp, "personal")
    public_src = make_source(tmp, "wiki")
    (public_src / "_shared" / "shared.md").write_text("public content")

    config = _config(
        SourceConfig(name="personal", path=private_src, private=True),
        SourceConfig(name="wiki", path=public_src, private=False),
    )
    stack = SourceStack(config)
    requesting = config.sources[0]  # personal (private)
    result = stack.resolve_partial("_shared/shared.md", requesting_source=requesting)
    assert result.read_text() == "public content"


def test_cross_source_override_is_reported(tmp: Path, capsys: pytest.CaptureFixture) -> None:
    """Two sources providing the same key+path: the loser is named, not silent.

    Overriding is whole-file, so a key served by several sources (guidance in
    one, memory wiring in another) can silently discard settings. USAGE.md has
    documented this warning since the beginning; the code never printed it.
    """
    s1 = make_source(tmp, "guidance")
    s2 = make_source(tmp, "memory")
    for src, body in ((s1, "guidance version"), (s2, "memory version")):
        (src / "beadpot" / "dot_claude").mkdir(parents=True)
        (src / "beadpot" / "dot_claude" / "settings.json").write_text(body)

    stack = SourceStack(AppConfig(sources=[
        SourceConfig(name="guidance", path=s1),
        SourceConfig(name="memory", path=s2),
    ]))
    files = list(stack.iter_files_for_key("beadpot"))

    assert len(files) == 1
    assert files[0][0].read_text() == "memory version"
    err = capsys.readouterr().err
    assert "override: beadpot/dot_claude/settings.json" in err
    assert "memory replaces guidance" in err


def test_editor_backups_and_caches_are_not_materialised(tmp: Path) -> None:
    """`settings.json~`, tool caches and friends never become live files."""
    src = make_source(tmp, "personal")
    (src / "penpot" / "dot_claude").mkdir(parents=True)
    (src / "penpot" / "dot_claude" / "settings.json").write_text("{}")
    (src / "penpot" / "dot_claude" / "settings.json~").write_text("{}")
    (src / "penpot" / "notes.md.swp").write_text("junk")
    (src / "penpot" / "__pycache__").mkdir()
    (src / "penpot" / "__pycache__" / "curate.cpython-313.pyc").write_text("bytecode")
    (src / "penpot" / ".ruff_cache").mkdir()
    (src / "penpot" / ".ruff_cache" / "CACHEDIR.TAG").write_text("x")

    stack = SourceStack(AppConfig(sources=[SourceConfig(name="personal", path=src)]))
    names = {p.name for p, _ in stack.iter_files_for_key("penpot")}
    assert names == {"settings.json"}, names
