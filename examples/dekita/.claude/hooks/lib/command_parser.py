#!/usr/bin/env python3
"""外部APIコマンド（gh, git, npm）の構造化パースを提供する。

Why:
    開発ワークフロー分析のため、コマンドを構造化データに変換し、
    操作種別・引数・結果を追跡可能にする。

What:
    - parse_command(): コマンド文字列を構造化dictに変換
    - extract_result_from_output(): stdout/stderrから結果を抽出
    - is_target_command(): ログ対象コマンドか判定

Remarks:
    - グローバルフラグのみ（--help, --version）は意図的に除外
    - 実際のパース処理はparsers/サブモジュールに委譲
    - 絶対パス（/usr/bin/git等）にも対応

Changelog:
    - silenvx/dekita#1177: グローバルフラグのみコマンド除外設計
    - silenvx/dekita#1230: parse_command()でグローバルフラグ適切処理
    - silenvx/dekita#1246: PR mergeStateStatus抽出追加
    - silenvx/dekita#1258: 絶対パスコマンド対応
    - silenvx/dekita#1693: issue_comment, rebase, worktree結果抽出追加
    - silenvx/dekita#1750: コンフリクト検出を共通化
    - silenvx/dekita#2411: parsers/サブモジュールにリファクタリング
"""

from __future__ import annotations

import json
import re
from typing import Any

from .command_utils import (
    COMMAND_WRAPPERS,
    ends_with_shell_separator,
    normalize_shell_separators,
)

# Import parsers from submodule
from .parsers import (
    CONFLICT_FILE_PATTERN,
    extract_conflict_info,
    extract_worktree_add_path,
    parse_gh_command,
    parse_git_command,
    parse_npm_command,
)

# Re-export for backward compatibility (Issue #1337, #2411)
_ends_with_shell_separator = ends_with_shell_separator
_COMMAND_WRAPPERS = COMMAND_WRAPPERS
_normalize_shell_separators = normalize_shell_separators

# Re-export from parsers for backward compatibility (Issue #2411)
_extract_conflict_info = extract_conflict_info


def parse_command(command: str) -> dict[str, Any] | None:
    """Parse a command and return structured data.

    Detects the command type and delegates to the appropriate parser.

    Issue #1258: Also handles commands called with absolute paths like
    /usr/bin/git or /opt/homebrew/bin/gh.

    Args:
        command: The full command string

    Returns:
        Parsed command data or None if not a target command
    """
    if not command or not command.strip():
        return None

    # Detect command type
    stripped = command.strip()

    # Normalize shell separators to ensure proper tokenization
    # This handles cases like 'echo foo;gh pr list' by adding spaces
    normalized = _normalize_shell_separators(stripped)

    # Try each parser. If one returns None (e.g., false positive from regex),
    # continue trying other parsers. This handles cases like
    # "echo /usr/bin/gh && git push" where the gh regex matches but the actual
    # command is git.
    result = None

    # gh CLI commands (including absolute paths like /usr/bin/gh)
    # Match either "gh " as word or path ending with "/gh "
    if re.search(r"(?:\bgh\s+|/gh\s+)", normalized):
        result = parse_gh_command(normalized)
        if result is not None:
            return result

    # git commands (including absolute paths like /usr/bin/git)
    if re.search(r"(?:\bgit\s+|/git\s+)", normalized):
        result = parse_git_command(normalized)
        if result is not None:
            return result

    # npm/pnpm commands (including absolute paths)
    if re.search(r"(?:\b(?:npm|pnpm)\s+|/(?:npm|pnpm)\s+)", normalized):
        result = parse_npm_command(normalized)
        if result is not None:
            return result

    return None


# Known valid mergeStateStatus values from GitHub API
_VALID_MERGE_STATES = frozenset(
    {"CLEAN", "DIRTY", "BLOCKED", "BEHIND", "UNKNOWN", "UNSTABLE", "HAS_HOOKS"}
)


