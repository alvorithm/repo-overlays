"""Key and destination resolution: map a path to (key, dest_root, is_fixed)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Iterator

from .config import AppConfig


def _git_toplevel(path: Path) -> Path | None:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    return Path(result.stdout.strip()) if result.returncode == 0 else None


def _git_remote_candidates(toplevel: Path) -> tuple[str | None, str | None]:
    """Return ``(owner_repo slug, bare repo name)`` from origin, or (None, None).

    A linked worktree shares its origin with the main checkout, so this
    resolves the repo identity regardless of the worktree's directory name.
    """
    result = subprocess.run(
        ["git", "-C", str(toplevel), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None, None
    url = result.stdout.strip()
    # git@host:owner/repo.git  or  https://host/owner/repo.git  ->  owner/repo
    url = re.sub(r"^(git@|https?://)([^:/]+)[:/]", "", url)
    path = re.sub(r"\.git$", "", url)
    return path.replace("/", "_"), path.rsplit("/", 1)[-1]


def _collect_project_keys(config: AppConfig) -> set[str]:
    """Return the set of non-fixed overlay key names across all sources."""
    keys: set[str] = set()
    for src in config.sources:
        if not src.path.is_dir():
            continue
        for child in src.path.iterdir():
            if child.is_dir() and not child.name.startswith("_") and child.name not in src.ignore_keys:
                keys.add(child.name)
    return keys


def _resolve_project_key(toplevel: Path, config: AppConfig) -> str:
    """Resolve a project overlay key for a git toplevel.

    Match order against the overlay keys present in some source:
      1. git remote slug (``owner_repo``),
      2. toplevel directory basename,
      3. bare git remote repo name (``repo``).

    (3) makes linked worktrees resolve correctly even when their directory is
    not named after the repo — e.g. ``~/Code/worktrees/penpot-feature`` or
    ``<repo>/.claude/worktrees/<name>``. A worktree shares origin with its main
    checkout, so the bare repo name still matches the ``repo`` overlay key.
    Returns the first candidate that matches; falls back to the slug (else
    basename) when none match.
    """
    project_keys = _collect_project_keys(config)
    remote_slug, remote_repo = _git_remote_candidates(toplevel)
    basename_key = toplevel.name
    for key in (remote_slug, basename_key, remote_repo):
        if key and key in project_keys:
            return key
    return remote_slug or basename_key


def resolve_key_dest(
    path: Path,
    config: AppConfig,
) -> tuple[str, Path, bool] | None:
    """Return ``(key, dest_root, is_fixed)`` for a given path, or None.

    Resolution order:
    1. Longest-prefix match against unified [targets].
    2. Git remote slug (owner_repo).
    3. Git toplevel basename.
    """
    path = path.resolve()
    targets = config.unified_targets

    # 1. Fixed-target longest-prefix match.
    best_key: str | None = None
    best_len = -1
    for key, tpath in targets.items():
        tpath_r = tpath.resolve()
        try:
            path.relative_to(tpath_r)
            match_len = len(str(tpath_r))
            if match_len > best_len:
                best_key = key
                best_len = match_len
        except ValueError:
            continue
    if best_key is not None:
        return best_key, targets[best_key], True

    # 2 & 3. Git-based resolution.
    toplevel = _git_toplevel(path)
    if toplevel is None:
        return None
    key = _resolve_project_key(toplevel, config)
    return key, toplevel, False


_MAX_SEARCH_DEPTH = 4


def _linked_worktrees(toplevel: Path) -> list[Path]:
    """Return the linked worktrees of the repo at *toplevel* (git's own list).

    Asking git is both exhaustive and O(1) in filesystem work: worktrees are
    registered in the common dir, so they are found wherever they live — inside
    the repo, under a watched root, or anywhere else on disk.
    """
    result = subprocess.run(
        ["git", "-C", str(toplevel), "worktree", "list", "--porcelain"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    return [
        Path(line[len("worktree ") :].strip())
        for line in result.stdout.splitlines()
        if line.startswith("worktree ")
    ]


def _iter_repo_roots(root: Path, depth: int = _MAX_SEARCH_DEPTH) -> Iterator[Path]:
    """Yield directories under *root* that hold a ``.git`` entry.

    Descent stops at a repository: its worktrees come from
    :func:`_linked_worktrees`, so there is no reason to walk (potentially
    enormous) working trees.  This is what keeps discovery off ``node_modules``
    and friends.
    """
    if depth < 0 or not root.is_dir():
        return
    try:
        children = list(root.iterdir())
    except OSError:
        return
    if any(c.name == ".git" for c in children):
        yield root
        return
    for child in children:
        if child.is_dir() and not child.is_symlink() and not child.name.startswith("."):
            yield from _iter_repo_roots(child, depth - 1)


def iter_all_destinations(config: AppConfig) -> Iterator[tuple[str, Path, bool]]:
    """Yield ``(key, dest_root, is_fixed)`` for every known destination.

    Covers fixed targets + every git repo under watched_roots (depth ≤ 4) and
    every linked worktree of those repos, wherever it is checked out.
    """
    targets = config.unified_targets
    for key, tpath in targets.items():
        if tpath.exists():
            yield key, tpath, True

    seen_tops: set[Path] = set()
    for root in config.all_watched_roots:
        for repo_root in _iter_repo_roots(root):
            for toplevel in [repo_root, *_linked_worktrees(repo_root)]:
                if toplevel in seen_tops or not toplevel.is_dir():
                    continue
                seen_tops.add(toplevel)
                key = _resolve_project_key(toplevel, config)
                yield key, toplevel, False
