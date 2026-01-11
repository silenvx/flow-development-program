#!/usr/bin/env python3
"""worktree作成時にブランチ名にIssue番号を含むことを強制。

Why:
    Issueを作成せずにworktreeを作成すると、作業の追跡が困難になる。
    ブランチ名にIssue番号を含めることで、作業とIssueを紐付ける。

What:
    - `git worktree add` コマンドを検出
    - ブランチ名にIssue番号（issue-123, #123等）が含まれているか確認
    - 含まれていない場合はブロック

Remarks:
    - ブロック型フック（Issue番号なしはブロック）
    - PreToolUse:Bashで発火

Changelog:
    - silenvx/dekita#2735: フック追加
"""

from __future__ import annotations

import json
import re

from lib.execution import log_hook_execution
from lib.results import make_approve_result, make_block_result
from lib.session import parse_hook_input

# Issue番号のパターン（issue-123, #123, Issue-123等）
ISSUE_PATTERNS = [
    r"issue-\d+",  # issue-123
    r"#\d+",  # #123
    r"Issue-\d+",  # Issue-123
    r"ISSUE-\d+",  # ISSUE-123
]


def extract_branch_name(command: str) -> str | None:
    """git worktree addコマンドからブランチ名を抽出する。

    Supports:
        - git worktree add <path> -b <branch>
        - git worktree add --lock <path> -b <branch>
        - git worktree add -b <branch> <path>
    """
    # -b オプションの後のブランチ名を抽出
    match = re.search(r"-b\s+([^\s]+)", command)
    if match:
        return match.group(1)
    return None


def has_issue_number(branch_name: str) -> bool:
    """ブランチ名にIssue番号が含まれているか確認する。"""
    for pattern in ISSUE_PATTERNS:
        if re.search(pattern, branch_name, re.IGNORECASE):
            return True
    return False


def main() -> None:
    """メイン処理。"""
    hook_input = parse_hook_input()
    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})
    command = tool_input.get("command", "")

    # Bashツール以外はスキップ
    if tool_name != "Bash":
        print(json.dumps(make_approve_result("issue-branch-check")))
        return

    # git worktree addコマンド以外はスキップ
    if "git worktree add" not in command:
        print(json.dumps(make_approve_result("issue-branch-check")))
        return

    # ブランチ名を抽出
    branch_name = extract_branch_name(command)
    if not branch_name:
        # -bオプションがない場合はスキップ（既存ブランチへのチェックアウト）
        print(json.dumps(make_approve_result("issue-branch-check")))
        return

    # Issue番号チェック
    if has_issue_number(branch_name):
        print(json.dumps(make_approve_result("issue-branch-check")))
        log_hook_execution(
            hook_name="issue-branch-check",
            decision="approved",
            reason=f"Branch name contains issue number: {branch_name}",
        )
        return

    # Issue番号がない場合はブロック
    message = f"""[issue-branch-check] ブランチ名にIssue番号が含まれていません。

**検出されたブランチ名**: `{branch_name}`

**対処法**: 先にIssueを作成してから、ブランチ名にIssue番号を含めてください。

**正しいブランチ名の例**:
- `docs/issue-2735-plugin-workflow`
- `feat/issue-123-add-feature`
- `fix/issue-456-bug-fix`

**手順**:
1. `gh issue create` でIssueを作成
2. Issue番号を含むブランチ名でworktreeを作成
   ```
   git worktree add --lock .worktrees/issue-<番号> -b <type>/issue-<番号>-<description>
   ```

💡 ブロック後も作業を継続してください。
代替アクションのツール呼び出しを行い、テキストのみの応答で終わらないでください。"""

    print(json.dumps(make_block_result("issue-branch-check", message)))
    log_hook_execution(
        hook_name="issue-branch-check",
        decision="blocked",
        reason=f"Branch name missing issue number: {branch_name}",
    )


if __name__ == "__main__":
    main()
