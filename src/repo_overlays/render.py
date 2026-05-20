"""Mustache {{>partial}} rendering with cross-source resolution and drift detection."""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path
from typing import Literal

from .sources import SourceStack, SourceConfig


RenderStatus = Literal["ok", "diverged", "unchanged"]


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
    """
    loader = _PartialLoader(stack, requesting)
    template_text = src.read_text()
    rendered = _resolve_partials(template_text, loader)
    rendered_bytes = rendered.encode()

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
    try:
        subprocess.run(
            ["notify-send", "-u", "normal", "repo-overlays drift", str(path)],
            check=False,
            capture_output=True,
        )
    except FileNotFoundError:
        pass
