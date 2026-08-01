"""Per-destination applied-links manifest (.repo-overlays.toml)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import tomli_w


MANIFEST_FILENAME = ".repo-overlays.toml"
#: Marker file that opts a single destination out of overlays entirely.
SKIP_FILENAME = ".repo-overlays-skip"
SCHEMA_VERSION = 1


@dataclass
class LinkRecord:
    path: str           # relative to dest_root
    source: str         # source name
    key: str            # overlay key
    target: str         # absolute path of symlink target
    #: SHA-256 of the render output behind a *.mo link (None for literal
    #: files). Lets _already_applied detect content changes — a partial edit
    #: alters no path, only this hash — without re-rendering on every cd.
    render_hash: str | None = None


@dataclass
class Manifest:
    links: list[LinkRecord] = field(default_factory=list)
    #: Live files found diverged on the last apply, relative to dest_root.
    #: A diverged file is *not* a link (apply refuses to overwrite it), so its
    #: location would otherwise be unrecoverable — see divergent_markers().
    drift: list[str] = field(default_factory=list)

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
            render_hash=lr.get("render_hash"),
        )
        for lr in data.get("link", [])
    ]
    return Manifest(links=links, drift=list(data.get("drift", [])))


def write(dest_root: Path, links: list[LinkRecord], drift: list[str] | None = None) -> None:
    """Write manifest to dest_root."""
    dest_root.mkdir(parents=True, exist_ok=True)
    data: dict = {
        "schema": SCHEMA_VERSION,
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "drift": sorted(drift or []),
        "link": [
            {
                "path": lr.path,
                "source": lr.source,
                "key": lr.key,
                "target": lr.target,
                **({"render_hash": lr.render_hash} if lr.render_hash is not None else {}),
            }
            for lr in links
        ],
    }
    _manifest_path(dest_root).write_bytes(tomli_w.dumps(data).encode())


def divergent_markers(dest_root: Path) -> list[Path]:
    """Return existing ``.divergent`` markers for *dest_root*.

    Derived from the manifest rather than an ``rglob``: a marker is only ever
    written next to a live file (render.py), and every live file is a manifest
    entry, so the candidate set is the parent directories of the recorded
    links.  Walking the whole destination tree instead costs seconds on large
    repos and finds nothing extra.
    """
    manifest = read(dest_root)
    dirs = {dest_root}
    for lr in manifest.links:
        dirs.add((dest_root / lr.path).parent)
    for rel in manifest.drift:
        dirs.add((dest_root / rel).parent)
    return sorted(d / ".divergent" for d in dirs if (d / ".divergent").exists())


def _prune_empty_dirs(dest_root: Path, link: Path) -> None:
    """Remove directories left empty by a pruned link, up to *dest_root*.

    A renamed or removed subtree (a skill directory, say) otherwise leaves its
    empty parents behind forever, and a leftover `skills/<name>/` still looks
    like a skill to the harnesses that scan that directory.
    """
    parent = link.parent
    while parent != dest_root and dest_root in parent.parents:
        try:
            parent.rmdir()
        except OSError:
            return
        parent = parent.parent


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
                _prune_empty_dirs(dest_root, link)
                pruned.append(lr.path)
    write(dest_root, kept, manifest.drift)
    return pruned
