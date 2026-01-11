#!/usr/bin/env python3
"""PRマージ後または/reflect skill invoke後の振り返り完了を検証する。

Why:
    PRマージ後に振り返りをせずにセッションを終了すると、学習機会を逃し、
    同じ問題を繰り返す可能性がある。振り返りの完了をブロックで強制する。

What:
    - reflection_requiredフラグの確認（post-merge-reflection-enforcerが設定）
    - flow stateからマージ完了フェーズを検知
    - /reflect skillの呼び出し確認
    - 五省分析が実行されたか検証
    - [IMMEDIATE: action]タグの実行検証
    - 要件未達成時はセッション終了をブロック

State:
    reads: /tmp/claude-hooks/reflection-required-{session_id}.json
    reads: .claude/logs/flow/*.jsonl

Remarks:
    - post-merge-reflection-enforcer.pyがフラグ設定、本フックが検証
    - Issue作成強制はsystematization-check.pyが担当
    - 振り返り完了条件: 五省分析キーワード検出、または「振り返り完了」明示
    - lib/reflection.pyの共通関数を使用（Issue #2694）

Changelog:
    - silenvx/dekita#2140: /reflect skill呼び出し確認追加
    - silenvx/dekita#2172: flow stateからmerge完了検知
    - silenvx/dekita#2186: [IMMEDIATE]タグ実行検証追加
    - silenvx/dekita#2545: HookContextパターン移行
"""

import json
import re
import tempfile
from pathlib import Path

from common import FLOW_LOG_DIR
from lib.execution import log_hook_execution
from lib.reflection import (
    check_immediate_action_executed,
    check_skill_invocation,
    check_transcript_for_reflection,
    extract_immediate_tags,
)
from lib.results import make_block_result
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
    }


def save_reflection_state(state: dict) -> None:
    """Save reflection state to session file."""
    try:
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        state_file = get_reflection_state_file()
        state_file.write_text(json.dumps(state, indent=2))
    except Exception:
        pass  # Best effort - state save may fail


def check_merge_phase_completed() -> list[str]:
    """Check if any workflow has completed the merge phase in this session.

    Issue #2172: Read the flow state file to detect merge completions.
    This replaces the old state-file-based detection that was removed in #2159.

    Issue #2545: HookContextパターンに移行。グローバルの_ctxからsession_idを取得。

    Returns:
        List of workflow IDs that have completed the merge phase.
    """
    session_id = _ctx.get_session_id() if _ctx else None
    if not session_id:
        session_id = "unknown"
    state_file = FLOW_LOG_DIR / f"state-{session_id}.json"

    try:
        if not state_file.exists():
            return []

        with open(state_file, encoding="utf-8") as f:
            state = json.load(f)

        workflows = state.get("workflows", {})
        merged_workflows = []

        for workflow_id, workflow_state in workflows.items():
            phases = workflow_state.get("phases", {})
            merge_phase = phases.get("merge", {})

            # Check if merge phase is completed
            if merge_phase.get("status") == "completed":
                merged_workflows.append(workflow_id)

        return merged_workflows

    except (OSError, json.JSONDecodeError):
        return []


