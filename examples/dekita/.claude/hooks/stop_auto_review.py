#!/usr/bin/env python3
"""セッション終了時に未レビューの変更を検出しレビュー実行を促す。

Why:
    実装完了後にレビューせずセッションを終了すると、動作不備を見逃す。
    セッション終了時に未レビューを検出してレビュー実行を強制する。

What:
    - セッション終了時（Stop）に発火
    - main以外のブランチで未プッシュコミットまたは未コミット変更を検出
    - codex reviewの実行履歴を確認
    - 未レビューの場合はセッション終了をブロック

State:
    - reads: .claude/logs/markers/codex-review-*.done
    - writes: /tmp/claude-hooks/stop-auto-review-*.json（リトライカウント）

Remarks:
    - ブロック型フック（未レビュー時はセッション終了をブロック）
    - MAX_REVIEW_RETRIES（2回）を超えると自動許可
    - SKIP_STOP_AUTO_REVIEW=1でスキップ可能

Changelog:
    - silenvx/dekita#2166: フック追加
"""

import json
import os
import sys
import tempfile
from pathlib import Path

# Add hooks directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from common import MARKERS_LOG_DIR
from lib.execution import log_hook_execution
from lib.git import get_current_branch, get_diff_hash, get_head_commit
from lib.results import make_approve_result, make_block_result
from lib.session import create_hook_context, parse_hook_input
from lib.strings import sanitize_branch_name

# セッション状態ディレクトリ
SESSION_DIR = Path(tempfile.gettempdir()) / "claude-hooks"

# 最大リトライ回数（無限ループ防止）
MAX_REVIEW_RETRIES = 2

# スキップ用環境変数
SKIP_STOP_AUTO_REVIEW_ENV = "SKIP_STOP_AUTO_REVIEW"


def get_state_file(session_id: str) -> Path:
    """Get the file path for storing stop-auto-review state.

    Args:
        session_id: The Claude session ID to scope the file.

    Returns:
        Path to session-specific state file.
    """
    return SESSION_DIR / f"stop-auto-review-{session_id}.json"


def load_state(session_id: str) -> dict:
    """Load stop-auto-review state.

    Args:
        session_id: The Claude session ID.

    Returns:
        State dictionary with retry_count.
    """
    state_file = get_state_file(session_id)
    if state_file.exists():
        try:
            return json.loads(state_file.read_text())
        except Exception:
            pass  # Best effort - corrupted state is ignored
    return {"retry_count": 0}


def save_state(session_id: str, state: dict) -> None:
    """Save stop-auto-review state.

    Args:
        session_id: The Claude session ID.
        state: State dictionary to save.
    """
    try:
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        state_file = get_state_file(session_id)
        state_file.write_text(json.dumps(state))
    except Exception:
        pass  # Best effort


