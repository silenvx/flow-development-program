#!/usr/bin/env python3
"""worktree作成・PR作成時にdevelopment-workflow Skillを参照するようリマインド。

Why:
    AIエージェントはセッション間で学習しないため「手順は身についている」は誤り。
    常にSkillを参照することで、手順の見落としを防ぐ。

What:
    - Bashコマンド実行前（PreToolUse:Bash）に発火
    - git worktree add / gh pr create を検出
    - development-workflow Skill参照のリマインダーを表示
    - チェックリスト付きのメッセージで確認事項を提示

Remarks:
    - 警告型フック（systemMessage、ブロックしない）
    - hook-change-detectorはフックファイル変更、本フックはワークフロー操作
    - Issue #2387: 「手順が身についている」思考を防止

Changelog:
    - silenvx/dekita#2387: フック追加
"""

import json
import re
import sys

from lib.execution import log_hook_execution
from lib.session import parse_hook_input
from lib.strings import split_command_chain, strip_quoted_strings


def is_worktree_add_command(command: str) -> bool:
    """Check if command contains git worktree add.

    Handles command chains like:
    - git worktree add .worktrees/xxx -b branch
    - SKIP_PLAN=1 git worktree add ...
    """
    stripped = strip_quoted_strings(command)
    subcommands = split_command_chain(stripped)
    for subcmd in subcommands:
        # Match: optional env vars, then git worktree add
        if re.search(r"(?:^|\s)git\s+worktree\s+add(\s|$)", subcmd):
            return True
    return False


def is_pr_create_command(command: str) -> bool:
    """Check if command contains gh pr create.

    Handles command chains like:
    - gh pr create --title "..."
    - git push && gh pr create
    """
    stripped = strip_quoted_strings(command)
    subcommands = split_command_chain(stripped)
    for subcmd in subcommands:
        if re.search(r"(?:^|\s)gh\s+pr\s+create(\s|$)", subcmd):
            return True
    return False


def build_worktree_skill_reminder() -> str:
    """Build reminder message for worktree creation."""
    return (
        "📚 workflow-skill-reminder: worktree作成が検出されました。\n\n"
        "【development-workflow Skill 参照リマインダー】\n"
        "worktree作成時は `development-workflow` Skill を参照してください。\n\n"
        "**確認すべき内容:**\n"
        "□ worktree作成直後のチェック（main最新との差分確認）\n"
        "□ `--lock` オプションの使用（他エージェントの削除防止）\n"
        "□ ブランチ命名規則（`feat/issue-123-desc`）\n"
        "□ setup-worktree.sh の実行\n\n"
        "**Skill呼び出し方法:**\n"
        "  /development-workflow\n\n"
        "💡 「単純な作業だからSkill不要」は誤った判断です。\n"
        "   AIエージェントはセッション間で学習しないため、常にSkillを参照してください。"
    )


def build_pr_create_skill_reminder() -> str:
    """Build reminder message for PR creation."""
    return (
        "📚 workflow-skill-reminder: PR作成が検出されました。\n\n"
        "【development-workflow Skill 参照リマインダー】\n"
        "PR作成時は `development-workflow` Skill を参照してください。\n\n"
        "**確認すべき内容:**\n"
        "□ ローカルテスト・Lintの実行（PR作成前必須）\n"
        "□ Codexレビューの実行（`codex review --base main`）\n"
        "□ コミットメッセージ規約（背景/Whyを含める）\n"
        "□ UI変更時はスクリーンショット必須\n\n"
        "**Skill呼び出し方法:**\n"
        "  /development-workflow\n\n"
        "💡 「単純な変更だからSkill不要」は誤った判断です。\n"
        "   既存パターンを見落とすリスクを回避するため、常に参照してください。"
    )


def main():
    """PreToolUse hook for Bash commands.

    Warns when worktree or PR creation commands are detected,
    reminding to reference development-workflow Skill.
    """
    try:
        data = parse_hook_input()
        tool_input = data.get("tool_input", {})
        command = tool_input.get("command", "")

        if not command:
            # No command, nothing to check
            print(json.dumps({"decision": "approve"}))
            sys.exit(0)

        warnings = []

        # Check for worktree add
        if is_worktree_add_command(command):
            warnings.append(build_worktree_skill_reminder())
            log_hook_execution(
                "workflow-skill-reminder",
                "approve",
                None,
                {"command_type": "worktree_add", "warning": "skill_reminder"},
            )

        # Check for PR create
        if is_pr_create_command(command):
            warnings.append(build_pr_create_skill_reminder())
            log_hook_execution(
                "workflow-skill-reminder",
                "approve",
                None,
                {"command_type": "pr_create", "warning": "skill_reminder"},
            )

        # Return with warnings if any
        if warnings:
            combined_warning = "\n\n---\n\n".join(warnings)
            result = {
                "decision": "approve",
                "systemMessage": combined_warning,
            }
            print(json.dumps(result))
            sys.exit(0)

        # No relevant commands detected
        print(json.dumps({"decision": "approve"}))

    except Exception as e:
        # On error, approve to avoid blocking
        print(f"[workflow-skill-reminder] Hook error: {e}", file=sys.stderr)
        result = {"decision": "approve", "reason": f"Hook error: {e}"}
        print(json.dumps(result))

    sys.exit(0)


if __name__ == "__main__":
    main()
