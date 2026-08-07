"""Watcher wiring tests.

Regression: watch() called ``inotify_simple.flags.ALL_EVENTS``, which does not
exist in inotify_simple ≥2.0 (it lives in ``masks``).  Under systemd this
turned into a restart loop that re-ran apply_all every RestartSec.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from repo_overlays.config import AppConfig, SourceConfig
from repo_overlays.watch import _WATCH_FLAGS, watch
from tests.conftest import make_source


class _StubINotify:
    """Records add_watch calls; ends the watch loop on first read()."""

    def __init__(self) -> None:
        self.watched: list[tuple[str, int]] = []

    def add_watch(self, path: str, mask: int) -> int:
        self.watched.append((path, mask))
        return len(self.watched)

    def read(self, timeout: int | None = None) -> list:
        raise KeyboardInterrupt


def test_watch_registers_watches_with_supported_mask(
    tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """watch() adds watches using the raw event mask, not a library constant.

    ``flags`` in inotify_simple 2.x carries no ALL_EVENTS; the stub mirrors that
    so any reintroduction of the attribute access fails here.
    """
    stub_inotify = _StubINotify()
    module = types.ModuleType("inotify_simple")
    module.INotify = lambda: stub_inotify  # type: ignore[attr-defined]
    module.flags = type("flags", (), {})  # no ALL_EVENTS, as in ≥2.0
    monkeypatch.setitem(sys.modules, "inotify_simple", module)

    src = make_source(tmp, "personal")
    config = AppConfig(sources=[SourceConfig(name="personal", path=src)])
    with pytest.raises(KeyboardInterrupt):
        watch(config)

    assert stub_inotify.watched, "no inotify watches registered"
    assert all(mask == _WATCH_FLAGS for _p, mask in stub_inotify.watched)


class _Event:
    """Minimal stand-in for inotify_simple's Event tuple."""

    def __init__(self, wd: int, mask: int, cookie: int, name: str) -> None:
        self.wd, self.mask, self.cookie, self.name = wd, mask, cookie, name


