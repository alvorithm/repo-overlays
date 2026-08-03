"""CLI entry point: repo-overlay <subcommand> [args]."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .apply import apply_all, apply_one, tracked_links
from .config import TOP_LEVEL_CONFIG, AppConfig, load_config
from .manifest import divergent_markers, read as read_manifest
from .promote import promote
from .render import lint_data_refs, render_hash, render_text, resolve_template_text
from .resolve import iter_all_destinations, resolve_key_dest
from .sources import SourceStack
from .watch import watch


def _load(args: argparse.Namespace) -> AppConfig:
    cfg_path = getattr(args, "config", None)
    return load_config(Path(cfg_path) if cfg_path else None)


# ── subcommands ───────────────────────────────────────────────────────────────


def cmd_apply(args: argparse.Namespace) -> int:
    config = _load(args)
    path = getattr(args, "path", None)
    if path:
        ok = apply_one(Path(path).resolve(), config)
        return 0 if ok else 1
    apply_all(config)
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    config = _load(args)
    from .render import render_template
    from .sources import SourceStack

    src = Path(args.src)
    dst = Path(args.dst)
    stack = SourceStack(config)
    status = render_template(src=src, rendered_dest=dst, stack=stack)
    print(f"render: {status}")
    return 0


def cmd_promote(args: argparse.Namespace) -> int:
    config = _load(args)
    key = getattr(args, "key", None)
    promote(config, key=key)
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    config = _load(args)
    watch(config, once=args.once)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """List every file tracked by an overlay, one per line, relative to $HOME.

    Useful for chezmoi to know which files to .chezmoiignore because they
    are managed by repo-overlays rather than dotfile management.
    """
    config = _load(args)
    home = Path.home()
    seen: set[str] = set()
    for _key, dest_root, _is_fixed in iter_all_destinations(config):
        manifest = read_manifest(dest_root)
        for lr in manifest.links:
            full = (dest_root / lr.path)
            try:
                rel = full.relative_to(home)
                line = str(rel)
            except ValueError:
                # Path not under $HOME — print absolute
                line = str(full)
            if line not in seen:
                seen.add(line)
                print(line)
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    """Bootstrap a destination's overlay key from each source's _template/."""
    from .bootstrap import bootstrap

    config = _load(args)
    path = Path(getattr(args, "path", None) or ".").resolve()
    return bootstrap(
        path,
        config,
        only_sources=args.source or None,
        slug=args.slug,
        key=args.key,
        write=args.write,
    )


def cmd_config(args: argparse.Namespace) -> int:
    config = _load(args)
    print(f"Config: {TOP_LEVEL_CONFIG.expanduser()}")
    print(f"\nSources ({len(config.sources)}):")
    for src in config.sources:
        priv = " [private]" if src.private else ""
        print(f"  {src.name}{priv}: {src.path}")
        for k, v in src.targets.items():
            print(f"    target {k!r} → {v}")
        for r in src.watched_roots:
            print(f"    watched_root: {r}")
    print("\nUnified targets:")
    for k, v in config.unified_targets.items():
        print(f"  {k!r} → {v}")
    print("\nAll watched roots:")
    for r in config.all_watched_roots:
        print(f"  {r}")
    return 0


def _render_is_stale(lr, stack: SourceStack) -> bool:
    """True when the render behind a recorded *.mo link is no longer current.

    The recorded ``render_hash`` was computed from the template plus its
    transitive partials at apply time; re-hashing the same inputs and seeing a
    different value means a source changed and no apply re-rendered since.
    """
    try:
        src = stack.source_by_name(lr.source)
    except KeyError:
        return False  # source vanished: the link will be reported broken/pruned
    target = Path(lr.target)
    rend_root = src.path / "_rendered" / lr.key
    if not target.is_relative_to(rend_root):
        return False  # hand-made or foreign target: nothing to compare against
    mo = src.path / lr.key / (target.relative_to(rend_root).as_posix() + ".mo")
    try:
        return render_hash(mo, stack, src, lr.key) != lr.render_hash
    except FileNotFoundError:
        return False  # source .mo gone: a path-level issue, not a stale render


def cmd_status(args: argparse.Namespace) -> int:
    """Report broken links, drift and missing partials.

    With a *path*, only that destination is checked and template partials are
    skipped — cheap enough (a manifest read plus one stat per link) to run from
    a directory-enter hook.
    """
    config = _load(args)
    path = getattr(args, "path", None)
    issues = 0
    stack = SourceStack(config)

    if path:
        resolved = resolve_key_dest(Path(path).resolve(), config)
        if resolved is None:
            return 0
        destinations = [resolved]
    else:
        destinations = list(iter_all_destinations(config))
        # Zero destinations means the config never loaded (missing file, parse
        # error, empty source stack) — not a clean state. Reporting "clean"
        # here hides exactly the failure a drift digest exists to surface.
        if not destinations:
            cfg_path = getattr(args, "config", None) or TOP_LEVEL_CONFIG.expanduser()
            print(f"no-destinations: no overlays resolved from {cfg_path}")
            return 1

    for key, dest_root, is_fixed in destinations:
        manifest = read_manifest(dest_root)
        for lr in manifest.links:
            link = dest_root / lr.path
            if link.is_symlink() and not link.exists():
                print(f"broken: {link}")
                issues += 1
            elif link.exists() and not link.is_symlink():
                print(f"regular-file: {link}")
                issues += 1
            elif not path and lr.render_hash is not None and _render_is_stale(lr, stack):
                # The source changed after the last apply and nothing
                # re-rendered: the live file carries old content. Usually the
                # watcher re-applies within seconds, so a report here means it
                # was down for that edit (or never saw it).
                print(f"stale: {link}")
                issues += 1

        # A tracked overlay link is a one-way trap: info/exclude only hides
        # untracked paths, so it stays tracked until untracked by hand.
        for rel in tracked_links(dest_root, [lr.path for lr in manifest.links]):
            print(f"tracked: {dest_root / rel} — git -C {dest_root} rm --cached {rel}")
            issues += 1

        for marker in divergent_markers(dest_root):
            print(f"diverged: {marker.parent}")
            issues += 1

    # Unkeyed repos are surfaced only behind --unmanaged: the daily drift digest
    # consumes `status` and treats any line as an issue, so emitting these
    # unconditionally would turn a signal into a permanent nag. Off by default
    # keeps the digest contract (line-per-issue, exit 1) intact.
    if not path and getattr(args, "unmanaged", False):
        from .bootstrap import unmanaged_destinations

        for dest in unmanaged_destinations(config):
            print(f"unmanaged: {dest} — repo-overlay init {dest}")
            issues += 1

    if not path:
        for key in stack.iter_keys():
            data = stack.data_for_key(key)
            for abs_src, src in stack.iter_files_for_key(key):
                if abs_src.suffix != ".mo":
                    continue
                try:
                    text = resolve_template_text(abs_src, stack, src)
                except FileNotFoundError as e:
                    print(f"missing-partial: {e}")
                    issues += 1
                    continue
                # A data-active key renders variables; a ref that resolves
                # against no data key renders as an empty string instead of
                # the intended value — worth a line in the daily digest. The
                # lint runs on the partial-resolved text, tags intact.
                if data is not None:
                    for ref in lint_data_refs(text, data):
                        print(f"unknown-data-ref: {abs_src}: {ref}")
                        issues += 1
                # A *.json.mo must render to parseable JSON; the apply refused
                # to write an invalid render, so the issue lives only here
                # (plus the event log) until the source is fixed.
                if abs_src.with_suffix("").suffix == ".json":
                    rendered = render_text(abs_src, stack, src, key)
                    try:
                        json.loads(rendered)
                    except ValueError as e:
                        print(f"invalid-json: {abs_src}: {e}")
                        issues += 1

    if issues == 0 and not path:
        print("All overlays clean.")
    return 0 if issues == 0 else 1


# ── parser ────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="repo-overlay", description="Manage repo overlays")
    p.add_argument(
        "--config",
        metavar="FILE",
        help=f"Top-level config (default: {TOP_LEVEL_CONFIG})",
    )
    sub = p.add_subparsers(dest="subcommand", required=True)

    ap = sub.add_parser("apply", help="Materialise overlays")
    ap.add_argument("path", nargs="?", help="Destination path (default: all)")
    ap.set_defaults(func=cmd_apply)

    rp = sub.add_parser("render", help="Render one template (internal)")
    rp.add_argument("src", help="Source .mo file")
    rp.add_argument("dst", help="Destination file")
    rp.set_defaults(func=cmd_render)

    pp = sub.add_parser("promote", help="Reconcile diverged overlays (interactive)")
    pp.add_argument("key", nargs="?", help="Limit to this overlay key")
    pp.set_defaults(func=cmd_promote)

    wp = sub.add_parser("watch", help="Watch and auto-apply on changes")
    wp.add_argument("--once", action="store_true", help="Apply once then exit")
    wp.set_defaults(func=cmd_watch)

    lp = sub.add_parser("list", help="List every live file tracked by overlays ($HOME-relative)")
    lp.set_defaults(func=cmd_list)

    ip = sub.add_parser("init", help="Bootstrap a repo's overlay key from _template/")
    ip.add_argument("path", nargs="?", help="Destination path (default: cwd)")
    ip.add_argument(
        "--source", action="append", metavar="NAME",
        help="Limit to this source (repeatable; default: every source with a _template/)",
    )
    ip.add_argument(
        "--slug",
        help="Memory/project slug for {{slug}}, not the key (default: kebab-cased key)",
    )
    ip.add_argument("--key", help="Overlay key directory name (default: bare remote repo name, else basename)")
    ip.add_argument(
        "--write", action="store_true",
        help="Create the missing files (default: dry-run), then apply the destination",
    )
    ip.set_defaults(func=cmd_init)

    cp = sub.add_parser("config", help="Print effective configuration")
    cp.set_defaults(func=cmd_config)

    sp = sub.add_parser("status", help="Report broken links, drift, missing partials")
    sp.add_argument("path", nargs="?", help="Limit to the destination containing PATH (fast)")
    sp.add_argument(
        "--unmanaged", action="store_true",
        help="Also list git repos under watched roots that no overlay key covers",
    )
    sp.set_defaults(func=cmd_status)

    return p


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
