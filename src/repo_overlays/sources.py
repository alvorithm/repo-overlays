"""Source stack: partial lookup and per-key file merging across sources."""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path
from typing import Iterator

from .config import AppConfig, SourceConfig


_NAMED_PARTIAL_RE = re.compile(r"^@(?P<name>[^/]+)/(?P<rest>.+)$")

#: Editor leftovers. Materialising `settings.json~` next to `settings.json`
#: puts junk in the destination repo and can confuse the reading application.
_BACKUP_SUFFIXES = ("~", ".swp", ".swo", ".orig", ".rej")
#: Tool caches that live inside a source tree. A skill directory carrying a
#: `__pycache__` had its .pyc files symlinked into ~/.config/claude/skills/.
_JUNK_DIRS = frozenset({"__pycache__", ".ruff_cache", ".pytest_cache", ".mypy_cache", ".git"})

#: Marker file declaring the directory it sits in wholly overlay-owned, so the
#: destination excludes that *directory* instead of each file under it. It is a
#: declaration, never materialised.
OWN_MARKER = ".overlay-own"

#: Per-key template data file. Its presence makes the key *data-active* (see
#: ``data_for_key``); it is a declaration, never materialised.
DATA_FILENAME = "data.toml"


def is_tool_junk(rel: Path) -> bool:
    """True for editor leftovers and tool caches, which no copy should carry."""
    if rel.name.endswith(_BACKUP_SUFFIXES) or rel.name.startswith(".#"):
        return True
    return any(part in _JUNK_DIRS for part in rel.parts)


def _is_junk(rel: Path) -> bool:
    """True for editor leftovers, tool caches and markers, which never materialise."""
    if rel.name == OWN_MARKER:
        return True
    # Only the key-root data.toml is the contract; a nested one is an ordinary file.
    if rel.name == DATA_FILENAME and len(rel.parts) == 1:
        return True
    return is_tool_junk(rel)


