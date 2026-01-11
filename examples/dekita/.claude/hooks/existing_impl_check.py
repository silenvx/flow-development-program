#!/usr/bin/env python3
"""worktree作成時に既存実装の存在を警告し、検証を促す。

Why:
    「コードが存在する」≠「動作している」。Issueがオープンなのに
    関連コードが存在する場合、そのコードが正常に動作していない
    可能性が高い。検証せずに「実装済み」と判断すると時間を無駄にする。

What:
    - git worktree addコマンドからIssue番号を抽出
    - コメント・ファイル名からIssue番号やキーワードで関連コードを検索
    - 関連コードが見つかった場合は検証を促す警告を表示

Remarks:
    - ブロックせず警告のみ
    - 検索結果は最大5件まで表示

Changelog:
    - silenvx/dekita#????: フック追加
"""

import json
import re
import subprocess
import sys

from lib.constants import TIMEOUT_MEDIUM
from lib.execution import log_hook_execution
from lib.session import parse_hook_input


def extract_issue_number_from_command(command: str) -> int | None:
    """Extract issue number from git worktree add command.

    Checks both branch name (-b option) and path for issue numbers.
    """
    if "git worktree add" not in command:
        return None

    # Patterns to find issue numbers
    patterns = [
        r"issue[_-](\d+)",  # issue-123, issue_123
        r"#(\d+)",  # #123
        r"/(\d+)[-_]",  # /123-description
        r"[-_](\d+)[-_]",  # feature-123-name
        r"[-_](\d+)$",  # feature-123 (at end)
        r"[-_](\d+)\s",  # feature-123 (followed by space)
    ]

    for pattern in patterns:
        match = re.search(pattern, command, re.IGNORECASE)
        if match:
            return int(match.group(1))

    return None


def get_issue_title(issue_number: int) -> str | None:
    """Get issue title from GitHub."""
    try:
        result = subprocess.run(
            ["gh", "issue", "view", str(issue_number), "--json", "title"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return data.get("title", "")
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass  # Fail-open: エラー時はNoneを返す
    return None


def search_related_code(issue_number: int, issue_title: str | None) -> list[str]:
    """Search for code that might be related to this issue.

    Returns list of potentially related files.
    """
    related_files: list[str] = []

    # Search strategies:
    # 1. Search for issue number in code comments
    # 2. Search for keywords from issue title in filenames
    # 3. Search for common patterns based on issue type

    # Strategy 1: Issue number in comments (e.g., "# Issue #123", "// #123")
    try:
        result = subprocess.run(
            ["git", "grep", "-l", f"#{issue_number}"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode == 0:
            related_files.extend(result.stdout.strip().split("\n"))
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass  # Fail-open: 検索失敗時は継続

    # Strategy 2: Extract keywords from issue title and search filenames
    if issue_title:
        # Extract potential function/file names from title
        # e.g., "feat(hooks): worktree作成時にIssue自動アサイン" → "auto-assign", "worktree"
        keywords = extract_keywords_from_title(issue_title)

        for keyword in keywords:
            if len(keyword) >= 4:  # Skip very short keywords
                try:
                    # Search for files containing the keyword in their name
                    result = subprocess.run(
                        ["git", "ls-files", f"*{keyword}*"],
                        capture_output=True,
                        text=True,
                        timeout=TIMEOUT_MEDIUM,
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        for f in result.stdout.strip().split("\n"):
                            if f and f not in related_files:
                                related_files.append(f)
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    pass  # Fail-open: 検索失敗時は継続

    # Deduplicate and filter
    seen = set()
    unique_files = []
    for f in related_files:
        # Filter out hidden files (like .gitignore) but keep .claude/ directory
        if f and f not in seen:
            if not f.startswith(".") or f.startswith(".claude/"):
                seen.add(f)
                unique_files.append(f)

    return unique_files[:5]  # Limit to 5 files


def extract_keywords_from_title(title: str) -> list[str]:
    """Extract potential code-related keywords from issue title."""
    keywords = []

    # Common patterns in issue titles
    # "feat(hooks): xxx" → extract "hooks"
    scope_match = re.search(r"\(([^)]+)\)", title)
    if scope_match:
        keywords.append(scope_match.group(1))

    # Extract hyphenated words that look like code names
    # e.g., "auto-assign", "issue-check"
    hyphenated = re.findall(r"[a-z]+-[a-z]+", title.lower())
    keywords.extend(hyphenated)

    # Extract camelCase or PascalCase words
    camel = re.findall(r"[A-Z][a-z]+[A-Z][a-z]+", title)
    keywords.extend([w.lower() for w in camel])

    # Japanese keywords that might indicate functionality
    jp_keywords = {
        "自動": "auto",
        "アサイン": "assign",
        "チェック": "check",
        "検証": "verif",
        "作成": "create",
        "削除": "delete",
        "更新": "update",
        "レビュー": "review",
        "マージ": "merge",
    }
    for jp, en in jp_keywords.items():
        if jp in title:
            keywords.append(en)

    return keywords


def main():
    """PreToolUse hook for Bash commands.

    Detects git worktree add and checks for existing implementations.
    """
    result = {"decision": "approve"}

    try:
        input_data = parse_hook_input()
        tool_input = input_data.get("tool_input", {})
        command = tool_input.get("command", "")

        # Only process git worktree add commands
        if "git worktree add" not in command:
            pass
        else:
            issue_number = extract_issue_number_from_command(command)
            if issue_number:
                issue_title = get_issue_title(issue_number)
                related_files = search_related_code(issue_number, issue_title)

                if related_files:
                    files_list = "\n".join(f"   - {f}" for f in related_files)
                    result["systemMessage"] = (
                        f"⚠️ **既存実装の検証が必要です** (Issue #{issue_number})\n\n"
                        f"関連する既存コードが見つかりました:\n{files_list}\n\n"
                        f"**重要**: 「コードが存在する」≠「動作している」\n"
                        f"Issueが存在する理由を確認し、既存実装が\n"
                        f"**実際に期待通り動作するか**を検証してください。\n\n"
                        f"検証せずに「実装済み」と判断すると、\n"
                        f"問題の見落としにつながります。"
                    )

    except Exception as e:
        # Don't block on errors
        print(f"[existing-impl-check] Error: {e}", file=sys.stderr)

    log_hook_execution(
        "existing-impl-check", result.get("decision", "approve"), result.get("reason")
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
