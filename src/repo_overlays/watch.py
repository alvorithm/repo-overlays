"""inotify-based watcher: re-apply on source/destination changes."""

from __future__ import annotations

import time
from pathlib import Path

from .apply import apply_all
from .config import AppConfig, TOP_LEVEL_CONFIG
from .events import record


# Events that should trigger a re-apply.
_WATCH_FLAGS = (
    0x00000008  # IN_CLOSE_WRITE
    | 0x00000100  # IN_CREATE
    | 0x00000200  # IN_DELETE
    | 0x00000040  # IN_MOVED_FROM
    | 0x00000080  # IN_MOVED_TO
)

# Raw inotify bits: a rename arrives as MOVED_FROM + MOVED_TO sharing a cookie,
# and ISDIR distinguishes the directory moves worth recording from the file
# churn every editor produces.
_IN_MOVED_FROM = 0x00000040
_IN_MOVED_TO = 0x00000080
_IN_ISDIR = 0x40000000

_DEBOUNCE_S = 0.2
_EXCLUDE_PARTS = frozenset([".git", "_rendered", "__pycache__"])


def _should_ignore(path: str) -> bool:
    return any(p in path for p in _EXCLUDE_PARTS) or path.endswith((".swp", "~"))


def _collect_watch_paths(config: AppConfig) -> list[Path]:
    paths: list[Path] = []

    def _add_tree(root: Path, depth: int = 3) -> None:
        if not root.is_dir() or _should_ignore(str(root)):
            return
        paths.append(root)
        if depth > 0:
            for child in root.iterdir():
                if child.is_dir():
                    _add_tree(child, depth - 1)

    for src in config.sources:
        _add_tree(src.path)
        # The parent, non-recursively: a rename of the source directory itself
        # is reported to its parent's watch, not to its own. $HOME is excluded —
        # watching it would wake the debounce on every stray file.
        parent = src.path.parent
        if parent != Path.home() and parent.is_dir():
            paths.append(parent)
    for root in config.all_watched_roots:
        if root.is_dir():
            paths.append(root)
    top = TOP_LEVEL_CONFIG.expanduser()
    if top.exists():
        paths.append(top.parent)
    return paths


def _note_rename(event, wd_paths: dict[int, Path], pending: dict[int, Path]) -> None:
    """Record a completed directory rename to the event log.

    Only directory moves, and only when both halves are seen: a directory
    leaving the watched set has no destination to record. Consumers (notably
    the memory system's `curate.py paths --from-log`) turn these into rewrite
    pairs, which is why the pair must be exact rather than inferred.
    """
    if not (event.mask & _IN_ISDIR) or not event.cookie:
        return
    parent = wd_paths.get(event.wd)
    if parent is None:
        return
    if event.mask & _IN_MOVED_FROM:
        pending[event.cookie] = parent / event.name
    elif event.mask & _IN_MOVED_TO:
        old = pending.pop(event.cookie, None)
        if old is not None:
            record("rename", str(old), str(parent / event.name))


def watch(config: AppConfig, once: bool = False) -> None:
    """Watch all sources and watched_roots; re-apply on changes.

    With ``once=True``: apply once and exit (for systemd ExecStart).
    """
    apply_all(config)
    if once:
        return

    try:
        import inotify_simple
    except ImportError:
        raise SystemExit(
            "inotify_simple is required for watch mode: uv add inotify-simple"
        )

    inotify = inotify_simple.INotify()
    watch_paths = _collect_watch_paths(config)
    wd_paths: dict[int, Path] = {}
    for path in watch_paths:
        try:
            # Raw mask, not a library constant: inotify_simple ≥2.0 moved
            # ALL_EVENTS from `flags` to `masks`, and _WATCH_FLAGS is the
            # narrower set we actually want anyway.
            wd_paths[inotify.add_watch(str(path), _WATCH_FLAGS)] = path
        except OSError:
            pass

    pending = False
    last_event_t = 0.0
    pending_moves: dict[int, Path] = {}

    while True:
        events = inotify.read(timeout=int(_DEBOUNCE_S * 1000))
        for e in events:
            _note_rename(e, wd_paths, pending_moves)
        if events:
            relevant = [e for e in events if not _should_ignore(e.name)]
            if relevant:
                pending = True
                last_event_t = time.monotonic()

        if pending and (time.monotonic() - last_event_t) >= _DEBOUNCE_S:
            pending = False
            apply_all(config)