class SourceStack:
    def __init__(self, config: AppConfig) -> None:
        self._config = config

    @property
    def sources(self) -> list[SourceConfig]:
        return self._config.sources

    def iter_keys(self) -> Iterator[str]:
        """Yield unique overlay keys present across all sources (deduped)."""
        seen: set[str] = set()
        for src in self._config.sources:
            if not src.path.is_dir():
                continue
            for child in src.path.iterdir():
                if not child.is_dir():
                    continue
                key = child.name
                if key.startswith(("_rendered", "_template", ".git", "__pycache__")):
                    continue
                if key in src.ignore_keys:
                    continue
                if key not in seen:
                    seen.add(key)
                    yield key

    def iter_files_for_key(
        self, key: str, *, report_overrides: bool = True
    ) -> Iterator[tuple[Path, SourceConfig]]:
        """Yield (absolute_file_path, source) for each file under key.

        Later sources override earlier ones on per-relative-path conflict, and
        every override is reported: overriding is *whole-file*, never a merge,
        so two sources contributing the same path silently discard one of them.
        That is the hazard when one key is served by several sources (guidance
        in one, memory wiring in another).  Yields in final precedence order
        (each file once, from the winning source).

        *report_overrides* is off for callers that only need the file set, not
        the side effect — the ``_already_applied`` cd-hook check would otherwise
        reprint every override on each directory entry.
        """
        # Build map: rel_path → (abs_path, source); later sources win.
        merged: dict[Path, tuple[Path, SourceConfig]] = {}
        for src in self._config.sources:
            if key in src.ignore_keys:
                continue
            key_dir = src.path / key
            if not key_dir.is_dir():
                continue
            for abs_path in key_dir.rglob("*"):
                if not abs_path.is_file():
                    continue
                rel = abs_path.relative_to(key_dir)
                if _is_junk(rel):
                    continue
                if rel in merged and report_overrides:
                    loser = merged[rel][1]
                    print(
                        f"  override: {key}/{rel}: {src.name} replaces {loser.name} "
                        "(whole file, not merged)",
                        file=sys.stderr,
                    )
                merged[rel] = (abs_path, src)
        yield from ((abs_path, src) for abs_path, src in merged.values())

    def owned_dirs_for_key(self, key: str) -> set[Path]:
        """Return key-relative directories declared wholly overlay-owned.

        A directory carrying an ``.overlay-own`` marker is excluded at the
        destination as a directory (``/work/``) rather than file by file.  The
        per-file form leaks: a file materialised (or written by an agent)
        before its exclude line exists is briefly visible to git, and one
        ``git add -A`` in that window tracks it forever — ``info/exclude``
        cannot hide a tracked path.  Declared here rather than inferred from
        the destination, so the exclusion does not flip granularity as files
        come and go.
        """
        owned: set[Path] = set()
        for src in self._config.sources:
            if key in src.ignore_keys:
                continue
            key_dir = src.path / key
            if not key_dir.is_dir():
                continue
            for marker in key_dir.rglob(OWN_MARKER):
                if marker.is_file():
                    owned.add(marker.parent.relative_to(key_dir))
        return owned

    def resolve_partial(
        self,
        ref: str,
        requesting_source: SourceConfig | None = None,
    ) -> Path:
        """Return absolute path for a Mustache partial reference.

        Supports:
          - ``_shared/foo.md``  → first source that has it (stack order)
          - ``@name/_shared/foo.md`` → only that named source

        Raises FileNotFoundError if not found.
        Raises PermissionError if a public source requests a private partial.
        """
        m = _NAMED_PARTIAL_RE.match(ref)
        if m:
            src_name = m.group("name")
            rest = m.group("rest")
            src = self._source_by_name(src_name)
            candidate = src.path / rest
            if not candidate.exists():
                raise FileNotFoundError(
                    f"Partial @{src_name}/{rest} not found in source {src.path}"
                )
            self._check_privacy(requesting_source, src, ref)
            return candidate

        # Stack lookup: first source wins.
        for src in self._config.sources:
            candidate = src.path / ref
            if candidate.exists():
                self._check_privacy(requesting_source, src, ref)
                return candidate
        raise FileNotFoundError(f"Partial {ref!r} not found in any source")

    def resolve_data(
        self,
        key: str,
        requesting_source: SourceConfig | None = None,
    ) -> Path | None:
        """Return the data.toml that makes *key* data-active, or None.

        First source in stack order that has ``<key>/data.toml`` wins — whole
        file, never merged, exactly like partials and file overrides. Raises
        PermissionError if a public source requests data from a private one.
        """
        for src in self._config.sources:
            if key in src.ignore_keys:
                continue
            candidate = src.path / key / DATA_FILENAME
            if candidate.exists():
                self._check_privacy(requesting_source, src, f"{key}/{DATA_FILENAME}")
                return candidate
        return None

    def data_for_key(
        self,
        key: str,
        requesting_source: SourceConfig | None = None,
    ) -> dict | None:
        """Return the parsed, position-annotated template data for *key*, or None.

        None means the key is *not* data-active: its templates render with the
        legacy partial-only path and every ``{{variable}}``/``{{#section}}``
        stays verbatim. The file's presence is the whole gate.
        """
        path = self.resolve_data(key, requesting_source)
        if path is None:
            return None
        with path.open("rb") as f:
            data = tomllib.load(f)
        _inject_positions(data)
        return data

    def source_by_name(self, name: str) -> SourceConfig:
        return self._source_by_name(name)

    def _source_by_name(self, name: str) -> SourceConfig:
        for src in self._config.sources:
            if src.name == name:
                return src
        raise KeyError(f"No source named {name!r}")

    def _check_privacy(
        self,
        requesting: SourceConfig | None,
        providing: SourceConfig,
        ref: str,
    ) -> None:
        """Raise PermissionError if a public source pulls from a private one."""
        if requesting is None or requesting.private:
            return
        if providing.private:
            raise PermissionError(
                f"Public source {requesting.name!r} references private partial {ref!r} "
                f"from source {providing.name!r}"
            )


def _inject_positions(data: dict) -> None:
    """Add ``first``/``last`` booleans to every list-of-table value.

    Mustache has no index or join; a template joins items with
    ``{{^first}}``/``{{^last}}`` inverted sections instead. The keys are
    reserved: an existing value in the data file is kept.
    """
    def walk(node: object) -> None:
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            if node and all(isinstance(item, dict) for item in node):
                for index, item in enumerate(node):
                    item.setdefault("first", index == 0)
                    item.setdefault("last", index == len(node) - 1)
            for item in node:
                walk(item)

    walk(data)
