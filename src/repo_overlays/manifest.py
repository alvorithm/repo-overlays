"""Per-destination applied-links manifest (.repo-overlays.toml)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import tomli_w


MANIFEST_FILENAME = ".repo-overlays.toml"
SCHEMA_VERSION = 1


@dataclass
class LinkRecord:
    path: str           # relative to dest_root
    source: str         # source name
    key: str            # overlay key
    target: str         # absolute path of symlink target


@dataclass
class Manifest:
    links: list[LinkRecord] = field(default_factory=list)

    def by_path(self) -> dict[str, LinkRecord]:
        return {lr.path: lr for lr in self.links}


def _manifest_path(dest_root: Path) -> Path:
    return dest_root / MANIFEST_FILENAME


def read(dest_root: Path) -> Manifest:
    """Return Manifest for dest_root, or empty Manifest if none exists."""
    p = _manifest_path(dest_root)
    if not p.exists():
        return Manifest()
    with p.open("rb") as f:
        data = tomllib.load(f)
    links = [
        LinkRecord(
            path=lr["path"],
            source=lr["source"],
            key=lr["key"],
            target=lr["target"],
        )
        for lr in data.get("link", [])
    ]
    return Manifest(links=links)


def write(dest_root: Path, links: list[LinkRecord]) -> None:
    """Write manifest to dest_root."""
    dest_root.mkdir(parents=True, exist_ok=True)
    data: dict = {
        "schema": SCHEMA_VERSION,
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "link": [
            {
                "path": lr.path,
                "source": lr.source,
                "key": lr.key,
                "target": lr.target,
            }
            for lr in links
        ],
    }
    _manifest_path(dest_root).write_bytes(tomli_w.dumps(data).encode())


def prune(dest_root: Path, current_paths: set[str]) -> list[str]:
    """Remove symlinks no longer in current_paths; return list of pruned rel-paths."""
    manifest = read(dest_root)
    pruned: list[str] = []
    kept: list[LinkRecord] = []
    for lr in manifest.links:
        if lr.path in current_paths:
            kept.append(lr)
        else:
            link = dest_root / lr.path
            if link.is_symlink():
                link.unlink()
                pruned.append(lr.path)
    write(dest_root, kept)
    return pruned
