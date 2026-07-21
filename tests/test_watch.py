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
