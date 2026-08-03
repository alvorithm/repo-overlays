"""``repo-overlay init``: bootstrap a repo's overlay key from ``_template/``.

A repo cloned under a watched root gets zero overlay coverage until a key
directory is hand-crafted for it — forgotten essentially every time, so the
repo runs without memory routing or agent guidance until someone notices.
``init`` closes that gap: it instantiates each source's ``_template/`` skeleton
into ``<source>/<key>/`` for the resolved destination, then applies it.

The template is ordinary source content, not a second dialect. ``init``
substitutes only the four *init variables* — ``{{key}}``, ``{{slug}}``,
``{{dest}}``, ``{{remote}}`` — in file paths and bodies; ``{{>partials}}`` and
every other ``{{…}}`` are left verbatim, so a ``.mo`` copied into the key dir is
rendered by ``apply`` later exactly as any hand-authored template would be.

The key directory is the one ``resolve_key_dest`` finds, except for a key no
source provides yet: there the default is the bare git remote repo name
(``repo`` out of ``owner/repo``), falling back to the destination's basename,
and ``--key`` overrides it. Fixed targets keep their own key. A ``--key`` the
resolver could never reach for that destination (neither its remote slug, nor
its basename, nor its bare repo name) is refused before anything is written:
the key dir would be dead weight no ``apply`` would ever look at.

Exit codes split by mode. A dry-run follows the ``status`` contract: 0 =
nothing to create, 1 = files would be created, 2 or more = error. ``--write``
is a mutating command like ``apply``: 0 on success whether or not it created
anything, 2 when the destination is unresolvable or the trailing apply fails.
It never returns 1.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterator

from .apply import _dot_rewrite, _is_opted_out, apply_one
from .config import AppConfig
from .manifest import SKIP_FILENAME
from .resolve import _collect_project_keys, _git_remote_candidates, resolve_key_dest
from .sources import SourceStack, is_tool_junk

#: Reserved source subdirectory holding the bootstrap skeleton. Like ``_shared``
#: and ``_rendered`` it is never itself an overlay key.
TEMPLATE_DIRNAME = "_template"

#: The only variables ``init`` interpolates. Kept deliberately tiny — every
#: addition is a new thing a template author has to learn.
_VAR_RE = re.compile(r"\{\{\s*(key|slug|dest|remote)\s*\}\}")


def _kebab(name: str) -> str:
    """Kebab-case *name* for the default slug: lower, non-alnum runs → ``-``."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _template_vars(
    key: str, dest_root: Path, slug: str | None, remote_slug: str | None
) -> dict[str, str]:
    """Resolve the four init variables for *key* at *dest_root*.

    The slug defaults to the *key*, not to the destination's basename: a
    worktree or a differently named clone would otherwise seed the memory slug
    with its directory name, which is the drift ``init`` exists to prevent
    (``project:beadpot-userlibs`` for a worktree of ``beadpot``).
    """
    return {
        "key": key,
        "slug": slug or _kebab(key),
        "dest": str(dest_root),
        "remote": remote_slug or "",
    }


def _subst(text: str, variables: dict[str, str]) -> str:
    """Replace the four init variables in *text*; leave all other ``{{…}}``."""
    return _VAR_RE.sub(lambda m: variables[m.group(1)], text)


def _iter_template_files(template_dir: Path) -> Iterator[Path]:
    """Yield template-relative paths of the skeleton's files (junk filtered)."""
    for abs_path in sorted(template_dir.rglob("*")):
        if not abs_path.is_file():
            continue
        rel = abs_path.relative_to(template_dir)
        # `_is_junk` also drops `.overlay-own` and a key-root `data.toml`: right
        # for materialisation, wrong here. This copy is source to source, so
        # those declarations are content a skeleton must be able to seed.
        if is_tool_junk(rel):
            continue
        yield rel


def _dest_rel(rel: Path) -> str:
    """Destination-relative path *rel* materialises to, ``.mo`` suffix dropped."""
    text = rel.as_posix()
    return text[:-3] if text.endswith(".mo") else text


def _already_provided(key: str, config: AppConfig) -> dict[str, str]:
    """Map destination-relative path to the source name providing it for *key*.

    A skeleton must not duplicate a file another source already contributes:
    two sources on one path is a whole-file override, so the template's copy
    would either lose silently or shadow the real content. Backfilling an
    existing key is the normal case (``init`` is idempotent), so this is what
    makes it safe.
    """
    stack = SourceStack(config)
    provided: dict[str, str] = {}
    for abs_path, src in stack.iter_files_for_key(key, report_overrides=False):
        provided[_dest_rel(abs_path.relative_to(src.path / key))] = src.name
    return provided


def _rel_repr(source_path: Path, key: str, out_rel: Path) -> str:
    """``<source>/<key>/<out_rel>`` for output, source named by its directory."""
    return f"{source_path.name}/{key}/{out_rel.as_posix()}"


