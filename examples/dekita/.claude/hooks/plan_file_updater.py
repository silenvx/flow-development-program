#!/usr/bin/env python3
"""gh pr merge成功後に計画ファイルのチェックボックスを自動更新する。

Why:
    PRマージ後に計画ファイルのチェックボックスが未完了のまま残ると、
    進捗状況が不明確になる。自動更新で一貫性を維持する。

What:
    - gh pr mergeの成功を検出
    - PRからブランチ名→Issue番号を抽出
    - 対応する計画ファイルを検索
    - 全チェックボックスを[ ]から[x]に更新

Remarks:
    - 自動化型フック（ブロックしない、ファイル自動更新）
    - PostToolUse:Bashで発火（gh pr merge成功時）
    - .claude/plans/と~/.claude/plans/の両方を検索
    - コードブロック内のチェックボックスは更新しない

Changelog:
    - silenvx/dekita#1336: フック追加
    - silenvx/dekita#1566: インデントコードブロック区別
"""

import json
import re
import subprocess
from pathlib import Path

from lib.constants import TIMEOUT_MEDIUM
from lib.execution import log_hook_execution
from lib.hook_input import get_exit_code, get_tool_result
from lib.repo import get_repo_root, is_merge_success
from lib.session import parse_hook_input


def extract_pr_number_from_command(command: str) -> int | None:
    """Extract PR number from gh pr merge command."""
    # Pattern: gh pr merge <number> or gh pr merge #<number>
    match = re.search(r"gh\s+pr\s+merge\s+#?(\d+)", command)
    if match:
        return int(match.group(1))
    return None


def get_pr_branch(pr_number: int, repo_root: Path) -> str | None:
    """Get the head branch name for a PR."""
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                str(pr_number),
                "--json",
                "headRefName",
                "-q",
                ".headRefName",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
            check=False,
            cwd=repo_root,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        # gh command failed or timed out - gracefully return None
        pass
    return None


