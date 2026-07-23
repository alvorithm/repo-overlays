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
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterator

from .apply import apply_one
from .config import AppConfig
from .resolve import _git_remote_candidates, resolve_key_dest
from .sources import _is_junk

#: Reserved source subdirectory holding the bootstrap skeleton. Like ``_shared``
#: and ``_rendered`` it is never itself an overlay key.
TEMPLATE_DIRNAME = "_template"

#: The only variables ``init`` interpolates. Kept deliberately tiny — every
#: addition is a new thing a template author has to learn.
_VAR_RE = re.compile(r"\{\{\s*(key|slug|dest|remote)\s*\}\}")


def _kebab(name: str) -> str:
    """Kebab-case *name* for the default slug: lower, non-alnum runs → ``-``."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _template_vars(key: str, dest_root: Path, slug: str | None) -> dict[str, str]:
    """Resolve the four init variables for *key* at *dest_root*."""
    remote_slug, _ = _git_remote_candidates(dest_root)
    return {
        "key": key,
        "slug": slug or _kebab(dest_root.name),
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
        if _is_junk(rel):
            continue
        yield rel


def _rel_repr(source_path: Path, key: str, out_rel: Path) -> str:
    """``<source>/<key>/<out_rel>`` for output, source named by its directory."""
    return f"{source_path.name}/{key}/{out_rel.as_posix()}"


def bootstrap(
    path: Path,
    config: AppConfig,
    only_sources: list[str] | None = None,
    slug: str | None = None,
    write: bool = False,
) -> int:
    """Instantiate every source's ``_template/`` into ``<source>/<key>/``.

    Dry-run by default: reports the files it would create and exits 1 if any
    are missing (the ``status`` contract — 0 = nothing to do, 1 = work found or
    done, ≥2 = error), so a caller can tell whether a bootstrap was needed.
    ``write=True`` creates the absent files, never clobbering an existing one,
    then applies the destination so it is live immediately.
    """
    resolved = resolve_key_dest(path, config)
    if resolved is None:
        roots = ", ".join(str(r) for r in config.all_watched_roots) or "(none configured)"
        print(
            f"error: {path} resolves to no overlay key — not a fixed target and "
            f"not a git repo under a watched root ({roots})",
            file=sys.stderr,
        )
        return 2

    key, dest_root, _is_fixed = resolved
    variables = _template_vars(key, dest_root, slug)
    print(f"init {key} → {dest_root}  (slug: {variables['slug']})")

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
            missing += 1
            if write:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(_subst((template_dir / rel).read_text(), variables))
                print(f"  create: {label}")
            else:
                print(f"  create: {label}  (dry-run)")

    if not write and missing:
        print(f"  {missing} file(s) to create — re-run with --write", file=sys.stderr)
    if write and missing:
        # The key dir just gained files; apply materialises them into the repo.
        apply_one(dest_root, config)

    return 1 if missing else 0


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
