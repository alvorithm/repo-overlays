"""inotify-based watcher: re-apply on source/destination changes."""

from __future__ import annotations

import time
from pathlib import Path

from .apply import apply_all
from .config import AppConfig, TOP_LEVEL_CONFIG
from .events import record
from .manifest import MANIFEST_FILENAME


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
# A directory created after startup (a new _shared/mcp/, a new overlay key)
# needs a watch of its own before edits inside it can trigger anything.
_IN_CREATE = 0x00000100

_DEBOUNCE_S = 0.2
_EXCLUDE_PARTS = frozenset([".git", "_rendered", "__pycache__", "node_modules", ".venv"])
# Artifacts an apply writes into destinations. Every destination gets a
# manifest, so a watch on one would otherwise turn each apply into the trigger
# for the next: the daemon would rewrite the same manifests forever and never
# return to idle.
_EXCLUDE_NAMES = frozenset([MANIFEST_FILENAME, ".divergent"])
_EXCLUDE_SUFFIXES = (".swp", "~", ".proposed")

# How far below a watch a directory created later may still be watched.
#
# A source tree is watched whole. It is small (238 directories across the six
# sources on this machine), every directory in it is hand-authored, and filing
# a note four levels inside a key (`<key>/work/wf-now/<branch>/`) is ordinary
# use. A cap here is a silent hole: the edit fires no event, so the file never
# materialises until something else runs an apply.
#
# A watched_root gets nothing below its top level. That is where the size is,
# and where the destinations are: a watched destination makes apply's own
# manifest write the trigger for the next apply.
_UNBOUNDED: int | None = None
_TOP_LEVEL_ONLY = 0


def _wider(a: int | None, b: int | None) -> int | None:
    """Return the more permissive of two descent budgets (None is unbounded)."""
    if a is _UNBOUNDED or b is _UNBOUNDED:
        return _UNBOUNDED
    return max(a, b)  # ty: ignore[invalid-argument-type]


def _should_ignore(path: str) -> bool:
    """Return True if *path* is none of the watcher's business.

    Takes either a full path or a bare event name. Three kinds of noise:
    git and render output, editor leftovers, and this tool's own writes.
    """
    return (
        any(p in path for p in _EXCLUDE_PARTS)
        or path.endswith(_EXCLUDE_SUFFIXES)
        or Path(path).name in _EXCLUDE_NAMES
    )


def _collect_watch_paths(config: AppConfig) -> list[tuple[Path, int | None]]:
    """Return every directory to watch, each with its remaining descent budget.

    The budget is how deep a directory created *later* may still be watched
    below this one. It mirrors the initial walk, so the watch set stays the
    shape this function describes however long the daemon runs.
    """
    paths: list[tuple[Path, int | None]] = []

    def _add_tree(root: Path, depth: int | None = _UNBOUNDED) -> None:
        if not root.is_dir() or _should_ignore(str(root)):
            return
        paths.append((root, depth))
        if depth is _UNBOUNDED or depth > 0:
            for child in root.iterdir():
                # Symlinked directories are not followed, here or in
                # `_watch_tree`: the walk is unbounded for a source, so a link
                # back to an ancestor would recurse until the stack gives out.
                if child.is_dir() and not child.is_symlink():
                    _add_tree(child, depth if depth is _UNBOUNDED else depth - 1)

    for src in config.sources:
        _add_tree(src.path)
        # The parent, non-recursively: a rename of the source directory itself
        # is reported to its parent's watch, not to its own. $HOME is excluded —
        # watching it would wake the debounce on every stray file.
        parent = src.path.parent
        if parent != Path.home() and parent.is_dir():
            paths.append((parent, _TOP_LEVEL_ONLY))
    for root in config.all_watched_roots:
        if root.is_dir():
            paths.append((root, _TOP_LEVEL_ONLY))
    top = TOP_LEVEL_CONFIG.expanduser()
    if top.exists():
        paths.append((top.parent, _TOP_LEVEL_ONLY))
    return paths


