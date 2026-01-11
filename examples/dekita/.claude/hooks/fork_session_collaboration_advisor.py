#!/usr/bin/env python3
"""Fork-session開始時に独立したIssue候補を提案する。

Why:
    Fork-sessionが親セッションと競合するIssueに着手すると、
    コンフリクトや重複作業が発生する。独立したIssue候補を
    提案することで、効率的な並行作業を実現する。

What:
    - Fork-sessionかどうかを検出
    - 親/siblingセッションのworktreeを特定
    - 競合しない独立したIssue候補を提案

Remarks:
    - 提案のみでブロックはしない
    - 通常セッションでは何も出力しない
    - session-worktree-statusは警告のみ、こちらは積極的な提案

Changelog:
    - silenvx/dekita#2513: フック追加
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Add hooks directory to path
HOOKS_DIR = Path(__file__).parent
sys.path.insert(0, str(HOOKS_DIR))

from lib.issue_dependency import suggest_independent_issues
from lib.session import is_fork_session, parse_hook_input
from lib.session_graph import get_active_worktree_sessions


def format_worktree_info(info) -> str:  # type: ignore[no-untyped-def]
    """Format worktree info for display."""
    parts = [f"  - Issue #{info.issue_number}" if info.issue_number else f"  - {info.path.name}"]

    if info.changed_files:
        # Show up to 3 files
        files = sorted(info.changed_files)[:3]
        files_str = ", ".join(files)
        if len(info.changed_files) > 3:
            files_str += f" (+{len(info.changed_files) - 3} more)"
        parts.append(f"    Files: {files_str}")

    return "\n".join(parts)


def format_issue_suggestion(issue: dict, index: int) -> str:
    """Format issue suggestion for display."""
    labels = issue.get("labels", [])
    priority_labels = [
        label.get("name") for label in labels if label.get("name", "").startswith("P")
    ]
    priority_str = f" [{priority_labels[0]}]" if priority_labels else ""

    return f"  {index}. #{issue['number']}: {issue['title']}{priority_str}"


def main() -> None:
    """Main function for fork-session collaboration advisor."""
    hook_input = parse_hook_input()

    # Get session info
    session_id = hook_input.get("session_id", "")
    source = hook_input.get("source", "")
    transcript_path = hook_input.get("transcript_path")

    # Only run for fork-sessions
    if not is_fork_session(session_id, source, transcript_path):
        return

    # Get active worktree sessions
    try:
        active_sessions = get_active_worktree_sessions(session_id, transcript_path)
    except Exception:
        # Fail silently - don't block on errors
        return

    # Build message
    lines: list[str] = []
    lines.append("")
    lines.append("[fork-session-collaboration-advisor]")
    lines.append("")
    lines.append("🔀 **あなたはfork-sessionです**")
    lines.append("")
    lines.append("**禁止事項**:")
    lines.append("- ❌ 「他のIssueはfork-sessionに任せます」という発言")
    lines.append("- ❌ 親セッションが作業中のIssueへの着手")
    lines.append("- ❌ 自分が親セッションであるかのような振る舞い")
    lines.append("")

    # Show ancestor worktrees
    ancestor_worktrees = active_sessions.get("ancestor", [])
    if ancestor_worktrees:
        lines.append("## 親セッションの作業中Issue")
        for info in ancestor_worktrees:
            lines.append(format_worktree_info(info))
        lines.append("")

    # Show sibling worktrees (potential conflicts)
    sibling_worktrees = active_sessions.get("sibling", [])
    if sibling_worktrees:
        lines.append("## sibling forkセッション (競合注意)")
        for info in sibling_worktrees:
            lines.append(format_worktree_info(info))
        lines.append("")

    # Combine all active worktrees for suggestion
    all_active = ancestor_worktrees + sibling_worktrees

    # Suggest independent issues
    try:
        suggested_issues = suggest_independent_issues(all_active)
    except Exception:
        suggested_issues = []

    if suggested_issues:
        lines.append("## 独立したIssue候補 (着手推奨)")
        for i, issue in enumerate(suggested_issues[:5], 1):
            lines.append(format_issue_suggestion(issue, i))
        lines.append("")
        lines.append(
            "上記のいずれかに着手しますか？番号で指定、または別のIssueを指定してください。"
        )
    elif not ancestor_worktrees and not sibling_worktrees:
        # No active worktrees - nothing to report
        return
    else:
        lines.append("## 独立したIssue候補")
        lines.append("  現在、PRのないオープンIssueはありません。")
        lines.append("")
        lines.append("新しいIssueを作成するか、既存PRのレビューを手伝ってください。")

    # Output as systemMessage
    if len(lines) > 3:  # Only output if we have meaningful content
        output = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "systemMessage": "\n".join(lines),
            }
        }
        print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
