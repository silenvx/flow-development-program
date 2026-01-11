#!/usr/bin/env python3
"""Git操作メトリクスを追跡してログに記録する。

Why:
    Update branch回数やConflict発生頻度を記録することで、
    開発フローのボトルネック分析や改善ポイントの特定に活用する。

What:
    - git pull/merge/rebase, gh pr merge/update-branchコマンドを検出
    - Conflict発生、Update branch、Rebase解決を記録
    - 終了コード、ブランチ名、コンフリクトファイル等をログ出力

State:
    writes: .claude/state/execution-logs/git-operations.log

Remarks:
    - 情報収集のみでブロックしない
    - SRP: Git操作のメトリクス追跡のみを担当

Changelog:
    - silenvx/dekita#1689: コンフリクトファイルリスト記録、Rebase解決コマンド検出追加
    - silenvx/dekita#1706: deleted by us/them パターン対応
"""

from __future__ import annotations

import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# 共通モジュール
HOOKS_DIR = Path(__file__).parent
sys.path.insert(0, str(HOOKS_DIR))
from common import EXECUTION_LOG_DIR
from lib.execution import log_hook_execution
from lib.git import get_current_branch
from lib.hook_input import get_tool_result
from lib.session import parse_hook_input

GIT_OPERATIONS_LOG = EXECUTION_LOG_DIR / "git-operations.log"


def is_git_operation_command(command: str) -> bool:
    """Git操作コマンドかどうかを判定"""
    if not command.strip():
        return False

    git_patterns = [
        r"\bgit\s+(pull|merge|rebase)\b",
        r"\bgh\s+pr\s+(merge|update-branch)\b",
    ]

    for pattern in git_patterns:
        if re.search(pattern, command):
            return True
    return False


def detect_conflict(output: str) -> bool:
    """出力からConflictを検出"""
    conflict_patterns = [
        r"CONFLICT",
        r"Automatic merge failed",
        r"merge conflict",
        r"Merge conflict",
        r"fix conflicts and then commit",
        r"needs merge",
        r"Unmerged files",
        r"both modified:",
        r"both added:",
    ]

    for pattern in conflict_patterns:
        if re.search(pattern, output, re.IGNORECASE):
            return True
    return False


def extract_conflict_files(output: str) -> list[str]:
    """出力からコンフリクトしたファイル名を抽出 (Issue #1689)

    対応パターン:
    - CONFLICT (content): Merge conflict in <filename>
    - both modified: <filename>
    - both added: <filename>
    - both deleted: <filename>
    - deleted by us: <filename> (Issue #1706)
    - deleted by them: <filename> (Issue #1706)
    - 引用符で囲まれたファイル名（空白を含む場合）
    """
    files: set[str] = set()

    # パターン1: CONFLICT (content): Merge conflict in <filename>
    # 引用符対応: "file name" または file_name
    for match in re.finditer(r'CONFLICT\s*\([^)]+\):\s*.*?in\s+(?:"([^"]+)"|(\S+))', output):
        files.add(match.group(1) or match.group(2))

    # パターン2-4: both modified/added/deleted: <filename>
    # 引用符対応: "file name" または file_name
    for match in re.finditer(r'both (?:modified|added|deleted):\s+(?:"([^"]+)"|(\S+))', output):
        files.add(match.group(1) or match.group(2))

    # パターン5-6: deleted by us/them: <filename> (Issue #1706)
    # 引用符対応: "file name" または file_name
    for match in re.finditer(r'deleted by (?:us|them):\s+(?:"([^"]+)"|(\S+))', output):
        files.add(match.group(1) or match.group(2))

    return sorted(files)


def detect_rebase_resolution(command: str) -> str | None:
    """Rebase解決コマンドを検出 (Issue #1689)

    Returns:
        "skip", "continue", "abort" のいずれか。該当しない場合はNone。
    """
    if re.search(r"\bgit\s+rebase\s+--skip\b", command):
        return "skip"
    if re.search(r"\bgit\s+rebase\s+--continue\b", command):
        return "continue"
    if re.search(r"\bgit\s+rebase\s+--abort\b", command):
        return "abort"
    return None


