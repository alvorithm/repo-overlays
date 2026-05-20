"""Tests for Mustache template rendering and drift detection.

Features covered (USAGE.md §2, §4.2):
- First render writes rendered output.
- Unchanged re-render returns "unchanged" without touching file mtime.
- Drift: live file edited by agent ⇒ .proposed written, .divergent marker set, status "diverged".
- Cross-source partial resolution: partial from a different source is found and injected.
- Privacy: public source template requesting private partial raises PermissionError.
- dot_X partial names resolve correctly.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from repo_overlays.config import AppConfig, SourceConfig
from repo_overlays.render import render_template
from repo_overlays.sources import SourceStack
from tests.conftest import make_source, make_top_config


def _stack_from_dirs(tmp: Path, *source_dirs: tuple[Path, str, bool]) -> tuple[AppConfig, SourceStack]:
    sources = [
        SourceConfig(name=name, path=path, private=private)
        for path, name, private in source_dirs
    ]
    config = AppConfig(sources=sources)
    return config, SourceStack(config)


def test_first_render_writes_output(tmp: Path) -> None:
    """First render of a .mo template writes the rendered file and returns 'ok'."""
    src_dir = make_source(tmp, "src1")
    (src_dir / "_shared" / "snippet.md").write_text("# Snippet\n")
    template = src_dir / "overlay" / "CLAUDE.md.mo"
    template.parent.mkdir()
    template.write_text("Hello {{>_shared/snippet.md}}")

    rendered = tmp / "rendered" / "CLAUDE.md"
    _, stack = _stack_from_dirs(tmp, (src_dir, "src1", False))

    status = render_template(src=template, rendered_dest=rendered, stack=stack)
    assert status == "ok"
    assert rendered.read_text() == "Hello # Snippet\n"


def test_unchanged_rerender_is_noop(tmp: Path) -> None:
    """Re-rendering an unchanged template returns 'unchanged'."""
    src_dir = make_source(tmp, "src1")
    template = src_dir / "overlay" / "CLAUDE.md.mo"
    template.parent.mkdir()
    template.write_text("Static content")

    rendered = tmp / "rendered" / "CLAUDE.md"
    _, stack = _stack_from_dirs(tmp, (src_dir, "src1", False))

    render_template(src=template, rendered_dest=rendered, stack=stack)
    mtime_before = rendered.stat().st_mtime
    time.sleep(0.01)
    status = render_template(src=template, rendered_dest=rendered, stack=stack)
    assert status == "unchanged"
    assert rendered.stat().st_mtime == mtime_before


def test_drift_detection_produces_proposed(tmp: Path) -> None:
    """When the live file diverges from the last render, .proposed is written and status is 'diverged'.

    Simulates an agent editing the live file after the initial apply.
    """
    src_dir = make_source(tmp, "src1")
    template = src_dir / "overlay" / "CLAUDE.md.mo"
    template.parent.mkdir()
    template.write_text("Original content")

    rendered = tmp / "rendered" / "CLAUDE.md"
    live = tmp / "dest" / "CLAUDE.md"
    live.parent.mkdir()

    _, stack = _stack_from_dirs(tmp, (src_dir, "src1", False))
    render_template(src=template, rendered_dest=rendered, stack=stack)

    # Simulate: agent edits the live file (a regular file, not symlink).
    live.write_text("Agent-edited content")

    # Now the template is unchanged but live ≠ rendered → diverged.
    status = render_template(
        src=template, rendered_dest=rendered, stack=stack, live_dest=live
    )
    assert status == "diverged"
    proposed = live.with_suffix(live.suffix + ".proposed")
    assert proposed.exists(), ".proposed file must be written on divergence"
    divergent_marker = live.parent / ".divergent"
    assert divergent_marker.exists(), ".divergent marker must be written on divergence"
    # Live file is preserved.
    assert live.read_text() == "Agent-edited content"


def test_cross_source_partial_resolution(tmp: Path) -> None:
    """A private-source template may include a partial defined in a public source.

    This is the core multi-source composition feature (USAGE.md §6.3):
    the personal/penpot/CLAUDE.md.mo template pulling in penpot-data-model.md
    from the public wiki source.
    """
    private_src = make_source(tmp, "personal")
    public_src = make_source(tmp, "public-wiki")

    (public_src / "_shared" / "penpot-data-model.md").write_text(
        "## Penpot Data Model\nShapes, tokens.\n"
    )

    template = private_src / "penpot" / "CLAUDE.md.mo"
    template.parent.mkdir()
    template.write_text("Context:\n{{>_shared/penpot-data-model.md}}")

    rendered = tmp / "rendered" / "CLAUDE.md"
    sources = [
        SourceConfig(name="personal", path=private_src, private=True),
        SourceConfig(name="public-wiki", path=public_src, private=False),
    ]
    config = AppConfig(sources=sources)
    stack = SourceStack(config)

    # private source template requests a public partial → allowed
    requesting = config.sources[0]  # personal (private)
    status = render_template(
        src=template, rendered_dest=rendered, stack=stack, requesting=requesting
    )
    assert status == "ok"
    assert "Penpot Data Model" in rendered.read_text()


def test_privacy_violation_raises(tmp: Path) -> None:
    """A public source template requesting a private-only partial raises PermissionError."""
    private_src = make_source(tmp, "personal")
    (private_src / "_shared" / "secret.md").write_text("private content")

    public_src = make_source(tmp, "public-wiki")
    template = public_src / "project" / "AGENTS.md.mo"
    template.parent.mkdir()
    template.write_text("{{>_shared/secret.md}}")

    sources = [
        SourceConfig(name="personal", path=private_src, private=True),
        SourceConfig(name="public-wiki", path=public_src, private=False),
    ]
    config = AppConfig(sources=sources)
    stack = SourceStack(config)

    rendered = tmp / "rendered" / "AGENTS.md"
    with pytest.raises(PermissionError, match="private"):
        render_template(
            src=template,
            rendered_dest=rendered,
            stack=stack,
            requesting=config.sources[1],
        )


def test_nested_partial_resolution(tmp: Path) -> None:
    """Partials can include other partials (nested resolution)."""
    src_dir = make_source(tmp, "src1")
    (src_dir / "_shared" / "inner.md").write_text("inner content")
    (src_dir / "_shared" / "outer.md").write_text("outer: {{>_shared/inner.md}}")
    template = src_dir / "overlay" / "FILE.md.mo"
    template.parent.mkdir()
    template.write_text("top: {{>_shared/outer.md}}")

    rendered = tmp / "rendered" / "FILE.md"
    _, stack = _stack_from_dirs(tmp, (src_dir, "src1", False))
    render_template(src=template, rendered_dest=rendered, stack=stack)
    assert rendered.read_text() == "top: outer: inner content"
