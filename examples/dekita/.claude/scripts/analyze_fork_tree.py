#!/usr/bin/env python3
"""fork-sessionの親子関係を分析・可視化する。

Why:
    複数のClaude Codeセッション間の関係を把握するため、
    transcriptからfork-sessionツリーを構築する機能が必要。

What:
    - get_project_id(): 現在のプロジェクトIDを取得
    - build_fork_tree(): fork-sessionツリーを構築
    - display_tree(): ツリーを可視化

State:
    - reads: ~/.claude/projects/*/*.jsonl（transcript）

Remarks:
    - --json オプションでJSON形式出力
    - CLAUDE_PROJECT_DIR環境変数からプロジェクトを推測

Changelog:
    - silenvx/dekita#2308: fork-sessionツリー分析機能を追加
    - silenvx/dekita#2496: get_claude_session_id削除に対応
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Add hooks directory to path for importing session module
# Issue #2496: get_claude_session_id を削除し、handle_session_id_arg の戻り値を使用
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))
from lib.session import handle_session_id_arg

# Claude Codeのプロジェクトディレクトリ
CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"


def get_project_id() -> str | None:
    """現在のプロジェクトIDを取得

    CLAUDE_PROJECT_DIR環境変数からプロジェクトIDを推測する。
    """
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if not project_dir:
        # cwdから推測
        project_dir = os.getcwd()

    # パスをプロジェクトID形式に変換（/を-に、先頭の/を削除）
    project_id = project_dir.replace("/", "-")
    if project_id.startswith("-"):
        project_id = project_id[1:]

    # プロジェクトディレクトリが存在するか確認
    project_path = CLAUDE_PROJECTS_DIR / project_id
    if project_path.exists():
        return project_id

    # 存在しない場合はNone
    return None


def get_first_parent_uuid(transcript_path: Path) -> str | None:
    """セッションの最初のメッセージのparentUuidを取得"""
    try:
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    # user または assistant タイプのメッセージを探す
                    if entry.get("type") in ("user", "assistant"):
                        return entry.get("parentUuid")
                except json.JSONDecodeError:
                    continue
    except OSError:
        # ファイルが存在しないか読み取り不可の場合はNoneを返す
        pass
    return None


def get_session_info(transcript_path: Path) -> dict[str, Any]:
    """セッションの基本情報を取得"""
    info: dict[str, Any] = {
        "session_id": transcript_path.stem,
        "first_timestamp": None,
        "last_timestamp": None,
        "message_count": 0,
    }

    try:
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if entry.get("type") in ("user", "assistant"):
                        info["message_count"] += 1
                        ts = entry.get("timestamp")
                        if ts:
                            if info["first_timestamp"] is None:
                                info["first_timestamp"] = ts
                            info["last_timestamp"] = ts
                except json.JSONDecodeError:
                    continue
    except OSError:
        # ファイルが存在しないか読み取り不可の場合はデフォルト値を返す
        pass

    return info


def find_parent_session(
    child_session_id: str, project_dir: Path, uuid_to_session: dict[str, str]
) -> str | None:
    """子セッションの親セッションIDを特定

    Args:
        child_session_id: 子セッションID
        project_dir: プロジェクトディレクトリ
        uuid_to_session: uuid -> session_id のマッピング（キャッシュ用）

    Returns:
        親セッションIDまたはNone
    """
    child_transcript = project_dir / f"{child_session_id}.jsonl"
    first_parent_uuid = get_first_parent_uuid(child_transcript)

    if not first_parent_uuid:
        return None  # 新規セッション（forkではない）

    # キャッシュから検索
    if first_parent_uuid in uuid_to_session:
        parent_session = uuid_to_session[first_parent_uuid]
        if parent_session != child_session_id:
            return parent_session

    # キャッシュにない場合は全ファイルを検索
    for transcript in project_dir.glob("*.jsonl"):
        session_id = transcript.stem
        if session_id == child_session_id:
            continue

        try:
            with open(transcript, encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line.strip())
                        uuid = entry.get("uuid")
                        if uuid:
                            uuid_to_session[uuid] = session_id
                            if uuid == first_parent_uuid:
                                return session_id
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue

    return None


def build_fork_tree(project_dir: Path) -> dict[str, list[str]]:
    """forkツリーを構築

    Returns:
        親セッションID -> 子セッションIDリストのマッピング
    """
    parent_to_children: dict[str, list[str]] = {}
    uuid_to_session: dict[str, str] = {}

    # 全セッションを走査
    transcripts = list(project_dir.glob("*.jsonl"))

    for transcript in transcripts:
        session_id = transcript.stem
        parent_id = find_parent_session(session_id, project_dir, uuid_to_session)

        if parent_id:
            if parent_id not in parent_to_children:
                parent_to_children[parent_id] = []
            parent_to_children[parent_id].append(session_id)

    return parent_to_children


def get_root_sessions(project_dir: Path, parent_to_children: dict[str, list[str]]) -> set[str]:
    """ルートセッション（親を持たないセッション）を取得"""
    all_sessions = {t.stem for t in project_dir.glob("*.jsonl")}
    all_children = set()
    for children in parent_to_children.values():
        all_children.update(children)

    # 子として登場しないセッションがルート
    return all_sessions - all_children


def format_tree(
    project_dir: Path,
    parent_to_children: dict[str, list[str]],
    current_session_id: str | None = None,
) -> str:
    """フォークツリーを文字列でフォーマット"""
    lines = []

    root_sessions = get_root_sessions(project_dir, parent_to_children)

    # forkを持つルートセッションのみ表示
    roots_with_forks = {root for root in root_sessions if root in parent_to_children}

    if not roots_with_forks:
        return "No fork relationships found."

    def format_node(session_id: str, prefix: str = "", is_last: bool = True) -> None:
        connector = "└── " if is_last else "├── "
        session_short = session_id[:8]

        # 現在のセッションにマーク
        marker = " <- current" if session_id == current_session_id else ""

        # セッション情報を取得
        info = get_session_info(project_dir / f"{session_id}.jsonl")
        ts_info = ""
        if info["first_timestamp"]:
            try:
                dt = datetime.fromisoformat(info["first_timestamp"].replace("Z", "+00:00"))
                ts_info = f" ({dt.strftime('%m/%d %H:%M')})"
            except (ValueError, TypeError):
                # タイムスタンプが不正な場合は日時情報なしで表示する
                pass

        lines.append(f"{prefix}{connector}{session_short}...{ts_info}{marker}")

        children = parent_to_children.get(session_id, [])
        new_prefix = prefix + ("    " if is_last else "│   ")
        for i, child in enumerate(sorted(children)):
            format_node(child, new_prefix, i == len(children) - 1)

    for i, root in enumerate(sorted(roots_with_forks)):
        root_short = root[:8]
        info = get_session_info(project_dir / f"{root}.jsonl")
        ts_info = ""
        if info["first_timestamp"]:
            try:
                dt = datetime.fromisoformat(info["first_timestamp"].replace("Z", "+00:00"))
                ts_info = f" ({dt.strftime('%m/%d %H:%M')})"
            except (ValueError, TypeError):
                # タイムスタンプが不正な場合は日時情報なしで表示する
                pass

        marker = " <- current" if root == current_session_id else ""
        lines.append(f"{root_short}... (root){ts_info}{marker}")

        children = parent_to_children.get(root, [])
        for j, child in enumerate(sorted(children)):
            format_node(child, "", j == len(children) - 1)

        if i < len(roots_with_forks) - 1:
            lines.append("")

    return "\n".join(lines)


def format_json(project_dir: Path, parent_to_children: dict[str, list[str]]) -> dict[str, Any]:
    """JSON形式で出力"""
    result: dict[str, Any] = {
        "fork_relationships": [],
        "root_sessions": [],
    }

    root_sessions = get_root_sessions(project_dir, parent_to_children)
    roots_with_forks = [root for root in root_sessions if root in parent_to_children]

    for root in roots_with_forks:
        tree = {
            "root": root,
            "children": build_subtree(root, parent_to_children),
        }
        result["fork_relationships"].append(tree)

    result["root_sessions"] = list(root_sessions)
    return result


def build_subtree(
    session_id: str, parent_to_children: dict[str, list[str]]
) -> list[dict[str, Any]]:
    """サブツリーを構築"""
    children = parent_to_children.get(session_id, [])
    return [
        {"session_id": child, "children": build_subtree(child, parent_to_children)}
        for child in children
    ]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze fork-session relationships from Claude Code transcripts"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output in JSON format",
    )
    parser.add_argument(
        "--project",
        "-p",
        help="Project ID (default: current project)",
    )
    parser.add_argument(
        "--session-id",
        type=str,
        default=None,
        help="Claude session ID for current session marker (Issue #2317)",
    )

    args = parser.parse_args()

    # Issue #2496: handle_session_id_arg の戻り値を使用
    validated_session_id = handle_session_id_arg(args.session_id)
    # Fallback to PPID-based session ID if not provided
    current_session_id = validated_session_id or f"ppid-{os.getppid()}"

    # プロジェクトIDを取得
    project_id = args.project or get_project_id()
    if not project_id:
        print("Error: Could not determine project ID.", file=sys.stderr)
        print("Set CLAUDE_PROJECT_DIR or use --project option.", file=sys.stderr)
        return 1

    project_dir = CLAUDE_PROJECTS_DIR / project_id
    if not project_dir.exists():
        print(f"Error: Project directory not found: {project_dir}", file=sys.stderr)
        return 1

    # forkツリーを構築
    parent_to_children = build_fork_tree(project_dir)

    if args.json:
        result = format_json(project_dir, parent_to_children)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"Fork Tree for project: {project_id}")
        print("=" * 50)
        print()
        output = format_tree(project_dir, parent_to_children, current_session_id)
        print(output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
