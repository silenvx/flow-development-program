#!/usr/bin/env python3
"""Issue作成後にAIレビュー（Gemini/Codex）を実行し結果を通知する。

Why:
    Issue作成時点でAIレビューを実行することで、Issue内容の品質を
    即座に向上させる機会を提供する。レビュー結果をClaudeに通知し、
    Issue内容への反映を促す。

What:
    - gh issue createの成功を検出
    - Gemini/Codexによる同期レビューを実行
    - レビュー結果をIssueにコメント投稿
    - systemMessageでClaudeにレビュー結果を通知

Remarks:
    - 同期実行（レビュー完了まで待機）
    - issue-ai-review.shスクリプトを呼び出し

Changelog:
    - silenvx/dekita#xxx: フック追加
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from common import complete_flow_step, start_flow
from lib.constants import TIMEOUT_HEAVY, TIMEOUT_LONG
from lib.execution import log_hook_execution
from lib.hook_input import get_tool_result
from lib.results import print_continue_and_log_skip
from lib.session import create_hook_context, parse_hook_input

# Minimum length for a suggestion content to be included (filters out short/noise items)
MIN_SUGGESTION_LENGTH = 10
# Maximum length for a single suggestion line before truncation
MAX_SUGGESTION_LENGTH = 150
# Length to truncate to (leaving room for ellipsis)
TRUNCATED_SUGGESTION_LENGTH = 147
# Maximum number of suggestions to include in the notification
MAX_SUGGESTIONS_COUNT = 5


def extract_issue_number(output: str) -> int | None:
    """Extract issue number from gh issue create output."""
    match = re.search(r"github\.com/[^/]+/[^/]+/issues/(\d+)", output)
    if match:
        return int(match.group(1))
    return None


def run_ai_review(issue_number: int) -> str | None:
    """Run AI reviews synchronously and return the review content.

    Calls issue-ai-review.sh which runs Gemini and Codex reviews,
    then fetches the review comment from the issue.

    Returns:
        Review content string, or None if review failed.
    """
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
    scripts_dir = Path(project_dir) / ".claude" / "scripts"
    review_script = scripts_dir / "issue-ai-review.sh"

    if not review_script.exists():
        log_hook_execution(
            "issue-ai-review",
            "approve",
            f"Review script not found: {review_script}",
        )
        return None

    # Run review script synchronously (may take up to 2+ minutes)
    try:
        result = subprocess.run(
            [str(review_script), str(issue_number)],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LONG,
            check=False,
        )

        if result.returncode != 0:
            log_hook_execution(
                "issue-ai-review",
                "approve",
                f"Review script failed: {result.stderr[:200]}",
            )
            return None

        log_hook_execution(
            "issue-ai-review",
            "approve",
            f"AI review completed for issue #{issue_number}",
        )

        # Fetch the AI Review comment from the issue
        return fetch_ai_review_comment(issue_number)

    except subprocess.TimeoutExpired:
        log_hook_execution(
            "issue-ai-review",
            "approve",
            f"Review timed out for issue #{issue_number}",
        )
        return None
    except Exception as e:
        log_hook_execution(
            "issue-ai-review",
            "approve",
            f"Failed to run review: {e}",
        )
        return None


def fetch_ai_review_comment(issue_number: int) -> str | None:
    """Fetch the latest AI Review comment from a GitHub issue.

    Returns:
        The full body of the latest AI Review comment, or None if not found.
    """
    try:
        # Use jq to get only the last matching comment's body
        # The 'last()' function returns the final element from the stream
        result = subprocess.run(
            [
                "gh",
                "issue",
                "view",
                str(issue_number),
                "--json",
                "comments",
                "--jq",
                '[.comments[] | select(.body | contains("🤖 AI Review"))] | last | .body',
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_HEAVY,
            check=False,
        )

        body = result.stdout.strip()

        # jq returns "null" when no matching comment exists
        if result.returncode != 0 or not body or body == "null":
            return None

        # Return the full comment body (may contain multiple lines)
        return body

    except subprocess.TimeoutExpired:
        return None
    except subprocess.SubprocessError:
        # Catch subprocess-specific errors (CalledProcessError, etc.)
        return None
    except OSError:
        # Catch FileNotFoundError when gh command is not available
        return None


def extract_edit_suggestions(review_content: str) -> list[str]:
    """Extract actionable edit suggestions from AI review content.

    Looks for patterns like:
    - 「提案」「改善提案」「改善点」「推奨」keywords
    - Bullet points after these keywords (e.g., "- suggestion")
    - Numbered list items after these keywords (e.g., "1. suggestion")

    Returns:
        List of specific edit suggestions for the issue.
    """
    suggestions = []
    lines = review_content.split("\n")

    # Track if we're in a suggestion section
    in_suggestion_section = False
    keywords = ["提案", "改善提案", "改善点", "推奨"]

    for line in lines:
        stripped = line.strip()

        # Check if this is a bullet point (process first to avoid keyword false positives)
        is_bullet = stripped.startswith(("-", "*", "・", "•"))
        # Check for numbered list (e.g., "1.", "2.", "10.")
        numbered_match = re.match(r"^(\d+)\.\s*", stripped)
        is_numbered = numbered_match is not None

        if in_suggestion_section:
            if is_bullet:
                content = stripped.lstrip("-*・• ").strip()
                if len(content) > MIN_SUGGESTION_LENGTH:
                    suggestions.append(content)
            elif is_numbered:
                # Extract content after "N. " using regex match
                content = stripped[numbered_match.end() :].strip()
                if len(content) > MIN_SUGGESTION_LENGTH:
                    suggestions.append(content)
            # Exit suggestion section on empty line or new header
            elif not stripped or stripped.startswith("#"):
                in_suggestion_section = False
        # Check for suggestion section headers (only if not a bullet/numbered)
        elif not is_bullet and not is_numbered:
            if any(keyword in stripped for keyword in keywords):
                in_suggestion_section = True
                # If this line itself contains a suggestion after colon
                # Use the first colon (either ":" or "：") found
                colon_pos = -1
                for c in [":", "："]:
                    pos = stripped.find(c)
                    if pos != -1 and (colon_pos == -1 or pos < colon_pos):
                        colon_pos = pos
                if colon_pos != -1:
                    after_colon = stripped[colon_pos + 1 :].strip()
                    if after_colon and len(after_colon) > MIN_SUGGESTION_LENGTH:
                        suggestions.append(after_colon)

        # Limit suggestions
        if len(suggestions) >= 8:
            break

    return suggestions


def build_review_notification(issue_number: int, review_content: str) -> str:
    """Build a notification message for Claude about the AI review.

    Returns:
        A formatted message prompting Claude to incorporate the review feedback.
    """
    # Extract actionable edit suggestions using keyword-based extraction
    edit_suggestions = extract_edit_suggestions(review_content)

    # Build edit suggestions section
    if edit_suggestions:
        # Truncate long suggestions using module-level constants
        suggestion_lines = []
        for s in edit_suggestions[:MAX_SUGGESTIONS_COUNT]:
            if len(s) > MAX_SUGGESTION_LENGTH:
                suggestion_lines.append(f"- {s[:TRUNCATED_SUGGESTION_LENGTH]}...")
            else:
                suggestion_lines.append(f"- {s}")
        suggestion_text = "\n".join(suggestion_lines)
    else:
        suggestion_text = "（具体的な編集提案なし - レビュー全文を確認してください）"

    return f"""📋 Issue #{issue_number} のAIレビューが完了しました。