def detect_update_branch(command: str, output: str) -> bool:
    """出力からUpdate branchを検出"""
    # 明示的なupdate-branchコマンド
    if "update-branch" in command:
        return True

    update_patterns = [
        r"Updating\s+[a-f0-9]+\.\.[a-f0-9]+",
        r"Fast-forward",
        r"Already up to date",
        r"Your branch is behind",
        r"branch.*updated",
    ]

    for pattern in update_patterns:
        if re.search(pattern, output, re.IGNORECASE):
            return True
    return False


def detect_rebase(command: str, output: str) -> bool:
    """Rebase操作を検出"""
    if "rebase" in command.lower():
        return True
    if re.search(r"rebas(e|ing|ed)", output, re.IGNORECASE):
        return True
    return False


def log_git_operation(
    operation_type: str,
    command: str,
    success: bool,
    details: dict[str, Any] | None = None,
) -> None:
    """Git操作をログに記録"""
    EXECUTION_LOG_DIR.mkdir(parents=True, exist_ok=True)

    log_entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "type": "git_operation",
        "operation": operation_type,
        "command": command[:200],  # コマンドは200文字まで
        "success": success,
        "branch": get_current_branch(),
    }

    if details:
        log_entry["details"] = details

    try:
        with open(GIT_OPERATIONS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    except OSError:
        pass  # ログ書き込み失敗は無視


def main():
    """Track git operations and log them for analysis."""
    hook_input = parse_hook_input()
    if not hook_input:
        print(json.dumps({"continue": True}))
        return

    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})
    tool_result = get_tool_result(hook_input) or {}

    # Bashツール以外はスキップ
    if tool_name != "Bash":
        print(json.dumps({"continue": True}))
        return

    command = tool_input.get("command", "")

    # Git操作コマンドかチェック
    if not is_git_operation_command(command):
        print(json.dumps({"continue": True}))
        return

    # 出力を取得
    stdout = tool_result.get("stdout", "")
    stderr = tool_result.get("stderr", "")
    output = f"{stdout}\n{stderr}"
    exit_code = tool_result.get("exit_code", 0)
    success = exit_code == 0

    operations_detected = []

    # Conflict検出 (Issue #1689: ファイルリストも記録)
    if detect_conflict(output):
        conflict_files = extract_conflict_files(output)
        log_git_operation(
            "conflict",
            command,
            success=False,
            details={
                "exit_code": exit_code,
                "files": conflict_files,  # Issue #1689
            },
        )
        operations_detected.append("conflict")

    # Rebase解決コマンド検出 (Issue #1689)
    rebase_resolution = detect_rebase_resolution(command)
    if rebase_resolution:
        log_git_operation(
            "rebase_resolution",
            command,
            success=success,
            details={
                "exit_code": exit_code,
                "method": rebase_resolution,  # skip, continue, or abort
            },
        )
        operations_detected.append(f"rebase_{rebase_resolution}")

    # Update branch検出
    if detect_update_branch(command, output):
        log_git_operation(
            "update_branch",
            command,
            success=success,
            details={"exit_code": exit_code},
        )
        operations_detected.append("update_branch")

    # Rebase検出（解決コマンド以外のrebase）
    if detect_rebase(command, output) and not rebase_resolution:
        log_git_operation(
            "rebase",
            command,
            success=success,
            details={"exit_code": exit_code},
        )
        operations_detected.append("rebase")

    # 何も検出されなかった場合でもgit pull/merge, gh pr mergeは記録
    if not operations_detected and re.search(
        r"\bgit\s+(pull|merge)\b|\bgh\s+pr\s+merge\b", command
    ):
        log_git_operation(
            "merge",
            command,
            success=success,
            details={"exit_code": exit_code},
        )
        operations_detected.append("merge")

    log_hook_execution(
        "git-operations-tracker",
        "approve",
        f"Detected: {', '.join(operations_detected)}" if operations_detected else None,
        {"operations": operations_detected} if operations_detected else None,
    )

    print(json.dumps({"continue": True}))


if __name__ == "__main__":
    main()
