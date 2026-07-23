"""CLI entry point: repo-overlay <subcommand> [args]."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .apply import apply_all, apply_one, tracked_links
from .config import TOP_LEVEL_CONFIG, AppConfig, load_config
from .manifest import divergent_markers, read as read_manifest
from .promote import promote
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


def cmd_status(args: argparse.Namespace) -> int:
    """Report broken links, drift and missing partials.

    With a *path*, only that destination is checked and template partials are
    skipped — cheap enough (a manifest read plus one stat per link) to run from
    a directory-enter hook.
    """
    config = _load(args)
    path = getattr(args, "path", None)
    issues = 0

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
        stack = SourceStack(config)
        for key in stack.iter_keys():
            for abs_src, src in stack.iter_files_for_key(key):
                if abs_src.suffix == ".mo":
                    try:
                        from .render import _resolve_partials, _PartialLoader
                        loader = _PartialLoader(stack, src)
                        template = abs_src.read_text()
                        _resolve_partials(template, loader)
                    except FileNotFoundError as e:
                        print(f"missing-partial: {e}")
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
    ip.add_argument("--slug", help="Slug for {{slug}} (default: kebab-cased basename)")
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