def _watch_tree(
    root: Path,
    budget: int | None,
    wd_paths: dict[int, Path],
    wd_budget: dict[int, int | None],
    inotify,
) -> int:
    """Watch *root* and every subdirectory of it already on disk. Returns the count.

    The descendants matter because of a race that `mkdir -p a/b/c`, `git
    clone` and `cp -r` all lose: the CREATE of `a` reaches us only after `b`
    and `c` exist, and their own CREATEs went to a watch that did not exist
    yet. Watching just `a` would leave the tree permanently half-seen, so the
    catch-up walk is the only way a directory tree created in one go ends up
    fully watched.
    """
    if _should_ignore(str(root)) or not root.is_dir():
        return 0
    try:
        wd = inotify.add_watch(str(root), _WATCH_FLAGS)
    except OSError:
        return 0
    wd_paths[wd] = root
    wd_budget[wd] = _wider(wd_budget.get(wd), budget)
    added = 1
    if budget is _UNBOUNDED or budget > 0:
        child_budget = budget if budget is _UNBOUNDED else budget - 1
        try:
            children = sorted(root.iterdir())
        except OSError:
            return added
        for child in children:
            # Symlinked directories are not followed: an unbounded walk would
            # otherwise recurse through a link back to an ancestor.
            if child.is_dir() and not child.is_symlink():
                added += _watch_tree(child, child_budget, wd_paths, wd_budget, inotify)
    return added


def _watch_created_dir(
    event, wd_paths: dict[int, Path], wd_budget: dict[int, int | None], inotify
) -> bool:
    """Watch a directory created after startup, so edits inside it re-apply.

    The initial watch set is a one-shot walk; a directory that appears later
    (a new ``_shared/`` subtree, a new overlay key) is invisible to inotify
    until the watcher restarts. Watching it as it appears keeps partial edits
    in new directories on the re-apply path without a service restart.

    The parent's descent budget bounds this, and the bound is the whole point:
    a watched_root is watched at its top level, so the repos under it stay
    unwatched however they are created, while a source tree carries the same
    unbounded claim the initial walk gives it. Without the bound, one `git
    clone` or one `pnpm install` under a watched root pulls a whole checkout
    into the watch set, and any destination watched that way makes the tool's
    own manifest write the trigger for the next apply.

    Returns True if any watch was added, which the caller treats as an event
    in its own right: the files that arrived during the race fired nothing, so
    the apply that materialises them has to be scheduled here.
    """
    if not (event.mask & _IN_ISDIR) or not (event.mask & (_IN_CREATE | _IN_MOVED_TO)):
        return False
    parent = wd_paths.get(event.wd)
    budget = wd_budget.get(event.wd, _TOP_LEVEL_ONLY)
    if parent is None or (budget is not _UNBOUNDED and budget <= 0):
        return False
    child_budget = budget if budget is _UNBOUNDED else budget - 1
    added = _watch_tree(parent / event.name, child_budget, wd_paths, wd_budget, inotify)
    return added > 0


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
    wd_budget: dict[int, int | None] = {}
    for path, budget in watch_paths:
        try:
            # Raw mask, not a library constant: inotify_simple ≥2.0 moved
            # ALL_EVENTS from `flags` to `masks`, and _WATCH_FLAGS is the
            # narrower set we actually want anyway.
            wd = inotify.add_watch(str(path), _WATCH_FLAGS)
        except OSError:
            continue
        wd_paths[wd] = path
        # One directory can arrive twice (a source that is also a watched_root)
        # and inotify returns the same wd for both. Keep the wider budget, so
        # the recursive claim wins over the top-level one.
        wd_budget[wd] = _wider(wd_budget.get(wd, _TOP_LEVEL_ONLY), budget)

    pending = False
    last_event_t = 0.0
    pending_moves: dict[int, Path] = {}

    while True:
        events = inotify.read(timeout=int(_DEBOUNCE_S * 1000))
        watched_more = False
        for e in events:
            _note_rename(e, wd_paths, pending_moves)
            watched_more |= _watch_created_dir(e, wd_paths, wd_budget, inotify)
        if events:
            # A directory tree that appears in one go keeps re-arming the
            # debounce as its subdirectories are caught up, so the apply lands
            # after the last file, not in the middle of the copy.
            relevant = [e for e in events if not _should_ignore(e.name)]
            if relevant or watched_more:
                pending = True
                last_event_t = time.monotonic()

        if pending and (time.monotonic() - last_event_t) >= _DEBOUNCE_S:
            pending = False
            apply_all(config)