**編集提案:**
{suggestion_text}

**【自動実行】Task toolでSubAgentを起動してAIレビュー対応を実行してください:**

```
Task tool parameters:
- subagent_type: "haiku"
- run_in_background: true
- prompt: "Issue #{issue_number} のAIレビューを確認し、Issueを更新する。
  1. gh issue view {issue_number} --comments でレビュー確認
  2. レビュー内容に基づいてIssue本文を更新（gh issue edit {issue_number}）"
```

SubAgentがバックグラウンドで処理するため、メインセッションは継続できます。"""


def main():
    """PostToolUse hook for Bash commands.

    Triggers AI review after successful `gh issue create`.
    Returns review results via systemMessage so Claude can incorporate feedback.
    """
    result = {"continue": True}

    try:
        input_data = parse_hook_input()
        # Issue #2607: Create context for session_id logging (even if input_data is empty)
        ctx = create_hook_context(input_data or {})
        if not input_data:
            print_continue_and_log_skip("issue-ai-review", "no input data", ctx=ctx)
            return
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {})

        # Issue #1842: Use standardized helper for tool result extraction
        # Ensure we have a dict for .get() calls (tool_result can be a string)
        raw_result = get_tool_result(input_data)
        tool_result = raw_result if isinstance(raw_result, dict) else {}

        # Only process Bash commands
        if tool_name != "Bash":
            print_continue_and_log_skip("issue-ai-review", f"not Bash: {tool_name}", ctx=ctx)
            return

        command = tool_input.get("command", "")

        # Check if this is a gh issue create command
        if "gh issue create" not in command:
            print_continue_and_log_skip("issue-ai-review", "not gh issue create", ctx=ctx)
            return

        # Log that we detected gh issue create with input structure
        input_keys = list(input_data.keys())
        log_hook_execution(
            "issue-ai-review",
            "approve",
            f"gh issue create. input_keys={input_keys}",
        )

        # Only process if command succeeded
        # Default to 0 (success) as Claude Code may not always include exit_code
        exit_code = tool_result.get("exit_code", 0)
        if exit_code != 0:
            log_hook_execution(
                "issue-ai-review",
                "approve",
                f"Command failed: exit={exit_code}",
            )
            print(json.dumps(result))
            return

        # Extract issue number from stdout or output field
        stdout = tool_result.get("stdout", "") or tool_result.get("output", "")
        issue_number = extract_issue_number(stdout)

        if issue_number:
            # Run review synchronously and get content
            review_content = run_ai_review(issue_number)

            if review_content:
                # Start flow to track that Claude should review and update the issue
                flow_instance_id = start_flow(
                    "issue-ai-review",
                    {"issue_number": issue_number},
                )
                if flow_instance_id:
                    # Mark review_posted step as completed
                    complete_flow_step(flow_instance_id, "review_posted", "issue-ai-review")
                    log_hook_execution(
                        "issue-ai-review",
                        "approve",
                        f"Flow started: {flow_instance_id}",
                    )
                else:
                    # Flow tracking failed, but still send notification
                    log_hook_execution(
                        "issue-ai-review",
                        "approve",
                        f"Warning: Flow tracking failed for issue #{issue_number}",
                    )

                # Notify Claude about the review via systemMessage
                notification = build_review_notification(issue_number, review_content)
                result["systemMessage"] = notification
                log_hook_execution(
                    "issue-ai-review",
                    "approve",
                    f"Review notification sent for issue #{issue_number}",
                )
            else:
                log_hook_execution(
                    "issue-ai-review",
                    "approve",
                    f"No review content for issue #{issue_number}",
                )
        else:
            # Log the tool_result structure for debugging
            keys = list(tool_result.keys())
            if stdout:
                max_len = 200
                preview = stdout[:max_len]
                if len(stdout) > max_len:
                    preview += f"...[len={len(stdout)}]"
            else:
                preview = "empty"
            log_hook_execution(
                "issue-ai-review",
                "approve",
                f"No issue#. keys={keys}, cmd={command!r}, out={preview}",
            )

    except Exception as e:
        log_hook_execution(
            "issue-ai-review",
            "approve",
            f"Error: {e}",
        )
        print(f"[issue-ai-review] Error: {e}", file=sys.stderr)

    print(json.dumps(result))


if __name__ == "__main__":
    main()
