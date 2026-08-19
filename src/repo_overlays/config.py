"""Load and validate top-level + per-source configuration."""

from __future__ import annotations

import subprocess
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
    ignore_keys: frozenset[str] = frozenset()
    #: The `path` written in config.toml, when `path` above was redirected to a
    #: worktree of it. `None` on every source resolving to its registered tree,
    #: so a caller that must name the registered path asks for this first.
    registered_path: Path | None = None


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


def _worktree_redirect() -> tuple[Path, Path] | None:
    """Return `(main checkout, worktree)` when the process runs inside a linked worktree.

    A worktree and its main checkout share a git common directory and differ in
    their top level, so a source registered at the main checkout can be resolved
    to the tree the caller is actually standing in. Returns `None` outside git
    and inside a main checkout, which is nearly every invocation and costs the
    one `rev-parse`: the second command runs only for a caller who is genuinely
    in a worktree.
    """
    probe = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        return None
    lines = probe.stdout.splitlines()
    if len(lines) != 2:
        return None
    common_dir, toplevel = Path(lines[0]), Path(lines[1]).resolve()
    if common_dir.parent.resolve() == toplevel:
        return None  # a main checkout owns its common dir: nothing to redirect
    # Ask git for the main checkout rather than deriving it from the common
    # dir, which is wrong for a repository whose git dir sits outside its tree.
    listing = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        capture_output=True,
        text=True,
    )
    if listing.returncode != 0:
        return None
    head = listing.stdout.splitlines()[:1]
    if not head or not head[0].startswith("worktree "):
        return None
    main = Path(head[0][len("worktree "):]).resolve()
    return (main, toplevel) if main != toplevel else None


def _load_ignore_keys(source_path: Path, data: dict) -> frozenset[str]:
    """Top-level dirs that are NOT overlay keys for this source.

    Union of `ignore_keys` in the per-source config.toml and the lines of an
    `.overlay-ignore` file at the source root (one key per line, `#` comments).
    Lets a source repo carry non-overlay content (docs, staging dirs) without
    the directory name accidentally matching a repo under a watched root.
    """
    keys = set(data.get("ignore_keys", []))
    ignore_file = source_path / ".overlay-ignore"
    if ignore_file.exists():
        for line in ignore_file.read_text().splitlines():
            entry = line.split("#", 1)[0].strip()
            if entry:
                keys.add(entry.rstrip("/"))
    return frozenset(keys)


def _load_source_config(source_path: Path) -> tuple[list[Path], dict[str, Path], frozenset[str]]:
    """Read watched_roots, [targets] and ignore_keys from per-source config."""
    cfg_file = source_path / "config.toml"
    data: dict = {}
    if cfg_file.exists():
        with cfg_file.open("rb") as f:
            data = tomllib.load(f)
    watched_roots = [_expand(r) for r in data.get("watched_roots", [])]
    targets = {k: _expand(v) for k, v in data.get("targets", {}).items()}
    return watched_roots, targets, _load_ignore_keys(source_path, data)


def load_config(top_config: Path | None = None) -> AppConfig:
    """Return AppConfig from top-level config.toml.

    Falls back gracefully when no top-level config exists (single-source mode
    using AGENT_OVERLAY_ROOT or the hardcoded default).

    A source the caller is standing in a worktree of resolves to that worktree
    rather than to its registered path, so a second session can render and read
    what it is editing. Every other source keeps the path config.toml gives it.
    """
    cfg_path = (top_config or TOP_LEVEL_CONFIG).expanduser()

    if not cfg_path.exists():
        return AppConfig(sources=[])

    with cfg_path.open("rb") as f:
        data = tomllib.load(f)

    sources: list[SourceConfig] = []
    entries = data.get("sources", [])
    redirect = _worktree_redirect() if entries else None
    for entry in entries:
        src_path = _expand(entry["path"])
        registered_path = None
        if redirect is not None and src_path == redirect[0]:
            registered_path, src_path = src_path, redirect[1]
        watched_roots, targets, ignore_keys = _load_source_config(src_path)
        sources.append(
            SourceConfig(
                name=entry["name"],
                path=src_path,
                private=entry.get("private", False),
                remote=entry.get("remote"),
                watched_roots=watched_roots,
                targets=targets,
                ignore_keys=ignore_keys,
                registered_path=registered_path,
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
