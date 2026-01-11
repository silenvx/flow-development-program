#!/usr/bin/env python3
"""[IMMEDIATE]タグの実行漏れを早期検出する。

Why:
    PRマージ後に[IMMEDIATE: /reflect]が表示されても、ユーザーが次の入力をした場合、
    Claudeが別のタスクに移って振り返りを忘れることがある。
    Stop hookのみでは検出が遅く、セッション終了時まで気づかない。

What:
    - UserPromptSubmit時にpending状態ファイルを確認
    - 未実行の[IMMEDIATE]アクションがあれば即座にブロック
    - 実行済みなら状態ファイルを削除してフローを継続

State:
    - reads: /tmp/claude-hooks/immediate-pending-{session_id}.json
    - deletes: same (on successful verification)

Remarks:
    - post-merge-reflection-enforcer.pyがpending状態を書き込み
    - reflection-completion-check.pyはStop hookで最終チェック
    - 本フックはUserPromptSubmitで早期検出
    - lib/reflection.pyの共通関数を使用（Issue #2694）

Changelog:
    - silenvx/dekita#2690: 新規作成
    - silenvx/dekita#2695: timestampベースのトランスクリプトフィルタリング追加
"""

import json
import tempfile
from datetime import datetime
from pathlib import Path

from lib.execution import log_hook_execution
from lib.path_validation import is_safe_transcript_path
from lib.reflection import check_immediate_action_executed
from lib.session import create_hook_context, is_valid_session_id, parse_hook_input

# Session state directory (same as post-merge-reflection-enforcer.py)
SESSION_DIR = Path(tempfile.gettempdir()) / "claude-hooks"


def get_immediate_pending_file(session_id: str) -> Path | None:
    """Get the file path for storing immediate pending action state.

    Args:
        session_id: The Claude session ID to scope the file.

    Returns:
        Path to session-specific immediate pending state file, or None if
        session_id is invalid (security: prevents path traversal).
    """
    # Security: Validate session_id to prevent path traversal attacks
    if not is_valid_session_id(session_id):
        return None
    return SESSION_DIR / f"immediate-pending-{session_id}.json"


def load_immediate_pending_state(session_id: str) -> dict | None:
    """Load immediate pending action state.

    Args:
        session_id: The Claude session ID.

    Returns:
        State dictionary if file exists and is valid, None otherwise.
    """
    try:
        state_file = get_immediate_pending_file(session_id)
        if state_file is None:
            return None  # Invalid session_id
        if state_file.exists():
            return json.loads(state_file.read_text())
    except Exception:
        pass  # Best effort - corrupted state is ignored
    return None


def delete_immediate_pending_state(session_id: str) -> None:
    """Delete immediate pending action state file.

    Called when the action has been verified as executed.

    Args:
        session_id: The Claude session ID.
    """
    try:
        state_file = get_immediate_pending_file(session_id)
        if state_file is None:
            return  # Invalid session_id
        if state_file.exists():
            state_file.unlink()
    except Exception:
        pass  # Best effort - deletion may fail


def read_transcript(transcript_path: str | None, since_timestamp: str | None = None) -> str:
    """Read the transcript file, optionally filtering by timestamp.

    Issue #2695: Filter transcript entries by timestamp to avoid false positives
    from reflection keywords that occurred before the IMMEDIATE action was created.

    Args:
        transcript_path: Path to the transcript file from input_data.
        since_timestamp: ISO format timestamp. If provided, only entries after this
            timestamp will be included in the result.

    Returns:
        Transcript content as string, or empty string if unavailable.
    """
    if not transcript_path:
        return ""
    try:
        if not is_safe_transcript_path(transcript_path):
            return ""
        path = Path(transcript_path)
        if not path.exists():
            return ""

        if since_timestamp is None:
            # No filtering, return full content
            return path.read_text()

        # Parse the cutoff timestamp
        try:
            cutoff_dt = datetime.fromisoformat(since_timestamp.replace("Z", "+00:00"))
        except ValueError:
            # Invalid timestamp format, return full content
            return path.read_text()

        # Filter JSONL entries by timestamp
        filtered_lines: list[str] = []
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                entry_ts = entry.get("timestamp") or entry.get("snapshot", {}).get("timestamp")
                if entry_ts:
                    entry_dt = datetime.fromisoformat(entry_ts.replace("Z", "+00:00"))
                    if entry_dt >= cutoff_dt:
                        filtered_lines.append(line)
                else:
                    # No timestamp in entry, include it (conservative)
                    filtered_lines.append(line)
            except (json.JSONDecodeError, ValueError, TypeError):
                # Invalid JSON, timestamp format, or timezone mismatch
                # (naive vs aware datetime), include line (conservative)
                filtered_lines.append(line)

        return "\n".join(filtered_lines)

    except Exception:
        pass  # Best effort - transcript read failure should not block hook
    return ""


def main():
    """UserPromptSubmit hook to detect unexecuted [IMMEDIATE] actions early.

    Issue #2690: If a pending action exists and hasn't been executed,
    block the user's next input to force execution.
    """
    result = {"continue": True}

    try:
        input_data = parse_hook_input()
        ctx = create_hook_context(input_data)
        session_id = ctx.get_session_id()

        if not session_id:
            print(json.dumps(result))
            return

        # Check for pending immediate action
        pending_state = load_immediate_pending_state(session_id)
        if not pending_state:
            # No pending action, continue normally
            print(json.dumps(result))
            return

        action = pending_state.get("action", "")
        context = pending_state.get("context", "")
        # Issue #2695: Get timestamp to filter transcript entries
        since_timestamp = pending_state.get("timestamp")

        # Read transcript and check if action was executed
        # Issue #2695: Only check transcript entries after the pending state was created
        transcript_path = input_data.get("transcript_path")
        transcript_content = read_transcript(transcript_path, since_timestamp)
        if check_immediate_action_executed(action, transcript_content):
            # Action was executed, delete pending state and continue
            delete_immediate_pending_state(session_id)
            log_hook_execution(
                "immediate-pending-check",
                "approve",
                f"Immediate action '{action}' verified as executed",
                {"action": action, "context": context},
            )
            print(json.dumps(result))
            return

        # Action not executed, block
        log_hook_execution(
            "immediate-pending-check",
            "block",
            f"Immediate action '{action}' not executed",
            {"action": action, "context": context},
        )

        message = (
            f"⚠️ 未実行の[IMMEDIATE]アクションがあります\n\n"
            f"**アクション**: `{action}`\n"
            f"**コンテキスト**: {context}\n\n"
            f"このアクションを実行してから次の作業に進んでください。\n\n"
            f"[IMMEDIATE: {action}]"
        )

        result = {
            "decision": "block",
            "reason": message,
        }

    except Exception as e:
        log_hook_execution(
            "immediate-pending-check",
            "error",
            f"Hook error: {e}",
        )

    print(json.dumps(result))


if __name__ == "__main__":
    main()
