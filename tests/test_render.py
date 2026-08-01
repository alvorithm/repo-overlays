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

import json
import time
from pathlib import Path

import pytest

from repo_overlays.config import AppConfig, SourceConfig
from repo_overlays.render import render_template
from repo_overlays.sources import SourceStack
from tests.conftest import make_source


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


def test_drift_is_logged_for_later_inspection(tmp: Path, monkeypatch) -> None:
    """Every drift notification is also appended to the event log.

    A desktop notification vanishes; the log is what makes "what was that
    notification?" answerable minutes later.
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp / "state"))
    monkeypatch.delenv("REPO_OVERLAYS_NO_NOTIFY", raising=False)

    src = make_source(tmp, "personal")
    template = src / "guide.md.mo"
    template.write_text("v1\n")
    rendered = src / "_rendered" / "guide.md"
    live = tmp / "live-guide.md"

    stack = SourceStack(AppConfig(sources=[SourceConfig(name="personal", path=src)]))
    assert render_template(src=template, rendered_dest=rendered, stack=stack, live_dest=None) == "ok"

    live.write_text("the agent's own version\n")
    template.write_text("v2\n")
    status = render_template(src=template, rendered_dest=rendered, stack=stack, live_dest=live)
    assert status == "diverged"

    log = tmp / "state" / "repo-overlays" / "events.log"
    assert log.exists(), "drift must be recorded"
    line = log.read_text().strip()
    assert "drift" in line and str(live) in line


def test_json_template_renders_ok(tmp: Path) -> None:
    """A *.json.mo that renders parseable JSON writes normally."""
    src_dir = make_source(tmp, "src1")
    (src_dir / "_shared" / "servers.json").write_text('"penpot": {"port": 4401}\n')
    template = src_dir / "overlay" / "mcp.json.mo"
    template.parent.mkdir()
    template.write_text('{\n  "mcpServers": {\n{{>_shared/servers.json}}\n  }\n}\n')

    rendered = tmp / "rendered" / "mcp.json"
    _, stack = _stack_from_dirs(tmp, (src_dir, "src1", False))

    assert render_template(src=template, rendered_dest=rendered, stack=stack) == "ok"
    assert json.loads(rendered.read_text())["mcpServers"]["penpot"]["port"] == 4401


def test_invalid_json_render_is_refused(tmp: Path) -> None:
    """A *.json.mo that renders unparseable JSON returns 'invalid' and writes
    nothing: a broken partial must never ship garbage to a harness."""
    src_dir = make_source(tmp, "src1")
    (src_dir / "_shared" / "servers.json").write_text('"penpot": {"port": 4401},\n')
    template = src_dir / "overlay" / "mcp.json.mo"
    template.parent.mkdir()
    template.write_text('{\n  "mcpServers": {\n{{>_shared/servers.json}}\n  }\n}\n')

    rendered = tmp / "rendered" / "mcp.json"
    _, stack = _stack_from_dirs(tmp, (src_dir, "src1", False))

    status = render_template(src=template, rendered_dest=rendered, stack=stack)
    assert status == "invalid"
    assert not rendered.exists(), "an invalid render must not be written"


# ── data-driven templates (key-gated by data.toml) ──────────────────────────


def test_no_data_key_keeps_variables_verbatim(tmp: Path) -> None:
    """A key without data.toml is not data-active: {{var}} and {{#section}}
    pass through byte-identically (the documented legacy contract)."""
    src_dir = make_source(tmp, "src1")
    key_dir = src_dir / "overlay"
    key_dir.mkdir()
    template = key_dir / "CLAUDE.md.mo"
    template.write_text("Hi {{name}}!{{#list}}x{{/list}}")

    rendered = tmp / "rendered" / "CLAUDE.md"
    _, stack = _stack_from_dirs(tmp, (src_dir, "src1", False))
    status = render_template(src=template, rendered_dest=rendered, stack=stack, key="overlay")
    assert status == "ok"
    assert rendered.read_text() == "Hi {{name}}!{{#list}}x{{/list}}"


def test_data_key_renders_sections_and_raw_json(tmp: Path) -> None:
    """A key with data.toml gets full Mustache: sections iterate, first/last
    join members with commas, {{{raw}}} emits unescaped JSON fragments."""
    import json as _json

    src_dir = make_source(tmp, "src1")
    key_dir = src_dir / "overlay"
    key_dir.mkdir()
    (key_dir / "data.toml").write_text(
        "[[servers]]\n"
        'name = "penpot"\n'
        'command = "npx"\n'
        'args = \'["-y", "mcp-remote"]\'\n\n'
        "[[servers]]\n"
        'name = "playwright"\n'
        'command = "npx"\n'
        'args = \'["@playwright/mcp@latest"]\'\n'
    )
    template = key_dir / "mcp.json.mo"
    template.write_text(
        '{\n  "mcpServers": {\n'
        "{{#servers}}\n"
        '    "{{name}}": {\n'
        '      "command": "{{command}}",\n'
        '      "args": {{{args}}}\n'
        "    }{{^last}},{{/last}}\n"
        "{{/servers}}\n"
        "  }\n}\n"
    )

    rendered = tmp / "rendered" / "mcp.json"
    _, stack = _stack_from_dirs(tmp, (src_dir, "src1", False))
    render_template(src=template, rendered_dest=rendered, stack=stack, key="overlay")
    out = rendered.read_text()
    parsed = _json.loads(out)  # valid JSON, so the comma joining worked
    assert list(parsed["mcpServers"]) == ["penpot", "playwright"]
    assert parsed["mcpServers"]["penpot"]["args"] == ["-y", "mcp-remote"]
    assert out.count('\n    "') == 2, out


def test_first_last_injected_into_table_lists(tmp: Path) -> None:
    """List-of-table data gets first/last booleans (reserved keys) for joining."""
    src_dir = make_source(tmp, "src1")
    key_dir = src_dir / "overlay"
    key_dir.mkdir()
    (key_dir / "data.toml").write_text(
        "[[items]]\nname = 'a'\n[[items]]\nname = 'b'\n[[items]]\nname = 'c'\n"
    )
    _, stack = _stack_from_dirs(tmp, (src_dir, "src1", False))
    data = stack.data_for_key("overlay")
    assert data is not None
    assert data["items"][0]["first"] is True and data["items"][0]["last"] is False
    assert data["items"][1]["first"] is False and data["items"][1]["last"] is False
    assert data["items"][2]["first"] is False and data["items"][2]["last"] is True


def test_unknown_variable_renders_empty(tmp: Path) -> None:
    """Mustache semantics: a missing name renders as an empty string (the
    status lint is what makes that visible)."""
    src_dir = make_source(tmp, "src1")
    key_dir = src_dir / "overlay"
    key_dir.mkdir()
    (key_dir / "data.toml").write_text('name = "x"\n')
    template = key_dir / "CLAUDE.md.mo"
    template.write_text("{{name}}|{{typo}}")

    rendered = tmp / "rendered" / "CLAUDE.md"
    _, stack = _stack_from_dirs(tmp, (src_dir, "src1", False))
    render_template(src=template, rendered_dest=rendered, stack=stack, key="overlay")
    assert rendered.read_text() == "x|"


def test_data_first_source_wins(tmp: Path) -> None:
    """The data file resolves like a partial: first source in stack order."""
    first = make_source(tmp, "first")
    k1 = first / "k"
    k1.mkdir()
    (k1 / "data.toml").write_text("name = 'first'\n")
    second = make_source(tmp, "second")
    k2 = second / "k"
    k2.mkdir()
    (k2 / "data.toml").write_text("name = 'second'\n")

    _, stack = _stack_from_dirs(tmp, (first, "first", False), (second, "second", False))
    assert stack.data_for_key("k")["name"] == "first"


def test_data_privacy_violation_raises(tmp: Path) -> None:
    """A public source template must not render with data from a private source."""
    from repo_overlays.config import AppConfig, SourceConfig
    from repo_overlays.sources import SourceStack

    private = make_source(tmp, "priv")
    priv_key = private / "myproject"
    priv_key.mkdir()
    (priv_key / "data.toml").write_text("name = 'secret'\n")
    public = make_source(tmp, "pub")
    pub_key = public / "myproject"
    pub_key.mkdir()
    template = pub_key / "CLAUDE.md.mo"
    template.write_text("{{name}}\n")

    priv_cfg = SourceConfig(name="priv", path=private, private=True)
    pub_cfg = SourceConfig(name="pub", path=public, private=False)
    stack = SourceStack(AppConfig(sources=[priv_cfg, pub_cfg]))
    rendered = tmp / "rendered" / "CLAUDE.md"
    with pytest.raises(PermissionError):
        render_template(src=template, rendered_dest=rendered, stack=stack, requesting=pub_cfg, key="myproject")


def test_lint_data_refs_tracks_sections(tmp: Path) -> None:
    """The status lint knows section context: refs inside {{#servers}} resolve
    against the section's item keys; plain unknown refs are reported."""
    from repo_overlays.render import lint_data_refs

    data = {
        "servers": [{"name": "penpot", "command": "npx", "first": True, "last": False}],
        "mode": "pro",
    }
    text = (
        "{{mode}} {{typo}} "
        "{{#servers}}{{name}} {{command}}{{/servers}} "
        "{{^last}},{{/last}} {{{raw}}} {{.}} {{&amp}} {{!c}}"
    )
    unknown = lint_data_refs(text, data)
    assert unknown == ["typo", "amp"], unknown

    # triple-stache and dotted names that resolve are exempt
    assert lint_data_refs("{{{args}}} {{nested.deep}}", {"nested": {"deep": 1}}) == []
    assert lint_data_refs("{{nested.missing}}", {"nested": {"deep": 1}}) == ["nested.missing"]
