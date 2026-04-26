from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from uuid import UUID

from zeno.orchestrator.errors import InitializationError, MergeError

logger = logging.getLogger(__name__)


async def _run_git(
    args: list[str],
    *,
    cwd: str,
) -> tuple[int, str, str]:
    logger.debug("Git cmd | cwd=%s args=%s", cwd, args)
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as e:
        raise InitializationError("Failed to spawn git", detail=str(e)) from e

    out_b, err_b = await proc.communicate()
    out = (out_b or b"").decode("utf-8", errors="replace")
    err = (err_b or b"").decode("utf-8", errors="replace")
    return proc.returncode or 0, out, err


async def ensure_git_initialized(working_directory: str) -> None:
    git_dir = Path(working_directory).resolve() / ".git"
    if git_dir.exists():
        return

    logger.debug("Git init | path=%s", working_directory)
    rc, out, err = await _run_git(["init"], cwd=str(Path(working_directory).resolve()))
    if rc != 0:
        raise InitializationError(
            "Failed to initialize git repository",
            detail=(out + "\n" + err).strip(),
        )


async def ensure_initial_commit(working_directory: str) -> None:
    """
    Create an empty initial commit if the repo has no commits yet.

    This prevents `git worktree add -b ...` and later merges from failing when the
    target branch has no history (e.g. freshly `git init`'d repos).
    """
    root = str(Path(working_directory).resolve())
    rc, _out, _err = await _run_git(["rev-parse", "HEAD"], cwd=root)
    if rc == 0:
        return

    logger.debug("Initial commit needed | path=%s", root)
    rc2, out2, err2 = await _run_git(
        ["-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "chore: initial zeno commit"],
        cwd=root,
    )
    if rc2 != 0:
        raise InitializationError(
            "Failed to create initial git commit",
            detail=(out2 + "\n" + err2).strip(),
        )
    logger.info("Initial commit created | path=%s", root)


async def create_worktree(
    working_directory: str,
    session_id: UUID,
    task_id: UUID,
) -> tuple[str, str]:
    root = Path(working_directory).resolve()
    worktree_path = root / ".zeno" / "worktrees" / str(session_id) / str(task_id)
    worktree_path.parent.mkdir(parents=True, exist_ok=True)

    branch_name = f"zeno/{session_id}/{task_id}"

    logger.debug("Worktree create | path=%s branch=%s", str(worktree_path), branch_name)
    rc, out, err = await _run_git(
        ["worktree", "add", "-b", branch_name, str(worktree_path)],
        cwd=str(root),
    )
    if rc != 0:
        raise InitializationError(
            "Failed to create git worktree",
            detail=(out + "\n" + err).strip(),
        )

    return str(worktree_path), branch_name


async def merge_worktree(
    working_directory: str,
    branch_name: str,
    task_title: str,
) -> None:
    root = Path(working_directory).resolve()
    msg = f"zeno: {task_title}".strip()

    logger.info("Merge | branch=%s msg=%s", branch_name, msg)
    rc, out, err = await _run_git(
        ["-c", "commit.gpgsign=false", "merge", "--no-ff", branch_name, "-m", msg],
        cwd=str(root),
    )
    if rc != 0:
        raise MergeError(
            f"Failed to merge worktree branch for task: {task_title}",
            detail=(out + "\n" + err).strip(),
        )
    logger.info("Merge complete | branch=%s", branch_name)


async def cleanup_worktree(
    working_directory: str,
    worktree_path: str,
    branch_name: str,
) -> None:
    root = Path(working_directory).resolve()

    rc1, out1, err1 = await _run_git(
        ["worktree", "remove", "--force", worktree_path],
        cwd=str(root),
    )
    if rc1 != 0:
        logger.warning("worktree remove failed: %s", (out1 + "\n" + err1).strip())

    rc2, out2, err2 = await _run_git(["branch", "-D", branch_name], cwd=str(root))
    if rc2 != 0:
        logger.warning("branch delete failed: %s", (out2 + "\n" + err2).strip())
    logger.debug("Worktree cleanup | path=%s branch=%s", worktree_path, branch_name)


async def get_changed_files(worktree_path: str) -> tuple[list[str], list[str], list[str]]:
    """
    Return (created, updated, deleted) file lists by parsing `git status --porcelain`.

    Called as a fallback when the worker's structured_output could not be reconciled,
    so the orchestrator can still record which artifacts exist on disk.
    """
    root = str(Path(worktree_path).resolve())
    rc, out, _ = await _run_git(["status", "--porcelain"], cwd=root)
    if rc != 0 or not out.strip():
        return [], [], []

    created: list[str] = []
    updated: list[str] = []
    deleted: list[str] = []

    for line in out.splitlines():
        if len(line) < 4:
            continue
        xy = line[:2]
        path = line[3:].strip()
        # Handle renames: "old -> new" format
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        x, y = xy[0], xy[1]
        # Untracked files (not staged at all)
        if xy == "??":
            created.append(path)
        elif x == "D" or y == "D":
            deleted.append(path)
        elif x == "A" or y == "A":
            created.append(path)
        else:
            updated.append(path)

    return created, updated, deleted


async def commit_worktree_changes(worktree_path: str, task_title: str) -> None:
    """Stage and commit all changes in the worktree."""
    root = str(Path(worktree_path).resolve())

    rc, out, err = await _run_git(["status", "--porcelain"], cwd=root)
    if rc != 0:
        raise InitializationError(
            "Failed to check worktree status",
            detail=(out + "\n" + err).strip(),
        )
    if not out.strip():
        logger.warning("Nothing to commit | path=%s", root)
        return

    logger.debug("Staging changes | path=%s files=%s", root, out.strip())
    rc2, out2, err2 = await _run_git(["add", "-A"], cwd=root)
    if rc2 != 0:
        raise InitializationError(
            "Failed to stage worktree changes",
            detail=(out2 + "\n" + err2).strip(),
        )

    msg = f"feat: {task_title}".strip()
    rc3, out3, err3 = await _run_git(["-c", "commit.gpgsign=false", "commit", "-m", msg], cwd=root)
    if rc3 != 0:
        raise InitializationError(
            "Failed to commit worktree changes",
            detail=(out3 + "\n" + err3).strip(),
        )
    logger.info("Committed | path=%s message=%s", root, msg)

