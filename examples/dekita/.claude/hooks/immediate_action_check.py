#!/usr/bin/env python3
"""PRマージ後のreflect実行を強制する。

Why:
    PRマージ後に/reflectを実行しないと、学習機会を逃し、同じ問題を繰り返す。
    トランスクリプトを解析してマージ後のreflect実行を検証する。

What:
    - トランスクリプトから成功したgh pr mergeコマンドを検出
    - 各マージ後にSkill(reflect)呼び出しがあるか確認
    - 対応するreflect呼び出しがないマージがあればブロック

Remarks:
    - post-merge-reflection-enforcerはreflect指示、本フックは実行検証
    - [IMMEDIATE]タグはトランスクリプトに記録されないため直接マージ検出を使用

Changelog:
    - silenvx/dekita#2219: フック追加
    - silenvx/dekita#2269: 検出方法を直接マージ検出に変更
"""

from __future__ import annotations

import json
import re
from typing import Any

from lib.execution import log_hook_execution
from lib.repo import is_merge_success
from lib.session import parse_hook_input
from lib.transcript import load_transcript

# Pattern to match gh pr merge command
PR_MERGE_PATTERN = re.compile(r"gh\s+pr\s+merge")


def find_pr_merges(transcript: list[dict]) -> list[tuple[int, str]]:
    """Find successful PR merge commands in transcript.

    Searches for Bash tool_use blocks containing `gh pr merge` command,
    then checks the corresponding tool_result for success.

    Args:
        transcript: Parsed transcript as list of message dicts.

    Returns:
        List of (message_index, pr_number) tuples for successful merges.
    """
    merges: list[tuple[int, str]] = []
    tool_use_map: dict[str, tuple[int, str]] = {}  # tool_use_id -> (index, command)

    for idx, entry in enumerate(transcript):
        content = entry.get("content", [])
        if not isinstance(content, list):
            continue

        for block in content:
            if not isinstance(block, dict):
                continue

            block_type = block.get("type", "")

            # Track Bash tool_use with gh pr merge
            if block_type == "tool_use" and block.get("name") == "Bash":
                tool_input = block.get("input", {})
                command = tool_input.get("command", "")
                if PR_MERGE_PATTERN.search(command):
                    tool_use_id = block.get("id", "")
                    if tool_use_id:
                        tool_use_map[tool_use_id] = (idx, command)

            # Check tool_result for merge success
            elif block_type == "tool_result":
                tool_use_id = block.get("tool_use_id", "")
                if tool_use_id in tool_use_map:
                    merge_idx, command = tool_use_map[tool_use_id]
                    # Check if merge was successful
                    stdout = _extract_stdout(block)
                    stderr = _extract_stderr(block)
                    exit_code = block.get("exit_code", 0)

                    if is_merge_success(exit_code, stdout, command, stderr=stderr):
                        # Extract PR number from command; if not present, fall back to stdout
                        pr_match = re.search(r"gh\s+pr\s+merge\s+(\d+)", command)
                        pr_number = "?"
                        if pr_match:
                            pr_number = pr_match.group(1)
                        else:
                            # gh pr merge without explicit number shows PR as "#123" in stdout
                            stdout_pr_match = re.search(r"#(\d+)", stdout or "")
                            if stdout_pr_match:
                                pr_number = stdout_pr_match.group(1)
                        merges.append((merge_idx, pr_number))

    return merges


def _extract_stdout(block: dict) -> str:
    """Extract stdout from a tool_result block."""
    # Try stdout field first
    stdout = block.get("stdout", "")
    if stdout:
        return stdout

    # Try content field
    content = block.get("content", [])
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, str):
                texts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                texts.append(item.get("text", ""))
        return "\n".join(texts)

    return ""


def _extract_stderr(block: dict) -> str:
    """Extract stderr from a tool_result block."""
    return block.get("stderr", "")


def find_skill_calls_after(transcript: list[dict], start_idx: int, action: str) -> bool:
    """Check if a Skill tool call for the action exists after start_idx.

    Args:
        transcript: Parsed transcript.
        start_idx: Index after which to search.
        action: The action name to find (e.g., "reflect").

    Returns:
        True if a matching Skill call was found.
    """
    for idx in range(start_idx + 1, len(transcript)):
        entry = transcript[idx]
        if entry.get("role") != "assistant":
            continue

        content = entry.get("content", [])
        if not isinstance(content, list):
            continue

        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_use":
                continue

            # Check for Skill tool with matching skill name
            if block.get("name") == "Skill":
                tool_input = block.get("input", {})
                skill_name = tool_input.get("skill", "")
                if skill_name == action:
                    return True

    return False


def check_post_merge_reflection(transcript: list[dict]) -> list[str]:
    """Check if all PR merges have corresponding reflect calls.

    Args:
        transcript: Parsed transcript.

    Returns:
        List of PR numbers that lack reflect calls.
    """
    merges = find_pr_merges(transcript)
    if not merges:
        return []

    unreflected: list[str] = []
    for merge_idx, pr_number in merges:
        if not find_skill_calls_after(transcript, merge_idx, "reflect"):
            unreflected.append(pr_number)

    return unreflected


def main() -> None:
    """Stop hook to enforce reflection after PR merge.

    Blocks session end if a PR was merged but /reflect was not called.
    """
    result: dict[str, Any] = {"decision": "approve"}

    try:
        input_data = parse_hook_input()

        # Skip if stop hook is already active (prevents infinite loops)
        if input_data.get("stop_hook_active"):
            log_hook_execution("immediate-action-check", "approve", "stop_hook_active")
            print(json.dumps(result))
            return

        transcript_path = input_data.get("transcript_path")
        if not transcript_path:
            # No transcript path - approve to avoid false blocks
            # Issue #2269: Changed to fail-open since we're detecting merges directly
            log_hook_execution(
                "immediate-action-check",
                "approve",
                "no transcript path provided",
            )
            print(json.dumps(result))
            return

        transcript = load_transcript(transcript_path)
        if not transcript:
            # Transcript load failed - approve to avoid false blocks
            # Issue #2269: Changed to fail-open since we're detecting merges directly
            # Issue #2274: Added (skipping check) to log for debugging
            log_hook_execution(
                "immediate-action-check",
                "approve",
                f"transcript load failed (skipping check): {transcript_path}",
            )
            print(json.dumps(result))
            return

        unreflected = check_post_merge_reflection(transcript)

        if unreflected:
            prs_str = ", ".join(f"#{pr}" for pr in unreflected)
            log_hook_execution(
                "immediate-action-check",
                "block",
                f"unreflected PRs: {prs_str}",
            )

            result = {
                "decision": "block",
                "reason": (
                    f"PRマージ後の振り返りが未実行です: {prs_str}\n\n"
                    "PRをマージしましたが、/reflect が呼び出されていません。\n\n"
                    "セッション終了前に /reflect を実行してください。\n"
                    "[IMMEDIATE: /reflect]"
                ),
            }
        else:
            log_hook_execution("immediate-action-check", "approve", "all merges reflected")

    except Exception as e:
        log_hook_execution("immediate-action-check", "approve", f"error: {e}")

    print(json.dumps(result))


if __name__ == "__main__":
    main()