def _extract_pr_merge_state(stdout: str) -> str | None:
    """Extract mergeStateStatus from gh pr view JSON output.

    Issue #1246: Detects PR conflict/DIRTY state from gh pr view output.

    Possible mergeStateStatus values:
        - CLEAN: No conflicts, ready to merge
        - DIRTY: Has merge conflicts
        - BLOCKED: Blocked by branch protection or other rules
        - BEHIND: Base branch has been updated
        - UNKNOWN: State cannot be determined
        - UNSTABLE: CI checks failing (not a merge conflict)
        - HAS_HOOKS: Has hooks that need to run

    Args:
        stdout: The stdout from gh pr view command

    Returns:
        The mergeStateStatus value if found, otherwise None
    """
    stripped = stdout.strip()

    # Try to parse as JSON object
    try:
        # Handle JSON output (gh pr view --json mergeStateStatus)
        data = json.loads(stripped)
        if isinstance(data, dict):
            merge_state = data.get("mergeStateStatus")
            # Validate against known states to prevent invalid data from API changes
            if merge_state and isinstance(merge_state, str) and merge_state in _VALID_MERGE_STATES:
                return merge_state
    except json.JSONDecodeError:
        pass  # Not JSON output, fall through to other extraction methods

    # Handle jq/--jq output: raw string value like "DIRTY" or DIRTY
    # Strip quotes if present and validate against known states
    if stripped.startswith('"') and stripped.endswith('"'):
        unquoted = stripped[1:-1]
        if unquoted in _VALID_MERGE_STATES:
            return unquoted
    elif stripped in _VALID_MERGE_STATES:
        return stripped

    # Try to extract from mixed output (e.g., prefix {"mergeStateStatus": "DIRTY"} suffix)
    # Look for JSON-like patterns and validate against known states
    json_match = re.search(r'"mergeStateStatus"\s*:\s*"([A-Z_]+)"', stdout)
    if json_match:
        matched_state = json_match.group(1)
        if matched_state in _VALID_MERGE_STATES:
            return matched_state

    return None


def extract_result_from_output(
    parsed: dict[str, Any], stdout: str, stderr: str = ""
) -> dict[str, Any]:
    """Extract structured result data from command output.

    Args:
        parsed: The parsed command data
        stdout: Standard output from the command
        stderr: Standard error from the command

    Returns:
        Extracted result data (URLs, numbers, etc.)
    """
    result: dict[str, Any] = {}
    combined = f"{stdout}\n{stderr}"

    if not parsed:
        return result

    cmd_type = parsed.get("type")
    operation = parsed.get("operation", "")

    if cmd_type == "gh":
        # Extract GitHub URLs
        url_match = re.search(r"https://github\.com/[^\s]+/(pull|issues?)/(\d+)", combined)
        if url_match:
            result["url"] = url_match.group(0)
            result["number"] = int(url_match.group(2))
            result["resource_type"] = "pr" if "pull" in url_match.group(1) else "issue"

        # Extract PR/Issue number from create output
        if operation in ("pr_create", "issue_create"):
            num_match = re.search(r"#(\d+)", combined)
            if num_match and "number" not in result:
                result["number"] = int(num_match.group(1))

            # Issue #1693: Extract title and other details from parsed args
            args = parsed.get("args", {})
            if args.get("--title"):
                result["title"] = args["--title"]
            if args.get("--label"):
                result["label"] = args["--label"]
            if args.get("--base"):
                result["base_branch"] = args["--base"]
            if args.get("--head"):
                result["head_branch"] = args["--head"]

        # Extract merge result
        if operation == "pr_merge":
            if "was merged" in combined.lower():
                result["merged"] = True
            elif "already merged" in combined.lower():
                result["already_merged"] = True

        # Issue #1246: Extract PR state from gh pr view JSON output
        if operation == "pr_view":
            pr_state = _extract_pr_merge_state(stdout)
            if pr_state:
                result["merge_state"] = pr_state
                # Log non-CLEAN states as they indicate potential issues
                if pr_state in ("DIRTY", "BLOCKED", "BEHIND", "UNKNOWN"):
                    result["has_merge_issue"] = True

        # Issue #1692: Extract issue comment result
        if operation == "issue_comment":
            # gh issue comment outputs URL like:
            # https://github.com/owner/repo/issues/123#issuecomment-456789
            comment_match = re.search(
                r"https://github\.com/([^/]+/[^/]+)/issues/(\d+)#issuecomment-(\d+)", combined
            )
            if comment_match:
                result["issue_number"] = int(comment_match.group(2))
                result["comment_id"] = int(comment_match.group(3))
                result["comment_added"] = True
            # Also extract issue number from parsed args if available
            if "issue_number" not in result:
                issue_num = parsed.get("issue_number")
                if issue_num:
                    result["issue_number"] = issue_num

    elif cmd_type == "git":
        # Extract commit hash from commit output
        if operation == "commit":
            hash_match = re.search(r"\[[\w/-]+\s+([a-f0-9]{7,40})\]", combined)
            if hash_match:
                result["commit_hash"] = hash_match.group(1)

        # Extract push result
        if operation == "push":
            if "Everything up-to-date" in combined:
                result["already_up_to_date"] = True
            elif "->" in combined:
                result["pushed"] = True

            # Issue #1248: Detect force push for tracking comment ID invalidation
            args = parsed.get("args", {})
            if args.get("force") or args.get("force_with_lease"):
                result["force_push"] = True

        # Extract branch info from checkout/switch
        if operation in ("checkout", "switch"):
            branch_match = re.search(r"Switched to.*'([^']+)'", combined)
            if branch_match:
                result["switched_to"] = branch_match.group(1)

        # Issue #1693: Extract rebase result
        if operation == "rebase":
            # Issue #1750: Use shared helper for conflict detection
            extract_conflict_info(combined, result)
            # Detect successful rebase (only if no conflict)
            if not result.get("conflict_detected") and "Successfully rebased" in combined:
                result["rebase_completed"] = True
            # Detect abort
            args = parsed.get("args", {})
            if args.get("abort"):
                result["rebase_aborted"] = True
            # Detect continue
            if args.get("continue"):
                result["rebase_continued"] = True

        # Issue #1693: Extract merge result
        if operation == "merge":
            # Issue #1750: Use shared helper for conflict detection
            extract_conflict_info(combined, result)
            if not result.get("conflict_detected"):
                if "Already up to date" in combined:
                    result["already_up_to_date"] = True
                elif "Merge made by" in combined or "Fast-forward" in combined:
                    result["merge_completed"] = True

        # Issue #1693: Extract worktree result
        if operation.startswith("worktree_"):
            worktree_action = parsed.get("worktree_action")
            path = parsed.get("path")
            if path:
                result["worktree_path"] = path
            if worktree_action == "add":
                if "Preparing worktree" in combined:
                    result["worktree_created"] = True
            elif worktree_action == "remove":
                # Remove has no output on success; stderr indicates error
                if not stderr:
                    result["worktree_removed"] = True

    elif cmd_type == "npm":
        # Check for success/failure patterns
        if "ERR!" in combined:
            result["has_errors"] = True
        if "WARN" in combined:
            result["has_warnings"] = True

    return result


