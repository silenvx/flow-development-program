#!/usr/bin/env python3
"""特定操作の前にSkill使用を強制。

Why:
    worktree作成やPR作成時にSkillの手順を確認せずに進めると、
    推奨手順を見落とす。操作前にSkill使用を強制することで品質を担保する。

What:
    - 特定コマンド実行前（PreToolUse:Bash）に発火
    - OPERATION_SKILL_MAPで定義されたコマンドパターンを検出
    - transcriptから当該Skillの使用履歴を確認
    - Skill未使用の場合はブロック

Remarks:
    - ブロック型フック（Skill未使用時はブロック）
    - development-workflow: worktree add, gh pr create, gh pr merge
    - code-review: レビューコメント操作, スレッド解決

Changelog:
    - silenvx/dekita#2355: フック追加（Skill未使用時のブロック）
    - silenvx/dekita#2752: gh pr mergeパターン追加
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from lib.execution import log_hook_execution
from lib.path_validation import is_safe_transcript_path
from lib.results import make_approve_result, make_block_result
from lib.session import create_hook_context, parse_hook_input

# Operation to required Skill mapping
# Each pattern maps to a tuple of (skill_name, operation_description)
# Note: Patterns are simple strings, re.search() handles compilation automatically (Copilot review)
OPERATION_SKILL_MAP: dict[str, tuple[str, str]] = {
    r"git worktree add": ("development-workflow", "worktree作成"),
    r"gh pr create": ("development-workflow", "PR作成"),
    r"gh pr merge": ("development-workflow", "PRマージ"),
    # Review-related commands
    r"gh api repos/.*/pulls/.*/comments": ("code-review", "レビューコメント操作"),
    r"batch_resolve_threads\.py": ("code-review", "スレッド解決"),
}


def get_skill_usage_from_transcript(transcript_path: str | None, session_id: str) -> set[str]:
    """Extract Skill names used in the current session from transcript.

    Args:
        transcript_path: Path to the transcript JSONL file.
        session_id: Current session ID to filter entries.

    Returns:
        Set of Skill names used in this session.
    """
    skills_used: set[str] = set()

    if not transcript_path or not is_safe_transcript_path(transcript_path):
        return skills_used

    try:
        transcript_file = Path(transcript_path)
        if not transcript_file.exists():
            return skills_used

        with open(transcript_file, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    # Only check entries from current session
                    entry_session = entry.get("sessionId")
                    if entry_session and entry_session != session_id:
                        continue

                    # Check for Skill tool_use
                    message = entry.get("message", {})
                    content = message.get("content", [])
                    if isinstance(content, list):
                        for item in content:
                            if isinstance(item, dict):
                                if item.get("type") == "tool_use" and item.get("name") == "Skill":
                                    skill_input = item.get("input", {})
                                    if isinstance(skill_input, dict):
                                        skill_name = skill_input.get("skill")
                                        if skill_name:
                                            skills_used.add(skill_name)
                except (json.JSONDecodeError, KeyError):
                    continue
    except Exception:
        pass  # Best effort - transcript read failure should not break hook

    return skills_used


def check_command_for_skill_requirement(command: str) -> tuple[str, str] | None:
    """Check if command requires a specific Skill to be used first.

    Args:
        command: The bash command being executed.

    Returns:
        Tuple of (skill_name, operation_description) if Skill is required, None otherwise.
    """
    for pattern, (skill, desc) in OPERATION_SKILL_MAP.items():
        if re.search(pattern, command):
            return (skill, desc)
    return None


def main():
    """Main hook logic for PreToolUse event."""
    result = make_approve_result("skill-usage-reminder")

    try:
        input_data = parse_hook_input()

        ctx = create_hook_context(input_data)
        session_id = ctx.get_session_id()

        # Only check Bash tool
        tool_name = input_data.get("tool_name", "")
        if tool_name != "Bash":
            print(json.dumps(result))
            return

        # Get command from tool input
        tool_input = input_data.get("tool_input", {})
        command = tool_input.get("command", "")
        if not command:
            print(json.dumps(result))
            return

        # Check if command requires a Skill
        skill_requirement = check_command_for_skill_requirement(command)
        if not skill_requirement:
            print(json.dumps(result))
            return

        required_skill, operation_desc = skill_requirement

        # Get transcript path and check Skill usage
        transcript_path = input_data.get("transcript_path", "")
        skills_used = get_skill_usage_from_transcript(transcript_path, session_id)

        if required_skill in skills_used:
            # Skill was used, allow operation
            log_hook_execution(
                "skill-usage-reminder",
                "approve",
                f"Skill '{required_skill}' already used for '{operation_desc}'",
            )
            print(json.dumps(result))
            return

        # Skill was not used, BLOCK
        # Note: make_block_result() adds hook name prefix automatically (Copilot review)
        block_msg = (
            f"{operation_desc}の前にSkill使用が必要 - ブロック\n\n"
            f"**必要なSkill**: `{required_skill}`\n\n"
            f"このSkillを使用すると、手順を確認しながら安全に作業を進められます。\n\n"
            "**対処法**:\n"
            f"  1. `/skill {required_skill}` を実行してSkillの手順を確認\n"
            "  2. 手順に従って操作を再実行\n\n"
            "**ヒント**: Skillには推奨される手順とチェックリストが含まれています。\n"
        )

        # Note: make_block_result() internally calls log_hook_execution() (Copilot review)
        result = make_block_result("skill-usage-reminder", block_msg, ctx)

    except Exception as e:
        log_hook_execution("skill-usage-reminder", "error", f"Hook error: {e}")

    print(json.dumps(result))


if __name__ == "__main__":
    main()
