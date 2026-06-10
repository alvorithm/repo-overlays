"""Walk overlay sources and materialise symlinks into destinations."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterator

from .config import AppConfig, SourceConfig
from .manifest import LinkRecord, prune, read, write
from .render import render_template
from .resolve import iter_all_destinations, resolve_key_dest
from .sources import SourceStack


_HOME = Path.home()


def _fmt_path(p: Path) -> str:
    """Return ``p`` as a string, abbreviating ``$HOME`` as ``~``."""
    s = str(p)
    home_s = str(_HOME)
    if s == home_s:
        return "~"
    if s.startswith(home_s + os.sep):
        return "~" + s[len(home_s):]
    return s


def _contributing_sources(key: str, config: AppConfig) -> list[str]:
    """Return sorted names of sources that have directory *key*."""
    return [src.name for src in config.sources if (src.path / key).is_dir()]


def _dot_rewrite(rel: Path) -> Path:
    """Apply dot_<name> → .<name> to every component of rel."""
    parts = []
    for part in rel.parts:
        parts.append(f".{part[4:]}" if part.startswith("dot_") else part)
    return Path(*parts) if parts else rel


def _rendered_dir(source: SourceConfig, key: str) -> Path:
    return source.path / "_rendered" / key


def _apply_key(
    key: str,
    dest_root: Path,
    is_fixed: bool,
    stack: SourceStack,
    config: AppConfig,
) -> list[LinkRecord]:
    """Materialise one overlay key into dest_root; return link records installed."""
    links: list[LinkRecord] = []

    for abs_src, src in stack.iter_files_for_key(key):
        key_dir = src.path / key
        rel = abs_src.relative_to(key_dir)

        if is_fixed:
            dest_rel = rel
        else:
            dest_rel = _dot_rewrite(rel)

        is_template = rel.suffix == ".mo"
        if is_template:
            rendered_dir = _rendered_dir(src, key)
            rendered_dest = rendered_dir / rel.with_suffix("")
            live_dest_rel = dest_rel.with_suffix("")
            live_dest = dest_root / live_dest_rel

            status = render_template(
                src=abs_src,
                rendered_dest=rendered_dest,
                stack=stack,
                requesting=src,
                live_dest=live_dest if live_dest.exists() and not live_dest.is_symlink() else None,
            )
            if status == "diverged":
                print(
                    f"  drift: {live_dest_rel} (kept live; .proposed written)",
                    file=sys.stderr,
                )
                continue
            link_target = rendered_dest
            final_dest = live_dest
        else:
            link_target = abs_src
            final_dest = dest_root / dest_rel

        final_dest.parent.mkdir(parents=True, exist_ok=True)

        if final_dest.exists() and not final_dest.is_symlink():
            print(
                f"  skip: {final_dest} is a regular file (not a symlink); not overwriting",
                file=sys.stderr,
            )
            continue

        final_dest.symlink_to(link_target) if not final_dest.exists() else (
            final_dest.unlink() or final_dest.symlink_to(link_target)
        )

        rel_str = str(dest_rel.with_suffix("") if is_template else dest_rel)
        links.append(
            LinkRecord(
                path=rel_str,
                source=src.name,
                key=key,
                target=str(link_target),
            )
        )

    return links


def _already_applied(dest_root: Path, key: str) -> bool:
    """Return True if the overlay for *key* is already materialised at *dest_root*.

    Checks the manifest and verifies every recorded symlink still exists and
    points to the expected target.  Used by *apply_one* to skip redundant
    re-application when cd'ing into a subdirectory of an already-applied repo.
    """
    manifest = read(dest_root)
    if not manifest.links:
        return False
    for lr in manifest.links:
        if lr.key != key:
            continue
        link = dest_root / lr.path
        if not link.is_symlink() or not link.exists():
            return False
        # Resolve both target and recorded target so we compare real paths.
        if str(link.resolve()) != str(Path(lr.target).resolve()):
            return False
    return True


def apply_one(path: Path, config: AppConfig) -> bool:
    """Resolve and apply overlay for a single destination path.

    Returns True if an overlay key was found and applied.
    """
    result = resolve_key_dest(path, config)
    if result is None:
        return False
    key, dest_root, is_fixed = result
    if key.startswith(("_rendered", "_shared")):
        return False

    stack = SourceStack(config)
    # Check at least one source has this key.
    sources = _contributing_sources(key, config)
    if not sources:
        return False

    # Skip silently if the overlay is already in place (avoids noisy output
    # when cd'ing into subdirectories of an already-applied repo).
    if _already_applied(dest_root, key):
        return True

    print(f"Overlay {key} ({', '.join(sources)}) → {_fmt_path(dest_root)}")
    try:
        links = _apply_key(key, dest_root, is_fixed, stack, config)
    except FileNotFoundError as e:
        print(f"  error: {e}", file=sys.stderr)
        return False
    current_paths = {lr.path for lr in links}

    # Merge with existing manifest, prune removed links.
    existing = read(dest_root)
    merged = {lr.path: lr for lr in existing.links}
    for lr in links:
        merged[lr.path] = lr
    pruned = prune(dest_root, current_paths)
    if pruned:
        print(f"  pruned: {pruned}")
    write(dest_root, list(merged.values()))
    return True


def apply_all(config: AppConfig) -> None:
    """Apply every known destination."""
    for key, dest_root, is_fixed in iter_all_destinations(config):
        stack = SourceStack(config)
        sources = _contributing_sources(key, config)
        if not sources:
            continue
        print(f"Overlay {key} ({', '.join(sources)}) → {_fmt_path(dest_root)}")
        try:
            links = _apply_key(key, dest_root, is_fixed, stack, config)
        except FileNotFoundError as e:
            print(f"  error: {e}", file=sys.stderr)
            continue
        current_paths = {lr.path for lr in links}
        existing = read(dest_root)
        merged = {lr.path: lr for lr in existing.links}
        for lr in links:
            merged[lr.path] = lr
        pruned = prune(dest_root, current_paths)
        if pruned:
            print(f"  pruned: {pruned}")
        write(dest_root, list(merged.values()))
