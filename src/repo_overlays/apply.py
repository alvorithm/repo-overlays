"""Walk overlay sources and materialise symlinks into destinations."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable

from .config import AppConfig, SourceConfig
from .manifest import (
    MANIFEST_FILENAME,
    SKIP_FILENAME,
    LinkRecord,
    prune,
    read,
    write,
)
from .render import render_hash, render_template
from .resolve import iter_all_destinations, resolve_key_dest
from .sources import SourceStack, is_tool_junk


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


def _dest_rel_for(rel: Path, is_fixed: bool) -> Path:
    """Destination-relative path for a source-relative *rel*.

    Applies the ``dot_`` rewrite (project keys only) and strips a ``.mo``
    template suffix, so a source file and the checks that reason about where it
    lands never disagree on the mapping.
    """
    dest_rel = rel if is_fixed else _dot_rewrite(rel)
    if rel.suffix == ".mo":
        dest_rel = dest_rel.with_suffix("")
    return dest_rel


def _planned_dest(rel: Path, dest_root: Path, is_fixed: bool) -> Path | None:
    """Final destination path a source-relative *rel* would materialise to.

    None when it targets the git dir of a directory that is not a git repo —
    exactly the case ``_apply_key`` skips — so both agree on what gets placed.
    """
    return _resolve_dest(dest_root, _dest_rel_for(rel, is_fixed))


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


def tracked_links(dest_root: Path, paths: Iterable[str]) -> list[str]:
    """Return the destination-relative *paths* that git already tracks.

    ``info/exclude`` only suppresses *untracked* paths, so an overlay symlink
    staged once — a ``git add -A`` in the window before its exclude line was
    written — stays tracked forever and gets committed into the project, with
    an absolute target no other machine can resolve.  The condition is
    invisible in `git status` (the file looks like any staged addition), hence
    this check.  Fix: ``git rm --cached``.
    """
    rel = [p for p in paths if not os.path.isabs(p) and not p.startswith(".git" + os.sep)]
    if not rel:
        return []
    result = subprocess.run(
        ["git", "-C", str(dest_root), "ls-files", "-z", "--", *rel],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    return sorted(p for p in result.stdout.split("\0") if p)


def _collapsible_dirs(dest_root: Path, owned_dirs: list[str], warn: bool) -> list[str]:
    """Return the owned dirs that may be excluded wholesale, warning about the rest.

    A directory git already tracks something under is left per-file: blanket-
    excluding it would hide the neighbours of a tracked file, and the tracked
    file itself would stay tracked regardless — the marker cannot do what it
    promises there, so say so instead of pretending.  *warn* is off when the
    caller only wants to know what the block should look like.
    """
    if not owned_dirs:
        return []
    tracked = tracked_links(dest_root, owned_dirs)
    blocked: dict[str, int] = {}
    for path in tracked:
        for d in owned_dirs:
            if path == d or path.startswith(d + "/"):
                blocked[d] = blocked.get(d, 0) + 1
    if warn:
        for d, n in sorted(blocked.items()):
            print(
                f"  own-marker: {d}/ kept per-file, git tracks {n} path(s) there "
                f"(git -C {_fmt_path(dest_root)} rm --cached them to collapse it)",
                file=sys.stderr,
            )
    return sorted(d for d in owned_dirs if d not in blocked)


def _exclude_section(
    dest_root: Path,
    link_paths: list[str],
    owned_dirs: list[str],
    warn: bool = True,
) -> str:
    """Return the marked block *dest_root* should carry, or "" for no block.

    Directories declared overlay-owned (``.overlay-own``) contribute one
    ``/<dir>/`` entry that supersedes every link under them.
    """
    # Git never tracks anything inside the git dir, and absolute records live
    # outside the worktree: neither belongs in info/exclude.
    link_paths = [
        p for p in link_paths if not os.path.isabs(p) and not p.startswith(".git" + os.sep)
    ]

    owned = _collapsible_dirs(dest_root, owned_dirs, warn)
    entries = [f"/{d}/" for d in owned]
    entries += [
        f"/{p}" for p in link_paths if not any(p.startswith(d + os.sep) for d in owned)
    ]
    if not entries:
        return ""
    return "\n".join([_marker_start(dest_root), *sorted(entries), _MARKER_END]) + "\n"


def _update_git_exclude(
    dest_root: Path,
    link_paths: list[str],
    owned_dirs: list[str] | None = None,
) -> None:
    """Write this destination's block into ``.git/info/exclude``, idempotently.

    Repeated calls replace the block rather than duplicating entries.  An empty
    block is dropped instead of left behind (opt-out, or every file skipped).
    No-op when *dest_root* is not inside a git repo.
    """
    exclude_file = _git_info_exclude(dest_root)
    if exclude_file is None:
        return

    new_section = _exclude_section(dest_root, link_paths, owned_dirs or [])
    exclude_file.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude_file.read_text() if exclude_file.exists() else ""
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


def _owned_dirs(key: str, is_fixed: bool, stack: SourceStack) -> list[str]:
    """Destination-relative directories declared overlay-owned for *key*.

    Same dot_ rewrite the files get, so the marker's directory and its files
    agree on where they land.
    """
    out: list[str] = []
    for rel in stack.owned_dirs_for_key(key):
        dest_rel = rel if is_fixed else _dot_rewrite(rel)
        parts = dest_rel.parts
        if not parts or parts == (".",):
            # `/` would exclude the whole destination, overlay files and the
            # project's own tree alike.
            print(
                f"  own-marker: ignored at the root of key {key!r}, it would "
                "exclude the entire destination",
                file=sys.stderr,
            )
            continue
        if parts[0] == ".git":
            continue
        out.append(dest_rel.as_posix())
    return sorted(out)


def unmanaged_owned_paths(
    key: str,
    dest_root: Path,
    is_fixed: bool,
    stack: SourceStack,
    link_paths: Iterable[str],
) -> list[str]:
    """Return paths inside an own-marked tree that no source provides.

    The coarse ``/docs.local/`` entry is what makes the exclusion robust, and the
    same entry hides everything else written there.  A file an agent drops
    straight into an owned tree is therefore invisible twice over: git never
    mentions it and the manifest never recorded it, so ``git clean`` takes it
    and nobody notices it went.  Reporting it here is the only thing left that
    can see it.

    Symlinks to directories are reported rather than descended, since an
    unmanaged one is itself the finding.
    """
    owned = _owned_dirs(key, is_fixed, stack)
    if not owned:
        return []
    managed = {p for p in link_paths if not os.path.isabs(p)}
    out: list[str] = []
    for owned_dir in owned:
        root = dest_root / owned_dir
        if not root.is_dir():
            continue
        for parent, dirnames, filenames in os.walk(root):
            linked = [n for n in dirnames if os.path.islink(os.path.join(parent, n))]
            dirnames[:] = [n for n in dirnames if n != ".git" and n not in linked]
            for name in filenames + linked:
                rel = os.path.relpath(os.path.join(parent, name), dest_root)
                if rel in managed or is_tool_junk(Path(rel)):
                    continue
                out.append(rel)
    return sorted(out)


def _apply_key(
    key: str,
    dest_root: Path,
    is_fixed: bool,
    stack: SourceStack,
    config: AppConfig,
) -> tuple[list[LinkRecord], list[str]]:
    """Materialise one overlay key into dest_root.

    Returns the link records installed and the destination-relative paths of
    live files found diverged (kept as-is, with a ``.proposed`` render beside
    them).
    """
    links: list[LinkRecord] = []
    drift: list[str] = []

    for abs_src, src in stack.iter_files_for_key(key):
        key_dir = src.path / key
        rel = abs_src.relative_to(key_dir)
        dest_rel = _dest_rel_for(rel, is_fixed)

        final_dest = _resolve_dest(dest_root, dest_rel)
        if final_dest is None:
            print(
                f"  skip: {dest_rel} targets the git dir of "
                f"{_fmt_path(dest_root)}, which is not a git repo",
                file=sys.stderr,
            )
            continue

        if rel.suffix == ".mo":
            rendered_dest = _rendered_dir(src, key) / rel.with_suffix("")
            status = render_template(
                src=abs_src,
                rendered_dest=rendered_dest,
                stack=stack,
                requesting=src,
                live_dest=final_dest if final_dest.exists() and not final_dest.is_symlink() else None,
                key=key,
            )
            if status == "diverged":
                print(
                    f"  drift: {dest_rel} (kept live; .proposed written)",
                    file=sys.stderr,
                )
                drift.append(str(dest_rel))
                continue
            if status == "invalid":
                # The render is garbage; nothing was written, so the previous
                # (valid) render and its live symlink stay in service. Re-record
                # the existing link with the fresh hash, or the next apply
                # would prune it and the harness would silently lose the
                # server/config it was using.
                if final_dest.is_symlink():
                    links.append(
                        LinkRecord(
                            path=_record_path(dest_root, final_dest),
                            source=src.name,
                            key=key,
                            target=os.readlink(final_dest),
                            render_hash=render_hash(abs_src, stack, src, key),
                        )
                    )
                continue
            link_target = rendered_dest
        else:
            link_target = abs_src

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
                render_hash=render_hash(abs_src, stack, src, key) if rel.suffix == ".mo" else None,
            )
        )

    return links, drift


def _already_applied(dest_root: Path, key: str, is_fixed: bool, stack: SourceStack) -> bool:
    """Return True if the overlay for *key* is already materialised at *dest_root*.

    "Materialised" means a fresh apply would be a no-op: every source file the
    key currently produces is accounted for at the destination, every recorded
    link is intact, and the ``info/exclude`` block is current.  Used by
    *apply_one* to skip redundant re-application when cd'ing into a subdirectory
    of an already-applied repo.

    Comparing the *set* of produced paths (not just validating recorded links)
    is what catches a source file **added** since the last apply: its
    destination is absent from the manifest, so the check must not report
    "applied" while it is still missing on disk.  The old form validated only
    recorded links, so a new source file was never materialised through this
    path until something else invalidated the manifest.

    The ``info/exclude`` block is part of "materialised" too: exclusion policy
    can change with no change to any link (adding an ``.overlay-own`` marker
    does exactly that), and a stale block is the leak this all exists to
    prevent.
    """
    manifest = read(dest_root)
    if not manifest.links and not manifest.drift:
        return False  # no manifest yet (or empty): never applied here

    # Every destination path a fresh apply would place. report_overrides is off:
    # this runs on every cd, and the override warnings are the real apply's job.
    planned: set[str] = set()
    planned_hashes: dict[str, str] = {}
    for abs_src, src in stack.iter_files_for_key(key, report_overrides=False):
        rel = abs_src.relative_to(src.path / key)
        final_dest = _planned_dest(rel, dest_root, is_fixed)
        if final_dest is None:
            continue  # git-dir file in a non-git dir: apply skips it too
        record_path = _record_path(dest_root, final_dest)
        planned.add(record_path)
        # Content hashes: a partial or template edit changes no path, only the
        # render, so the path-set comparison alone would miss it and the live
        # file would go stale until something forced a full apply.
        if rel.suffix == ".mo":
            planned_hashes[record_path] = render_hash(abs_src, stack, src, key)

    recorded = [lr for lr in manifest.links if lr.key == key]
    # A diverged template produces no link but is accounted for: its path sits
    # in manifest.drift. Re-converging it is the watcher's job on the next
    # template edit, not this cheap presence check's — so treat it as placed,
    # else a destination with standing drift would re-apply on every cd.
    accounted = {lr.path for lr in recorded} | set(manifest.drift)
    # A planned path the destination holds as a regular file produces no link
    # either: `_apply_key` refuses to overwrite one, so a fresh apply really
    # would be a no-op there. Counting it as missing instead leaves such a
    # destination permanently "not applied", and every sweep then rewrites a
    # manifest nothing asked it to change.
    blocked = {p for p in planned - accounted if _blocks_a_link(dest_root / p)}
    if planned != accounted | blocked:
        return False  # a source file was added or removed since the last apply

    for lr in recorded:
        link = dest_root / lr.path
        if not link.is_symlink() or not link.exists():
            return False
        # Resolve both target and recorded target so we compare real paths.
        if str(link.resolve()) != str(Path(lr.target).resolve()):
            return False
        # A recorded hash that is missing (pre-hash manifest) or stale means
        # the live file is not current; a full apply backfills/re-renders.
        if lr.path in planned_hashes and lr.render_hash != planned_hashes[lr.path]:
            return False

    return _exclude_up_to_date(
        dest_root, [lr.path for lr in recorded], _owned_dirs(key, is_fixed, stack)
    )


def _blocks_a_link(path: Path) -> bool:
    """Return True if *path* is real content that apply will not replace.

    Mirrors the guard in `_apply_key`: anything present that is not a symlink
    stays, so upstream's own ``AGENTS.md`` is never overwritten.
    """
    return path.exists() and not path.is_symlink()


def _exclude_up_to_date(dest_root: Path, link_paths: list[str], owned_dirs: list[str]) -> bool:
    """Return True if *dest_root*'s exclude block already says what it should."""
    exclude_file = _git_info_exclude(dest_root)
    if exclude_file is None:
        return True  # not a git repo: no block to keep in sync
    expected = _exclude_section(dest_root, link_paths, owned_dirs, warn=False)
    existing = exclude_file.read_text() if exclude_file.exists() else ""
    for m in _EXCLUDE_BLOCK_RE.finditer(existing):
        if m.group("label") == str(dest_root):
            return m.group(0).strip("\n") == expected.strip("\n")
    return expected == ""


