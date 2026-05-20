"""Load and validate top-level + per-source configuration."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


TOP_LEVEL_CONFIG = Path("~/.config/repo-overlays/config.toml")


@dataclass(frozen=True)
class SourceConfig:
    name: str
    path: Path
    private: bool = False
    remote: str | None = None
    watched_roots: list[Path] = field(default_factory=list)
    targets: dict[str, Path] = field(default_factory=dict)


@dataclass(frozen=True)
class AppConfig:
    sources: list[SourceConfig]

    @property
    def unified_targets(self) -> dict[str, Path]:
        """Return merged targets, raising if two sources declare same key with different paths."""
        merged: dict[str, Path] = {}
        for src in self.sources:
            for key, path in src.targets.items():
                if key in merged and merged[key] != path:
                    raise ValueError(
                        f"Target key {key!r} declared by multiple sources with different paths: "
                        f"{merged[key]} vs {path}"
                    )
                merged[key] = path
        return merged

    @property
    def all_watched_roots(self) -> list[Path]:
        seen: set[Path] = set()
        result: list[Path] = []
        for src in self.sources:
            for root in src.watched_roots:
                if root not in seen:
                    seen.add(root)
                    result.append(root)
        return result


def _expand(raw: str) -> Path:
    return Path(raw).expanduser().resolve()


def _load_source_config(source_path: Path) -> tuple[list[Path], dict[str, Path]]:
    """Read watched_roots and [targets] from a per-source config.toml."""
    cfg_file = source_path / "config.toml"
    if not cfg_file.exists():
        return [], {}
    with cfg_file.open("rb") as f:
        data = tomllib.load(f)
    watched_roots = [_expand(r) for r in data.get("watched_roots", [])]
    targets = {k: _expand(v) for k, v in data.get("targets", {}).items()}
    return watched_roots, targets


def load_config(top_config: Path | None = None) -> AppConfig:
    """Return AppConfig from top-level config.toml.

    Falls back gracefully when no top-level config exists (single-source mode
    using AGENT_OVERLAY_ROOT or the hardcoded default).
    """
    cfg_path = (top_config or TOP_LEVEL_CONFIG).expanduser()

    if not cfg_path.exists():
        return AppConfig(sources=[])

    with cfg_path.open("rb") as f:
        data = tomllib.load(f)

    sources: list[SourceConfig] = []
    for entry in data.get("sources", []):
        src_path = _expand(entry["path"])
        watched_roots, targets = _load_source_config(src_path)
        sources.append(
            SourceConfig(
                name=entry["name"],
                path=src_path,
                private=entry.get("private", False),
                remote=entry.get("remote"),
                watched_roots=watched_roots,
                targets=targets,
            )
        )

    config = AppConfig(sources=sources)
    _validate(config)
    return config


def _validate(config: AppConfig) -> None:
    # unified_targets raises on key/path conflicts
    config.unified_targets

    # Check privacy: a public source must not declare same target key as a private one
    # (same path is fine, different path already caught above)
    private_keys = {
        k
        for src in config.sources
        if src.private
        for k in src.targets
    }
    for src in config.sources:
        if not src.private:
            for key in src.targets:
                if key in private_keys:
                    raise ValueError(
                        f"Public source {src.name!r} declares target key {key!r} "
                        "also declared by a private source"
                    )
