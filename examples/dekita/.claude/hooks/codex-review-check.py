#!/usr/bin/env python3
"""Codex CLIレビュー実行をPR作成・push前に強制する。

Why:
    コードレビューなしでPRを作成・pushすると、品質問題がCIやAIレビューで
    初めて発覚し、手戻りが発生する。事前のローカルレビューで品質を担保する。

What:
    - gh pr create / git pushコマンドを検出
    - 現在のブランチ・コミットでcodex reviewが実行済みか確認
    - 未実行またはコミット変更後は再レビューを要求
    - リベース後も差分が同一ならスキップ

State:
    - reads: .claude/logs/markers/codex-review-{branch}.done

Remarks:
    - ブロック型フック（レビュー未実行時はブロック）
    - PreToolUse:Bashで発火（gh pr create/git pushコマンド）
    - main/masterブランチはスキップ
    - SKIP_CODEX_REVIEW=1でバイパス可能
    - codex-review-logger.pyと連携（マーカーファイル読み込み）

Changelog:
    - silenvx/dekita#841: リベース後のdiffハッシュ比較でスキップ
    - silenvx/dekita#890: マージ済みPRのスキップ
    - silenvx/dekita#945: SKIP_CODEX_REVIEW環境変数対応
    - silenvx/dekita#956: truthy値の厳格化
"""

import json
import os
import re
import sys

from common import MARKERS_LOG_DIR
from lib.execution import log_hook_execution
from lib.git import get_current_branch, get_diff_hash, get_head_commit
from lib.github import get_pr_number_for_branch, is_pr_merged
from lib.results import make_approve_result, make_block_result
from lib.session import parse_hook_input
from lib.strings import (
    extract_inline_skip_env,
    is_skip_env_enabled,
    sanitize_branch_name,
    strip_quoted_strings,
)


def is_gh_pr_create_command(command: str) -> bool:
    """Check if command is actually a gh pr create command.

    Returns False for:
    - Commands inside quoted strings (e.g., echo 'gh pr create')
    - Empty commands
    """
    if not command.strip():
        return False

    # Strip quoted strings to avoid false positives
    stripped_command = strip_quoted_strings(command)

    # Check if gh pr create exists in the stripped command
    return bool(re.search(r"gh\s+pr\s+create\b", stripped_command))


SKIP_CODEX_REVIEW_ENV = "SKIP_CODEX_REVIEW"


def has_skip_codex_review_env(command: str) -> bool:
    """Check if SKIP_CODEX_REVIEW environment variable is set with truthy value.

    Supports both:
    - Exported: 環境変数が既に設定されている状態で `git push` を実行
    - Inline: SKIP_CODEX_REVIEW=1 git push ... (including SKIP_CODEX_REVIEW="1")

    Only "1", "true", "True" are considered truthy (Issue #956).

    Args:
        command: The command string to check for inline env var.

    Returns:
        True if SKIP_CODEX_REVIEW is set with truthy value, False otherwise.
    """
    # Check exported environment variable with value validation
    if is_skip_env_enabled(os.environ.get(SKIP_CODEX_REVIEW_ENV)):
        return True
    # Check inline environment variable in command (handles quoted values)
    inline_value = extract_inline_skip_env(command, SKIP_CODEX_REVIEW_ENV)
    if is_skip_env_enabled(inline_value):
        return True
    return False


def is_git_push_command(command: str) -> bool:
    """Check if command is a git push command.

    Returns False for:
    - Commands inside quoted strings (e.g., echo 'git push')
    - Empty commands
    - git push --help or similar non-push operations
    """
    if not command.strip():
        return False

    # Strip quoted strings to avoid false positives
    stripped_command = strip_quoted_strings(command)

    # Check if git push exists in the stripped command
    # Match "git push" but not "git push --help"
    if not re.search(r"git\s+push\b", stripped_command):
        return False

    # Exclude help commands
    if re.search(r"--help", stripped_command):
        return False

    return True


def check_review_done(
    branch: str, commit: str | None, current_diff_hash: str | None
) -> tuple[bool, str | None, bool]:
    """Check if codex review was executed for this branch at current commit or same diff.

    Args:
        branch: The git branch name.
        commit: The current HEAD commit hash.
        current_diff_hash: The current diff hash for comparison.

    Returns:
        Tuple of (is_reviewed, reviewed_commit, diff_matched).
        is_reviewed: True if review was done for the current commit or same diff.
        reviewed_commit: The commit hash that was reviewed (if any).
        diff_matched: True if review was approved due to diff hash match (not commit match).
    """
    safe_branch = sanitize_branch_name(branch)
    log_file = MARKERS_LOG_DIR / f"codex-review-{safe_branch}.done"

    if not log_file.exists():
        return False, None, False

    content = log_file.read_text().strip()

    # Parse branch:commit:diff_hash (3 parts) or branch:commit (2 parts) format
    # Note: codex-review-logger.py may write 2-part format if diff_hash is unavailable
    parts = content.split(":")
    if len(parts) >= 2:
        reviewed_commit = parts[1]
        reviewed_diff_hash = parts[2] if len(parts) >= 3 else None

        # Check if reviewed commit matches current HEAD
        if commit and reviewed_commit == commit:
            return True, reviewed_commit, False

        # If commit doesn't match, check if diff hash matches (Issue #841)
        # This allows skipping re-review after rebase when actual diff is unchanged
        if current_diff_hash and reviewed_diff_hash and current_diff_hash == reviewed_diff_hash:
            return True, reviewed_commit, True

        return False, reviewed_commit, False

    # Invalid format (only branch name) - treat as not reviewed
    return False, None, False


