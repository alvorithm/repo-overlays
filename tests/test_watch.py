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
