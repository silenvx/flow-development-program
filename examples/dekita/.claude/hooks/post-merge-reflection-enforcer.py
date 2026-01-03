#!/usr/bin/env python3
"""PRマージ成功後に振り返りを即時実行させる。

Why:
    PRマージ後の振り返りを後回しにすると、コンテキストが失われ、
    教訓が得られない。即時実行を促進することで、振り返りの質を高める。

What:
    - gh pr mergeの成功を検出
    - decision: "block" + continue: trueで即時アクションを誘発
    - [IMMEDIATE: /reflect]をsystemMessageで出力（トランスクリプトに記録）
    - reflection-completion-check.pyがタグを検出して実行を強制

Remarks:
    - reflection-reminderはリマインド表示、本フックは即時実行促進
    - ステートレス設計（状態ファイル管理なし）
    - guard_rules.pyがworktree内マージも同様に処理

Changelog:
    - silenvx/dekita#2089: block+continueパターン採用
    - silenvx/dekita#2159: ステートレス化
    - silenvx/dekita#2416: worktree削除後の対応
"""

import json
import os
import re
from pathlib import Path

from lib.execution import log_hook_execution
from lib.hook_input import get_exit_code, get_tool_result
from lib.repo import get_repo_root, is_merge_success
from lib.session import parse_hook_input
from lib.strings import strip_quoted_strings


def _check_project_dir_valid() -> tuple[bool, Path | None]:
    """Check if CLAUDE_PROJECT_DIR is valid and return repo root path.

    Returns:
        Tuple of (is_valid, repo_root_path).
        - is_valid: True if CLAUDE_PROJECT_DIR exists
        - repo_root_path: Repository root path if determinable, else None
          - When project dir exists: returns get_repo_root(project_path)
          - When project dir doesn't exist (worktree deleted): returns
            original repo path extracted from .worktrees pattern

    Issue #2416: Worktree may be deleted after merge, causing Skill failures.
    """
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not project_dir:
        return True, None  # No project dir set, assume valid

    project_path = Path(project_dir)
    if project_path.exists():
        # Project dir exists, get original repo for reference
        return True, get_repo_root(project_path)

    # Project dir doesn't exist (worktree deleted)
    # Try to find original repo from parent path pattern
    # Pattern: /path/to/repo/.worktrees/issue-xxx -> /path/to/repo
    if ".worktrees" in project_path.parts:
        worktrees_idx = project_path.parts.index(".worktrees")
        # worktrees_idx が 0 の場合、Path(*project_path.parts[:0]) は Path() となり
        # カレントディレクトリ (".") を指してしまうため、0 より大きい場合のみ許可する。
        if worktrees_idx > 0:
            original_path = Path(*project_path.parts[:worktrees_idx])
            if original_path.exists():
                return False, original_path

    return False, None


def is_pr_merge_command(command: str) -> bool:
    """Check if the command is a PR merge command.

    Returns False for:
    - Commands inside quoted strings (e.g., echo 'gh pr merge 123')
    - gh pr merge appearing INSIDE heredoc data (after << marker)
    - Empty commands

    Issue #2553: Avoid false positives from test data in heredoc/cat commands.

    Note: Strip quoted strings FIRST, then check positions.
    If gh pr merge appears BEFORE a heredoc, it's a real command.
    If gh pr merge appears AFTER a heredoc start, it's likely test data.
    """
    if not command.strip():
        return False

    # Strip quoted strings first to avoid false negatives
    # e.g., echo 'test <<' && gh pr merge 123 should be detected
    stripped_command = strip_quoted_strings(command)

    # Find gh pr merge position
    merge_match = re.search(r"gh\s+pr\s+merge", stripped_command)
    if not merge_match:
        return False

    # Find heredoc pattern position (cat/tee/bash/sh/zsh/ksh followed by << or <<-)
    # Use (?<!<)<<-?(?!<) to match << or <<- but exclude <<< (here-string)
    # Negative lookbehind (?<!<) ensures << is not preceded by <
    # Negative lookahead (?!<) ensures << is not followed by <
    heredoc_match = re.search(
        r"\b(cat|tee|bash|sh|zsh|ksh)\b[^\n]*(?<!<)<<-?(?!<)", stripped_command
    )

    if heredoc_match:
        # If merge command appears BEFORE heredoc, it's a real command
        # e.g., "gh pr merge 123 && cat <<EOF" -> merge at 0, heredoc at 18
        # If merge command appears AFTER heredoc start, it's in heredoc data
        # e.g., "cat <<EOF\ngh pr merge 123\nEOF" -> heredoc at 0, merge at 10
        if merge_match.start() > heredoc_match.start():
            return False

    return True