def has_unpushed_commits() -> bool:
    """Check if there are unpushed commits on the current branch.

    Returns:
        True if there are commits ahead of the remote or if the branch
        has no upstream (new branch with local commits).
    """
    import subprocess

    try:
        # First check if branch has an upstream
        upstream_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "@{upstream}"],
            capture_output=True,
            text=True,
            timeout=5,
        )

        if upstream_result.returncode != 0:
            # No upstream - check if there are any local commits
            # Compare with main branch to see if we have local work
            log_result = subprocess.run(
                ["git", "log", "main..HEAD", "--oneline"], capture_output=True, text=True, timeout=5
            )
            if log_result.returncode == 0 and log_result.stdout.strip():
                return True  # Has commits not in main
            return False

        # Has upstream - check if we're ahead
        result = subprocess.run(["git", "status", "-sb"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            # Look for "[ahead N]" pattern
            return "ahead" in result.stdout
    except Exception:
        pass  # Git command may fail in non-git directories or timeout
    return False


def has_uncommitted_changes() -> bool:
    """Check if there are uncommitted changes.

    Returns:
        True if working directory or staging area has changes.
    """
    import subprocess

    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return bool(result.stdout.strip())
    except Exception:
        pass  # Git command may fail in non-git directories or timeout
    return False


def check_review_done(
    branch: str, commit: str | None, current_diff_hash: str | None = None
) -> bool:
    """Check if codex review was executed for this branch at current commit or same diff.

    This mirrors the logic from codex-review-check.py.

    Args:
        branch: The git branch name.
        commit: The current HEAD commit hash.
        current_diff_hash: The current diff hash for comparison (optional).

    Returns:
        True if review was done for the current commit or same diff.
    """
    safe_branch = sanitize_branch_name(branch)
    log_file = MARKERS_LOG_DIR / f"codex-review-{safe_branch}.done"

    if not log_file.exists():
        return False

    content = log_file.read_text().strip()

    # Parse branch:commit:diff_hash (3 parts) or branch:commit (2 parts) format
    parts = content.split(":")
    if len(parts) >= 2:
        reviewed_commit = parts[1]
        reviewed_diff_hash = parts[2] if len(parts) >= 3 else None

        # Check if reviewed commit matches current HEAD
        if commit and reviewed_commit == commit:
            return True

        # If commit doesn't match, check if diff hash matches
        # This allows skipping re-review after rebase when actual diff is unchanged
        if current_diff_hash and reviewed_diff_hash and current_diff_hash == reviewed_diff_hash:
            return True

    return False


def main():
    """Stop hook to suggest codex review before session end."""
    try:
        input_data = parse_hook_input()

        ctx = create_hook_context(input_data)

        # Skip if Stop hook is already active (prevent recursion)
        if input_data.get("stop_hook_active"):
            print(json.dumps({"decision": "approve"}))
            return

        # Check skip environment variable
        if os.environ.get(SKIP_STOP_AUTO_REVIEW_ENV) == "1":
            log_hook_execution(
                "stop-auto-review",
                "approve",
                "Skipped via SKIP_STOP_AUTO_REVIEW environment variable",
            )
            print(json.dumps(make_approve_result("stop-auto-review", "Skipped via env")))
            return

        # Get current branch
        branch = get_current_branch()

        # Skip for main/master branches
        if branch in ("main", "master", None):
            log_hook_execution("stop-auto-review", "approve", f"Skipped for branch: {branch}")
            print(json.dumps(make_approve_result("stop-auto-review")))
            return

        # Check session state for retry count
        session_id = ctx.get_session_id()
        state = load_state(session_id)
        retry_count = state.get("retry_count", 0)

        # If max retries reached, allow session to end
        if retry_count >= MAX_REVIEW_RETRIES:
            log_hook_execution(
                "stop-auto-review",
                "approve",
                f"Max retries ({MAX_REVIEW_RETRIES}) reached, allowing session end",
                {"retry_count": retry_count},
            )
            print(json.dumps(make_approve_result("stop-auto-review")))
            return

        # Check if there are changes to review
        uncommitted = has_uncommitted_changes()
        unpushed = has_unpushed_commits()

        if not uncommitted and not unpushed:
            log_hook_execution(
                "stop-auto-review", "approve", "No unpushed commits or uncommitted changes"
            )
            print(json.dumps(make_approve_result("stop-auto-review")))
            return

        # If there are uncommitted changes, always require review
        # (even if the last commit was reviewed, new changes need review)
        if uncommitted:
            # Skip to blocking - uncommitted changes always need review
            pass
        else:
            # Only unpushed commits - check if review is already done
            commit = get_head_commit()
            current_diff_hash = get_diff_hash()
            if check_review_done(branch, commit, current_diff_hash):
                log_hook_execution(
                    "stop-auto-review",
                    "approve",
                    f"Review already done for {branch}@{commit[:7] if commit else 'unknown'}",
                )
                print(json.dumps(make_approve_result("stop-auto-review")))
                return

        commit = get_head_commit()

        # Increment retry count and save
        state["retry_count"] = retry_count + 1
        save_state(session_id, state)

        # Block and suggest review
        reason = (
            f"セッション終了前にコードレビューを実行してください。\n\n"
            f"**ブランチ**: {branch}\n"
            f"**コミット**: {commit[:7] if commit else 'unknown'}\n"
            f"**試行回数**: {retry_count + 1}/{MAX_REVIEW_RETRIES}\n\n"
            "以下のコマンドでローカルレビューを実行:\n\n"
            "```bash\n"
            "codex review --base main\n"
            "```\n\n"
            "レビュー完了後、問題があれば修正してください。\n"
            f"（{MAX_REVIEW_RETRIES}回試行後は自動的にセッション終了を許可します）"
        )

        log_hook_execution(
            "stop-auto-review",
            "block",
            f"Review not done for {branch}@{commit[:7] if commit else 'unknown'}",
            {"retry_count": retry_count + 1, "branch": branch},
        )

        result = make_block_result("stop-auto-review", reason, ctx)
        print(json.dumps(result))

    except Exception as e:
        # Hook failures should not block session
        log_hook_execution("stop-auto-review", "approve", f"Hook error: {e}")
        print(json.dumps(make_approve_result("stop-auto-review", f"Hook error: {e}")))


if __name__ == "__main__":
    main()
