"""Append-only log of events a user may have to explain afterwards.

Two kinds so far, both ephemeral at the moment they happen and consequential
later: a drift notification (a desktop popup that vanishes, possibly raised by
an apply running in a terminal nobody was watching) and a directory rename
(observed once, but it invalidates every stored path that pointed inside the
moved tree — including paths held outside this tool entirely).

The log is deliberately dumb: tab-separated, append-only, no rotation, no
readers of its own. Consumers pick out the lines they care about — the memory
system's `curate.py paths --from-log` turns `rename` lines into rewrite pairs.
Keeping it that way is what stops this tool from growing knowledge about the
memory system.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path


def log_path() -> Path:
    """Return the event log path (``$XDG_STATE_HOME/repo-overlays/events.log``)."""
    state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(state) / "repo-overlays" / "events.log"


def record(kind: str, *fields: str) -> None:
    """Append one event. Best-effort: logging never breaks an apply."""
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write("\t".join([stamp, kind, *fields]) + "\n")
    except OSError:
        pass
