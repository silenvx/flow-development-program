#!/usr/bin/env python3
"""
Session end worktree cleanup hook.

Stop hook that automatically cleans up merged/closed worktrees at session end.

Issue #778: マージ済みworktreeが自動クリーンアップされない
Issue #1315: セッション終了時に現在のworktreeのロックを自動解除

Unlike worktree-cleanup-suggester (which only suggests), this hook
actually performs the cleanup when safe to do so.
"""

# SRP: セッション終了時のworktree自動クリーンアップ＆ロック解除
# 既存フック: worktree-cleanup-suggester（提案のみ）を補完
# Issue #1315: 現在作業中のworktreeのロックを自動解除

import json
import subprocess
from pathlib import Path

from lib.constants import TIMEOUT_LIGHT, TIMEOUT_MEDIUM
from lib.execution import log_hook_execution
from lib.session import parse_hook_input


def get_worktrees_info() -> list[dict]:
    """Get information about all worktrees.

    Returns list of dicts with path, branch, is_main, locked.
    """
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
        )
        if result.returncode != 0:
            return []

        worktrees = []
        current = {}
        for line in result.stdout.strip().split("\n"):
            if line.startswith("worktree "):
                if current:
                    worktrees.append(current)
                current = {"path": line[9:]}
            elif line.startswith("branch refs/heads/"):
                current["branch"] = line[18:]
            elif line.startswith("locked"):
                # Handle both "locked" and "locked <reason>" formats
                current["locked"] = True
        if current:
            worktrees.append(current)

        # Mark first as main
        for i, wt in enumerate(worktrees):
            wt["is_main"] = i == 0
            wt.setdefault("locked", False)
            wt.setdefault("branch", "unknown")

        return worktrees

    except (subprocess.TimeoutExpired, OSError):
        return []


def unlock_current_worktree(cwd: Path | None, worktrees: list[dict]) -> str | None:
    """Unlock the worktree that contains the current working directory.

    Issue #1315: When a session ends (e.g., context overflow), unlock the
    worktree so the next session can work with it (e.g., run ci-monitor).

    Args:
        cwd: Current working directory (resolved Path).
        worktrees: List of worktree info dicts from get_worktrees_info().

    Returns:
        Message describing the action taken, or None if no action needed.
    """
    if not cwd:
        return None

    for wt in worktrees:
        # Skip main repo
        if wt["is_main"]:
            continue

        wt_path = Path(wt["path"]).resolve()

        # Check if cwd is inside this worktree
        try:
            cwd.relative_to(wt_path)
        except ValueError:
            # cwd is not inside this worktree
            continue

        # Found the worktree containing cwd
        if not wt["locked"]:
            # Already unlocked, nothing to do
            return None

        # Unlock this worktree
        try:
            result = subprocess.run(
                ["git", "worktree", "unlock", str(wt_path)],
                capture_output=True,
                timeout=TIMEOUT_LIGHT,
                check=False,
            )
            if result.returncode == 0:
                return f"🔓 {wt_path.name} のロックを解除しました"
            # git command ran but failed (returncode != 0)
            return f"⚠️ {wt_path.name} のロック解除に失敗しました"
        except (subprocess.TimeoutExpired, OSError):
            return f"⚠️ {wt_path.name} のロック解除に失敗しました"

    return None


def get_pr_state(branch: str) -> str | None:
    """Get PR state for a branch (MERGED, CLOSED, OPEN, or None)."""
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--state",
                "all",
                "--head",
                branch,
                "--json",
                "state",
                "--jq",
                ".[0].state // empty",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        # Fail-open: If gh is unavailable or times out, skip cleanup for this worktree
        pass
    return None


