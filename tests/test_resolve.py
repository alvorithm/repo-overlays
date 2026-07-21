"""Destination discovery: repo roots, linked worktrees, and what is not walked."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from repo_overlays.config import AppConfig, SourceConfig
from repo_overlays.resolve import iter_all_destinations
from tests.conftest import make_source


def _git_init(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)


def _commit_empty(path: Path) -> None:
    subprocess.run(["git", "-C", str(path), "commit", "-q", "--allow-empty", "-m", "init"],
                   check=True, capture_output=True,
                   env={**os.environ,
                        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


def _destinations(config: AppConfig) -> set[Path]:
    return {dest for _key, dest, _fixed in iter_all_destinations(config)}


def test_discovers_worktrees_outside_watched_roots(tmp: Path) -> None:
    """Linked worktrees come from git, so they are found wherever they live.

    The worktree here sits outside every watched_root; a filesystem walk of the
    watched roots could not see it.
    """
    watched = tmp / "Code"
    project = watched / "myproject"
    _git_init(project)
    _commit_empty(project)
    worktree = tmp / "Elsewhere" / "myproject-feature"
    subprocess.run(["git", "-C", str(project), "worktree", "add", "-q",
                    "-b", "feature", str(worktree)],
                   check=True, capture_output=True)

    src = make_source(tmp, "personal", watched_roots=[str(watched)])
    config = AppConfig(sources=[SourceConfig(
        name="personal", path=src, watched_roots=[watched])])

    found = _destinations(config)
    assert project in found
    assert worktree in found


def test_discovery_does_not_walk_inside_repositories(tmp: Path) -> None:
    """Descent stops at a repo root: nested heavy trees are never scanned.

    A ``.git``-looking path buried in a dependency tree must not be reported as
    a destination (and, more to the point, must not be walked to find out).
    """
    watched = tmp / "Code"
    project = watched / "myproject"
    _git_init(project)
    buried = project / "node_modules" / "pkg"
    buried.mkdir(parents=True)
    (buried / ".git").mkdir()

    src = make_source(tmp, "personal", watched_roots=[str(watched)])
    config = AppConfig(sources=[SourceConfig(
        name="personal", path=src, watched_roots=[watched])])

    found = _destinations(config)
    assert project in found
    assert buried not in found


def test_discovers_repos_nested_under_watched_root(tmp: Path) -> None:
    """Repos below the first level of a watched root are still discovered."""
    watched = tmp / "Code"
    project = watched / "group" / "sub" / "myproject"
    _git_init(project)

    src = make_source(tmp, "personal", watched_roots=[str(watched)])
    config = AppConfig(sources=[SourceConfig(
        name="personal", path=src, watched_roots=[watched])])

    assert project in _destinations(config)
