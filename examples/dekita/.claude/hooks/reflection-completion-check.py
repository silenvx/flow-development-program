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


def check_transcript_for_reflection(transcript_content: str) -> bool:
    """Check the conversation transcript for reflection indicators.

    Returns True if reflection keywords (五省, 振り返り, etc.) are found.
    """
    reflection_patterns = [
        r"五省",
        r"振り返り",
        r"反省点",
        r"改善点",
        r"教訓",
        r"要件理解.*悖",
        r"実装.*恥",
        r"検証.*欠",
        r"対応.*憾",
        r"効率.*欠",
    ]
    for pattern in reflection_patterns:
        if re.search(pattern, transcript_content):
            return True
    return False


def check_skill_invocation(transcript_content: str) -> bool:
    """Check if /reflect skill was invoked in the session.

    Issue #2140: Detect when the reflect skill is invoked so that
    reflection completion can be enforced even without PR merge.

    Issue #2489: Exclude [IMMEDIATE: /reflect] tags from detection.
    The IMMEDIATE tag is issued by the hook system, not by actual skill invocation.

    Returns True if skill invocation patterns are found.
    """
    # Issue #2489: Remove [IMMEDIATE: ...] tags before checking
    # These are hook-issued tags, not actual skill invocations
    # ReDoS mitigation: limit content length to 256 characters to prevent catastrophic backtracking
    cleaned_content = re.sub(
        r"\[IMMEDIATE:\s*[^\]]{1,256}\]", "", transcript_content, flags=re.IGNORECASE
    )

    # Performance: combine patterns with | and search once instead of looping
    skill_pattern = "|".join(
        [
            r"Skill: reflect",  # Skill tool invocation
            r"@\.claude/prompts/reflection/execute\.md",  # Direct prompt reference
            r"/reflect\b",  # Slash command
            r"Skill\(.*reflect.*\)",  # Skill tool call syntax
        ]
    )
    return bool(re.search(skill_pattern, cleaned_content, re.IGNORECASE))


# Whitelist of allowed [IMMEDIATE] actions
# Only these specific commands are recognized to prevent false positives
# from test examples, documentation, and code fragments in transcripts.
ALLOWED_IMMEDIATE_ACTIONS = frozenset(
    [
        "/reflect",
    ]
)


def is_valid_immediate_action(action: str) -> bool:
    """Validate that an extracted action is an allowed command.

    Issue #2193: The regex pattern can match code examples in the transcript,
    such as pattern definitions or test strings. This function filters out
    such false positives.

    Issue #2201: Restricted to slash commands only.

    Issue #2209: Further restricted to explicit whitelist to prevent false
    positives from test examples like [IMMEDIATE: /test] or [IMMEDIATE: /commit].

    Valid actions:
    - Only commands in ALLOWED_IMMEDIATE_ACTIONS whitelist
    - Currently only /reflect is allowed

    Args:
        action: The extracted action string

    Returns:
        True if the action is in the allowed whitelist
    """
    action = action.strip().lower()
    return action in ALLOWED_IMMEDIATE_ACTIONS


def extract_immediate_tags(transcript_content: str) -> list[str]:
    """Extract [IMMEDIATE: action] tags from transcript.

    Issue #2186: Detect [IMMEDIATE: /reflect] or similar tags that require
    immediate execution without user confirmation.

    Issue #2193: Validates extracted actions to filter out code fragments
    that accidentally match the pattern.

    Issue #2209: Normalizes actions to lowercase for consistent deduplication.

    Returns:
        List of actions that were requested (e.g., ["/reflect"])
    """
    # Pattern: [IMMEDIATE: action] where action can be a slash command or text
    pattern = r"\[IMMEDIATE:\s*([^\]]+)\]"
    matches = re.findall(pattern, transcript_content, re.IGNORECASE)
    # Normalize (lowercase), validate, and deduplicate
    actions = []
    for match in matches:
        action = match.strip().lower()  # Normalize to lowercase
        if action and action not in actions and is_valid_immediate_action(action):
            actions.append(action)
    return actions


def check_immediate_action_executed(action: str, transcript_content: str) -> bool:
    """Check if an [IMMEDIATE] action was executed.

    Issue #2186: Verify that the specified action was performed.

    For the special case of "/reflect", this verifies BOTH:
    1. Skill invocation (via `check_skill_invocation`) - actual /reflect skill was called
    2. Reflection content (via `check_transcript_for_reflection`) - 五省 keywords present

    Issue #2489: Manual 五省 summaries without skill invocation are not sufficient.

    For other (generic) actions, this function currently returns False
    because reliable verification requires action-specific logic (e.g.,
    checking command execution logs, test results, etc.) which is not
    yet implemented.

    Note: Future enhancement could add verification for other common
    actions like "/commit", "run tests", etc.

    Args:
        action: The action string (e.g., "/reflect", "run tests")
        transcript_content: Full transcript to search

    Returns:
        True if the action appears to have been executed.
        Currently only /reflect is verifiable; other actions return False.
    """
    action_lower = action.lower().strip()

    # Handle /reflect action - verify BOTH skill invocation AND reflection content
    # Issue #2489: Keyword-only detection allowed manual summaries to bypass enforcement
    if "/reflect" in action_lower:
        # Must verify skill was actually invoked (not just keywords in transcript)
        skill_invoked = check_skill_invocation(transcript_content)
        has_reflection_content = check_transcript_for_reflection(transcript_content)
        return skill_invoked and has_reflection_content

    # Future enhancement: For actions other than /reflect, implement
    # action-specific verification logic (e.g., check command execution
    # logs, test results, etc.).
    #
    # Currently, we cannot reliably verify generic actions, so we return
    # False to indicate "not verified" (which will trigger a block if
    # called from the main verification flow).
    return False


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
