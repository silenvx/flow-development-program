#!/usr/bin/env python3
"""セッション終了時に問題報告とIssue作成の整合性を確認。

Why:
    問題を発見してもIssueを作成せずセッションを終了すると、
    問題が放置される。問題報告パターンを検出し、Issue作成を促す。

What:
    - セッションのトランスクリプトを解析
    - Claude発言から問題報告パターン（バグ発見、エラー発生等）を検出
    - gh issue createコマンドの実行有無を確認
    - 問題報告ありかつIssue作成なしなら警告

Remarks:
    - 非ブロック型（誤検知リスクのため警告のみ）
    - Stopフック
    - AGENTS.md「Issue作成が必要なケース」を仕組み化

Changelog:
    - silenvx/dekita#421: フック追加
"""

import json
import re

from lib.execution import log_hook_execution
from lib.session import parse_hook_input
from lib.transcript import load_transcript

# Problem report patterns in Japanese and English
# These indicate Claude has identified a problem
PROBLEM_PATTERNS = [
    # Japanese patterns
    r"問題があり",
    r"バグを発見",
    r"バグが見つか",
    r"エラーが発生",
    r"動作していません",
    r"不具合を発見",
    r"不具合が見つか",
    r"予期せぬ動作",
    r"想定外の動作",
    r"異常を検知",
    r"障害を検知",
    r"失敗しています",
    # English patterns
    r"found a bug",
    r"discovered a bug",
    r"found an issue",
    r"discovered an issue",
    r"unexpected behavior",
    r"not working",
    r"fails to",
    r"error occurs",
    r"malfunction",
]

# Issue creation patterns in Bash commands
ISSUE_CREATION_PATTERNS = [
    r"gh\s+issue\s+create",
]

# Patterns that indicate false positives (skip these)
FALSE_POSITIVE_PATTERNS = [
    # Quoting or referencing patterns
    r'["「].*?問題.*?[」"]',  # Quoted problem mentions (non-greedy)
    r"#\d+",  # Issue number references like #123
    # Discussion patterns
    r"問題ないか",  # "Is there a problem?"
    r"問題ありません",  # "No problem"
    r"問題なし",  # "No problem"
    r"問題は解決",  # "Problem is resolved"
    r"問題が解決",  # "Problem was resolved"
]


def extract_claude_messages(transcript: list[dict]) -> list[str]:
    """Extract text from Claude's messages in the transcript.

    Returns:
        List of text strings from Claude's responses.
    """
    messages = []
    for entry in transcript:
        # Check if this is Claude's response (assistant message)
        if entry.get("role") == "assistant":
            content = entry.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "")
                        if text:
                            messages.append(text)
            elif isinstance(content, str):
                messages.append(content)
    return messages


def extract_bash_commands(transcript: list[dict]) -> list[str]:
    """Extract Bash commands from the transcript.

    Returns:
        List of Bash command strings.
    """
    commands = []
    for entry in transcript:
        if entry.get("role") == "assistant":
            content = entry.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        if block.get("name") == "Bash":
                            cmd = block.get("input", {}).get("command", "")
                            if cmd:
                                commands.append(cmd)
    return commands


def is_false_positive_match(msg: str, match_start: int, match_end: int) -> bool:
    """Check if a problem match overlaps with a false positive pattern.

    Instead of rejecting the entire message, this checks if the specific
    problem match is part of a false positive pattern (e.g., "問題ありません").

    Args:
        msg: The full message text.
        match_start: Start position of the problem pattern match.
        match_end: End position of the problem pattern match.

    Returns:
        True if this specific match is a false positive.
    """
    # Check context around the match (expand by 10 chars on each side)
    # Smaller window to avoid false positives from unrelated nearby text
    context_start = max(0, match_start - 10)
    context_end = min(len(msg), match_end + 10)
    context = msg[context_start:context_end]

    for fp_pattern in FALSE_POSITIVE_PATTERNS:
        if re.search(fp_pattern, context, re.IGNORECASE):
            return True
    return False


