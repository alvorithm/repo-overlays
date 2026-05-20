"""Tests for the mise enter-hook integration.

The hook is: enter = 'repo-overlay apply "$PWD" || true'

Three things to verify:
  1. Entering an unwatched directory: apply exits 1 (the hook needs || true).
  2. Entering a watched git repo: apply exits 0 and materialises symlinks.
  3. The live mise config declares the correct hook command.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import make_source, make_top_config


HOOK_COMMAND = 'repo-overlay apply "$PWD" || true'
MISE_CONFIG = Path("~/.config/mise/config.toml").expanduser()
# Use the installed entry-point script (same binary mise/shell would invoke).
_REPO_OVERLAY = Path(sys.executable).parent / "repo-overlay"


# ── helpers ────────────────────────────────────────────────────────────────


def _git_init(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)


def _run_apply(path: Path, config: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(_REPO_OVERLAY), "--config", str(config), "apply", str(path)],
        capture_output=True,
        text=True,
    )


# ── test 1: unwatched dir → exit 1 ────────────────────────────────────────


def test_apply_unwatched_dir_exits_nonzero(tmp_path):
    """apply <dir> returns 1 for a directory with no matching overlay key."""
    src = make_source(tmp_path, "src", watched_roots=[str(tmp_path / "projects")])
    cfg = make_top_config(tmp_path, [{"name": "src", "path": str(src)}])

    plain_dir = tmp_path / "unrelated"
    plain_dir.mkdir()

    result = _run_apply(plain_dir, cfg)
    assert result.returncode == 1
    assert result.stdout == ""   # no output for no-match


# ── test 2: watched git repo → exit 0, symlinks created ───────────────────


def test_apply_watched_repo_materialises_overlay(tmp_path):
    """apply <dir> returns 0 and creates a symlink for a watched project repo."""
    projects = tmp_path / "projects"
    projects.mkdir()
    dest = projects / "myproject"
    _git_init(dest)

    src = make_source(tmp_path, "src", watched_roots=[str(projects)])
    key_dir = src / "myproject"
    key_dir.mkdir()
    (key_dir / "AGENTS.md").write_text("# guidance\n")

    cfg = make_top_config(tmp_path, [{"name": "src", "path": str(src)}])

    result = _run_apply(dest, cfg)
    assert result.returncode == 0
    link = dest / "AGENTS.md"
    assert link.is_symlink(), "expected symlink to be created by apply"
    assert link.read_text() == "# guidance\n"


# ── test 3: mise config declares the right hook ────────────────────────────


@pytest.mark.skipif(
    not MISE_CONFIG.exists(),
    reason="~/.config/mise/config.toml not present",
)
def test_mise_config_has_correct_hook():
    """The live mise config must declare the repo-overlay enter hook."""
    try:
        import tomllib
    except ImportError:
        import tomllib  # type: ignore[no-redef]

    with MISE_CONFIG.open("rb") as f:
        data = tomllib.load(f)

    hooks = data.get("hooks", {})
    assert "enter" in hooks, "mise config missing [hooks] enter key"
    assert "repo-overlay apply" in hooks["enter"], (
        f"enter hook does not call repo-overlay apply: {hooks['enter']!r}"
    )
    assert "|| true" in hooks["enter"], (
        "enter hook must include '|| true' to suppress exit 1 for unwatched dirs"
    )
    settings = data.get("settings", {})
    assert settings.get("experimental") is True, (
        "mise [settings] experimental = true is required for hooks"
    )
