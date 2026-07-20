"""Tests for per-source key ignoring (config.toml `ignore_keys` + `.overlay-ignore`).

A source repo may carry non-overlay content (docs, staging dirs) at its top
level; ignored names are not overlay keys for that source, so they neither
materialize nor match repos under watched roots. Semantics are per-source: a
key ignored by one source may still be provided by another.
"""

from __future__ import annotations

from pathlib import Path

from repo_overlays.config import load_config
from repo_overlays.sources import SourceStack
from tests.conftest import make_source, make_top_config


def _cfg_with(tmp: Path, sources: list[dict]):
    return load_config(make_top_config(tmp, sources))


def test_config_toml_ignore_keys(tmp: Path) -> None:
    src = make_source(tmp, "membus")
    (src / "promotions").mkdir()
    (src / "promotions" / "draft.md").write_text("d\n")
    (src / "realkey").mkdir()
    (src / "realkey" / "AGENTS.md").write_text("a\n")
    cfg_text = (src / "config.toml").read_text()
    (src / "config.toml").write_text('ignore_keys = ["promotions"]\n' + cfg_text)

    config = _cfg_with(tmp, [{"name": "membus", "path": str(src)}])
    assert config.sources[0].ignore_keys == frozenset({"promotions"})
    keys = set(SourceStack(config).iter_keys())
    assert "realkey" in keys
    assert "promotions" not in keys


def test_overlay_ignore_file(tmp: Path) -> None:
    src = make_source(tmp, "membus")
    for name in ("promotions", "evaluation", "realkey"):
        (src / name).mkdir()
        (src / name / "f.md").write_text("x\n")
    (src / ".overlay-ignore").write_text(
        "# staging dirs are not overlay keys\npromotions\nevaluation/   # trailing slash ok\n\n"
    )

    config = _cfg_with(tmp, [{"name": "membus", "path": str(src)}])
    assert config.sources[0].ignore_keys == frozenset({"promotions", "evaluation"})
    keys = set(SourceStack(config).iter_keys())
    assert keys & {"promotions", "evaluation"} == set()
    assert "realkey" in keys


def test_ignore_is_per_source(tmp: Path) -> None:
    s1 = make_source(tmp, "one")
    (s1 / "shared").mkdir()
    (s1 / "shared" / "from-one.md").write_text("1\n")
    (s1 / ".overlay-ignore").write_text("shared\n")
    s2 = make_source(tmp, "two")
    (s2 / "shared").mkdir()
    (s2 / "shared" / "from-two.md").write_text("2\n")

    config = _cfg_with(
        tmp, [{"name": "one", "path": str(s1)}, {"name": "two", "path": str(s2)}]
    )
    stack = SourceStack(config)
    assert "shared" in set(stack.iter_keys())
    files = [p.name for p, _src in stack.iter_files_for_key("shared")]
    assert files == ["from-two.md"]