def find_problem_reports(messages: list[str]) -> list[str]:
    """Find problem report patterns in Claude's messages.

    Returns:
        List of message excerpts containing problem reports.
    """
    problems = []
    for msg in messages:
        # Check for problem patterns
        for pattern in PROBLEM_PATTERNS:
            match = re.search(pattern, msg, re.IGNORECASE)
            if match:
                # Check if this specific match is a false positive
                if is_false_positive_match(msg, match.start(), match.end()):
                    continue

                # Extract surrounding context (50 chars before and after)
                start = max(0, match.start() - 50)
                end = min(len(msg), match.end() + 50)
                excerpt = msg[start:end].strip()
                # Skip empty excerpts after strip
                if not excerpt:
                    continue
                # Add ellipsis if truncated
                if start > 0:
                    excerpt = "..." + excerpt
                if end < len(msg):
                    excerpt = excerpt + "..."
                problems.append(excerpt)
                break  # Only count once per message
    return problems


def find_issue_creations(commands: list[str]) -> int:
    """Count gh issue create commands.

    Returns:
        Number of issue creation commands found.
    """
    count = 0
    for cmd in commands:
        for pattern in ISSUE_CREATION_PATTERNS:
            if re.search(pattern, cmd, re.IGNORECASE):
                count += 1
                break
    return count


def main():
    """Stop hook to verify problem reports are documented as Issues.

    Reads the transcript, finds problem reports, and checks if
    Issues were created for them.
    """
    result = {"decision": "approve"}

    try:
        # Read input from stdin
        input_data = parse_hook_input()

        # Skip if stop hook is already active (prevent infinite loops)
        if input_data.get("stop_hook_active"):
            log_hook_execution("problem-report-check", "approve", "stop_hook_active")
            print(json.dumps(result))
            return

        # Get transcript path
        transcript_path = input_data.get("transcript_path")
        if not transcript_path:
            log_hook_execution("problem-report-check", "approve", "no transcript path")
            print(json.dumps(result))
            return

        # Load transcript
        transcript = load_transcript(transcript_path)
        if not transcript:
            log_hook_execution("problem-report-check", "approve", "transcript load failed")
            print(json.dumps(result))
            return

        # Extract Claude's messages and Bash commands
        claude_messages = extract_claude_messages(transcript)
        bash_commands = extract_bash_commands(transcript)

        # Find problem reports and issue creations
        problem_reports = find_problem_reports(claude_messages)
        issue_count = find_issue_creations(bash_commands)

        # If problems reported but no issues created, warn (but don't block)
        # Blocking would be too aggressive since problem detection has false positives
        if problem_reports and issue_count == 0:
            excerpts = problem_reports[:3]  # Show up to 3 examples
            excerpt_text = "\n".join(f"  - {e[:100]}" for e in excerpts)
            if len(problem_reports) > 3:
                excerpt_text += f"\n  - ... 他 {len(problem_reports) - 3} 件"

            result["systemMessage"] = (
                f"⚠️ [problem-report-check] 問題報告が検出されました（{len(problem_reports)}件）:\n"
                f"{excerpt_text}\n\n"
                "Issue作成を確認してください:\n"
                "- 問題を発見した場合は `gh issue create` でIssue作成\n"
                "- AGENTS.md: 「Issue作成が必要なケース」を参照\n"
                "- 誤検知の場合は無視してください"
            )
            log_hook_execution(
                "problem-report-check",
                "approve",
                f"problems detected: {len(problem_reports)}, issues: {issue_count}",
                {"problem_count": len(problem_reports), "issue_count": issue_count},
            )
        else:
            log_hook_execution(
                "problem-report-check",
                "approve",
                None,
                {"problem_count": len(problem_reports), "issue_count": issue_count},
            )

    except Exception as e:
        # Don't block on errors, just skip the check
        log_hook_execution("problem-report-check", "approve", f"error: {e}")

    print(json.dumps(result))


if __name__ == "__main__":
    main()
