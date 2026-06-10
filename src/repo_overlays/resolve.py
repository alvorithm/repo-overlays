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


def _git_remote_slug(toplevel: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(toplevel), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    url = result.stdout.strip()
    # git@host:owner/repo.git  or  https://host/owner/repo.git
    url = re.sub(r"^(git@|https?://)([^:/]+)[:/]", "", url)
    url = re.sub(r"\.git$", "", url)
    return url.replace("/", "_")


def _collect_project_keys(config: AppConfig) -> set[str]:
    """Return the set of non-fixed overlay key names across all sources."""
    keys: set[str] = set()
    for src in config.sources:
        if not src.path.is_dir():
            continue
        for child in src.path.iterdir():
            if child.is_dir() and not child.name.startswith("_"):
                keys.add(child.name)
    return keys


def _resolve_project_key(toplevel: Path, config: AppConfig) -> str:
    """Resolve a project overlay key for a git toplevel.

    Tries the git remote slug first (owner_repo format), then falls back
    to the toplevel directory basename.  Returns whichever matches an
    overlay key in at least one source; prefers remote slug on tie.
    """
    project_keys = _collect_project_keys(config)
    remote_key = _git_remote_slug(toplevel)
    basename_key = toplevel.name
    # Prefer remote slug, fall back to basename.
    for key in (remote_key, basename_key):
        if key and key in project_keys:
            return key
    # Neither matches — return whatever we have (caller may still use it).
    return remote_key or basename_key


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


def iter_all_destinations(config: AppConfig) -> Iterator[tuple[str, Path, bool]]:
    """Yield ``(key, dest_root, is_fixed)`` for every known destination.

    Covers fixed targets + every git repo under watched_roots (depth ≤ 4).
    """
    targets = config.unified_targets
    for key, tpath in targets.items():
        if tpath.exists():
            yield key, tpath, True

    seen_tops: set[Path] = set()
    for root in config.all_watched_roots:
        if not root.is_dir():
            continue
        for git_dir in root.rglob(".git"):
            # depth cap: count separators relative to root
            rel = git_dir.relative_to(root)
            if len(rel.parts) > 5:
                continue
            if not git_dir.is_dir():
                continue
            toplevel = git_dir.parent
            if toplevel in seen_tops:
                continue
            seen_tops.add(toplevel)
            key = _resolve_project_key(toplevel, config)
            yield key, toplevel, False
