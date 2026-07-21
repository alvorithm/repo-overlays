"""Walk overlay sources and materialise symlinks into destinations."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterator

from .config import AppConfig, SourceConfig
from .manifest import (
    MANIFEST_FILENAME,
    SKIP_FILENAME,
    LinkRecord,
    prune,
    read,
    write,
)
from .render import render_template
from .resolve import iter_all_destinations, resolve_key_dest
from .sources import SourceStack


_HOME = Path.home()

_MARKER_PREFIX = "# ── repo-overlays (auto-managed, do not edit between markers)"
_MARKER_END = "# ── end repo-overlays ──"

_EXCLUDE_BLOCK_RE = re.compile(
    re.escape(_MARKER_PREFIX)
    + r"(?: \[(?P<label>[^\]]*)\])? ──\n.*?"
    + re.escape(_MARKER_END)
    + r"\n*",
    re.DOTALL,
)


def _marker_start(dest_root: Path) -> str:
    """Block header for *dest_root*.

    Linked worktrees share one ``info/exclude`` with the main checkout (git
    resolves ``info/`` to the common dir), so each destination labels its own
    block; otherwise the last apply would clobber the other's entries.
    """
    return f"{_MARKER_PREFIX} [{dest_root}] ──"


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


def _git_path(dest_root: Path, rel: str) -> Path | None:
    """Return the real filesystem path of ``.git/<rel>`` for *dest_root*, or None.

    Delegates to ``git rev-parse --git-path``, which is the only correct way to
    map a git-dir-relative path in the presence of linked worktrees: there
    ``.git`` is a *file*, and git splits its contents between the per-worktree
    gitdir (``HEAD``, ``index``) and the shared common dir (``hooks/``,
    ``info/``, ``config``).  Naive ``dest_root / ".git" / rel`` raises
    NotADirectoryError in a worktree and would write to the wrong place in the
    cases where it does not.  Returns None when *dest_root* is not a git repo.
    """
    result = subprocess.run(
        ["git", "-C", str(dest_root), "rev-parse", "--git-path", rel],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    p = Path(result.stdout.strip())
    return p if p.is_absolute() else dest_root / p


def _resolve_dest(dest_root: Path, dest_rel: Path) -> Path | None:
    """Map an overlay-relative destination to its real path under *dest_root*.

    Destinations inside ``.git/`` (e.g. ``dot_git/hooks/post-merge``) go
    through :func:`_git_path`; everything else is a plain join.  Returns None
    for a ``.git/`` destination in a non-git directory.
    """
    if dest_rel.parts and dest_rel.parts[0] == ".git":
        rest = Path(*dest_rel.parts[1:])
        if not rest.parts:
            return None
        return _git_path(dest_root, str(rest))
    return dest_root / dest_rel


def _git_info_exclude(dest_root: Path) -> Path | None:
    """Return the path to ``.git/info/exclude`` for *dest_root*, or None."""
    return _git_path(dest_root, "info/exclude")


def _record_path(dest_root: Path, final_dest: Path) -> str:
    """Manifest path for *final_dest*: relative to dest_root, else absolute.

    Git-dir destinations of a linked worktree resolve outside *dest_root*
    (into the main checkout's ``.git``), so they are recorded absolute.
    ``dest_root / <absolute>`` yields the absolute path, so every consumer of
    ``LinkRecord.path`` keeps working unchanged.
    """
    try:
        return str(final_dest.relative_to(dest_root))
    except ValueError:
        return str(final_dest)


def _update_git_exclude(dest_root: Path, link_paths: list[str]) -> None:
    """Add overlay link paths to ``.git/info/exclude``, idempotently.

    Writes a marked section so repeated calls replace the block rather than
    duplicating entries.  No-op when *dest_root* is not inside a git repo.
    """
    exclude_file = _git_info_exclude(dest_root)
    if exclude_file is None:
        return

    # Git never tracks anything inside the git dir, and absolute records live
    # outside the worktree: neither belongs in info/exclude.
    link_paths = [
        p for p in link_paths if not os.path.isabs(p) and not p.startswith(".git" + os.sep)
    ]

    exclude_file.parent.mkdir(parents=True, exist_ok=True)

    existing = exclude_file.read_text() if exclude_file.exists() else ""

    if not link_paths:
        # Nothing to exclude: drop this destination's block instead of leaving
        # an empty one behind (opt-out, or every file skipped).
        exclude_file.write_text(_merge_exclude_blocks(existing, dest_root, ""))
        return

    marker_start = _marker_start(dest_root)
    lines = [marker_start]
    for p in sorted(link_paths):
        lines.append(f"/{p}")
    lines.append(_MARKER_END)
    new_section = "\n".join(lines) + "\n"

    exclude_file.write_text(_merge_exclude_blocks(existing, dest_root, new_section))


def _merge_exclude_blocks(existing: str, dest_root: Path, new_section: str) -> str:
    """Return *existing* with *dest_root*'s block set to *new_section*.

    Blocks of other destinations are preserved, except when their labelled
    destination no longer exists on disk: a moved or deleted worktree would
    otherwise leave its entries in the shared file forever (`git worktree move`
    is a normal operation, and the file is shared across all worktrees).
    An unlabelled block predates labelling and is adopted once.
    """
    ours = _marker_start(dest_root)
    adopt_legacy = ours not in existing

    out: list[str] = []
    written = False
    pos = 0
    for m in _EXCLUDE_BLOCK_RE.finditer(existing):
        label = m.group("label")
        out.append(existing[pos:m.start()])
        pos = m.end()
        is_ours = label == str(dest_root) or (label is None and adopt_legacy)
        if is_ours:
            if not written:
                out.append(new_section)
                written = True
        elif label is None or Path(label).is_dir():
            out.append(m.group(0))
    out.append(existing[pos:])

    updated = "".join(out)
    if not written:
        if updated and not updated.endswith("\n"):
            updated += "\n"
        updated += new_section
    return updated


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
            live_dest = _resolve_dest(dest_root, live_dest_rel)
            if live_dest is None:
                print(
                    f"  skip: {live_dest_rel} targets the git dir of "
                    f"{_fmt_path(dest_root)}, which is not a git repo",
                    file=sys.stderr,
                )
                continue

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
            resolved = _resolve_dest(dest_root, dest_rel)
            if resolved is None:
                print(
                    f"  skip: {dest_rel} targets the git dir of "
                    f"{_fmt_path(dest_root)}, which is not a git repo",
                    file=sys.stderr,
                )
                continue
            final_dest = resolved

        try:
            final_dest.parent.mkdir(parents=True, exist_ok=True)

            # Test is_symlink() first: a *dangling* symlink (its source moved)
            # is not exists(), so an exists()-driven branch would try to create
            # a link over it and raise FileExistsError.  Repointing it is
            # exactly what an apply after a source move must do.
            if final_dest.is_symlink():
                final_dest.unlink()
            elif final_dest.exists():
                print(
                    f"  skip: {final_dest} is a regular file (not a symlink); not overwriting",
                    file=sys.stderr,
                )
                continue

            final_dest.symlink_to(link_target)
        except OSError as e:
            # One unwritable destination must not abort the whole key.
            print(
                f"  error: {_fmt_path(abs_src)} → {_fmt_path(final_dest)}: "
                f"{type(e).__name__}: {e}",
                file=sys.stderr,
            )
            continue

        links.append(
            LinkRecord(
                path=_record_path(dest_root, final_dest),
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

    if _is_opted_out(dest_root):
        return _withdraw(dest_root)

    # Skip silently if the overlay is already in place (avoids noisy output
    # when cd'ing into subdirectories of an already-applied repo).
    if _already_applied(dest_root, key):
        return True

    return _apply_and_record(key, dest_root, is_fixed, stack, config, sources)


def _is_opted_out(dest_root: Path) -> bool:
    """Return True if *dest_root* carries the opt-out marker.

    Every git repo and worktree under a watched root receives its key's overlay
    by default, including worktrees.  Dropping an empty ``.repo-overlays-skip``
    file in one excludes it — a per-destination decision, so it fits a worktree
    (short-lived, not in any config file) better than a config entry would.
    """
    return (dest_root / SKIP_FILENAME).exists()


def _withdraw(dest_root: Path) -> bool:
    """Remove every link this tool installed in an opted-out destination."""
    removed = prune(dest_root, set())
    manifest_file = dest_root / MANIFEST_FILENAME
    if removed or manifest_file.exists():
        print(f"Overlay skipped ({SKIP_FILENAME}) → {_fmt_path(dest_root)}")
        if removed:
            print(f"  withdrew: {removed}")
        manifest_file.unlink(missing_ok=True)
        _update_git_exclude(dest_root, [])
    return False


def _apply_and_record(
    key: str,
    dest_root: Path,
    is_fixed: bool,
    stack: SourceStack,
    config: AppConfig,
    sources: list[str],
) -> bool:
    """Apply one key to one destination, update its manifest and git exclude.

    Any OSError is reported and swallowed: one unusable destination (unwritable
    dir, dangling worktree, vanished repo) must never abort the caller's sweep.
    """
    if _is_opted_out(dest_root):
        return _withdraw(dest_root)

    print(f"Overlay {key} ({', '.join(sources)}) → {_fmt_path(dest_root)}")
    try:
        links = _apply_key(key, dest_root, is_fixed, stack, config)
        current_paths = {lr.path for lr in links}

        # Remove links that are no longer produced, then record exactly what
        # this run installed.  Merging the previous manifest in would undo the
        # prune on paper: the symlink goes but its record stays forever, and
        # the manifest slowly fills with entries pointing at deleted files.
        pruned = prune(dest_root, current_paths)
        if pruned:
            print(f"  pruned: {pruned}")
        write(dest_root, links)
        _update_git_exclude(dest_root, list(current_paths))
    except OSError as e:
        print(f"  error: {_fmt_path(dest_root)}: {type(e).__name__}: {e}", file=sys.stderr)
        return False
    return True


def apply_all(config: AppConfig) -> None:
    """Apply every known destination."""
    for key, dest_root, is_fixed in iter_all_destinations(config):
        stack = SourceStack(config)
        sources = _contributing_sources(key, config)
        if not sources:
            continue
        _apply_and_record(key, dest_root, is_fixed, stack, config, sources)
