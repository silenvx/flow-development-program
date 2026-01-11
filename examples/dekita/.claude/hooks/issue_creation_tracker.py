#!/usr/bin/env python3
"""セッション内で作成されたIssue番号を記録し、実装を促す。

Why:
    セッション内で作成したIssueは同セッションで実装まで完遂する
    必要がある。Issue作成を追跡し、優先度に応じた実装指示を
    出すことで、Issue作成で終わらず実装まで誘導する。

What:
    - gh issue createの成功を検出しIssue番号を抽出
    - セッションIDごとのファイルにIssue番号を記録
    - 優先度（P0/P1/P2）を解析し適切なメッセージを表示
    - P0は即時実装、P1/P2は現タスク完遂後の実装を指示

State:
    - writes: .claude/logs/flow/session-created-issues-{session_id}.json
    - writes: .claude/logs/decisions/issue-decisions-{session_id}.jsonl

Remarks:
    - 非ブロック型（PostToolUse）
    - related-task-checkがセッション終了時に未実装Issueをチェック

Changelog:
    - silenvx/dekita#xxx: フック追加
    - silenvx/dekita#1943: P0即時実装警告を追加
    - silenvx/dekita#1950: --labelからの優先度解析でAPIコール削減
    - silenvx/dekita#1951: P1/P2の優先度を明示表示
    - silenvx/dekita#2076: セッション内Issue即着手ルール適用
    - silenvx/dekita#2121: 確認禁止の明確化
    - silenvx/dekita#2337: メッセージ強調と禁止事項明確化
    - silenvx/dekita#2677: Issue判定記録機能を追加
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from common import DECISIONS_LOG_DIR
from lib.constants import TIMEOUT_MEDIUM
from lib.execution import log_hook_execution
from lib.hook_input import get_tool_result
from lib.labels import extract_labels_from_command, extract_priority_from_labels
from lib.logging import log_to_session_file
from lib.session import create_hook_context, parse_hook_input


def _get_session_log_dir() -> Path:
    """Get the directory for session-specific log files.

    Uses .claude/logs/flow/ for consistency with other session logs
    (e.g., state-*.json, events-*.jsonl).

    Issue #1918: Moved from TMPDIR to project logs for persistence.

    Returns:
        Path to the session log directory.
    """
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
    return Path(project_dir) / ".claude" / "logs" / "flow"


def get_session_issues_file(session_id: str) -> Path:
    """Get the file path for storing session-created issues.

    Args:
        session_id: The Claude session ID to scope the file.

    Returns:
        Path to session-specific issues file.
    """
    return _get_session_log_dir() / f"session-created-issues-{session_id}.json"


def load_session_issues(session_id: str) -> list[int]:
    """Load list of issue numbers created in this session.

    Issue #2003: エラー時のログ追加。読み込み失敗時は空リストを返すが、
    原因究明のためstderrにログを出力する。

    Args:
        session_id: The Claude session ID.

    Returns:
        List of issue numbers created in this session.
    """
    issues_file = get_session_issues_file(session_id)
    if not issues_file.exists():
        # Normal case - no issues created yet in this session
        return []

    try:
        data = json.loads(issues_file.read_text())
        return data.get("issues", [])
    except json.JSONDecodeError as e:
        # Issue #2003: Log corrupted data for debugging
        print(
            f"[issue-creation-tracker] Warning: Corrupted issues file {issues_file}: {e}",
            file=sys.stderr,
        )
        return []
    except OSError as e:
        # Issue #2003: Log read errors for debugging
        print(
            f"[issue-creation-tracker] Warning: Failed to read issues file {issues_file}: {e}",
            file=sys.stderr,
        )
        return []


def save_session_issues(session_id: str, issues: list[int]) -> None:
    """Save list of issue numbers created in this session.

    Issue #2003: 原子的書き込み（tmp→replace）を実装。
    書き込み中の競合やクラッシュによるデータ破損を防止。

    Args:
        session_id: The Claude session ID.
        issues: List of issue numbers to save.
    """
    log_dir = _get_session_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    issues_file = get_session_issues_file(session_id)
    tmp_file = issues_file.with_name(issues_file.name + ".tmp")

    try:
        # Write to temp file first
        tmp_file.write_text(json.dumps({"issues": issues}))
        # Atomic replace (works cross-platform, overwrites existing file)
        tmp_file.replace(issues_file)
    except OSError as e:
        # Issue #2003: Log write errors for debugging
        print(
            f"[issue-creation-tracker] Warning: Failed to save issues file {issues_file}: {e}",
            file=sys.stderr,
        )
        # Clean up temp file if it exists
        try:
            tmp_file.unlink(missing_ok=True)
        except OSError as cleanup_err:
            # Best effort cleanup - temp file left behind is harmless, but log for debugging
            print(
                f"[issue-creation-tracker] Info: Failed to remove temp issues file {tmp_file}: {cleanup_err}",
                file=sys.stderr,
            )


def extract_issue_number(output: str) -> int | None:
    """Extract issue number from gh issue create output.

    gh issue create outputs URLs like:
    - https://github.com/owner/repo/issues/123

    Returns:
        Issue number if found, None otherwise.
    """
    # Match GitHub issue URL
    match = re.search(r"github\.com/[^/]+/[^/]+/issues/(\d+)", output)
    if match:
        return int(match.group(1))
    return None


def extract_priority_from_command(command: str) -> str | None:
    """Extract priority label from gh issue create command string.

    Issue #1950: Parse --label options to avoid extra API call.
    Issue #1957: Use shared shlex-based implementation from lib/labels.py.

    Args:
        command: The gh issue create command string.

    Returns:
        "P0", "P1", "P2" or None if no priority label found.
        Returns highest priority if multiple found (P0 > P1 > P2).
    """
    # Use shlex-based extraction from common.py for robust parsing
    labels = extract_labels_from_command(command)
    if not labels:
        return None

    # Use shared priority extraction (only checks P0-P2 for this hook's messages)
    # Note: P3 is valid but this hook only has special messages for P0-P2
    priority = extract_priority_from_labels(labels, priority_labels={"P0", "P1", "P2"})
    return priority


def record_issue_decision(
    session_id: str,
    issue_number: int,
    priority: str | None,
    command: str,
) -> None:
    """Record issue creation decision to decision log.

    Issue #2677: Issue判定記録機能を追加。

    Args:
        session_id: The Claude session ID.
        issue_number: The created issue number.
        priority: Priority label (P0, P1, P2, P3) or None.
        command: The original gh issue create command.
    """
    # Extract title from command for problem description
    title_match = re.search(r'--title\s+["\']([^"\']+)["\']', command)
    title = title_match.group(1) if title_match else f"Issue #{issue_number}"

    entry = {
        "decision": "create",
        "issue_number": issue_number,
        "problem": title,
        "reason": "Issue created via gh issue create",
    }

    if priority:
        entry["severity"] = priority

    log_to_session_file(DECISIONS_LOG_DIR, "issue-decisions", session_id, entry)


def get_issue_priority(issue_number: int) -> str | None:
    """Get priority label from issue.

    Issue #1943: Fetch issue labels to determine priority.

    Args:
        issue_number: The issue number to check.

    Returns:
        "P0", "P1", "P2" or None if no priority label found.
    """
    try:
        result = subprocess.run(
            ["gh", "issue", "view", str(issue_number), "--json", "labels"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode != 0:
            return None

        data = json.loads(result.stdout)
        labels = data.get("labels", [])
        # ラベル配列の順序に依存せず、P0 を最優先に返す。
        has_p1 = False
        has_p2 = False
        for label in labels:
            name = label.get("name", "").upper()
            if name in ("P0", "PRIORITY:P0"):
                # P0 が存在する場合は常に P0 を返す
                return "P0"
            if name in ("P1", "PRIORITY:P1"):
                has_p1 = True
            if name in ("P2", "PRIORITY:P2"):
                has_p2 = True
        if has_p1:
            return "P1"
        if has_p2:
            return "P2"
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError) as e:
        print(
            f"[issue-creation-tracker] Warning: Failed to get issue priority: {e}", file=sys.stderr
        )
    return None


def main():
    """
    PostToolUse hook for Bash commands.

    Tracks issues created via `gh issue create` command.
    """
    result = {"continue": True}

    try:
        input_data = parse_hook_input()

        ctx = create_hook_context(input_data)
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {})

        # Only process Bash commands
        if tool_name != "Bash":
            print(json.dumps(result))
            return

        command = tool_input.get("command", "")

        # Check if this is a gh issue create command
        if "gh issue create" not in command:
            print(json.dumps(result))
            return

        # Issue #1842: Use standardized helper for tool result extraction
        # Ensure we have a dict for .get() calls (tool_result can be a string)
        raw_result = get_tool_result(input_data)
        tool_result = raw_result if isinstance(raw_result, dict) else {}

        # Only record if command succeeded
        # Default to 0 (success) as Claude Code may not always include exit_code
        exit_code = tool_result.get("exit_code", 0)
        if exit_code != 0:
            print(json.dumps(result))
            return

        # Extract issue number from stdout or output field
        stdout = tool_result.get("stdout", "") or tool_result.get("output", "")
        issue_number = extract_issue_number(stdout)
        if issue_number:
            # Get session ID for session-specific file
            session_id = ctx.get_session_id()

            # Add to session issues
            issues = load_session_issues(session_id)
            if issue_number not in issues:
                issues.append(issue_number)
                save_session_issues(session_id, issues)

                # Issue #1950: Try to extract priority from command first (avoid API call)
                # Issue #1943: Check priority and output appropriate message
                priority = extract_priority_from_command(command)
                if priority is None:
                    # Fallback to API call if not found in command
                    priority = get_issue_priority(issue_number)

                # Issue #2677: Record issue decision for later evaluation
                record_issue_decision(session_id, issue_number, priority, command)

                # Issue #2076: セッション内で作成したIssueは確認なしに着手する
                # AGENTS.md「セッション内で作成したIssueは実装まで完遂」
                # Issue #2121: 「確認しますか？」と聞くことを明確に禁止
                # Issue #2337: メッセージ強調と禁止事項の明確化
                base_reminder = (
                    "⚠️ **重要（AGENTS.md原則）**: このセッションで作成したIssueは、"
                    "ユーザー確認なしに実装まで完遂してください。\n\n"
                    "**禁止**: \n"
                    "- 「実装しますか？」「着手しますか？」と確認を求めること\n"
                    "- 「何をしますか？」とユーザーに次の指示を求めること\n"
                    "- このメッセージを無視して別のタスクに移ること\n\n"
                    "**必須**: 今すぐworktreeを作成して実装を開始すること\n\n"
                    "※ Stop hookで未完了Issueはブロックされます"
                )

                if priority == "P0":
                    system_message = (
                        f"⚠️ P0 Issue を作成しました。即時実装が必要です。\n\n"
                        f"  #{issue_number}\n\n"
                        f"現在のタスクを中断し、このIssueを先に実装してください。\n\n"
                        f"{base_reminder}"
                    )
                    log_message = (
                        f"Recorded P0 issue #{issue_number} - immediate implementation required"
                    )
                elif priority == "P1":
                    # Issue #1951: Show P1 explicitly in message
                    system_message = (
                        f"ℹ️ P1 Issue を作成しました。\n\n"
                        f"  #{issue_number}\n\n"
                        f"現在のタスクを完遂後、このセッション内で実装してください。\n\n"
                        f"{base_reminder}"
                    )
                    log_message = (
                        f"Recorded P1 issue #{issue_number} - implement after current task"
                    )
                elif priority == "P2":
                    # Issue #1951: Show P2 explicitly in message
                    system_message = (
                        f"ℹ️ P2 Issue を作成しました。\n\n"
                        f"  #{issue_number}\n\n"
                        f"現在のタスクを完遂後、このセッション内で実装してください。\n\n"
                        f"{base_reminder}"
                    )
                    log_message = (
                        f"Recorded P2 issue #{issue_number} - implement after current task"
                    )
                else:
                    system_message = (
                        f"ℹ️ Issue を作成しました。\n\n"
                        f"  #{issue_number}\n\n"
                        f"現在のタスクを完遂後、このセッション内で実装してください。\n\n"
                        f"{base_reminder}"
                    )
                    log_message = f"Recorded issue #{issue_number} - no priority set"

                result["systemMessage"] = system_message
                log_hook_execution("issue-creation-tracker", "approve", log_message)

    except Exception as e:
        print(f"[issue-creation-tracker] Error: {e}", file=sys.stderr)

    print(json.dumps(result))


if __name__ == "__main__":
    main()