def cleanup_worktree(worktree_path: str) -> tuple[bool, str]:
    """Remove a worktree (unlock first if needed).

    Returns (success, message).
    """
    path = Path(worktree_path)
    name = path.name

    try:
        # Unlock first
        subprocess.run(
            ["git", "worktree", "unlock", worktree_path],
            capture_output=True,
            timeout=TIMEOUT_LIGHT,
            check=False,
        )

        # Try normal remove
        result = subprocess.run(
            ["git", "worktree", "remove", worktree_path],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
            check=False,
        )
        if result.returncode == 0:
            return True, f"✅ {name} 削除完了"

        # Try force remove
        result = subprocess.run(
            ["git", "worktree", "remove", "-f", worktree_path],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
            check=False,
        )
        if result.returncode == 0:
            return True, f"✅ {name} 強制削除完了"

        return False, f"⚠️ {name} 削除失敗: {result.stderr.strip()}"

    except subprocess.TimeoutExpired:
        return False, f"⚠️ {name} 削除タイムアウト"
    except OSError as e:
        return False, f"⚠️ {name} エラー: {e}"


def main():
    """Stop hook for automatic worktree cleanup and unlock.

    1. Unlocks the current worktree (Issue #1315)
    2. Cleans up worktrees whose PRs are MERGED or CLOSED

    Skips locked worktrees (except for unlock) and the current worktree (for cleanup).
    """
    result = {"ok": True, "decision": "approve"}
    cleaned = []
    skipped = []
    unlocked_msg = None

    try:
        input_json = parse_hook_input()

        # Exit early on invalid/empty input (parse_hook_input returns {} on error)
        # This maintains "fail closed" behavior for cleanup operations
        if not input_json:
            log_hook_execution("session-end-worktree-cleanup", "approve", "empty_input")
            print(json.dumps(result))
            return

        # Prevent infinite loops
        if input_json.get("stop_hook_active"):
            log_hook_execution("session-end-worktree-cleanup", "approve", "stop_hook_active")
            print(json.dumps(result))
            return

        # Get current directory to avoid cleaning up current worktree
        try:
            cwd = Path.cwd().resolve()
        except OSError:
            cwd = None

        worktrees = get_worktrees_info()

        # Issue #1315: Unlock the current worktree before cleanup
        unlocked_msg = unlock_current_worktree(cwd, worktrees)

        for wt in worktrees:
            # Skip main repo
            if wt["is_main"]:
                continue

            wt_path = Path(wt["path"]).resolve()

            # Skip if cwd is inside this worktree
            if cwd:
                try:
                    cwd.relative_to(wt_path)
                    skipped.append(f"{wt_path.name} (cwd内)")
                    continue
                except ValueError:
                    # ValueError means cwd is NOT inside wt_path - safe to continue
                    pass

            # Skip locked worktrees
            if wt["locked"]:
                skipped.append(f"{wt_path.name} (ロック中)")
                continue

            # Check PR state
            pr_state = get_pr_state(wt["branch"])
            if pr_state not in ("MERGED", "CLOSED"):
                continue

            # Clean up
            success, msg = cleanup_worktree(str(wt_path))
            if success:
                cleaned.append(wt_path.name)
            else:
                skipped.append(f"{wt_path.name} (削除失敗)")

        # Build message
        if unlocked_msg or cleaned or skipped:
            parts = []
            if unlocked_msg:
                parts.append(unlocked_msg)
            if cleaned:
                parts.append(f"🧹 クリーンアップ完了: {', '.join(cleaned)}")
            if skipped:
                parts.append(f"スキップ: {', '.join(skipped)}")
            result["systemMessage"] = "\n".join(parts)
            result["reason"] = (
                f"Unlocked: {1 if unlocked_msg else 0}, cleaned: {len(cleaned)}, skipped: {len(skipped)}"
            )
        else:
            result["reason"] = "No worktrees to clean up or unlock"

    except Exception as e:
        result["reason"] = f"Error: {e}"

    log_hook_execution(
        "session-end-worktree-cleanup",
        result["decision"],
        result.get("reason"),
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
