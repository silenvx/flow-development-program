#!/usr/bin/env python3
"""ユーザーフィードバック検出時の仕組み化を確認する。

Why:
    ユーザーが問題を指摘した場合、その問題の修正だけでなく、
    類似問題を将来検出できる仕組み化が必要。セッション終了時に
    仕組み化されていなければ警告する。

What:
    - セッション状態から `user_feedback_detected` フラグを確認
    - フィードバック検出時、仕組み化ファイル変更を検出
    - 仕組み化なしの場合にACTION_REQUIREDを出力

Remarks:
    - feedback_detector.py がセッション状態に記録
    - systematization_check.py とは別の観点（ユーザー指摘への対応）
    - ブロックではなく警告（exit 0）

Changelog:
    - silenvx/dekita#2754: 新規作成
"""

import json
import re
import sys

from common import FLOW_LOG_DIR
from lib.execution import log_hook_execution
from lib.session import create_hook_context, parse_hook_input
from lib.transcript import load_transcript

# Systematization file patterns (same as systematization_check.py)
SYSTEMATIZATION_PATTERNS = [
    r"\.claude/hooks/.*\.py$",
    r"\.github/workflows/.*\.ya?ml$",
    r"\.claude/scripts/.*\.(?:py|sh)$",
    r"\.claude/skills/.*/SKILL\.md$",
]

# Patterns indicating add-perspective was executed
ADD_PERSPECTIVE_PATTERNS = [
    r"/add-perspective",
    r"add-perspective",
    r"振り返り観点.*追加",
    r"perspective.*追加",
]


def load_session_state(session_id: str) -> dict:
    """Load session state from flow state file.

    Args:
        session_id: The Claude Code session ID.

    Returns:
        The session state dict, or empty dict if not found.
    """
    if not session_id:
        return {}

    state_file = FLOW_LOG_DIR / f"state-{session_id}.json"
    try:
        if state_file.exists():
            return json.loads(state_file.read_text())
    except (json.JSONDecodeError, OSError):
        pass  # Best effort - invalid/missing state file returns empty dict
    return {}


def extract_file_operations(transcript: list[dict]) -> list[str]:
    """Extract file paths from Edit/Write tool uses."""
    files = []
    for entry in transcript:
        if entry.get("role") == "assistant":
            content = entry.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tool_name = block.get("name", "")
                        if tool_name in ("Edit", "Write"):
                            file_path = block.get("input", {}).get("file_path", "")
                            if file_path:
                                files.append(file_path)
    return files


def extract_skill_invocations(transcript: list[dict]) -> list[str]:
    """Extract Skill invocations from transcript."""
    skills = []
    for entry in transcript:
        if entry.get("role") == "assistant":
            content = entry.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tool_name = block.get("name", "")
                        if tool_name == "Skill":
                            skill_name = block.get("input", {}).get("skill", "")
                            if skill_name:
                                skills.append(skill_name)
    return skills


def find_systematization_files(files: list[str]) -> list[str]:
    """Find files that indicate systematization."""
    systematized = []
    for file_path in files:
        for pattern in SYSTEMATIZATION_PATTERNS:
            if re.search(pattern, file_path):
                systematized.append(file_path)
                break
    return systematized


def check_add_perspective_executed(transcript: list[dict], skills: list[str]) -> bool:
    """Check if add-perspective was executed.

    Args:
        transcript: The session transcript.
        skills: List of skill names invoked.

    Returns:
        True if add-perspective was executed, False otherwise.
    """
    # Check if add-perspective skill was invoked
    if "add-perspective" in skills:
        return True

    # Check transcript content for add-perspective patterns
    for entry in transcript:
        if entry.get("role") == "assistant":
            content = entry.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "")
                        for pattern in ADD_PERSPECTIVE_PATTERNS:
                            if re.search(pattern, text, re.IGNORECASE):
                                return True

    return False


