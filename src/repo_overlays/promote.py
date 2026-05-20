"""Interactive reconciliation of diverged overlay files."""

from __future__ import annotations

import difflib
import os
import subprocess
import sys
from pathlib import Path

from .config import AppConfig
from .resolve import iter_all_destinations
from .sources import SourceStack


def _find_divergent(dest_root: Path, key: str | None = None) -> list[Path]:
    """Return all .divergent marker files under dest_root (or scoped to key)."""
    search_root = dest_root
    markers = list(search_root.rglob(".divergent"))
    return markers


def _find_proposed_for_marker(marker: Path) -> list[Path]:
    return list(marker.parent.glob("*.proposed"))


def _diff(live: Path, proposed: Path) -> str:
    live_lines = live.read_text().splitlines(keepends=True)
    proposed_lines = proposed.read_text().splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            live_lines,
            proposed_lines,
            fromfile=f"live:{live}",
            tofile=f"proposed:{proposed}",
        )
    )


def promote(config: AppConfig, key: str | None = None) -> int:
    """Interactively reconcile diverged overlays.

    Returns number of items resolved.
    """
    found = 0
    resolved = 0

    for _, dest_root, _ in iter_all_destinations(config):
        markers = _find_divergent(dest_root, key)
        for marker in markers:
            for proposed in _find_proposed_for_marker(marker):
                live = Path(str(proposed)[: -len(".proposed")])
                if not live.exists():
                    marker.unlink(missing_ok=True)
                    proposed.unlink(missing_ok=True)
                    continue

                found += 1
                rel = live.relative_to(dest_root)
                print(f"\n── {rel} ──")
                print(f"  live:     {live}")
                print(f"  proposed: {proposed}")
                print()
                print("[d]iff  [a]ccept proposed  [k]eep live  [e]dit source  [s]kip")

                action = _prompt()
                match action:
                    case "d":
                        diff_text = _diff(live, proposed)
                        pager = os.environ.get("PAGER", "less")
                        try:
                            proc = subprocess.Popen(
                                [pager], stdin=subprocess.PIPE, text=True
                            )
                            proc.communicate(diff_text)
                        except Exception:
                            print(diff_text)
                    case "a":
                        proposed.replace(live)
                        marker.unlink(missing_ok=True)
                        resolved += 1
                        print(f"  accepted: {rel}")
                    case "k":
                        proposed.unlink(missing_ok=True)
                        marker.unlink(missing_ok=True)
                        resolved += 1
                        print(f"  kept live: {rel}")
                    case "e":
                        editor = os.environ.get("EDITOR", "vi")
                        subprocess.run([editor, str(live)])
                    case _:
                        print("  skipped")

    if found == 0:
        print("No divergent overlays.")
    return resolved


def _prompt() -> str:
    try:
        return input("> ").strip().lower()
    except EOFError:
        return "s"