def is_target_command(command: str) -> bool:
    """Check if a command should be logged.

    This function determines whether a command represents an actionable
    API operation worth logging for development workflow analysis.

    Issue #1230: Uses parse_command() to properly handle global flags.
    Previous implementation used regex that required subcommand immediately
    after the base command, missing commands like `gh --repo x issue view`.

    Design Intent (Issue #1177):
        Commands with only global flags are intentionally EXCLUDED because:
        - Help/version commands are not API operations
        - They add noise to workflow analysis
        - Only state-changing or query operations are valuable for analysis

    Examples:
        Excluded - global flags only (no actionable subcommand):

        >>> is_target_command("gh --help")
        False
        >>> is_target_command("gh --version")
        False
        >>> is_target_command("git --version")
        False

        Included - actionable subcommands:

        >>> is_target_command("gh pr list")
        True
        >>> is_target_command("gh --repo owner/repo pr list")
        True
        >>> is_target_command("gh -R owner/repo issue view 123")
        True
        >>> is_target_command("git push origin main")
        True
        >>> is_target_command("npm run test")
        True

        Edge cases:

        >>> is_target_command("")
        False
        >>> is_target_command("ls -la")
        False

    Args:
        command: The command string

    Returns:
        True if the command should be logged (has actionable subcommand)
    """
    if not command:
        return False

    # Issue #1230: Use parse_command() which properly skips global flags
    # to find the subcommand. This handles cases like:
    # - gh --repo owner/repo pr list
    # - gh -R owner/repo issue view 123
    parsed = parse_command(command)
    if parsed is None:
        return False

    cmd_type = parsed.get("type")
    main_cmd = parsed.get("main_command", parsed.get("subcommand", ""))

    # Filter to specific target subcommands
    target_subcommands = {
        "gh": {"pr", "issue", "api", "run", "auth"},
        "git": {"push", "pull", "commit", "worktree", "checkout", "switch", "merge", "rebase"},
        "npm": {"run", "install", "test", "build", "i", "add", "t"},
    }

    if cmd_type in target_subcommands:
        return main_cmd in target_subcommands[cmd_type]

    return False


# Re-export from parsers for backward compatibility (Issue #2411)
__all__ = [
    # Main API
    "extract_result_from_output",
    "extract_worktree_add_path",
    "is_target_command",
    "parse_command",
    # Individual parsers (for direct use if needed)
    "parse_gh_command",
    "parse_git_command",
    "parse_npm_command",
    # Utilities
    "CONFLICT_FILE_PATTERN",
    "extract_conflict_info",
]
