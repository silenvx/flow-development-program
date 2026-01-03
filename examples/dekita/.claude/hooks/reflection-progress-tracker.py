#!/usr/bin/env python3
"""振り返り中のIssue作成を検出して進捗を追跡。

Why:
    振り返りで発見した改善点はIssue化が必要。Issue作成を追跡し、
    reflection-completion-checkが振り返り完了を判定できるようにする。

What:
    - gh issue create コマンドの成功を検出
    - 作成されたIssue番号を抽出
    - セッション状態ファイルにIssue番号を記録

State:
    - writes: /tmp/claude-hooks/reflection-required-{session_id}.json

Remarks:
    - 非ブロック型（PostToolUse）
    - post-merge-reflection-enforcerがフラグ設定、本フックは進捗追跡
    - reflection-completion-checkがセッション終了時に検証

Changelog:
    - silenvx/dekita#xxx: フック追加
    - silenvx/dekita#2203: get_exit_code()で終了コード取得を統一
    - silenvx/dekita#2545: HookContextパターン移行
"""

import json
import re
import tempfile
from pathlib import Path

from lib.execution import log_hook_execution
from lib.hook_input import get_exit_code, get_tool_result
from lib.session import HookContext, create_hook_context, parse_hook_input

# Session state directory
SESSION_DIR = Path(tempfile.gettempdir()) / "claude-hooks"
REFLECTION_REQUIRED_FILE = "reflection-required-{session_id}.json"

# グローバルコンテキスト（Issue #2545: HookContextパターン移行）
_ctx: HookContext | None = None


def get_reflection_state_file() -> Path:
    """Get the reflection state file path for the current session.

    Issue #2545: HookContextパターンに移行。グローバルの_ctxからsession_idを取得。
    """
    session_id = _ctx.get_session_id() if _ctx else None
    if not session_id:
        session_id = "unknown"
    return SESSION_DIR / REFLECTION_REQUIRED_FILE.format(session_id=session_id)


def load_reflection_state() -> dict:
    """Load reflection state from session file."""
    try:
        state_file = get_reflection_state_file()
        if state_file.exists():
            return json.loads(state_file.read_text())
    except Exception:
        pass  # Best effort - corrupted state is ignored
    return {
        "reflection_required": False,
        "merged_prs": [],
        "reflection_done": False,
        "issues_created": [],
    }


def save_reflection_state(state: dict) -> None:
    """Save reflection state to session file."""
    try:
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        state_file = get_reflection_state_file()
        state_file.write_text(json.dumps(state, indent=2))
    except Exception:
        pass  # Best effort - state save may fail


def is_issue_create_command(command: str) -> bool:
    """Check if the command is a GitHub Issue creation command."""
    return bool(re.search(r"gh\s+issue\s+create", command))


def extract_issue_number(output: str) -> str | None:
    """Extract issue number from gh issue create output."""
    # gh issue create outputs URL like: https://github.com/owner/repo/issues/123
    match = re.search(r"/issues/(\d+)", output)
    if match:
        return match.group(1)
    return None


def main():
    """Track reflection progress after issue creation."""
    global _ctx
    result = {"continue": True}

    try:
        input_data = parse_hook_input()
        # Issue #2545: HookContextパターンでsession_idを取得
        _ctx = create_hook_context(input_data)
        tool_name = input_data.get("tool_name", "")

        if tool_name != "Bash":
            print(json.dumps(result))
            return

        tool_input = input_data.get("tool_input", {})
        tool_result = get_tool_result(input_data) or {}
        command = tool_input.get("command", "")

        # Check if this is an issue creation
        if is_issue_create_command(command):
            stdout = tool_result.get("stdout", "") if isinstance(tool_result, dict) else ""
            # Issue #2203: Use get_exit_code() for consistent default value
            exit_code = get_exit_code(tool_result)

            if exit_code == 0:
                issue_number = extract_issue_number(stdout)
                if issue_number:
                    state = load_reflection_state()

                    # Track the created issue
                    if issue_number not in state.get("issues_created", []):
                        state.setdefault("issues_created", []).append(issue_number)
                        save_reflection_state(state)

                        log_hook_execution(
                            "reflection-progress-tracker",
                            "approve",
                            f"Issue #{issue_number} created, tracking for reflection",
                        )

    except Exception as e:
        log_hook_execution(
            "reflection-progress-tracker",
            "error",
            f"Hook error: {e}",
        )

    print(json.dumps(result))


if __name__ == "__main__":
    main()
