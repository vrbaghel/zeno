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

    rc2, out2, err2 = await _run_git(
        ["-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "chore: initial zeno commit"],
        cwd=root,
    )
    if rc2 != 0:
        raise InitializationError(
            "Failed to create initial git commit",
            detail=(out2 + "\n" + err2).strip(),
        )


async def create_worktree(
    working_directory: str,
    session_id: UUID,
    task_id: UUID,
) -> tuple[str, str]:
    root = Path(working_directory).resolve()
    worktree_path = root / ".zeno" / "worktrees" / str(session_id) / str(task_id)
    worktree_path.parent.mkdir(parents=True, exist_ok=True)

    branch_name = f"zeno/{session_id}/{task_id}"

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

    rc, out, err = await _run_git(
        ["-c", "commit.gpgsign=false", "merge", "--no-ff", branch_name, "-m", msg],
        cwd=str(root),
    )
    if rc != 0:
        raise MergeError(
            f"Failed to merge worktree branch for task: {task_title}",
            detail=(out + "\n" + err).strip(),
        )


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
        return

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