def main():
    """Stop hook to check systematization for sessions with user feedback.

    Uses exit code 0 with ACTION_REQUIRED message to allow continuation.
    Claude Code will see the message and take action autonomously.
    """
    result = {"decision": "approve"}

    try:
        input_data = parse_hook_input()
        ctx = create_hook_context(input_data)

        # Skip if stop hook is already active (prevents infinite loops)
        if input_data.get("stop_hook_active"):
            log_hook_execution("user-feedback-systematization-check", "approve", "stop_hook_active")
            print(json.dumps(result))
            return

        session_id = ctx.get_session_id()
        if not session_id:
            log_hook_execution("user-feedback-systematization-check", "approve", "no session_id")
            print(json.dumps(result))
            return

        # Check session state for user feedback flag
        state = load_session_state(session_id)
        if not state.get("user_feedback_detected"):
            log_hook_execution("user-feedback-systematization-check", "approve", "no user feedback")
            print(json.dumps(result))
            return

        # User feedback was detected - check for systematization
        transcript_path = input_data.get("transcript_path")
        if not transcript_path:
            log_hook_execution(
                "user-feedback-systematization-check", "approve", "no transcript path"
            )
            print(json.dumps(result))
            return

        transcript = load_transcript(transcript_path)
        if not transcript:
            log_hook_execution(
                "user-feedback-systematization-check",
                "approve",
                "transcript load failed",
            )
            print(json.dumps(result))
            return

        file_operations = extract_file_operations(transcript)
        skill_invocations = extract_skill_invocations(transcript)
        systematized_files = find_systematization_files(file_operations)

        # Check if add-perspective was executed
        add_perspective_executed = check_add_perspective_executed(transcript, skill_invocations)

        # Decision logic:
        # - If systematization files were created -> approve
        # - If add-perspective was executed -> approve with note
        # - Otherwise -> warn with ACTION_REQUIRED
        if systematized_files:
            result["systemMessage"] = (
                f"✅ [user-feedback-systematization-check] "
                f"ユーザーフィードバック対応: 仕組み化ファイル作成 ({len(systematized_files)}件)"
            )
            log_hook_execution(
                "user-feedback-systematization-check",
                "approve",
                f"systematized: {len(systematized_files)} files",
            )
        elif add_perspective_executed:
            result["systemMessage"] = (
                "✅ [user-feedback-systematization-check] "
                "ユーザーフィードバック対応: /add-perspective 実行済み"
            )
            log_hook_execution(
                "user-feedback-systematization-check",
                "approve",
                "add-perspective executed",
            )
        else:
            # ACTION_REQUIRED format for Claude Code to take autonomous action
            reason = (
                "[ACTION_REQUIRED: FEEDBACK_SYSTEMATIZATION]\n"
                "ユーザーフィードバックが検出されましたが、仕組み化されていません。\n\n"
                "ユーザーが問題を指摘した場合、類似問題を将来検出できる仕組み化が必要です。\n\n"
                "以下のいずれかを実行してください:\n"
                "1. `/add-perspective` で振り返り観点を追加\n"
                "2. `.claude/hooks/` にフックを作成して検出機構を実装\n"
                "3. `.github/workflows/` にCIチェックを追加\n"
                "4. 仕組み化が不要な理由をIssueに記録\n\n"
                "ドキュメント（AGENTS.md等）への追記だけでは不十分です。"
            )
            log_hook_execution(
                "user-feedback-systematization-check",
                "warn",
                "feedback detected but not systematized",
            )
            # Print to stderr for Claude Code to see
            print(f"[user-feedback-systematization-check] {reason}", file=sys.stderr)

    except Exception as e:
        log_hook_execution("user-feedback-systematization-check", "approve", f"error: {e}")
        print(f"[user-feedback-systematization-check] Error: {e}", file=sys.stderr)

    print(json.dumps(result))


if __name__ == "__main__":
    main()