def get_block_reason(
    branch: str,
    commit: str | None,
    reviewed_commit: str | None,
    command_type: str,
) -> str:
    """Generate block reason message based on review state.

    Args:
        branch: Current branch name.
        commit: Current HEAD commit.
        reviewed_commit: Last reviewed commit (if any).
        command_type: Either "pr_create" or "git_push".

    Returns:
        Block reason message.
    """
    action = "PRを作成する" if command_type == "pr_create" else "プッシュする"

    if reviewed_commit and commit:
        # Review was done but for a different commit
        return (
            f"Codex CLIレビュー後に新しいコミットがあります。\n"
            f"- ブランチ: {branch}\n"
            f"- レビュー済みコミット: {reviewed_commit}\n"
            f"- 現在のHEAD: {commit}\n\n"
            "新しいコミットに対してレビューを再実行してください:\n\n"
            "```bash\n"
            "codex review --base main\n"
            "```"
        )
    else:
        # No review record found
        return (
            f"Codex CLIレビューが実行されていません（ブランチ: {branch}）。\n\n"
            f"{action}前に、以下のコマンドでローカルレビューを実行してください:\n\n"
            "```bash\n"
            "codex review --base main\n"
            "```\n\n"
            f"レビュー完了後、再度{action}してください。\n"
            "（参考: AGENTS.md「Codex CLIローカルレビュー」セクション）"
        )


def check_and_block_if_not_reviewed(command_type: str) -> dict | None:
    """Check if review is done for current branch and return block result if not.

    Args:
        command_type: Either "pr_create" or "git_push".

    Returns:
        Block result dict if review is not done, None if review is done.
    """
    branch = get_current_branch()

    # Skip check for main/master branches
    if branch in ("main", "master"):
        return None

    # Issue #890: Skip check if the branch's PR is already merged
    # This prevents false positives when another hook (e.g., locked-worktree-guard)
    # has already completed the merge before this hook runs.
    if branch:
        pr_number = get_pr_number_for_branch(branch)
        if pr_number and is_pr_merged(pr_number):
            log_hook_execution(
                "codex-review-check",
                "approve",
                f"PR #{pr_number} for branch '{branch}' is already merged, skipping check",
            )
            return None

    # Block if branch is None (git error)
    if branch is None:
        return make_block_result(
            "codex-review-check",
            "ブランチ名を取得できませんでした。\n"
            "gitリポジトリ内で実行しているか確認してください。\n"
            "\n【対処法】\n"
            "1. カレントディレクトリがgitリポジトリ内か確認: git status\n"
            "2. .gitディレクトリが存在するか確認: ls -la .git\n"
            "3. リポジトリルートに移動してから再実行",
        )

    commit = get_head_commit()
    current_diff_hash = get_diff_hash()
    is_reviewed, reviewed_commit, diff_matched = check_review_done(
        branch, commit, current_diff_hash
    )

    if not is_reviewed:
        reason = get_block_reason(branch, commit, reviewed_commit, command_type)
        return make_block_result("codex-review-check", reason)

    # Log if review was approved due to diff hash match (Issue #841)
    if diff_matched:
        log_hook_execution(
            "codex-review-check",
            "approve",
            f"Diff hash match: リベース後も差分が同一のためスキップ (branch={branch}, reviewed_commit={reviewed_commit})",
        )

    return None


def main():
    """
    PreToolUse hook for Bash commands.

    Blocks `gh pr create` and `git push` if Codex CLI review has not been executed.
    """
    try:
        data = parse_hook_input()
        tool_input = data.get("tool_input", {})
        command = tool_input.get("command", "")

        # Check for SKIP_CODEX_REVIEW environment variable (Issue #945)
        if has_skip_codex_review_env(command):
            log_hook_execution(
                "codex-review-check",
                "approve",
                "SKIP_CODEX_REVIEW でスキップ",
            )
            result = make_approve_result("codex-review-check", "SKIP_CODEX_REVIEW でスキップ")
            print(json.dumps(result))
            sys.exit(0)

        # Detect gh pr create command (excluding quoted strings)
        if is_gh_pr_create_command(command):
            block_result = check_and_block_if_not_reviewed("pr_create")
            if block_result:
                log_hook_execution(
                    "codex-review-check", "block", block_result.get("reason"), {"type": "pr_create"}
                )
                print(json.dumps(block_result))
                sys.exit(0)

        # Detect git push command (excluding quoted strings)
        if is_git_push_command(command):
            block_result = check_and_block_if_not_reviewed("git_push")
            if block_result:
                log_hook_execution(
                    "codex-review-check", "block", block_result.get("reason"), {"type": "git_push"}
                )
                print(json.dumps(block_result))
                sys.exit(0)

        # All checks passed
        result = make_approve_result("codex-review-check")

    except Exception as e:
        print(f"[codex-review-check] Hook error: {e}", file=sys.stderr)
        result = make_approve_result("codex-review-check", f"Hook error: {e}")

    log_hook_execution("codex-review-check", result.get("decision", "approve"))
    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
