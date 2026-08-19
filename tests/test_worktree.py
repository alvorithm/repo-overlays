"""A source resolves to the worktree the caller stands in.

A destination already resolves by remote repository name, so a worktree of a
covered project gets the same overlay as its main checkout. A *source* used to
resolve by the literal `path` in the top-level config, so a worktree of one was
not a source at all and a session editing there could not see what it rendered.

Every test drives the installed `repo-overlay` binary with an explicit `cwd`,
because the tree the caller stands in is the whole subject and only a real
process has one.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tests.conftest import make_source, make_top_config


_REPO_OVERLAY = Path(sys.executable).parent / "repo-overlay"

_GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


# ── helpers ────────────────────────────────────────────────────────────────


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **_GIT_IDENTITY},
    ).stdout


def _git_init(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)


def _run(cwd: Path, config: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(_REPO_OVERLAY), "--config", str(config), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


def _make_committed_source(root: Path, name: str, body: str) -> Path:
    """Return a source repo holding key `myproject`, committed so it can branch."""
    src = make_source(root / "Overlays", name)
    _git_init(src)
    key = src / "myproject"
    key.mkdir()
    (key / "AGENTS.md").write_text(body)
    _git(src, "add", "config.toml", "myproject")
    _git(src, "commit", "-q", "-m", "init")
    return src


def _add_worktree(src: Path, at: Path, branch: str, body: str) -> Path:
    """Return a linked worktree of *src* whose AGENTS.md carries *body*."""
    _git(src, "worktree", "add", "-q", "-b", branch, str(at))
    assert (at / ".git").is_file(), "expected a linked worktree"
    (at / "myproject" / "AGENTS.md").write_text(body)
    return at


def _scene(tmp: Path) -> tuple[Path, Path, Path, Path]:
    """Return `(source, worktree, project, config)` for the one-source scene."""
    src = _make_committed_source(tmp, "src", "from the main checkout")
    wt = _add_worktree(src, tmp / "Worktrees" / "src-feature", "feature", "from the worktree")
    project = tmp / "myproject"
    _git_init(project)
    cfg = make_top_config(tmp, [{"name": "src", "path": str(src)}])
    return src, wt, project, cfg


# ── the substitution ───────────────────────────────────────────────────────


def test_apply_from_a_worktree_materialises_the_worktree(tmp: Path) -> None:
    """Standing in a worktree of a source renders that worktree's content."""
    src, wt, project, cfg = _scene(tmp)

    assert _run(wt, cfg, "apply", str(project)).returncode == 0

    live = project / "AGENTS.md"
    assert live.is_symlink()
    assert live.read_text() == "from the worktree"
    assert Path(os.readlink(live)).is_relative_to(wt)


def test_apply_from_the_main_checkout_materialises_the_registered_path(tmp: Path) -> None:
    """The source's own checkout is not a worktree of itself, so nothing moves."""
    src, wt, project, cfg = _scene(tmp)

    assert _run(src, cfg, "apply", str(project)).returncode == 0

    assert (project / "AGENTS.md").read_text() == "from the main checkout"


def test_apply_from_an_unrelated_repo_materialises_the_registered_path(tmp: Path) -> None:
    """A caller in some other git repo gets the registered tree, not a worktree."""
    src, wt, project, cfg = _scene(tmp)

    assert _run(project, cfg, "apply", str(project)).returncode == 0

    live = project / "AGENTS.md"
    assert live.read_text() == "from the main checkout"
    assert Path(os.readlink(live)).is_relative_to(src)


def test_a_worktree_apply_is_undone_by_applying_from_the_registered_tree(tmp: Path) -> None:
    """The redirect is not sticky: the next apply elsewhere moves every link back.

    The manifest records the tree each link came from, so the cheap
    "already applied" check cannot mistake a worktree's links for the
    registered tree's and skip the run that has to replace them.
    """
    src, wt, project, cfg = _scene(tmp)
    _run(wt, cfg, "apply", str(project))
    assert (project / "AGENTS.md").read_text() == "from the worktree"

    assert _run(src, cfg, "apply", str(project)).returncode == 0

    live = project / "AGENTS.md"
    assert live.read_text() == "from the main checkout"
    assert Path(os.readlink(live)).is_relative_to(src)


def test_only_the_source_you_stand_in_is_redirected(tmp: Path) -> None:
    """A second source keeps its registered path while the first is redirected."""
    one = _make_committed_source(tmp, "one", "one main")
    two = _make_committed_source(tmp, "two", "two main")
    wt = _add_worktree(one, tmp / "Worktrees" / "one-feature", "feature", "one worktree")
    cfg = make_top_config(
        tmp, [{"name": "one", "path": str(one)}, {"name": "two", "path": str(two)}]
    )

    out = _run(wt, cfg, "config").stdout

    assert f"one: {wt}" in out
    assert f"two: {two}" in out


# ── the substitution is visible ────────────────────────────────────────────


def test_config_names_the_registered_path_of_a_redirected_source(tmp: Path) -> None:
    """`config` says which tree a redirected source came from, and which one registered it."""
    src, wt, project, cfg = _scene(tmp)

    out = _run(wt, cfg, "config").stdout

    assert f"src: {wt}" in out
    assert f"worktree of {src}" in out


def test_config_is_silent_when_no_source_is_redirected(tmp: Path) -> None:
    """No redirect, no line: `config` reports a substitution only when there is one."""
    src, wt, project, cfg = _scene(tmp)

    out = _run(src, cfg, "config").stdout

    assert f"src: {src}" in out
    assert "worktree of" not in out


def test_status_names_the_tree_the_live_files_came_from(tmp: Path) -> None:
    """Live files rendered from a worktree are one line of `status`, not a silence."""
    src, wt, project, cfg = _scene(tmp)
    _run(wt, cfg, "apply", str(project))

    result = _run(project, cfg, "status", str(project))

    assert result.returncode == 1
    assert f"foreign-tree: {project} rendered from {wt}, not {src}" in result.stdout


def test_status_is_quiet_when_the_live_files_came_from_the_registered_tree(tmp: Path) -> None:
    """An ordinary apply reports no foreign tree, so the digest keeps its meaning."""
    src, wt, project, cfg = _scene(tmp)
    _run(src, cfg, "apply", str(project))

    result = _run(project, cfg, "status", str(project))

    assert result.returncode == 0
    assert "foreign-tree" not in result.stdout


def test_the_manifest_records_the_tree_each_source_rendered_from(tmp: Path) -> None:
    """The manifest carries the source's path, because its name no longer names a tree."""
    import tomllib

    src, wt, project, cfg = _scene(tmp)
    _run(wt, cfg, "apply", str(project))

    data = tomllib.loads((project / ".repo-overlays.toml").read_text())
    assert data["source_paths"] == {"src": str(wt)}
