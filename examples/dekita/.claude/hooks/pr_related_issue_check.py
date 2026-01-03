#!/usr/bin/env python3
# - 責務: gh pr create 時に関連オープンIssueの確認を促す
# - 重複なし: closes-keyword-check.py はClosesキーワードの有無をチェック、本フックは関連Issue検索
# - 非ブロック型: 警告のみ、PRは作成可能
# - AGENTS.md: Issue #1849 に基づく実装
"""
Hook to warn about related open Issues when creating a PR.

When `gh pr create` is executed, this hook:
1. Extracts keywords from PR title and body
2. Searches for related open Issues using `gh issue list --search`
3. Shows a warning with related Issues (non-blocking)

Issue #1849: PR作成時に関連Issue確認を促すフック
"""

import json
import re
import subprocess
import sys

from lib.execution import log_hook_execution
from lib.session import parse_hook_input
from lib.strings import strip_quoted_strings

# Hook name for logging
HOOK_NAME = "pr-related-issue-check"

# Stop words to exclude from keyword extraction (English and Japanese)
STOP_WORDS = {
    # English
    "a",
    "an",
    "the",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "have",
    "has",
    "had",
    "do",
    "does",
    "did",
    "will",
    "would",
    "could",
    "should",
    "may",
    "might",
    "must",
    "shall",
    "can",
    "need",
    "and",
    "or",
    "but",
    "if",
    "then",
    "else",
    "when",
    "where",
    "why",
    "how",
    "what",
    "which",
    "who",
    "whom",
    "this",
    "that",
    "these",
    "those",
    "for",
    "with",
    "from",
    "into",
    "onto",
    "upon",
    "about",
    "after",
    "before",
    "above",
    "below",
    "between",
    "under",
    "over",
    "through",
    "during",
    "until",
    "while",
    "of",
    "at",
    "by",
    "in",
    "on",
    "to",
    "as",
    "it",
    "its",
    "not",
    "no",
    "yes",
    "all",
    "any",
    "both",
    "each",
    "few",
    "more",
    "most",
    "other",
    "some",
    "such",
    "only",
    "own",
    "same",
    "so",
    "than",
    "too",
    "very",
    "just",
    "also",
    "now",
    "new",
    # Japanese particles
    "を",
    "が",
    "に",
    "で",
    "は",
    "の",
    "と",
    "も",
    "や",
    "から",
    "まで",
    "より",
    "へ",
    "など",
    "か",
    "ね",
    "よ",
    "わ",
    # Common PR/Git words
    "fix",
    "feat",
    "feature",
    "add",
    "update",
    "remove",
    "delete",
    "change",
    "modify",
    "refactor",
    "improve",
    "bug",
    "issue",
    "pr",
    "pull",
    "request",
    "merge",
    "branch",
    "commit",
    "push",
    "test",
    "docs",
    "chore",
}

# Maximum number of keywords to extract
MAX_KEYWORDS = 5

# Maximum number of Issues to display
MAX_ISSUES_TO_DISPLAY = 5

# Minimum keyword length
MIN_KEYWORD_LENGTH = 3


def is_gh_pr_create_command(command: str) -> bool:
    """Check if command is a gh pr create command."""
    if not command.strip():
        return False
    stripped_command = strip_quoted_strings(command)
    return bool(re.search(r"gh\s+pr\s+create\b", stripped_command))


def extract_pr_title(command: str) -> str | None:
    """Extract the PR title from gh pr create command.

    Handles:
    - --title "..."
    - -t "..."
    - --title="..."

    Returns None if title is not explicitly specified.
    """
    # Standard patterns (ordered by specificity)
    dq_content = r'([^"\\]*(?:\\.[^"\\]*)*)'  # Double-quoted content with escapes
    sq_content = r"([^'\\]*(?:\\.[^'\\]*)*)"  # Single-quoted content with escapes
    patterns = [
        rf'--title="{dq_content}"',  # --title="..."
        rf"--title='{sq_content}'",  # --title='...'
        rf'-t="{dq_content}"',  # -t="..."
        rf"-t='{sq_content}'",  # -t='...'
        rf'--title\s+"{dq_content}"',  # --title "..."
        rf"--title\s+'{sq_content}'",  # --title '...'
        rf'-t\s+"{dq_content}"',  # -t "..."
        rf"-t\s+'{sq_content}'",  # -t '...'
    ]

    for pattern in patterns:
        match = re.search(pattern, command)
        if match:
            return match.group(1)

    return None