def apply_one(path: Path, config: AppConfig) -> bool:
    """Resolve and apply overlay for a single destination path.

    Returns True if an overlay key was found and applied.
    """
    result = resolve_key_dest(path, config)
    if result is None:
        return False
    key, dest_root, is_fixed = result
    if key.startswith(("_rendered", "_shared", "_template")):
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
    if _already_applied(dest_root, key, is_fixed, stack):
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
        links, drift = _apply_key(key, dest_root, is_fixed, stack, config)
        current_paths = {lr.path for lr in links}

        # Remove links that are no longer produced, then record exactly what
        # this run installed.  Merging the previous manifest in would undo the
        # prune on paper: the symlink goes but its record stays forever, and
        # the manifest slowly fills with entries pointing at deleted files.
        pruned = prune(dest_root, current_paths)
        if pruned:
            print(f"  pruned: {pruned}")
        write(dest_root, links, drift)
        _update_git_exclude(dest_root, list(current_paths), _owned_dirs(key, is_fixed, stack))
    except OSError as e:
        print(f"  error: {_fmt_path(dest_root)}: {type(e).__name__}: {e}", file=sys.stderr)
        return False
    return True


def apply_all(config: AppConfig) -> None:
    """Apply every known destination, skipping the ones already materialised.

    The skip is not just about output noise. `_apply_and_record` rewrites a
    manifest and an ``info/exclude`` block per destination, and the watcher
    watches directories that hold them, so an unconditional sweep makes each
    apply the trigger for the next one and the daemon never returns to idle.
    An opted-out destination still goes through, because withdrawing its links
    is work that the presence check would read as "nothing to do".
    """
    for key, dest_root, is_fixed in iter_all_destinations(config):
        stack = SourceStack(config)
        sources = _contributing_sources(key, config)
        if not sources:
            continue
        if not _is_opted_out(dest_root) and _already_applied(dest_root, key, is_fixed, stack):
            continue
        _apply_and_record(key, dest_root, is_fixed, stack, config, sources)
