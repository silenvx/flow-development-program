#!/usr/bin/env python3
"""Codex CLIレビュー実行をログ記録する（codex-review-checkと連携）。

Why:
    codex-review-checkがPR作成/push前にレビュー実行済みかを確認するため、
    レビュー実行時にブランチ・コミット情報を記録しておく必要がある。

What:
    - codex reviewコマンドを検出
    - ブランチ名、コミットハッシュ、diffハッシュを記録
    - main/masterブランチでは記録しない

State:
    - writes: .claude/logs/markers/codex-review-{branch}.done

Remarks:
    - 記録型フック（ブロックしない、マーカーファイル書き込み）
    - PreToolUse:Bashで発火（codex reviewコマンド）
    - codex-review-check.pyと連携（マーカーファイル参照元）
    - diffハッシュ記録によりリベース後のスキップ判定が可能

Changelog:
    - silenvx/dekita#xxx: フック追加
"""

import json
import re
import sys

from common import MARKERS_LOG_DIR
from lib.execution import log_hook_execution
from lib.git import get_current_branch, get_diff_hash, get_head_commit
from lib.session import parse_hook_input
from lib.strings import sanitize_branch_name, strip_quoted_strings


def is_codex_review_command(command: str) -> bool:
    """Check if command is actually a codex review command.

    Returns False for commands inside quoted strings.
    """
    if not command.strip():
        return False
    stripped_command = strip_quoted_strings(command)
    return bool(re.search(r"codex\s+review\b", stripped_command))


def log_review_execution(branch: str, commit: str | None, diff_hash: str | None) -> None:
    """Log that codex review was executed for this branch at specific commit.

    Args:
        branch: The git branch name.
        commit: The HEAD commit hash when review was executed.
        diff_hash: The hash of the diff content for detecting unchanged diffs after rebase.
    """
    MARKERS_LOG_DIR.mkdir(parents=True, exist_ok=True)
    safe_branch = sanitize_branch_name(branch)
    log_file = MARKERS_LOG_DIR / f"codex-review-{safe_branch}.done"
    # Store branch:commit:diff_hash format
    # diff_hash allows skipping re-review when only commit hash changed (e.g., after rebase)
    if commit and diff_hash:
        content = f"{branch}:{commit}:{diff_hash}"
    elif commit:
        content = f"{branch}:{commit}"
    else:
        content = branch
    log_file.write_text(content)


def main():
    """
    PreToolUse hook for Bash commands.

    Detects `codex review` commands and logs the execution.
    """
    try:
        data = parse_hook_input()
        tool_input = data.get("tool_input", {})
        command = tool_input.get("command", "")

        # Detect codex review command (excluding quoted strings)
        if is_codex_review_command(command):
            branch = get_current_branch()
            if branch and branch not in ("main", "master"):
                commit = get_head_commit()
                diff_hash = get_diff_hash()
                log_review_execution(branch, commit, diff_hash)

        # Always approve - this hook only logs
        result = {"decision": "approve"}

    except Exception as e:
        print(f"[codex-review-logger] Hook error: {e}", file=sys.stderr)
        result = {"decision": "approve"}

    log_hook_execution(
        "codex-review-logger", result.get("decision", "approve"), result.get("reason")
    )
    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