def extract_pr_body(command: str) -> str | None:
    """Extract the PR body from gh pr create command.

    Handles:
    - --body "..."
    - -b "..."
    - --body="..."
    - HEREDOC patterns: --body "$(cat <<'EOF' ... EOF)"

    Returns None if body is not explicitly specified inline.
    """
    # Try HEREDOC pattern first (most complex)
    heredoc_pattern = r'--body\s+"\$\(cat\s+<<[\'"]?(\w+)[\'"]?\s*(.*?)\s*\1\s*\)"'
    heredoc_match = re.search(heredoc_pattern, command, re.DOTALL)
    if heredoc_match:
        return heredoc_match.group(2)

    # Standard patterns (ordered by specificity)
    dq_content = r'([^"\\]*(?:\\.[^"\\]*)*)'  # Double-quoted content with escapes
    sq_content = r"([^'\\]*(?:\\.[^'\\]*)*)"  # Single-quoted content with escapes
    patterns = [
        rf'--body="{dq_content}"',  # --body="..."
        rf"--body='{sq_content}'",  # --body='...'
        rf'-b="{dq_content}"',  # -b="..."
        rf"-b='{sq_content}'",  # -b='...'
        rf'--body\s+"{dq_content}"',  # --body "..."
        rf"--body\s+'{sq_content}'",  # --body '...'
        rf'-b\s+"{dq_content}"',  # -b "..."
        rf"-b\s+'{sq_content}'",  # -b '...'
    ]

    for pattern in patterns:
        match = re.search(pattern, command)
        if match:
            return match.group(1)

    return None


def extract_keywords(title: str | None, body: str | None) -> list[str]:
    """Extract keywords from PR title and body.

    Args:
        title: PR title text
        body: PR body text

    Returns:
        List of keywords (max MAX_KEYWORDS), sorted by length descending
        to prioritize more specific terms.
    """
    text = ""
    if title:
        text += title + " "
    if body:
        text += body

    if not text.strip():
        return []

    # Extract words: alphanumeric sequences and Japanese characters
    words = re.findall(r"[a-zA-Z0-9\u3040-\u309f\u30a0-\u30ff\u4e00-\u9fff]+", text)

    # Filter words
    keywords = []
    seen = set()
    for word in words:
        word_lower = word.lower()
        # Skip if too short, is a stop word, or already seen
        if len(word) < MIN_KEYWORD_LENGTH:
            continue
        if word_lower in STOP_WORDS:
            continue
        if word_lower in seen:
            continue
        seen.add(word_lower)
        keywords.append(word)

    # Sort by length descending (longer words are more specific)
    keywords.sort(key=len, reverse=True)

    return keywords[:MAX_KEYWORDS]


def search_related_issues(keywords: list[str]) -> list[dict]:
    """Search for related open Issues using gh CLI.

    Args:
        keywords: List of keywords to search for

    Returns:
        List of Issue dicts with 'number' and 'title' keys.
    """
    if not keywords:
        return []

    # Build search query: OR-join keywords for broader match
    # GitHub search with spaces uses AND, so we explicitly use "OR" operator
    search_query = " OR ".join(keywords)

    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "list",
                "--search",
                search_query,
                "--state",
                "open",
                "--limit",
                "10",
                "--json",
                "number,title",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return []

        issues = json.loads(result.stdout)
        return issues[:MAX_ISSUES_TO_DISPLAY]

    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return []


def main():
    """
    PreToolUse hook for Bash commands.

    Warns about related open Issues when creating a PR.
    """
    result = {"decision": "approve"}
    keywords_used = []

    try:
        data = parse_hook_input()
        tool_input = data.get("tool_input", {})
        command = tool_input.get("command", "")

        # Only check gh pr create commands
        if is_gh_pr_create_command(command):
            # Extract title and body
            title = extract_pr_title(command)
            body = extract_pr_body(command)

            # Extract keywords
            keywords = extract_keywords(title, body)
            keywords_used = keywords

            # Search for related Issues if keywords found
            if keywords:
                related_issues = search_related_issues(keywords)

                if related_issues:
                    # Format the warning message
                    issue_list = "\n".join(
                        f"  #{issue['number']}: {issue['title']}" for issue in related_issues
                    )
                    result["systemMessage"] = f"""⚠️ 関連するオープンIssueがあります

以下のIssueを確認しましたか？
{issue_list}

確認済みの場合は続行してください。

（検索キーワード: {", ".join(keywords)}）"""

                    if len(related_issues) >= MAX_ISSUES_TO_DISPLAY:
                        result["systemMessage"] += "\n\n💡 他にも関連Issueがある可能性があります。"

    except Exception as e:
        print(f"[{HOOK_NAME}] Hook error: {e}", file=sys.stderr)
        result = {"decision": "approve"}

    # Log execution
    log_hook_execution(
        HOOK_NAME,
        result.get("decision", "approve"),
        result.get("systemMessage"),
        {"keywords": keywords_used} if keywords_used else None,
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
