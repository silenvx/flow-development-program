"""Monitor state management.

This module handles saving and loading ci-monitor state for background execution support.
Extracted from ci-monitor.py as part of Issue #1754 refactoring.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# State file configuration
STATE_FILE_DIR = ".claude/state"
STATE_FILE_PREFIX = "ci-monitor-"


def _get_main_repo_path() -> Path:
    """Get the main repository path (works from worktree too).

    Returns:
        Path to the main repository root.
    """
    try:
        # Try to get main repo via worktree list
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                if line.startswith("worktree "):
                    return Path(line[9:])  # First entry is main repo
    except Exception:
        # git worktree list が失敗した場合（git 未インストール、タイムアウト等）
        # はフォールバック手段を試みる
        pass

    # Fall back to current directory's git root
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip())
    except Exception:
        # git rev-parse も失敗した場合は cwd にフォールバックする
        pass

    return Path.cwd()


def get_state_file_path(pr_number: str) -> Path:
    """Get the state file path for a PR.

    Issue #1311: State files are stored in the main repository's .claude/state directory,
    not in worktrees, to allow status checking from any location.

    Args:
        pr_number: The PR number.

    Returns:
        Path to the state file.

    Raises:
        ValueError: If pr_number contains invalid characters.
    """
    if not pr_number.isalnum():
        raise ValueError(f"Invalid pr_number specified: {pr_number}")

    base_dir = _get_main_repo_path()
    return base_dir / STATE_FILE_DIR / f"{STATE_FILE_PREFIX}{pr_number}.json"


def save_monitor_state(pr_number: str, state: dict[str, Any]) -> bool:
    """Save monitor state to file for background execution support.

    Issue #1311: Allows status checking while ci-monitor runs in background.
    Uses atomic write (temp file + rename) to prevent partial reads during status checks.

    Args:
        pr_number: The PR number being monitored.
        state: Dictionary containing current monitor state (not mutated).

    Returns:
        True if save succeeded, False otherwise.
    """
    temp_file: Path | None = None
    try:
        state_file = get_state_file_path(pr_number)
        state_file.parent.mkdir(parents=True, exist_ok=True)

        state_to_save = dict(state)
        state_to_save["updated_at"] = datetime.now(UTC).isoformat()
        state_to_save["pr_number"] = pr_number

        temp_file = state_file.with_suffix(".tmp")
        temp_file.write_text(json.dumps(state_to_save, indent=2, ensure_ascii=False))
        temp_file.rename(state_file)
        return True
    except ValueError as e:
        print(f"Warning: Invalid PR number: {e}", file=sys.stderr)
        return False
    except OSError as e:
        print(f"Warning: Failed to save state: {e}", file=sys.stderr)
        if temp_file is not None and temp_file.exists():
            try:
                temp_file.unlink()
            except OSError:
                # ベストエフォートのクリーンアップとして一時ファイル削除を試みるが、
                # 削除に失敗してもアプリケーションの動作には影響しないため無視する
                pass
        return False


def load_monitor_state(pr_number: str) -> dict[str, Any] | None:
    """Load saved monitor state from file.

    Issue #1311: Used by --status and --result options.

    Args:
        pr_number: The PR number to load state for.

    Returns:
        State dictionary if found, None otherwise.
    """
    try:
        state_file = get_state_file_path(pr_number)
        if state_file.exists():
            return json.loads(state_file.read_text())
        return None
    except ValueError as e:
        print(f"Warning: Invalid PR number: {e}", file=sys.stderr)
        return None
    except (OSError, json.JSONDecodeError) as e:
        print(f"Warning: Failed to load state: {e}", file=sys.stderr)
        return None


def clear_monitor_state(pr_number: str) -> bool:
    """Clear saved monitor state file.

    Issue #1311: Called when monitoring completes to clean up.

    Args:
        pr_number: The PR number to clear state for.

    Returns:
        True if cleared or didn't exist, False on error.
    """
    try:
        state_file = get_state_file_path(pr_number)
        if state_file.exists():
            state_file.unlink()
        return True
    except ValueError as e:
        print(f"Warning: Invalid PR number: {e}", file=sys.stderr)
        return False
    except OSError as e:
        print(f"Warning: Failed to clear state: {e}", file=sys.stderr)
        return False
