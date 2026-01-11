#!/usr/bin/env python3
"""PRマージ後にコードベース内の類似パターンを検索し修正漏れを防ぐ。

Why:
    共通パターン（json.dumps等）を修正する際、同様のパターンが他ファイルに
    存在すると修正漏れが発生する。マージ後に自動検索して警告する。

What:
    - PRマージ成功後（PostToolUse:Bash）に発火
    - PR diffから関数呼び出しパターンを抽出
    - 変更されたファイル以外で同パターンを検索
    - 見つかった場合はsystemMessageで通知

Remarks:
    - 非ブロック型（情報提供のみ）
    - duplicate-issue-checkはIssue重複、本フックはコードパターン重複
    - 一般的すぎる関数（print, len等）はCOMMON_FUNCTIONSで除外

Changelog:
    - silenvx/dekita#2103: フック追加（Issue #2054/2065の再発防止）
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).parent
sys.path.insert(0, str(HOOKS_DIR))
from lib.constants import TIMEOUT_LIGHT, TIMEOUT_MEDIUM
from lib.execution import log_hook_execution
from lib.hook_input import get_exit_code, get_tool_result
from lib.repo import is_merge_success
from lib.session import parse_hook_input

# 検索結果の最大表示件数
MAX_RESULTS = 5

# 検索対象から除外するパターン
EXCLUDE_PATTERNS = [
    "*.pyc",
    "__pycache__",
    "node_modules",
    ".git",
    "*.min.js",
    "*.min.css",
    "pnpm-lock.yaml",
    "package-lock.json",
]

# 抽出する関数呼び出しパターン（よくある修正対象）
# 否定後読みでdef/class定義を除外し、関数呼び出しのみをマッチ
# Note: \bは否定後読み内では使用しない（ゼロ幅アサーションのため）
FUNCTION_PATTERN = re.compile(
    r"(?<!def )(?<!class )\b([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)\s*\(",
)

# 除外する一般的すぎる関数名
COMMON_FUNCTIONS = {
    "print",
    "len",
    "str",
    "int",
    "float",
    "list",
    "dict",
    "set",
    "tuple",
    "range",
    "enumerate",
    "zip",
    "map",
    "filter",
    "sorted",
    "reversed",
    "open",
    "type",
    "isinstance",
    "hasattr",
    "getattr",
    "setattr",
    "self",
    "super",
    "return",
    "if",
    "for",
    "while",
    "with",
    "assert",
    "raise",
    "except",
    "import",
    "from",
    "class",
    "def",
    "async",
    "await",
    "lambda",
    # 非常に一般的な関数
    "get",
    "add",
    "remove",
    "pop",
    "append",
    "extend",
    "update",
    "items",
    "keys",
    "values",
    "join",
    "split",
    "strip",
    "replace",
    "format",
    "lower",
    "upper",
    "startswith",
    "endswith",
    "find",
    "index",
    "count",
}


def is_pr_merge_command(command: str) -> bool:
    """Check if the command is a PR merge command."""
    return "gh pr merge" in command


def extract_pr_number(command: str) -> int | None:
    """Extract PR number from merge command or current branch PR."""
    # Match patterns like: gh pr merge 123, gh pr merge #123, gh pr merge --squash 789
    match = re.search(r"gh\s+pr\s+merge\s+.*?#?(\d+)", command)
    if match:
        return int(match.group(1))

    # If no PR number in command, get PR for current branch
    try:
        result = subprocess.run(
            ["gh", "pr", "view", "--json", "number"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return data.get("number")
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        # gh CLI not available or network error - return None
        pass

    return None


def get_pr_diff(pr_number: int) -> str | None:
    """Get the diff of a PR."""
    try:
        result = subprocess.run(
            ["gh", "pr", "diff", str(pr_number)],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode == 0:
            return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        # gh CLI not available or timeout - return None
        pass
    return None


def get_changed_files(pr_number: int) -> list[str]:
    """Get list of files changed in the PR."""
    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--json", "files"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            files = data.get("files", [])
            return [f.get("path", "") for f in files if f.get("path")]
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        # gh CLI not available or error - return empty list
        pass
    return []


def extract_function_patterns(diff: str) -> set[str]:
    """Extract function call patterns from diff.

    Focuses on added/modified lines (lines starting with +).
    """
    patterns = set()

    for line in diff.split("\n"):
        # Focus on added/modified lines
        if not line.startswith("+"):
            continue
        # Skip diff headers
        if line.startswith("+++"):
            continue

        # Extract function calls
        matches = FUNCTION_PATTERN.findall(line)
        for match in matches:
            # Skip common functions
            func_name = match.split(".")[-1]  # Get last part for method calls
            if func_name.lower() not in COMMON_FUNCTIONS:
                patterns.add(match)

    return patterns


def search_pattern_in_codebase(pattern: str, exclude_files: list[str]) -> list[dict]:
    """Search for a pattern in the codebase using ripgrep."""
    results = []

    # Build exclude arguments
    exclude_args = []
    for excl in EXCLUDE_PATTERNS:
        exclude_args.extend(["-g", f"!{excl}"])

    # Exclude changed files
    for f in exclude_files:
        exclude_args.extend(["-g", f"!{f}"])

    try:
        # Escape pattern for regex
        escaped_pattern = re.escape(pattern) + r"\s*\("

        result = subprocess.run(
            [
                "rg",
                "--line-number",
                "--no-heading",
                "--max-count",
                "10",  # Limit matches per file
                *exclude_args,
                escaped_pattern,
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
            cwd=Path.cwd(),
        )

        if result.returncode == 0 and result.stdout.strip():
            for line in result.stdout.strip().split("\n")[:MAX_RESULTS]:
                # Parse rg output: file:line:content
                parts = line.split(":", 2)
                if len(parts) >= 3:
                    results.append(
                        {
                            "file": parts[0],
                            "line": parts[1],
                            "content": parts[2].strip()[:80],  # Truncate
                        }
                    )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        # rg not available or timeout - return empty
        pass

    return results


def format_info_message(pattern_results: dict[str, list[dict]]) -> str:
    """Format the informational message."""
    lines = [
        "🔍 **修正漏れの可能性があります**",
        "",
        "PRで変更されたパターンと類似のコードが他のファイルにあります:",
        "",
    ]

    for pattern, results in pattern_results.items():
        lines.append(f"**`{pattern}`**:")
        for r in results[:3]:  # Show max 3 per pattern
            lines.append(f"  - `{r['file']}:{r['line']}` - {r['content']}")
        if len(results) > 3:
            lines.append(f"  - ... 他 {len(results) - 3} 件")
        lines.append("")

    lines.append("同様の修正が必要ないか確認してください。")

    return "\n".join(lines)


def main() -> None:
    """Main hook logic."""
    result = {"decision": "approve"}

    try:
        input_data = parse_hook_input()
        if not input_data:
            print(json.dumps(result))
            return

        tool_name = input_data.get("tool_name", "")
        if tool_name != "Bash":
            print(json.dumps(result))
            return

        tool_input = input_data.get("tool_input", {})
        command = tool_input.get("command", "")

        if not is_pr_merge_command(command):
            print(json.dumps(result))
            return

        tool_output = input_data.get("tool_output", "")
        # Issue #2203: Use get_exit_code() for consistent default value
        # Use get_tool_result() to handle both tool_result and tool_response
        tool_result = get_tool_result(input_data) or {}
        exit_code = get_exit_code(tool_result)

        if not is_merge_success(exit_code, tool_output, command):
            print(json.dumps(result))
            return

        pr_number = extract_pr_number(command)
        if not pr_number:
            log_hook_execution(
                "similar-pattern-search",
                "approve",
                "skipped: could not extract PR number",
            )
            print(json.dumps(result))
            return

        # Get PR diff
        diff = get_pr_diff(pr_number)
        if not diff:
            log_hook_execution(
                "similar-pattern-search",
                "approve",
                f"skipped: could not get diff for PR #{pr_number}",
            )
            print(json.dumps(result))
            return

        # Get changed files to exclude from search
        changed_files = get_changed_files(pr_number)

        # Extract function patterns from diff
        patterns = extract_function_patterns(diff)
        if not patterns:
            log_hook_execution(
                "similar-pattern-search",
                "approve",
                f"skipped: no patterns extracted from PR #{pr_number}",
            )
            print(json.dumps(result))
            return

        # Search for each pattern
        limited_patterns = list(patterns)[:5]  # Limit to 5 patterns
        pattern_results = {}
        for pattern in limited_patterns:
            results = search_pattern_in_codebase(pattern, changed_files)
            if results:
                pattern_results[pattern] = results

        if pattern_results:
            result["systemMessage"] = format_info_message(pattern_results)
            log_hook_execution(
                "similar-pattern-search",
                "approve",
                f"found similar patterns for PR #{pr_number}",
                {
                    "patterns": list(pattern_results.keys()),
                    "total_matches": sum(len(r) for r in pattern_results.values()),
                },
            )
        else:
            log_hook_execution(
                "similar-pattern-search",
                "approve",
                f"no similar patterns found for PR #{pr_number}",
                {"patterns_checked": limited_patterns},
            )

    except Exception as e:
        print(f"[similar-pattern-search] Error: {e}", file=sys.stderr)
        log_hook_execution("similar-pattern-search", "approve", f"error: {e}")

    print(json.dumps(result))


if __name__ == "__main__":
    main()
