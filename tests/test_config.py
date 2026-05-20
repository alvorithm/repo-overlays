"""Tests for config loading, validation, and the unified-targets / privacy rules.

Features covered (USAGE.md §6.2, §6.3):
- Top-level config loads multiple sources.
- unified_targets raises when two sources declare same key with conflicting paths.
- Privacy: public source may not declare same target key as private source.
- Per-source watched_roots and targets are merged correctly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repo_overlays.config import load_config
from tests.conftest import make_source, make_top_config


def test_load_two_sources(tmp: Path) -> None:
    """Top-level config enumerates and orders sources; both are loaded."""
    s1 = make_source(tmp, "personal", private=True)
    s2 = make_source(tmp, "public-wiki")
    cfg_file = make_top_config(
        tmp,
        [
            {"name": "personal", "path": str(s1), "private": True},
            {"name": "public-wiki", "path": str(s2)},
        ],
    )
    config = load_config(cfg_file)
    assert len(config.sources) == 2
    assert config.sources[0].name == "personal"
    assert config.sources[0].private is True
    assert config.sources[1].name == "public-wiki"
    assert config.sources[1].private is False


def test_unified_targets_merge(tmp: Path) -> None:
    """Sources with non-conflicting targets merge cleanly."""
    fixed = tmp / "fixed_target"
    fixed.mkdir()
    s1 = make_source(tmp, "src1", targets={"_claude": str(fixed)})
    cfg_file = make_top_config(tmp, [{"name": "src1", "path": str(s1)}])
    config = load_config(cfg_file)
    assert config.unified_targets["_claude"] == fixed


def test_unified_targets_conflict_raises(tmp: Path) -> None:
    """Same target key bound to different paths across sources raises ValueError."""
    t1 = tmp / "target1"
    t2 = tmp / "target2"
    t1.mkdir()
    t2.mkdir()
    s1 = make_source(tmp, "src1", targets={"_claude": str(t1)})
    s2 = make_source(tmp, "src2", targets={"_claude": str(t2)})
    cfg_file = make_top_config(
        tmp,
        [{"name": "src1", "path": str(s1)}, {"name": "src2", "path": str(s2)}],
    )
    with pytest.raises(ValueError, match="_claude"):
        load_config(cfg_file)


def test_missing_top_config_returns_empty(tmp: Path) -> None:
    """When no top-level config exists, an empty AppConfig is returned gracefully."""
    config = load_config(tmp / "nonexistent.toml")
    assert config.sources == []


def test_per_source_watched_roots(tmp: Path) -> None:
    """watched_roots declared in per-source config.toml are propagated."""
    watched = tmp / "Code"
    watched.mkdir()
    s1 = make_source(tmp, "src1", watched_roots=[str(watched)])
    cfg_file = make_top_config(tmp, [{"name": "src1", "path": str(s1)}])
    config = load_config(cfg_file)
    assert watched in config.sources[0].watched_roots