def test_directory_rename_is_recorded_as_a_pair(tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A moved directory is logged as (old, new), the form a rewrite needs.

    inotify reports a rename as MOVED_FROM + MOVED_TO sharing a cookie; the
    pair is what makes a stale-path rewrite exact rather than guessed.
    """
    from repo_overlays.events import log_path
    from repo_overlays.watch import _IN_ISDIR, _IN_MOVED_FROM, _IN_MOVED_TO, _note_rename

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp / "state"))
    wd_paths = {1: tmp / "Overlays"}
    pending: dict[int, Path] = {}

    _note_rename(_Event(1, _IN_MOVED_FROM | _IN_ISDIR, 42, "ai-overlay"), wd_paths, pending)
    assert not log_path().exists(), "half a rename is not a rename"
    _note_rename(_Event(1, _IN_MOVED_TO | _IN_ISDIR, 42, "defaults"), wd_paths, pending)

    line = log_path().read_text().strip().split("\t")
    assert line[1] == "rename"
    assert line[2] == str(tmp / "Overlays" / "ai-overlay")
    assert line[3] == str(tmp / "Overlays" / "defaults")


class _Inotify:
    """Records add_watch calls and hands out increasing watch descriptors."""

    def __init__(self) -> None:
        self.watched: list[tuple[str, int]] = []

    def add_watch(self, path: str, mask: int) -> int:
        self.watched.append((path, mask))
        return len(self.watched) + 1


def test_created_directory_gets_a_watch(tmp: Path) -> None:
    """A directory created after startup is watched immediately.

    The initial watch set is a one-shot walk, so a new ``_shared/mcp/`` or a
    new overlay key would otherwise be invisible to inotify until the watcher
    restarted — partial edits inside it would never re-apply.
    """
    from repo_overlays.watch import _IN_CREATE, _IN_ISDIR, _UNBOUNDED, _watch_created_dir

    src = tmp / "Overlays" / "defaults"
    src.mkdir(parents=True)
    (src / "_shared").mkdir()  # the event follows the on-disk mkdir
    wd_paths = {1: src}
    wd_budget = {1: _UNBOUNDED}

    ino = _Inotify()
    _watch_created_dir(_Event(1, _IN_CREATE | _IN_ISDIR, 0, "_shared"), wd_paths, wd_budget, ino)
    assert wd_paths[2] == src / "_shared"
    assert ino.watched == [(str(src / "_shared"), _WATCH_FLAGS)]
    assert wd_budget[2] is _UNBOUNDED, "a source tree's claim carries down"

    # File creates and ignored dirs (git dirs, render output) must not add watches.
    _watch_created_dir(_Event(1, _IN_CREATE, 0, "note.md"), wd_paths, wd_budget, ino)
    _watch_created_dir(_Event(1, _IN_CREATE | _IN_ISDIR, 0, "_rendered"), wd_paths, wd_budget, ino)
    _watch_created_dir(_Event(1, _IN_CREATE | _IN_ISDIR, 0, ".git"), wd_paths, wd_budget, ino)
    assert len(ino.watched) == 1, ino.watched


def test_repo_created_under_a_watched_root_is_not_descended_into(tmp: Path) -> None:
    """A watched_root stays top-level, however its children come into being.

    Regression: `_watch_created_dir` used to watch every directory created
    under any watch, unbounded. One clone under a watched root then dragged
    the whole checkout in (`node_modules` and all), and once a *destination*
    was watched, apply's own manifest write scheduled the next apply. The
    watch set grew 166 → 1141 and the daemon burned a core indefinitely.
    """
    from repo_overlays.watch import (
        _IN_CREATE,
        _IN_ISDIR,
        _TOP_LEVEL_ONLY,
        _watch_created_dir,
    )

    code = tmp / "Code"
    (code / "newrepo").mkdir(parents=True)
    wd_paths = {1: code}
    wd_budget = {1: _TOP_LEVEL_ONLY}

    ino = _Inotify()
    _watch_created_dir(_Event(1, _IN_CREATE | _IN_ISDIR, 0, "newrepo"), wd_paths, wd_budget, ino)

    assert ino.watched == [], "a repo under a watched_root must not be watched"
    assert 2 not in wd_paths


def test_a_source_is_watched_to_any_depth(tmp: Path) -> None:
    """A note filed deep inside a key still reaches the watcher.

    Regression: the walk stopped at depth 3, which cuts through the house
    layout for working notes (`<key>/wip.local/done/<yyyy-mm>-<set>/demo/`, depth 4). Files
    there fire events at their own directory, so a cap there is a silent hole:
    the edit never re-applies, and nothing reports the omission.
    """
    from repo_overlays.watch import _IN_CREATE, _IN_ISDIR, _UNBOUNDED, _watch_created_dir

    src = tmp / "Overlays" / "beadpot-docs"
    branch_dir = src / "beadpot" / "wip.local" / "done" / "2026-08-feature-x" / "detail"
    branch_dir.mkdir(parents=True)

    ino = _Inotify()
    wd_paths, wd_budget = {1: src}, {1: _UNBOUNDED}

    # One event for the top of the new tree, as `mkdir -p` delivers it.
    added = _watch_created_dir(
        _Event(1, _IN_CREATE | _IN_ISDIR, 0, "beadpot"), wd_paths, wd_budget, ino
    )

    assert added is True, "the caller needs to know an apply is owed"
    watched = {p for p, _m in ino.watched}
    assert watched == {
        str(src / "beadpot"),
        str(src / "beadpot" / "wip.local"),
        str(src / "beadpot" / "wip.local" / "done"),
        str(src / "beadpot" / "wip.local" / "done" / "2026-08-feature-x"),
        str(branch_dir),
    }, "the whole subtree, at any depth"


def test_a_tree_created_in_one_go_is_caught_up(tmp: Path) -> None:
    """`mkdir -p a/b/c` loses a race that only a catch-up walk can win.

    The CREATE of `a` reaches the watcher after `b` and `c` already exist, so
    their own CREATEs went to watches that did not exist yet. Watching `a`
    alone leaves the tree half-seen: a note written into `c` fires nothing,
    never materialises, and nothing reports the omission. Observed live on
    `mkdir -p <source>/<key>/wip.local/done/<yyyy-mm>-<set>`.
    """
    from repo_overlays.watch import _IN_CREATE, _IN_ISDIR, _UNBOUNDED, _watch_created_dir

    src = tmp / "Overlays" / "defaults"
    (src / "keydir" / "wip.local" / "done" / "2026-08-branch-x").mkdir(parents=True)

    ino = _Inotify()
    wd_paths, wd_budget = {1: src}, {1: _UNBOUNDED}
    # Only the top directory's event ever arrives; the rest were lost.
    _watch_created_dir(_Event(1, _IN_CREATE | _IN_ISDIR, 0, "keydir"), wd_paths, wd_budget, ino)

    assert str(src / "keydir" / "wip.local" / "done" / "2026-08-branch-x") in {p for p, _m in ino.watched}


def test_initial_walk_covers_a_deep_source_but_not_a_watched_root(tmp: Path) -> None:
    """The two tree kinds get opposite treatment, and the walk proves it."""
    from repo_overlays.config import AppConfig, SourceConfig
    from repo_overlays.watch import _TOP_LEVEL_ONLY, _UNBOUNDED, _collect_watch_paths

    src = make_source(tmp, "defaults", watched_roots=[str(tmp / "Code")])
    deep = src / "beadpot" / "wip.local" / "done" / "2026-08-feature-x"
    deep.mkdir(parents=True)
    (tmp / "Code" / "somerepo" / "node_modules" / "pkg").mkdir(parents=True)

    config = AppConfig(
        sources=[SourceConfig(name="defaults", path=src, watched_roots=[tmp / "Code"])]
    )
    got = dict(_collect_watch_paths(config))

    assert got[deep] is _UNBOUNDED, "a source is watched whole"
    assert got[tmp / "Code"] == _TOP_LEVEL_ONLY
    assert tmp / "Code" / "somerepo" not in got, "a repo under a watched_root is not watched"
    assert not any("node_modules" in str(p) for p in got)


def test_a_symlink_loop_inside_a_source_terminates(tmp: Path) -> None:
    """An unbounded walk must not recurse forever through a self-referring link."""
    from repo_overlays.config import AppConfig, SourceConfig
    from repo_overlays.watch import _collect_watch_paths

    src = make_source(tmp, "defaults")
    (src / "keydir").mkdir()
    (src / "keydir" / "loop").symlink_to(src, target_is_directory=True)

    config = AppConfig(sources=[SourceConfig(name="defaults", path=src)])
    got = dict(_collect_watch_paths(config))

    assert src / "keydir" in got


def test_own_manifest_write_is_not_an_event(tmp: Path) -> None:
    """The artifacts apply writes must never trigger the apply that writes them.

    A destination that ends up watched (a fixed target inside a source's
    parent, say) would otherwise loop: write manifest → event → apply → write
    manifest. Belt to `apply_all`'s braces, which is not to write at all when
    there is nothing to do.
    """
    from repo_overlays.manifest import MANIFEST_FILENAME
    from repo_overlays.watch import _should_ignore

    assert _should_ignore(MANIFEST_FILENAME)
    assert _should_ignore(str(tmp / "Code" / "proj" / MANIFEST_FILENAME))
    assert _should_ignore("CLAUDE.md.proposed")
    assert _should_ignore(".divergent")
    # A skip marker is a real instruction: creating one must still re-apply,
    # so that the destination's links are withdrawn.
    assert not _should_ignore(".repo-overlays-skip")
    assert not _should_ignore("AGENTS.md")


def test_file_moves_and_unpaired_moves_are_not_recorded(tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only completed *directory* moves are events; editor churn is not."""
    from repo_overlays.events import log_path
    from repo_overlays.watch import _IN_ISDIR, _IN_MOVED_FROM, _IN_MOVED_TO, _note_rename

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp / "state"))
    wd_paths = {1: tmp / "Code"}
    pending: dict[int, Path] = {}

    # A file rename (no ISDIR) — editors do this constantly.
    _note_rename(_Event(1, _IN_MOVED_FROM, 7, "notes.md"), wd_paths, pending)
    _note_rename(_Event(1, _IN_MOVED_TO, 7, "notes.md~"), wd_paths, pending)
    # A directory moved *out* of the watched set: no destination to record.
    _note_rename(_Event(1, _IN_MOVED_FROM | _IN_ISDIR, 9, "gone"), wd_paths, pending)

    assert not log_path().exists()