def bootstrap(
    path: Path,
    config: AppConfig,
    only_sources: list[str] | None = None,
    slug: str | None = None,
    key: str | None = None,
    write: bool = False,
) -> int:
    """Instantiate every source's ``_template/`` into ``<source>/<key>/``.

    Dry-run by default: reports the files it would create and follows the
    ``status`` contract (0 = nothing to create, 1 = files would be created,
    ≥2 = error), so a caller can tell whether a bootstrap is needed.
    ``write=True`` is a mutating command like ``apply``: it creates the absent
    files, never clobbering an existing one, applies the destination so it is
    live immediately (skipping that step for a destination carrying
    ``.repo-overlays-skip``, which opted out on purpose), and returns 0 on
    success (whether or not anything was created) or 2 on error. It never
    returns 1.

    Args:
        path: Destination path to bootstrap; resolved to its overlay key.
        config: Loaded application config.
        only_sources: Restrict writing to these source names.
        slug: Override for the ``{{slug}}`` variable.
        key: Override for the overlay key directory name. Wins over every
            resolution rule, including a fixed target's own key, but a key the
            resolver cannot reach for this destination is refused.
        write: Create the files and apply, instead of reporting a dry-run.

    Returns:
        The process exit code described above.
    """
    resolved = resolve_key_dest(path, config)
    if resolved is None:
        roots = ", ".join(str(r) for r in config.all_watched_roots) or "(none configured)"
        print(
            f"error: {path} resolves to no overlay key: not a fixed target and "
            f"not a git repo under a watched root ({roots})",
            file=sys.stderr,
        )
        return 2

    resolved_key, dest_root, is_fixed = resolved
    project_keys = _collect_project_keys(config)
    remote_slug, remote_repo = _git_remote_candidates(dest_root)
    if key is None:
        if is_fixed or resolved_key in project_keys:
            key = resolved_key
        else:
            # A brand new key. `resolve_key_dest` falls back to the
            # owner-qualified slug, but hand-made keys are bare names, so
            # minting the first `owner_repo` one is a silent, permanent naming
            # divergence; a worktree's basename is wrong for a different reason
            # (it names the branch, not the repo). The bare remote repo name is
            # the convention, and `--key` disambiguates a name collision.
            key = remote_repo or dest_root.name
    elif not is_fixed:
        candidates = {c for c in (remote_slug, dest_root.name, remote_repo) if c}
        if key not in candidates:
            # An arbitrary --key would produce a key dir that nothing ever
            # resolves to: refuse before writing, not after leaving dead files.
            print(
                f"error: key {key!r} is unreachable from {dest_root}: apply resolves "
                f"this destination to one of {', '.join(sorted(candidates))}",
                file=sys.stderr,
            )
            return 2

    variables = _template_vars(key, dest_root, slug, remote_slug)
    print(f"init {key} → {dest_root}  (slug: {variables['slug']})")
    if not is_fixed and key not in project_keys:
        print(f"  new key: {key} (no source has this key yet; override with --key)")

    provided = _already_provided(key, config)
    missing = 0
    for src in config.sources:
        requested = only_sources is None or src.name in only_sources
        if not requested:
            continue
        template_dir = src.path / TEMPLATE_DIRNAME
        if not template_dir.is_dir():
            # Only mention a source the user named explicitly; silently skipping
            # the many sources without a template keeps the default run quiet.
            if only_sources is not None:
                print(f"  skip:   {src.name} (no {TEMPLATE_DIRNAME}/)")
            continue

        for rel in _iter_template_files(template_dir):
            out_rel = Path(_subst(rel.as_posix(), variables))
            out_path = src.path / key / out_rel
            label = _rel_repr(src.path, key, out_rel)
            if out_path.exists():
                print(f"  ok:     {label}")
                continue
            dest_rel = _dest_rel(out_rel)
            owner = provided.get(dest_rel)
            if owner is not None and owner != src.name:
                print(f"  have:   {dest_rel} (from {owner})")
                continue
            live = dest_root / (dest_rel if is_fixed else _dot_rewrite(Path(dest_rel)))
            if live.exists() and not live.is_symlink():
                # The destination tracks its own file there. `apply` would refuse
                # to overwrite it on every run, so the skeleton's copy would be
                # nothing but a permanent warning (penpot's upstream AGENTS.md).
                print(f"  have:   {dest_rel} (regular file at the destination)")
                continue
            missing += 1
            if write:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(_subst((template_dir / rel).read_text(), variables))
                print(f"  create: {label}")
            else:
                print(f"  create: {label}  (dry-run)")

    if not write:
        if missing:
            print(f"  {missing} file(s) to create -> re-run with --write", file=sys.stderr)
        return 1 if missing else 0

    # Mutating run: apply unconditionally, so an already populated key dir is
    # still materialised into the destination.
    if _is_opted_out(dest_root):
        # The destination opted out on purpose; the key dir is still the point.
        print(f"  skip:   apply ({SKIP_FILENAME} at the destination)")
        return 0
    if not apply_one(dest_root, config):
        print(
            f"error: nothing materialised at {dest_root} (init wrote key {key}): "
            f"check `repo-overlay status {dest_root}`",
            file=sys.stderr,
        )
        return 2
    return 0


def unmanaged_destinations(config: AppConfig) -> list[Path]:
    """Return git repos under watched roots whose key no source provides.

    These are the repos ``init`` exists for: discovered by ``iter_all_destinations``
    but skipped by ``apply`` because nothing contributes their key.
    """
    from .apply import _contributing_sources
    from .resolve import iter_all_destinations

    out: list[Path] = []
    for key, dest_root, is_fixed in iter_all_destinations(config):
        if is_fixed:
            continue
        if not _contributing_sources(key, config):
            out.append(dest_root)
    return out
