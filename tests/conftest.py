"""Shared fixtures for repo-overlays tests.

All temporary directories are created under /tmp to satisfy the constraint that
test file creation/deletion happens there.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_side_effects(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Keep the suite out of the developer's session and state directory.

    Drift fixtures used to fire real `notify-send` popups and append to the
    real ~/.local/state/repo-overlays/events.log while the tests ran.
    """
    monkeypatch.setenv("REPO_OVERLAYS_NO_NOTIFY", "1")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path_factory.mktemp("state-")))


@pytest.fixture()
def tmp(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Return a fresh temp dir under /tmp."""
    return tmp_path_factory.mktemp("repo-overlays-", numbered=True)


def make_source(
    root: Path,
    name: str,
    targets: dict[str, str] | None = None,
    watched_roots: list[str] | None = None,
    private: bool = False,
) -> Path:
    """Create a minimal source directory with config.toml."""
    src = root / name
    src.mkdir(parents=True, exist_ok=True)
    (src / "_shared").mkdir(exist_ok=True)
    (src / "_rendered").mkdir(exist_ok=True)

    toml_lines = []
    if watched_roots:
        entries = ", ".join(f'"{r}"' for r in watched_roots)
        toml_lines.append(f"watched_roots = [{entries}]")
    if targets:
        toml_lines.append("[targets]")
        for k, v in targets.items():
            toml_lines.append(f'{k} = "{v}"')
    (src / "config.toml").write_text("\n".join(toml_lines) + "\n")
    return src


def make_top_config(root: Path, sources: list[dict]) -> Path:
    """Create ~/.config/repo-overlays/config.toml equivalent in root."""
    lines = []
    for s in sources:
        lines.append("[[sources]]")
        lines.append(f'name = "{s["name"]}"')
        lines.append(f'path = "{s["path"]}"')
        if s.get("private"):
            lines.append("private = true")
        if s.get("remote"):
            lines.append(f'remote = "{s["remote"]}"')
        lines.append("")
    cfg = root / "top_config.toml"
    cfg.write_text("\n".join(lines))
    return cfg