def extract_issue_number_from_branch(branch_name: str) -> str | None:
    """Extract Issue number from branch name.

    Examples:
        feat/issue-1336-xxx -> 1336
        fix/issue-123-cleanup -> 123
        issue-999 -> 999
    """
    match = re.search(r"issue-(\d+)", branch_name, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def _safe_mtime(f: Path) -> float:
    """Get file mtime safely, returning 0.0 on errors.

    Used for sorting files by modification time. Returns 0.0 if stat fails
    (e.g., permission denied, broken symlinks) to put problematic files at the end.
    """
    try:
        return f.stat().st_mtime
    except OSError:
        return 0.0


def find_plan_file(issue_number: str, repo_root: Path) -> Path | None:
    """Find plan file for the given Issue number.

    Searches in order:
    1. .claude/plans/issue-{number}.md (exact match, always preferred)
    2. .claude/plans/ files containing issue-{number} pattern (newest first)
    3. ~/.claude/plans/ files containing issue-{number} pattern (newest first)

    Exact match (step 1) is always returned regardless of modification time.
    For pattern matches (steps 2 and 3), the most recently modified file is
    returned when multiple files match. For ~/.claude/plans/, files are sorted
    by mtime (newest first) and checked in order, stopping at the first match
    to optimize I/O when many files exist.
    """
    plans_dir = repo_root / ".claude" / "plans"

    # Try exact match first
    exact_path = plans_dir / f"issue-{issue_number}.md"
    if exact_path.exists():
        return exact_path

    # Search for pattern match in .claude/plans/
    # Return newest file if multiple match
    if plans_dir.exists():
        pattern = f"issue-{issue_number}"
        matches = [f for f in plans_dir.glob("*.md") if pattern in f.name.lower()]
        if matches:
            # Return the most recently modified file
            return max(matches, key=_safe_mtime)

    # Search in ~/.claude/plans/ (EnterPlanMode generates random names)
    # Optimize by checking newest files first and stopping on first match
    home_plans_dir = Path.home() / ".claude" / "plans"
    if home_plans_dir.exists():
        # Get all .md files sorted by modification time (newest first)
        try:
            all_files = sorted(home_plans_dir.glob("*.md"), key=_safe_mtime, reverse=True)
        except OSError:
            all_files = []

        for f in all_files:
            try:
                content = f.read_text(encoding="utf-8")
                # Check if file mentions this Issue
                if (
                    f"Issue #{issue_number}" in content
                    or f"issue-{issue_number}" in content.lower()
                ):
                    return f
            except (FileNotFoundError, PermissionError, IsADirectoryError):
                # File may have been deleted, moved, or is inaccessible - skip
                continue
            except UnicodeDecodeError:
                # File is not valid UTF-8 - skip
                continue

    return None


def _is_list_item(line: str) -> bool:
    """Check if a line is a list item (-, *, +, or numbered).

    Markdown list markers require at least one space or tab after the marker.
    Examples: "- item", "* item", "+ item", "1. item"
    Non-examples: "-text", "*bold*", "+1"
    """
    stripped = line.lstrip()
    if not stripped:
        return False
    # Check for unordered list markers with required space/tab after
    if stripped[0] in ("-", "*", "+") and len(stripped) > 1 and stripped[1] in (" ", "\t"):
        return True
    # Check for numbered list (any digit count: 1., 10., 100., etc.)
    if re.match(r"^\d+\.\s", stripped):
        return True
    return False


def _is_in_indented_code_block(
    lines: list[str], line_idx: int, *, is_first_segment: bool = True
) -> bool:
    """Check if a line is inside an indented code block.

    Indented code blocks in markdown:
    - Have 4+ spaces of indentation
    - Appear at file start or after a blank line (if preceded by non-list content)
    - List-like lines after a blank line that follows a list item = nested list
    - List-like lines after a blank line that follows non-list content = code block

    Args:
        lines: List of lines in the current segment
        line_idx: Index of the line to check
        is_first_segment: True if this is the first segment (file start context).
            False for segments after fenced code blocks.

    Issue #1566: Distinguish between nested lists and indented code blocks.
    """
    line = lines[line_idx]

    # Must have 4+ spaces indentation
    if not line.startswith("    "):
        return False

    # At segment start with 4+ spaces:
    # - First segment (file start): indented code block
    # - After fenced block: assume nested list (not code)
    if line_idx == 0:
        return is_first_segment

    # Look backwards for context
    for i in range(line_idx - 1, -1, -1):
        prev_line = lines[i]
        if prev_line.strip() == "":
            # Found blank line - continue looking for context
            continue
        if prev_line.startswith("    "):
            # Previous line is also indented - continue looking backwards
            continue
        # Found non-indented content
        # Check if this is list context or plain content context
        if _is_list_item(prev_line):
            # Previous non-indented line is a list item
            # Current line could be a nested list or continuation
            if _is_list_item(line):
                # Current line is also a list item = nested list, not code
                return False
            # Current line is not a list item, could be list continuation text
            return False
        # Previous non-indented line is not a list item
        # Check if there was a blank line between (indicating code block)
        for j in range(i + 1, line_idx):
            if lines[j].strip() == "":
                # Blank line after non-list content = code block
                return True
        # No blank line = not a code block (nested indentation)
        return False

    # Can't find non-indented context (all previous lines are 4+ indented)
    # - First segment: continuous code block from file start
    # - After fenced block: assume nested list to avoid false positives
    return is_first_segment


def update_plan_checkboxes(plan_path: Path) -> tuple[bool, int]:
    """Update unchecked list item boxes to checked in the plan file.

    Only targets markdown list items (-, *, +, or numbered) to avoid
    replacing checkboxes in code blocks or other contexts.

    Returns:
        Tuple of (updated: bool, count: int) - whether file was updated and how many boxes changed
    """
    try:
        content = plan_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        # Plan file was deleted before we could read it
        return False, 0
    except UnicodeDecodeError:
        # File is not valid UTF-8
        return False, 0
    except OSError as e:
        # Any other OS-level error (PermissionError, IsADirectoryError, EIO, EMFILE, etc.)
        log_hook_execution(
            "plan-file-updater",
            "skip",
            reason=f"Cannot read plan file {plan_path}: {e}",
        )
        return False, 0

    # Pattern matches list items with unchecked boxes:
    # - [ ] item, * [ ] item, + [ ] item, 1. [ ] item
    # Note: \s+ requires at least one space after list marker (markdown spec)
    list_checkbox_pattern = r"^(\s*(?:[-*+]|\d+\.)\s+)\[ \]"

    # Split content by fenced code blocks to avoid replacing inside them
    # Match fenced code blocks (``` or ~~~), including unclosed blocks at EOF
    # Fences must be at line start (after optional whitespace) to be valid Markdown
    # (?:^|(?<=\n)) ensures the fence is at file start or following a newline
    # This prevents inline backticks like "Use ``` for code" from matching
    code_block_pattern = (
        r"((?:^|(?<=\n))\s*```[\s\S]*?```"  # Closed ``` block
        r"|(?:^|(?<=\n))\s*~~~[\s\S]*?~~~"  # Closed ~~~ block
        r"|(?:^|(?<=\n))\s*```[\s\S]*$"  # Unclosed ``` block to EOF
        r"|(?:^|(?<=\n))\s*~~~[\s\S]*$)"  # Unclosed ~~~ block to EOF
    )
    segments = re.split(code_block_pattern, content)

    unchecked_count = 0
    updated_segments = []

    for i, segment in enumerate(segments):
        # Odd indices are fenced code blocks (matched by split pattern)
        if i % 2 == 1:
            # Keep fenced code blocks unchanged
            updated_segments.append(segment)
        else:
            # Process line by line to handle indented code blocks
            # First segment (i == 0) has file start context
            # Later segments (i == 2, 4, ...) are after fenced blocks
            is_first_segment = i == 0
            lines = segment.split("\n")
            updated_lines = []

            for line_idx, line in enumerate(lines):
                # Skip lines in indented code blocks
                if _is_in_indented_code_block(lines, line_idx, is_first_segment=is_first_segment):
                    updated_lines.append(line)
                    continue

                # Check if line matches list checkbox pattern
                match = re.match(list_checkbox_pattern, line)
                if match:
                    unchecked_count += 1
                    updated_line = re.sub(list_checkbox_pattern, r"\1[x]", line)
                    updated_lines.append(updated_line)
                else:
                    updated_lines.append(line)

            updated_segments.append("\n".join(updated_lines))

    if unchecked_count == 0:
        return False, 0

    updated_content = "".join(updated_segments)

    try:
        plan_path.write_text(updated_content, encoding="utf-8")
        return True, unchecked_count
    except FileNotFoundError:
        # File was deleted between read and write (concurrent edit)
        return False, 0
    except OSError as e:
        # Any other OS-level error (PermissionError, IsADirectoryError, EIO, EMFILE, etc.)
        log_hook_execution(
            "plan-file-updater",
            "skip",
            reason=f"Cannot write plan file {plan_path}: {e}",
        )
        return False, 0


def _check_merge_success(command: str, tool_result: dict) -> bool:
    """Check if gh pr merge command was successful.

    Wrapper around common.is_merge_success for backward compatibility.
    Issue #2203: Use get_exit_code() for consistent default value.
    """
    if "gh pr merge" not in command:
        return False

    exit_code = get_exit_code(tool_result)
    stdout = tool_result.get("stdout", "") if isinstance(tool_result, dict) else ""
    stderr = tool_result.get("stderr", "") if isinstance(tool_result, dict) else ""
    return is_merge_success(exit_code, stdout, command, stderr=stderr)


def main() -> None:
    """Main entry point for the hook."""
    hook_input = parse_hook_input()
    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})
    tool_result = get_tool_result(hook_input) or {}

    # Only handle Bash tool
    if tool_name != "Bash":
        return

    command = tool_input.get("command", "")

    # Only handle successful gh pr merge commands
    if not _check_merge_success(command, tool_result):
        return

    repo_root = get_repo_root()
    if not repo_root:
        return

    pr_number = extract_pr_number_from_command(command)
    if not pr_number:
        return

    branch_name = get_pr_branch(pr_number, repo_root)
    if not branch_name:
        return

    issue_number = extract_issue_number_from_branch(branch_name)
    if not issue_number:
        return

    plan_path = find_plan_file(issue_number, repo_root)
    if not plan_path:
        return

    updated, count = update_plan_checkboxes(plan_path)
    if updated:
        # Log the update
        log_hook_execution(
            "plan-file-updater",
            "success",
            {
                "pr_number": pr_number,
                "issue_number": issue_number,
                "plan_file": str(plan_path),
                "checkboxes_updated": count,
            },
        )

        # Output system message (non-blocking)
        result = {
            "continue": True,
            "systemMessage": f"✅ 計画ファイル更新: {plan_path.name} ({count}個のチェックボックスを完了)",
        }
        print(json.dumps(result))


if __name__ == "__main__":
    main()
