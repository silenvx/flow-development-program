#!/usr/bin/env python3
"""フロントエンド変更時にブラウザ確認を強制。

Why:
    フロントエンドファイル変更後にブラウザ確認せずコミットすると、
    ランタイムエラーやUI崩れに気づかないまま本番に反映される。

What:
    - git commit時（PreToolUse:Bash）に発火
    - frontend/src/配下のTS/TSX/JSON/CSS変更を検出
    - ブラウザ確認マーカーファイルがなければブロック
    - confirm-ui-check.py実行後にコミット可能

State:
    - reads: .claude/logs/markers/ui-check-*.done

Remarks:
    - ブロック型フック（確認なしはコミット不可）
    - main/masterブランチはスキップ
    - .ts/.tsxはlib/hooks/workersも含む（Issue #209）

Changelog:
    - silenvx/dekita#xxx: フック追加
    - silenvx/dekita#209: .ts/.tsxファイル対象を拡大
"""

import fnmatch
import json
import re
import subprocess
import sys

from common import MARKERS_LOG_DIR
from lib.constants import TIMEOUT_MEDIUM
from lib.execution import log_hook_execution
from lib.git import get_current_branch
from lib.results import make_block_result
from lib.session import parse_hook_input
from lib.strings import sanitize_branch_name, strip_quoted_strings

# File patterns that require browser verification
# These patterns are matched against git paths (relative to repo root)
# Expanded to cover ALL frontend source files (see Issue #209 for background)
FRONTEND_FILE_PATTERNS = [
    "frontend/src/**/*.ts",  # All TypeScript files (lib, hooks, workers, etc.)
    "frontend/src/**/*.tsx",  # All React components
    "frontend/src/i18n/locales/*.json",  # i18n translations
    "frontend/src/index.css",  # Global CSS
]


def matches_frontend_pattern(filepath: str) -> bool:
    """Check if a file path matches any frontend file pattern."""
    for pattern in FRONTEND_FILE_PATTERNS:
        # fnmatch doesn't support **, so we need to handle it manually
        if "**" in pattern:
            # Split pattern at **
            parts = pattern.split("**")
            if len(parts) == 2:
                prefix, suffix = parts
                # Check if file starts with prefix and ends with suffix pattern
                if filepath.startswith(prefix):
                    remaining = filepath[len(prefix) :]
                    # suffix might have a leading /, remove it
                    if suffix.startswith("/"):
                        suffix = suffix[1:]
                    if fnmatch.fnmatch(remaining, "*" + suffix) or fnmatch.fnmatch(
                        remaining, "*/" + suffix
                    ):
                        return True
        else:
            if fnmatch.fnmatch(filepath, pattern):
                return True
    return False


def is_git_commit_command(command: str) -> bool:
    """Check if command is a git commit command."""
    if not command.strip():
        return False

    stripped_command = strip_quoted_strings(command)
    return bool(re.search(r"git\s+commit\b", stripped_command))


def has_auto_stage_flag(command: str) -> bool:
    """Check if command has -a or -am flag (auto-staging modified files)."""
    stripped_command = strip_quoted_strings(command)
    # Match -a, -am, -ma, or --all flags
    return bool(re.search(r"git\s+commit\s+.*(-a\b|--all\b|-[a-z]*a[a-z]*\b)", stripped_command))


def get_staged_frontend_files() -> list[str]:
    """Get list of staged frontend files."""
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode != 0:
            return []

        output = result.stdout.strip()
        if not output:
            return []
        files = output.split("\n")
        frontend_files = [f for f in files if matches_frontend_pattern(f)]
        return frontend_files
    except Exception:
        return []


def get_modified_frontend_files() -> list[str]:
    """Get list of modified (unstaged) frontend files.

    Used to detect files that would be staged by `git commit -a`.
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode != 0:
            return []

        output = result.stdout.strip()
        if not output:
            return []
        files = output.split("\n")
        frontend_files = [f for f in files if matches_frontend_pattern(f)]
        return frontend_files
    except Exception:
        return []


def check_ui_verification_done(branch: str) -> bool:
    """Check if UI verification was confirmed for this branch."""
    safe_branch = sanitize_branch_name(branch)
    log_file = MARKERS_LOG_DIR / f"ui-check-{safe_branch}.done"
    return log_file.exists()


def main():
    """PreToolUse hook for git commit with frontend file changes."""
    result = {"decision": "approve"}

    try:
        data = parse_hook_input()
        tool_input = data.get("tool_input", {})
        command = tool_input.get("command", "")

        # Only check git commit commands
        if not is_git_commit_command(command):
            pass  # Early return case - still log at end
        else:
            # Check for staged frontend files
            frontend_files = get_staged_frontend_files()

            # Also check modified files if -a flag is used (git commit -a stages modified files)
            if has_auto_stage_flag(command):
                modified_files = get_modified_frontend_files()
                # Combine and deduplicate
                frontend_files = list(set(frontend_files + modified_files))

            if not frontend_files:
                pass  # No frontend files - still log at end
            else:
                # Get current branch
                branch = get_current_branch()
                if branch is None or branch in ("main", "master"):
                    pass  # Skip on main/master - still log at end
                elif check_ui_verification_done(branch):
                    pass  # Browser verification already confirmed - still log at end
                else:
                    # Block: frontend files staged but no browser verification confirmation
                    files_list = "\n".join(f"  - {f}" for f in sorted(frontend_files))
                    reason = (
                        f"フロントエンドファイルが変更されていますが、ブラウザ確認が完了していません。\n\n"
                        f"変更されたファイル:\n{files_list}\n\n"
                        "**必須手順**:\n"
                        "1. 開発サーバーを起動: `pnpm dev:frontend` (+ `pnpm dev:worker` if needed)\n"
                        "2. Chrome DevTools MCPで実際の動作を確認\n"
                        "   - `mcp__chrome-devtools__navigate_page` でアプリにアクセス\n"
                        "   - `mcp__chrome-devtools__take_snapshot` でDOM状態を確認\n"
                        "   - `mcp__chrome-devtools__list_console_messages` でエラーがないか確認\n"
                        "   - 必要に応じて `mcp__chrome-devtools__list_network_requests` でAPI/Analytics確認\n"
                        "3. 確認完了後、以下を実行:\n\n"
                        "```bash\n"
                        "python3 .claude/scripts/confirm-ui-check.py\n"
                        "```\n\n"
                        "その後、再度コミットを実行してください。"
                    )
                    result = make_block_result("ui-check-reminder", reason)

    except Exception as e:
        print(f"[ui-check-reminder] Hook error: {e}", file=sys.stderr)
        result = {"decision": "approve"}

    # Always log execution for accurate statistics
    log_hook_execution("ui-check-reminder", result.get("decision", "approve"), result.get("reason"))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