def main():
    """Main hook logic for Stop event."""
    global _ctx
    result = {"continue": True}

    try:
        input_data = parse_hook_input()
        # Issue #2545: HookContextパターンでsession_idを取得
        _ctx = create_hook_context(input_data)

        # Check if this is a Stop hook call
        hook_type = input_data.get("hook_type", "")
        if hook_type != "Stop":
            # Also handle case where hook_type might not be set
            # but we're called from Stop hook configuration
            pass

        # Load reflection state
        state = load_reflection_state()

        # Check transcript for reflection indicators
        # Stop hooks receive transcript_path in input_data
        transcript_path = input_data.get("transcript_path", "")
        transcript_content = ""
        if transcript_path:
            try:
                transcript_content = Path(transcript_path).read_text()
            except Exception:
                pass  # Best effort - transcript may not exist

        # Issue #2140: Check if /reflect skill was invoked
        skill_invoked = check_skill_invocation(transcript_content)

        # Issue #2172: Check flow state for completed merge phases
        merged_workflows = check_merge_phase_completed()
        flow_merge_required = len(merged_workflows) > 0

        # Issue #2186: Check for [IMMEDIATE] tags and verify execution
        immediate_actions = extract_immediate_tags(transcript_content)

        # Check all [IMMEDIATE] actions for execution
        unexecuted_immediate_actions = [
            action
            for action in immediate_actions
            if not check_immediate_action_executed(action, transcript_content)
        ]

        # If there are unexecuted [IMMEDIATE] actions, block session end
        if unexecuted_immediate_actions:
            actions_str = ", ".join(unexecuted_immediate_actions)
            reason = (
                f"`[IMMEDIATE]` タグで指定されたアクションが未実行です: {actions_str}\n\n"
                "**指定されたアクションを今すぐ実行してください。**\n\n"
                "これは強制力のある指示です。無視するとセッション終了がブロックされ続けます。"
            )
            result = make_block_result("reflection-completion-check", reason)
            result["continue"] = False
            log_hook_execution(
                "reflection-completion-check",
                "block",
                f"Unexecuted [IMMEDIATE] actions: {actions_str}",
            )
            print(json.dumps(result))
            return

        # Check if /reflect was requested via [IMMEDIATE] tag (already verified above)
        immediate_reflect_required = any(
            "/reflect" in action.lower() for action in immediate_actions
        )

        # Determine if reflection is required
        # Either from PR merge (state flag), flow state, skill invocation, or [IMMEDIATE] tag
        pr_merge_required = state.get("reflection_required", False)
        reflection_required = (
            pr_merge_required or flow_merge_required or skill_invoked or immediate_reflect_required
        )

        # If no reflection required from any source, allow continuation
        if not reflection_required:
            log_hook_execution(
                "reflection-completion-check",
                "approve",
                "No reflection required",
            )
            print(json.dumps(result))
            return

        # Check if reflection was already marked as done
        # Note: For skill invocation, we must verify reflection content exists in transcript
        # because the skill may have been invoked after a previous reflection was completed.
        # The reflection_done flag from state only applies to PR merge case (not flow merge).
        # Issue #2172: Don't bypass for flow_merge_required to ensure new merges are checked.
        if state.get("reflection_done", False) and not skill_invoked and not flow_merge_required:
            log_hook_execution(
                "reflection-completion-check",
                "approve",
                "Reflection already completed",
            )
            print(json.dumps(result))
            return

        # Get merged PRs/workflows for the message (may be empty if skill-invoked)
        merged_prs = state.get("merged_prs", [])
        # Issue #2172: Also include workflow IDs from flow state
        if not merged_prs and merged_workflows:
            # Use workflow IDs (e.g., "issue-123") as identifiers
            pr_list = ", ".join(merged_workflows)
        elif merged_prs:
            pr_list = ", ".join(f"#{pr}" for pr in merged_prs)
        else:
            pr_list = None

        # Check for reflection in transcript or explicit completion phrase
        has_reflection = check_transcript_for_reflection(transcript_content)

        # Also check for explicit completion acknowledgment
        explicit_completion = bool(
            re.search(r"振り返り完了|振り返りが完了|reflection complete", transcript_content, re.I)
        )

        if has_reflection or explicit_completion:
            # Reflection was done - mark as complete and allow
            state["reflection_done"] = True
            # Only save state for PR merge case, not skill invocation
            # (skill invocation should require reflection each time)
            if not skill_invoked:
                save_reflection_state(state)
            completion_reason = (
                f"Reflection completed for PRs: {pr_list}"
                if pr_list
                else "Reflection completed (skill invoked)"
            )
            log_hook_execution(
                "reflection-completion-check",
                "approve",
                completion_reason,
            )
            print(json.dumps(result))
            return

        # If reflection not done, block
        # Build appropriate message based on trigger
        if pr_list:
            reason = (
                f"PRマージ後の振り返りが未完了です（対象PR: {pr_list}）。\n\n"
                "**`/reflect` スキルを実行してください。**\n\n"
                "振り返りが不要な場合は「振り返り完了」と明示してください。"
            )
            block_reason = f"Reflection required for PRs: {pr_list}"
        else:
            reason = (
                "`/reflect` スキルが呼び出されましたが、振り返りが未完了です。\n\n"
                "**五省を実施してください。**\n\n"
                "振り返りが不要な場合は「振り返り完了」と明示してください。"
            )
            block_reason = "Reflection required (skill invoked but not completed)"

        result = make_block_result("reflection-completion-check", reason)
        result["continue"] = False

        log_hook_execution(
            "reflection-completion-check",
            "block",
            block_reason,
        )

    except Exception as e:
        log_hook_execution(
            "reflection-completion-check",
            "error",
            f"Hook error: {e}",
        )
        # Don't block on errors
        result = {"continue": True}

    print(json.dumps(result))


if __name__ == "__main__":
    main()