def _check_merge_success(tool_result: dict, command: str = "") -> bool:
    """Check if the merge was successful.

    Wrapper around common.is_merge_success for backward compatibility.
    Issue #2203: Use get_exit_code() for consistent default value.
    """
    exit_code = get_exit_code(tool_result)
    stdout = tool_result.get("stdout", "") if isinstance(tool_result, dict) else ""
    stderr = tool_result.get("stderr", "") if isinstance(tool_result, dict) else ""
    return is_merge_success(exit_code, stdout, command, stderr=stderr)


def extract_pr_number(command: str) -> str | None:
    """Extract PR number from command."""
    match = re.search(r"gh\s+pr\s+merge\s+(\d+)", command)
    if match:
        return match.group(1)
    return None


def main():
    """Enforce reflection requirement after PR merge.

    Issue #2089: Uses decision: "block" + continue: true pattern to force
    Claude Code to execute /reflect immediately after merge.

    Issue #2159: Stateless implementation - no state files used.
    """
    result = {"continue": True}

    try:
        input_data = parse_hook_input()
        tool_name = input_data.get("tool_name", "")

        if tool_name != "Bash":
            print(json.dumps(result))
            return

        tool_input = input_data.get("tool_input", {})
        tool_result = get_tool_result(input_data) or {}
        command = tool_input.get("command", "")

        # Check if this is a successful PR merge
        if is_pr_merge_command(command) and _check_merge_success(tool_result, command):
            pr_number = extract_pr_number(command)

            # Issue #2416: Check if project directory is still valid
            is_valid, original_repo = _check_project_dir_valid()

            log_hook_execution(
                "post-merge-reflection-enforcer",
                "block",
                f"Triggering immediate reflection for PR #{pr_number}",
                {"project_dir_valid": is_valid, "original_repo": str(original_repo)},
            )

            # Issue #2089: Use decision: "block" + continue: true to force action
            # Issue #2364: Output to both reason AND systemMessage
            # - reason: for Claude Code to read (may not be in transcript)
            # - systemMessage: recorded in transcript for detection by
            #   reflection-completion-check.py
            if is_valid:
                message = (
                    f"✅ PR #{pr_number or '?'} マージ完了\n\n"
                    "**動作確認チェックリスト**:\n"
                    "- [ ] 正常系: 期待動作の確認\n"
                    "- [ ] 異常系: エラーハンドリングの確認\n"
                    "- [ ] Dogfooding: 自分で使って問題ないか確認\n\n"
                    "[IMMEDIATE: /reflect]\n"
                    "振り返り（五省）を行い、教訓をIssue化してください。"
                )
            else:
                # Issue #2416: Worktree deleted, guide to original repo
                original_path = str(original_repo) if original_repo else "オリジナルリポジトリ"
                message = (
                    f"✅ PR #{pr_number or '?'} マージ完了\n\n"
                    "⚠️ **worktreeが削除されています**\n\n"
                    f"振り返りを実行する前に、オリジナルリポジトリに移動してください:\n"
                    f"```bash\ncd {original_path}\n```\n\n"
                    "**動作確認チェックリスト**:\n"
                    "- [ ] 正常系: 期待動作の確認\n"
                    "- [ ] 異常系: エラーハンドリングの確認\n"
                    "- [ ] Dogfooding: 自分で使って問題ないか確認\n\n"
                    "移動後、以下を実行:\n"
                    "[IMMEDIATE: /reflect]\n"
                    "振り返り（五省）を行い、教訓をIssue化してください。"
                )
            result = {
                "decision": "block",
                "continue": True,  # Don't stop, but force Claude to read message
                "reason": message,
                "systemMessage": message,
            }
            print(json.dumps(result))
            return

    except Exception as e:
        log_hook_execution(
            "post-merge-reflection-enforcer",
            "error",
            f"Hook error: {e}",
        )

    print(json.dumps(result))


if __name__ == "__main__":
    main()
