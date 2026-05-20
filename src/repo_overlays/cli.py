"""CLI entry point: repo-overlay <subcommand> [args]."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .apply import apply_all, apply_one
from .config import TOP_LEVEL_CONFIG, AppConfig, load_config
from .manifest import MANIFEST_FILENAME, read as read_manifest
from .promote import promote
from .resolve import iter_all_destinations
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
    config = _load(args)
    issues = 0
    for key, dest_root, is_fixed in iter_all_destinations(config):
        manifest = read_manifest(dest_root)
        for lr in manifest.links:
            link = dest_root / lr.path
            if link.is_symlink() and not link.exists():
                print(f"broken: {link}")
                issues += 1
            elif link.exists() and not link.is_symlink():
                print(f"regular-file: {link}")
                issues += 1

        # Check for divergent markers anywhere in dest_root.
        for marker in dest_root.rglob(".divergent"):
            print(f"diverged: {marker.parent}")
            issues += 1

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

    if issues == 0:
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

    cp = sub.add_parser("config", help="Print effective configuration")
    cp.set_defaults(func=cmd_config)

    sp = sub.add_parser("status", help="Report broken links, drift, missing partials")
    sp.set_defaults(func=cmd_status)

    return p


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
