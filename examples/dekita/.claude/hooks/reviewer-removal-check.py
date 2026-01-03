#!/usr/bin/env python3
"""
PreToolUse hook: Block removal of AI reviewers from PRs.

This hook prevents circumventing AI review by removing reviewers via API.
AI reviewers (Copilot, Codex) should complete their reviews naturally.

Blocks:
- gh api .../requested_reviewers -X DELETE with AI reviewers
- Any API call that would remove Copilot or Codex from reviewers

Detection methods:
- Here-string: <<< '{"reviewers":["Copilot"]}'
- Heredoc: << EOF ... EOF
- Flag: -f reviewers='["Copilot"]'
"""

import json
import re
import sys

from lib.execution import log_hook_execution
from lib.results import make_block_result
from lib.session import parse_hook_input

# AI reviewer patterns (case-insensitive)
AI_REVIEWER_PATTERNS = ["copilot", "codex"]


def is_ai_reviewer(name: str) -> bool:
    """Check if the name matches an AI reviewer."""
    name_lower = name.lower()
    return any(pattern in name_lower for pattern in AI_REVIEWER_PATTERNS)


def extract_json_from_heredoc(command: str) -> str | None:
    """Extract JSON content from heredoc syntax.

    Supports patterns like:
    - << 'EOF' ... EOF
    - << EOF ... EOF
    - <<'EOF' ... EOF
    - <<EOF ... EOF

    Returns:
        JSON string if found, None otherwise.
    """
    # Match heredoc: << 'EOF' or << EOF followed by content and closing EOF
    # The delimiter can be quoted or unquoted
    heredoc_match = re.search(
        r"<<\s*['\"]?(\w+)['\"]?\s*\n(.*?)\n\1",
        command,
        re.DOTALL,
    )
    if heredoc_match:
        return heredoc_match.group(2).strip()
    return None


def check_reviewer_removal(command: str) -> tuple[bool, str]:
    """
    Check if the command attempts to remove AI reviewers.

    Returns (should_block, message)
    """
    # Pattern: gh api .../requested_reviewers -X DELETE
    if "gh api" not in command:
        return False, ""

    if "requested_reviewers" not in command:
        return False, ""

    if "-X DELETE" not in command and "--method DELETE" not in command:
        return False, ""

    # Only check reviewer names in JSON input or -f flags
    # Do NOT check entire command string - causes false positives for repo names like "copilot-tools"

    # Check JSON input via here-string
    # Pattern: --input - <<< '{"reviewers":["Copilot"]}'
    json_match = re.search(r'<<<\s*[\'"]?(\{.*?\})[\'"]?', command, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
            reviewers = data.get("reviewers", [])
            for reviewer in reviewers:
                if is_ai_reviewer(reviewer):
                    return True, f"AIレビュアー ({reviewer}) の解除は禁止されています"
        except json.JSONDecodeError:
            # Invalid JSON in here-string, continue to check other patterns
            pass

    # Check JSON input via heredoc
    # Pattern: << EOF ... {"reviewers":["Copilot"]} ... EOF
    heredoc_content = extract_json_from_heredoc(command)
    if heredoc_content:
        try:
            data = json.loads(heredoc_content)
            reviewers = data.get("reviewers", [])
            for reviewer in reviewers:
                if is_ai_reviewer(reviewer):
                    return True, f"AIレビュアー ({reviewer}) の解除は禁止されています"
        except json.JSONDecodeError:
            # Invalid JSON in heredoc, continue to check other patterns
            pass

    # Check -f reviewers= pattern
    reviewers_match = re.search(r'-f\s+reviewers=[\'"]\[([^\]]+)\]', command)
    if reviewers_match:
        reviewer_str = reviewers_match.group(1)
        for pattern in AI_REVIEWER_PATTERNS:
            if pattern.lower() in reviewer_str.lower():
                return True, f"AIレビュアー ({pattern}) の解除は禁止されています"

    return False, ""


def main():
    """Main entry point for the hook."""
    result = {"decision": "approve"}

    try:
        # Read hook input from stdin
        try:
            hook_input = parse_hook_input()
        except json.JSONDecodeError:
            # If we can't parse input, approve the command
            log_hook_execution("reviewer-removal-check", "approve", "Invalid JSON input")
            print(json.dumps({"decision": "approve"}))
            return

        tool_name = hook_input.get("tool_name", "")
        tool_input = hook_input.get("tool_input", {})

        # Only check Bash commands
        if tool_name != "Bash":
            log_hook_execution("reviewer-removal-check", "approve", "Not a Bash command")
            print(json.dumps({"decision": "approve"}))
            return

        command = tool_input.get("command", "")

        should_block, message = check_reviewer_removal(command)

        if should_block:
            error_message = f"""{message}

AIレビューが完了するまで待ってください。

タイムアウトした場合の対応:
1. ci-monitor.py の --timeout オプションで待機時間を延長
2. GitHub Copilot/Codex のステータスを確認（障害の可能性）
3. ユーザーに状況を報告して指示を仰ぐ

レビュー不要な理由がある場合は、PRにコメントで説明してください。"""

            result = make_block_result("reviewer-removal-check", error_message)
        else:
            result = {"decision": "approve"}

    except Exception as e:
        # On error, approve to avoid blocking legitimate commands
        print(f"[reviewer-removal-check] Hook error: {e}", file=sys.stderr)
        result = {"decision": "approve", "reason": f"Hook error: {e}"}

    log_hook_execution(
        "reviewer-removal-check", result.get("decision", "approve"), result.get("reason")
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
