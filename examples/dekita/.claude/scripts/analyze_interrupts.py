#!/usr/bin/env python3
"""ユーザー中断・バックグラウンド化・ツール拒否を分析する。

Why:
    ユーザーの不満や改善ポイントを特定するため、
    中断（Escape）、Ctrl+B、ツール拒否を検出・統計化する。

What:
    - detect_interrupts(): 中断イベントを検出
    - detect_backgrounds(): バックグラウンド化を検出
    - detect_denials(): ツール拒否を検出
    - generate_summary(): 統計サマリーを生成

State:
    - reads: ~/.claude/projects/*/*.jsonl（transcript）
    - writes: .claude/logs/metrics/interrupts-*.jsonl（--save時）

Remarks:
    - --all で全セッションを分析
    - --summary で統計サマリーを表示
    - SRP: 中断・バックグラウンド化検出・分析のみを担当

Changelog:
    - silenvx/dekita#1600: 中断分析機能を追加
    - silenvx/dekita#1700: バックグラウンド化・ツール拒否分析を追加
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import TypedDict


class InterruptEvent(TypedDict):
    timestamp: str
    session_id: str
    interrupt_index: int
    before_action: str
    before_tool: str | None
    after_message: str | None
    inferred_reason: str
    category: str


class BackgroundEvent(TypedDict):
    timestamp: str
    session_id: str
    event_index: int
    command: str
    background_id: str
    initiated_by: str  # "user" (Ctrl+B) or "claude" (run_in_background)


class DenialEvent(TypedDict):
    timestamp: str
    session_id: str
    event_index: int
    tool_name: str
    denial_source: str  # "hook" or "user"
    denial_reason: str


INTERRUPT_MARKER = "[Request interrupted by user]"
LOGS_DIR = Path(__file__).parent.parent / "logs"
METRICS_LOG_DIR = LOGS_DIR / "metrics"
INTERRUPT_LOG = METRICS_LOG_DIR / "interrupt-analysis.jsonl"
BACKGROUND_LOG = METRICS_LOG_DIR / "background-analysis.jsonl"
DENIAL_LOG = METRICS_LOG_DIR / "denial-analysis.jsonl"

# コンテンツ切り詰め定数
TEXT_EXTRACT_LIMIT = 200  # extract_text_content での切り詰め（全テキスト抽出の基準）
DISPLAY_SHORT_LIMIT = 100  # 表示時の短縮
SESSION_ID_SHORT_LIMIT = 8  # session_id の短縮表示

# 中断理由推測用パターン
# (正規表現パターン, カテゴリ, 推測理由)
DIRECTION_PATTERNS: list[tuple[str, str, str]] = [
    (r"違う|ちがう|そうじゃな", "direction_change", "ユーザーが方向転換を要求"),
    (r"やめて|止めて|ストップ", "abort", "ユーザーが処理中止を要求"),
    (r"待って|ちょっと", "pause", "ユーザーが一時停止を要求"),
    (r"そうではなく|じゃなくて", "clarification", "ユーザーが意図を明確化"),
    (r"ここで|ここまで", "scope_limit", "スコープ制限の要求"),
    (r"先に|まず", "priority_change", "優先度変更の要求"),
    (r"質問|聞きたい", "question", "ユーザーが質問を挟んだ"),
]

# ツール別中断理由マッピング
# ツール名 -> (推測理由, カテゴリ)
TOOL_INTERRUPT_REASONS: dict[str, tuple[str, str]] = {
    "Task": ("サブエージェントの処理が長すぎた可能性", "long_running"),
    "Bash": ("コマンド実行を中断", "command_abort"),
    "Read": ("ファイル読み込み中に中断", "read_abort"),
    "Edit": ("編集操作を中断", "edit_abort"),
    "WebSearch": ("検索中に中断", "search_abort"),
    "WebFetch": ("ページ取得中に中断", "fetch_abort"),
}


def get_project_transcripts_dir() -> Path:
    """現在のプロジェクトのトランスクリプトディレクトリを取得する。

    Claude Codeはプロジェクトごとにトランスクリプトを保存する。
    保存先は ~/.claude/projects/-{escaped_path}/ となる。

    Worktree対応:
        worktree内で実行された場合、メインプロジェクトのパスを使用する。
        これによりworktree間でトランスクリプトが共有される。

        例: /path/to/project/.worktrees/feat-xxx → /path/to/project

    パスエスケープ:
        スラッシュをハイフンに置換してディレクトリ名を生成する。

        例: /Users/foo/bar → -Users-foo-bar

    Returns:
        トランスクリプトが保存されているディレクトリのPath。
        例: ~/.claude/projects/-Users-foo-project/
    """
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())

    # worktree内の場合、メインプロジェクトのパスを使用
    for worktree_marker in ["/.worktrees/"]:
        if worktree_marker in project_dir:
            project_dir = project_dir.split(worktree_marker)[0]
            break

    # パスをエスケープ（/Users/foo/bar → -Users-foo-bar）
    escaped = project_dir.replace("/", "-")
    if escaped.startswith("-"):
        escaped = escaped[1:]
    return Path.home() / ".claude" / "projects" / f"-{escaped}"


def load_transcript(path: Path) -> list[dict]:
    """トランスクリプトJSONLを読み込む"""
    events = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except (OSError, PermissionError) as e:
        print(f"Warning: ファイルを読み込めません: {path} ({e})", file=sys.stderr)
    return events


def extract_text_content(content: list | str) -> str:
    """メッセージコンテンツからテキストを抽出"""
    if isinstance(content, str):
        return content
    texts = []
    for item in content:
        if isinstance(item, dict):
            if item.get("type") == "text":
                texts.append(item.get("text", ""))
            elif item.get("type") == "tool_use":
                tool_name = item.get("name", "unknown")
                texts.append(f"[Tool: {tool_name}]")
    return " ".join(texts)[:TEXT_EXTRACT_LIMIT]


def extract_tool_name(content: list | str) -> str | None:
    """メッセージコンテンツからツール名を抽出"""
    if isinstance(content, str):
        return None
    for item in content:
        if isinstance(item, dict) and item.get("type") == "tool_use":
            return item.get("name")
    return None


def infer_interrupt_reason(
    before_action: str,
    before_tool: str | None,
    after_message: str | None,
) -> tuple[str, str]:
    """
    中断理由を推測する

    Returns:
        (inferred_reason, category)
    """
    # 後続メッセージがある場合、それを分析
    if after_message:
        for pattern, category, reason in DIRECTION_PATTERNS:
            if re.search(pattern, after_message):
                return reason, category

    # ツールベースの推測
    if before_tool and before_tool in TOOL_INTERRUPT_REASONS:
        return TOOL_INTERRUPT_REASONS[before_tool]

    # 一般的な推測
    return "理由不明（コンテキストから推測不可）", "unknown"


def find_interrupts(events: list[dict]) -> list[InterruptEvent]:
    """トランスクリプトから中断イベントを検出"""
    interrupts: list[InterruptEvent] = []

    for i, event in enumerate(events):
        # ユーザーメッセージで中断マーカーを検出
        if event.get("type") != "user":
            continue

        message = event.get("message", {})
        content = message.get("content", [])
        text = extract_text_content(content)

        if INTERRUPT_MARKER not in text:
            continue

        # 中断直前のassistantメッセージを探す
        before_action = ""
        before_tool = None
        for j in range(i - 1, -1, -1):
            prev = events[j]
            if prev.get("type") == "assistant":
                prev_msg = prev.get("message", {})
                prev_content = prev_msg.get("content", [])
                before_action = extract_text_content(prev_content)
                before_tool = extract_tool_name(prev_content)
                break

        # 中断直後のユーザーメッセージを探す
        after_message = None
        for j in range(i + 1, len(events)):
            next_ev = events[j]
            if next_ev.get("type") == "user":
                next_msg = next_ev.get("message", {})
                next_content = next_msg.get("content", [])

                # tool_resultメッセージはスキップ（ユーザー入力ではない）
                if isinstance(next_content, list) and next_content:
                    first_item = next_content[0]
                    if isinstance(first_item, dict) and first_item.get("type") == "tool_result":
                        continue

                after_text = extract_text_content(next_content)
                # 空のテキスト、ツール表記、中断マーカーは除外
                if (
                    after_text
                    and not after_text.startswith("[Tool:")
                    and INTERRUPT_MARKER not in after_text
                ):
                    after_message = after_text
                    break

        # 理由を推測
        inferred_reason, category = infer_interrupt_reason(
            before_action, before_tool, after_message
        )

        interrupts.append(
            {
                "timestamp": event.get("timestamp", ""),
                "session_id": event.get("sessionId", ""),
                "interrupt_index": i,
                "before_action": before_action,
                "before_tool": before_tool,
                "after_message": after_message,
                "inferred_reason": inferred_reason,
                "category": category,
            }
        )

    return interrupts


def find_backgrounds(events: list[dict]) -> list[BackgroundEvent]:
    """トランスクリプトからバックグラウンド化イベントを検出"""
    backgrounds: list[BackgroundEvent] = []

    for i, event in enumerate(events):
        # toolUseResultにbackgroundTaskIdがあるイベントを検出
        tool_result = event.get("toolUseResult", {})
        if not isinstance(tool_result, dict):
            continue

        bg_id = tool_result.get("backgroundTaskId")
        if not bg_id:
            continue

        # 対応するtool_useを探す
        msg = event.get("message", {})
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue

        found = False
        for item in content:
            if found:
                break
            if not isinstance(item, dict) or item.get("type") != "tool_result":
                continue

            tool_use_id = item.get("tool_use_id")
            if not tool_use_id:
                continue

            # 元のBashコマンドを探す
            for j in range(i - 1, -1, -1):
                if found:
                    break
                prev = events[j]
                if prev.get("type") != "assistant":
                    continue

                prev_content = prev.get("message", {}).get("content", [])
                if not isinstance(prev_content, list):
                    continue

                for pc in prev_content:
                    if not isinstance(pc, dict) or pc.get("type") != "tool_use":
                        continue
                    if pc.get("id") != tool_use_id or pc.get("name") != "Bash":
                        continue

                    inp = pc.get("input", {})
                    run_bg = inp.get("run_in_background", False)
                    command = inp.get("command", "")[:100]

                    backgrounds.append(
                        {
                            "timestamp": event.get("timestamp", ""),
                            "session_id": event.get("sessionId", ""),
                            "event_index": i,
                            "command": command,
                            "background_id": bg_id,
                            "initiated_by": "claude" if run_bg else "user",
                        }
                    )
                    found = True
                    break

    return backgrounds


def find_denials(events: list[dict]) -> list[DenialEvent]:
    """トランスクリプトからツール拒否イベントを検出

    検出パターン:
    1. フックによる拒否: "Hook PreToolUse:XXX denied this tool"
    2. ユーザーによる拒否: "User rejected" または "user rejected"
    """
    denials: list[DenialEvent] = []

    for i, event in enumerate(events):
        if event.get("type") != "user":
            continue

        message = event.get("message", {})
        content = message.get("content", [])

        if not isinstance(content, list):
            continue

        for item in content:
            if not isinstance(item, dict) or item.get("type") != "tool_result":
                continue

            is_error = item.get("is_error", False)
            if not is_error:
                continue

            result_content = item.get("content", "")
            if not isinstance(result_content, str):
                continue

            tool_use_id = item.get("tool_use_id", "")

            # フックによる拒否を検出
            # ツール名にハイフンなど非単語文字を含む可能性があるため[^\s]+を使用
            hook_match = re.search(r"Hook PreToolUse:([^\s]+) denied this tool", result_content)
            if hook_match:
                tool_name = hook_match.group(1)
                denials.append(
                    {
                        "timestamp": event.get("timestamp", ""),
                        "session_id": event.get("sessionId", ""),
                        "event_index": i,
                        "tool_name": tool_name,
                        "denial_source": "hook",
                        "denial_reason": result_content[:DISPLAY_SHORT_LIMIT],
                    }
                )
                continue

            # ユーザーによる拒否を検出
            if re.search(r"[Uu]ser rejected", result_content):
                # ツール名を特定するため、対応するtool_useを探す
                tool_name = _find_tool_name_for_id(events, i, tool_use_id)
                denials.append(
                    {
                        "timestamp": event.get("timestamp", ""),
                        "session_id": event.get("sessionId", ""),
                        "event_index": i,
                        "tool_name": tool_name,
                        "denial_source": "user",
                        "denial_reason": result_content[:DISPLAY_SHORT_LIMIT],
                    }
                )

    return denials


def _find_tool_name_for_id(events: list[dict], current_index: int, tool_use_id: str) -> str:
    """tool_use_idに対応するツール名を探す"""
    # 空のtool_use_idは誤マッチを防ぐため早期リターン
    if not tool_use_id:
        return "unknown"

    for j in range(current_index - 1, -1, -1):
        prev = events[j]
        if prev.get("type") != "assistant":
            continue

        prev_content = prev.get("message", {}).get("content", [])
        if not isinstance(prev_content, list):
            continue

        for pc in prev_content:
            if not isinstance(pc, dict) or pc.get("type") != "tool_use":
                continue
            if pc.get("id") == tool_use_id:
                return pc.get("name", "unknown")

    return "unknown"


def save_interrupt(interrupt: InterruptEvent) -> None:
    """中断イベントをログファイルに保存"""
    METRICS_LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(INTERRUPT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(interrupt, ensure_ascii=False) + "\n")


def save_background(background: BackgroundEvent) -> None:
    """バックグラウンドイベントをログファイルに保存"""
    METRICS_LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(BACKGROUND_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(background, ensure_ascii=False) + "\n")


def save_denial(denial: DenialEvent) -> None:
    """ツール拒否イベントをログファイルに保存"""
    METRICS_LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(DENIAL_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(denial, ensure_ascii=False) + "\n")


def analyze_session(session_path: Path) -> list[InterruptEvent]:
    """セッションを分析"""
    events = load_transcript(session_path)
    return find_interrupts(events)


def print_interrupt(interrupt: InterruptEvent) -> None:
    """中断イベントを表示"""
    session_id = interrupt["session_id"]
    session_short = (
        session_id[:SESSION_ID_SHORT_LIMIT]
        if len(session_id) >= SESSION_ID_SHORT_LIMIT
        else session_id
    )
    before_action = interrupt["before_action"]
    action_short = (
        before_action[:DISPLAY_SHORT_LIMIT]
        if len(before_action) > DISPLAY_SHORT_LIMIT
        else before_action
    )

    print(f"\n📍 中断検出: {interrupt['timestamp']}")
    print(f"   セッション: {session_short}...")
    print(f"   直前の操作: {action_short}...")
    if interrupt["before_tool"]:
        print(f"   使用ツール: {interrupt['before_tool']}")
    if interrupt["after_message"]:
        after_msg = interrupt["after_message"]
        after_short = (
            after_msg[:DISPLAY_SHORT_LIMIT] if len(after_msg) > DISPLAY_SHORT_LIMIT else after_msg
        )
        print(f"   直後のメッセージ: {after_short}...")
    print(f"   推測理由: {interrupt['inferred_reason']}")
    print(f"   カテゴリ: {interrupt['category']}")


def print_background(background: BackgroundEvent) -> None:
    """バックグラウンドイベントを表示"""
    session_id = background["session_id"]
    session_short = session_id[:8] if len(session_id) >= 8 else session_id
    initiated = "Ctrl+B" if background["initiated_by"] == "user" else "Claude"

    print(f"\n⏸️  バックグラウンド化: {background['timestamp']}")
    print(f"   セッション: {session_short}...")
    print(f"   コマンド: {background['command']}...")
    print(f"   発動: {initiated}")
    print(f"   ID: {background['background_id']}")


def print_denial(denial: DenialEvent) -> None:
    """ツール拒否イベントを表示"""
    session_id = denial["session_id"]
    session_short = (
        session_id[:SESSION_ID_SHORT_LIMIT]
        if len(session_id) >= SESSION_ID_SHORT_LIMIT
        else session_id
    )
    source_label = "フック" if denial["denial_source"] == "hook" else "ユーザー"

    print(f"\n🚫 ツール拒否: {denial['timestamp']}")
    print(f"   セッション: {session_short}...")
    print(f"   ツール: {denial['tool_name']}")
    print(f"   拒否元: {source_label}")
    print(f"   理由: {denial['denial_reason']}...")


def show_summary() -> None:
    """中断統計のサマリーを表示"""
    if not INTERRUPT_LOG.exists():
        print("中断ログがありません")
        return

    interrupts: list[InterruptEvent] = []
    with open(INTERRUPT_LOG, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    interrupts.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    if not interrupts:
        print("中断ログがありません")
        return

    total_count = len(interrupts)
    print("\n## 中断分析サマリー")
    print(f"\n総中断回数: {total_count}")

    # カテゴリ別集計
    categories: dict[str, int] = {}
    for event in interrupts:
        cat = event.get("category", "unknown")
        categories[cat] = categories.get(cat, 0) + 1

    print("\n### カテゴリ別")
    for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
        percentage = count * 100 // total_count if total_count > 0 else 0
        print(f"  {cat}: {count}回 ({percentage}%)")

    # ツール別集計
    tools: dict[str, int] = {}
    for event in interrupts:
        tool = event.get("before_tool") or "none"
        tools[tool] = tools.get(tool, 0) + 1

    print("\n### 中断時のツール")
    for tool, count in sorted(tools.items(), key=lambda x: -x[1])[:5]:
        print(f"  {tool}: {count}回")

    # 最近の中断
    print("\n### 最近の中断（直近5件）")
    recent = sorted(interrupts, key=lambda x: x.get("timestamp", ""), reverse=True)[:5]
    for event in recent:
        ts = event.get("timestamp", "")[:10]
        reason = event.get("inferred_reason", "不明")[:50]
        print(f"  [{ts}] {reason}")


def show_background_summary() -> None:
    """バックグラウンド化統計のサマリーを表示"""
    if not BACKGROUND_LOG.exists():
        print("バックグラウンドログがありません")
        return

    backgrounds: list[BackgroundEvent] = []
    with open(BACKGROUND_LOG, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    backgrounds.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    if not backgrounds:
        print("バックグラウンドログがありません")
        return

    total_count = len(backgrounds)
    user_count = sum(1 for b in backgrounds if b.get("initiated_by") == "user")
    claude_count = total_count - user_count

    print("\n## バックグラウンド化サマリー")
    print(f"\n総バックグラウンド化回数: {total_count}")
    print(
        f"  Ctrl+B（ユーザー）: {user_count}回 ({user_count * 100 // total_count if total_count else 0}%)"
    )
    print(
        f"  Claude指定: {claude_count}回 ({claude_count * 100 // total_count if total_count else 0}%)"
    )

    # よく使われるコマンド
    commands: dict[str, int] = {}
    for bg in backgrounds:
        if bg.get("initiated_by") == "user":
            cmd = bg.get("command", "")[:50]
            commands[cmd] = commands.get(cmd, 0) + 1

    if commands:
        print("\n### Ctrl+Bで多くバックグラウンド化されたコマンド")
        for cmd, count in sorted(commands.items(), key=lambda x: -x[1])[:5]:
            print(f"  {cmd}... ({count}回)")

    # 最近のバックグラウンド化
    print("\n### 最近のCtrl+B（直近5件）")
    user_bgs = [b for b in backgrounds if b.get("initiated_by") == "user"]
    recent = sorted(user_bgs, key=lambda x: x.get("timestamp", ""), reverse=True)[:5]
    for bg in recent:
        ts = bg.get("timestamp", "")[:10]
        cmd = bg.get("command", "")[:40]
        print(f"  [{ts}] {cmd}...")


def show_denial_summary() -> None:
    """ツール拒否統計のサマリーを表示"""
    if not DENIAL_LOG.exists():
        print("ツール拒否ログがありません")
        return

    denials: list[DenialEvent] = []
    with open(DENIAL_LOG, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    denials.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    if not denials:
        print("ツール拒否ログがありません")
        return

    total_count = len(denials)
    hook_count = sum(1 for d in denials if d.get("denial_source") == "hook")
    user_count = total_count - hook_count

    print("\n## ツール拒否サマリー")
    print(f"\n総拒否回数: {total_count}")
    print(f"  フック: {hook_count}回 ({hook_count * 100 // total_count if total_count else 0}%)")
    print(f"  ユーザー: {user_count}回 ({user_count * 100 // total_count if total_count else 0}%)")

    # ツール別集計
    tools: dict[str, int] = {}
    for denial in denials:
        tool = denial.get("tool_name", "unknown")
        tools[tool] = tools.get(tool, 0) + 1

    print("\n### よく拒否されるツール")
    for tool, count in sorted(tools.items(), key=lambda x: -x[1])[:5]:
        percentage = count * 100 // total_count if total_count else 0
        print(f"  {tool}: {count}回 ({percentage}%)")

    # 最近の拒否
    print("\n### 最近の拒否（直近5件）")
    recent = sorted(denials, key=lambda x: x.get("timestamp", ""), reverse=True)[:5]
    for denial in recent:
        ts = denial.get("timestamp", "")[:10]
        tool = denial.get("tool_name", "unknown")
        source = "フック" if denial.get("denial_source") == "hook" else "ユーザー"
        print(f"  [{ts}] {tool} ({source})")


def main():
    parser = argparse.ArgumentParser(
        description="中断・バックグラウンド化・ツール拒否分析スクリプト"
    )
    parser.add_argument("--session-id", help="分析するセッションID")
    parser.add_argument("--all", action="store_true", help="全セッションを分析")
    parser.add_argument("--summary", action="store_true", help="中断サマリーを表示")
    parser.add_argument("--backgrounds", action="store_true", help="バックグラウンド化を分析")
    parser.add_argument(
        "--bg-summary", action="store_true", help="バックグラウンド化サマリーを表示"
    )
    parser.add_argument("--denials", action="store_true", help="ツール拒否を分析")
    parser.add_argument("--denial-summary", action="store_true", help="ツール拒否サマリーを表示")
    parser.add_argument("--save", action="store_true", help="結果をログに保存")
    args = parser.parse_args()

    if args.summary:
        show_summary()
        return

    if args.bg_summary:
        show_background_summary()
        return

    if args.denial_summary:
        show_denial_summary()
        return

    transcripts_dir = get_project_transcripts_dir()

    if not transcripts_dir.exists():
        print(f"トランスクリプトディレクトリが見つかりません: {transcripts_dir}")
        sys.exit(1)

    if args.session_id:
        # 特定のセッション
        session_files = list(transcripts_dir.glob(f"{args.session_id}*.jsonl"))
        if not session_files:
            print(f"セッションが見つかりません: {args.session_id}")
            sys.exit(1)
    elif args.all:
        # 全セッション
        session_files = list(transcripts_dir.glob("*.jsonl"))
    else:
        # 最新のセッション
        session_files = sorted(transcripts_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)[
            -1:
        ]

    if args.backgrounds:
        # バックグラウンド化の分析
        total_backgrounds = 0
        user_backgrounds = 0
        for session_file in session_files:
            events = load_transcript(session_file)
            backgrounds = find_backgrounds(events)
            for bg in backgrounds:
                print_background(bg)
                if args.save:
                    save_background(bg)
                total_backgrounds += 1
                if bg["initiated_by"] == "user":
                    user_backgrounds += 1

        print(f"\n合計: {total_backgrounds}件のバックグラウンド化を検出")
        print(f"  Ctrl+B（ユーザー）: {user_backgrounds}件")
        print(f"  Claude指定: {total_backgrounds - user_backgrounds}件")
        if args.save:
            print(f"結果を保存しました: {BACKGROUND_LOG}")
    elif args.denials:
        # ツール拒否の分析
        total_denials = 0
        hook_denials = 0
        for session_file in session_files:
            events = load_transcript(session_file)
            denials = find_denials(events)
            for denial in denials:
                print_denial(denial)
                if args.save:
                    save_denial(denial)
                total_denials += 1
                if denial["denial_source"] == "hook":
                    hook_denials += 1

        print(f"\n合計: {total_denials}件のツール拒否を検出")
        print(f"  フック: {hook_denials}件")
        print(f"  ユーザー: {total_denials - hook_denials}件")
        if args.save:
            print(f"結果を保存しました: {DENIAL_LOG}")
    else:
        # 中断の分析（デフォルト）
        total_interrupts = 0
        for session_file in session_files:
            events = load_transcript(session_file)
            interrupts = find_interrupts(events)
            for interrupt in interrupts:
                print_interrupt(interrupt)
                if args.save:
                    save_interrupt(interrupt)
                total_interrupts += 1

        print(f"\n合計: {total_interrupts}件の中断を検出")
        if args.save:
            print(f"結果を保存しました: {INTERRUPT_LOG}")


if __name__ == "__main__":
    main()
