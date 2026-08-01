"""Mustache {{>partial}} rendering with cross-source resolution and drift detection."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Literal

from .events import record
from .sources import SourceStack, SourceConfig


RenderStatus = Literal["ok", "diverged", "unchanged", "invalid"]


class _PartialLoader:
    """Resolves {{>ref}} partial names to text across the source stack."""

    def __init__(self, stack: SourceStack, requesting: SourceConfig | None) -> None:
        self._stack = stack
        self._requesting = requesting

    def get(self, name: str) -> str:
        path = self._stack.resolve_partial(name, self._requesting)
        return path.read_text()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def render_bytes(
    src: Path,
    stack: SourceStack,
    requesting: SourceConfig | None = None,
) -> bytes:
    """Resolve a template's partials to the exact bytes a render would write.

    Pure (no filesystem writes); used for content hashing in the manifest so
    an apply can tell whether a live file is current without re-rendering.
    """
    loader = _PartialLoader(stack, requesting)
    return _resolve_partials(src.read_text(), loader).encode()


def render_hash(
    src: Path,
    stack: SourceStack,
    requesting: SourceConfig | None = None,
) -> str:
    """SHA-256 of the render output for *src* (see render_bytes)."""
    return _sha256(render_bytes(src, stack, requesting))


def render_template(
    src: Path,
    rendered_dest: Path,
    stack: SourceStack,
    requesting: SourceConfig | None = None,
    live_dest: Path | None = None,
) -> RenderStatus:
    """Render a *.mo template to rendered_dest; detect drift against live_dest.

    Args:
        src: Source .mo file.
        rendered_dest: Where to write the rendered output (in _rendered/).
        stack: Source stack for partial resolution.
        requesting: Source that owns the template (for privacy checks).
        live_dest: The live symlink target; used for drift detection.

    Returns:
        "ok"       — rendered and written (first render or re-render of unchanged live file).
        "unchanged" — rendered content identical to what was already at rendered_dest.
        "diverged"  — live file has been externally modified; .proposed written.
        "invalid"   — a *.json.mo rendered to unparseable JSON; nothing written.
    """
    rendered_bytes = render_bytes(src, stack, requesting)

    # A template that produces a *.json destination must render to parseable
    # JSON, or the harness reading the live file breaks (dirge would fall back
    # to its Exa default, omp/pi to no servers). Refuse to write anything —
    # the previous render stays live and last-good — and record the event so
    # `repo-overlay status` and the drift digest can surface it.
    if rendered_dest.suffix == ".json":
        try:
            json.loads(rendered_bytes)
        except ValueError as e:
            print(f"  invalid-json: {rendered_dest}: {e}", file=sys.stderr)
            record("invalid-json", str(rendered_dest))
            return "invalid"

    rendered_dest.parent.mkdir(parents=True, exist_ok=True)

    # Drift detection: compare against the live file (not the previous render).
    if live_dest is not None and live_dest.exists() and not live_dest.is_symlink():
        live_bytes = live_dest.read_bytes()
        live_hash = _sha256(live_bytes)

        # Check if live file has diverged from a prior render at rendered_dest.
        prior_render_hash = (
            _sha256(rendered_dest.read_bytes()) if rendered_dest.exists() else None
        )
        if prior_render_hash is not None and live_hash != prior_render_hash:
            # Live file was edited after the last render → diverged.
            proposed = live_dest.with_suffix(live_dest.suffix + ".proposed")
            proposed.write_bytes(rendered_bytes)
            divergent_marker = live_dest.parent / ".divergent"
            divergent_marker.write_text(live_hash)
            _notify(live_dest)
            return "diverged"

    # First render or re-render.
    if rendered_dest.exists() and _sha256(rendered_dest.read_bytes()) == _sha256(rendered_bytes):
        return "unchanged"

    rendered_dest.write_bytes(rendered_bytes)
    return "ok"


_PARTIAL_RE = re.compile(r"\{\{>\s*(.+?)\s*\}\}")


def _resolve_partials(template: str, loader: _PartialLoader) -> str:
    """Recursively resolve {{>ref}} partials using loader."""

    def _replace(m: re.Match) -> str:
        partial_content = loader.get(m.group(1))
        return _resolve_partials(partial_content, loader)

    return _PARTIAL_RE.sub(_replace, template)


def _notify(path: Path) -> None:
    """Raise a desktop notification for drift, and record it.

    Desktop notifications are ephemeral and the interactive apply that emitted
    one may have scrolled away in a terminal nobody is watching, so every
    notification is also appended to a log: "what was that notification?" has
    to be answerable afterwards.
    """
    if os.environ.get("REPO_OVERLAYS_NO_NOTIFY"):
        # Test runs (and any batch/CI use) must not raise desktop notifications
        # on a real session: a drift fixture is not a drift.
        return

    record("drift", str(path))

    try:
        subprocess.run(
            ["notify-send", "-u", "normal", "repo-overlays drift", str(path)],
            check=False,
            capture_output=True,
        )
    except FileNotFoundError:
        pass
