#!/usr/bin/env python3
"""Issue作成/不作成の判定を記録する。

Why:
    振り返り等でIssue作成/不作成を判定した際、その判定が適切だったか
    を後から評価するために、判定内容を記録する必要がある。

What:
    - create: Issue作成時の判定を記録
    - skip: Issue不作成時の判定を記録
    - 記録はJSONL形式でセッションIDごとに保存

Usage:
    # Issue作成時（issue-creation-tracker.pyから呼び出される）
    python3 record-issue-decision.py create \\
        --issue-number 1234 \\
        --problem "発見した問題" \\
        --reason "作成理由" \\
        --severity P2 \\
        --context reflect \\
        --session-id "session-uuid"

    # Issue不作成時（Claude Codeが明示的に実行）
    python3 record-issue-decision.py skip \\
        --problem "検討した問題" \\
        --reason "既存ルールでカバー" \\
        --context reflect \\
        --session-id "session-uuid"

    # 重複の場合
    python3 record-issue-decision.py skip \\
        --problem "検討した問題" \\
        --reason "重複" \\
        --related-issue 1234 \\
        --session-id "session-uuid"

Changelog:
    - silenvx/dekita#2677: 初期実装
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path


def _get_main_repo_from_worktree(cwd: Path) -> Path | None:
    """Get main repository path from a worktree.

    Issue #2505: Worktrees store their git info in a `.git` file (not directory)
    containing a path like `gitdir: /path/to/main/.git/worktrees/xxx`.
    """
    git_file = cwd / ".git"
    if not git_file.is_file():
        return None

    try:
        content = git_file.read_text().strip()
        if not content.startswith("gitdir:"):
            return None

        gitdir = content.split(":", 1)[1].strip()
        gitdir_path = Path(gitdir)

        if not gitdir_path.is_absolute():
            gitdir_path = (cwd / gitdir_path).resolve()

        if gitdir_path.parent.name == "worktrees" and gitdir_path.parent.parent.name == ".git":
            if not gitdir_path.exists():
                return None
            # gitdir_pathが存在するなら、その親ディレクトリも必ず存在する
            return gitdir_path.parent.parent.parent
    except (OSError, ValueError):
        # ファイル読み取りやパースに失敗した場合はworktreeではないとみなす
        pass

    return None


def _get_project_dir() -> Path:
    """Get project directory, resolving worktree to main repo.

    Issue #2677: Ensures decision logs are stored in main repo's .claude/logs/
    directory, consistent with hooks.
    """
    env_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_dir:
        env_path = Path(env_dir)
        main_repo = _get_main_repo_from_worktree(env_path)
        if main_repo:
            return main_repo
        return env_path

    cwd = Path.cwd()
    main_repo = _get_main_repo_from_worktree(cwd)
    if main_repo:
        return main_repo

    return cwd


def get_decisions_dir() -> Path:
    """Get the directory for decision log files."""
    return _get_project_dir() / ".claude" / "logs" / "decisions"


def get_decisions_file(session_id: str) -> Path:
    """Get the file path for storing issue decisions."""
    # パストラバーサル攻撃を防ぐため、session_idをサニタイズ
    safe_session_id = os.path.basename(session_id)
    return get_decisions_dir() / f"issue-decisions-{safe_session_id}.jsonl"


def record_decision(
    decision: str,
    session_id: str,
    problem: str,
    reason: str,
    context: str | None = None,
    issue_number: int | None = None,
    severity: str | None = None,
    related_issue: int | None = None,
) -> None:
    """Record an issue decision to the log file.

    Args:
        decision: "create" or "skip"
        session_id: Claude session ID
        problem: Description of the problem
        reason: Reason for the decision
        context: Context where decision was made (reflect, implementation, review)
        issue_number: Issue number (for create decisions)
        severity: Priority label (P0, P1, P2, P3)
        related_issue: Related issue number (for skip decisions due to duplicate)
    """
    log_dir = get_decisions_dir()
    log_dir.mkdir(parents=True, exist_ok=True)

    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "session_id": session_id,
        "decision": decision,
        "problem": problem,
        "reason": reason,
    }

    if context:
        entry["context"] = context

    if decision == "create":
        if issue_number:
            entry["issue_number"] = issue_number
        if severity:
            entry["severity"] = severity
    elif decision == "skip":
        if related_issue:
            entry["related_issue"] = related_issue

    decisions_file = get_decisions_file(session_id)
    with open(decisions_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    print(f"[record-issue-decision] Recorded {decision} decision for session {session_id}")


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Record issue creation/skip decisions for later evaluation"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # create subcommand
    create_parser = subparsers.add_parser("create", help="Record an issue creation decision")
    create_parser.add_argument(
        "--issue-number", type=int, required=True, help="Created issue number"
    )
    create_parser.add_argument("--problem", required=True, help="Description of the problem")
    create_parser.add_argument("--reason", required=True, help="Reason for creating the issue")
    create_parser.add_argument(
        "--severity", choices=["P0", "P1", "P2", "P3"], help="Priority label"
    )
    create_parser.add_argument(
        "--context",
        choices=["reflect", "implementation", "review"],
        help="Context where decision was made",
    )
    create_parser.add_argument("--session-id", required=True, help="Claude session ID")

    # skip subcommand
    skip_parser = subparsers.add_parser("skip", help="Record an issue skip decision")
    skip_parser.add_argument(
        "--problem", required=True, help="Description of the problem considered"
    )
    skip_parser.add_argument(
        "--reason",
        required=True,
        help="Reason for not creating an issue (e.g., 'existing rule covers', 'duplicate')",
    )
    skip_parser.add_argument(
        "--related-issue", type=int, help="Related issue number (if skipped due to duplicate)"
    )
    skip_parser.add_argument(
        "--context",
        choices=["reflect", "implementation", "review"],
        help="Context where decision was made",
    )
    skip_parser.add_argument("--session-id", required=True, help="Claude session ID")

    args = parser.parse_args()

    try:
        if args.command == "create":
            record_decision(
                decision="create",
                session_id=args.session_id,
                problem=args.problem,
                reason=args.reason,
                context=args.context,
                issue_number=args.issue_number,
                severity=args.severity,
            )
        elif args.command == "skip":
            record_decision(
                decision="skip",
                session_id=args.session_id,
                problem=args.problem,
                reason=args.reason,
                context=args.context,
                related_issue=args.related_issue,
            )
        return 0
    except Exception as e:
        print(f"[record-issue-decision] Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
