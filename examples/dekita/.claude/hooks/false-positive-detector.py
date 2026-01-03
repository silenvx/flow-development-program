#!/usr/bin/env python3
"""セッション終了時に誤検知パターンを検出して警告する。

Why:
    同じフックが短時間に連続でブロックする場合、誤検知の可能性が高い。
    セッション終了時にパターンを分析し、Issue作成を促すことで
    フックの品質改善につなげる。

What:
    - セッションのブロックログを読み込み
    - 30秒以内に同じフックが2回以上ブロックした連続パターンを検出
    - 検出した場合は警告を表示し、Issue作成を促す

Remarks:
    - Stop hookとして発動（セッション終了時）
    - ブロックはせず警告のみ

Changelog:
    - silenvx/dekita#2437: フック追加
"""

import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# Add hooks directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from lib.execution import log_hook_execution
from lib.results import make_approve_result
from lib.session import create_hook_context, parse_hook_input

# ログディレクトリ
PROJECT_DIR = Path(os.environ.get("CLAUDE_PROJECT_DIR", Path(__file__).parents[2]))
LOG_DIR = PROJECT_DIR / ".claude" / "logs" / "execution"

# 連続ブロックの閾値（秒）
CONSECUTIVE_BLOCK_THRESHOLD_SECONDS = 30

HOOK_NAME = "false-positive-detector"


def load_session_blocks(session_id: str) -> list[dict]:
    """セッションのブロックイベントを読み込む"""
    log_file = LOG_DIR / f"hook-execution-{session_id}.jsonl"
    if not log_file.exists():
        return []

    blocks = []
    try:
        with open(log_file, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if entry.get("decision") == "block":
                        blocks.append(entry)
                except json.JSONDecodeError:
                    continue
    except OSError:
        # ログファイルが読めない場合は空リストを返す（正常ケース）
        pass

    return blocks


def parse_timestamp(ts_str: str) -> datetime | None:
    """タイムスタンプ文字列をdatetimeに変換"""
    if not ts_str:
        return None

    # ISO形式のタイムスタンプをパース
    # 例: 2026-01-02T15:29:15.383537+09:00
    try:
        # Python 3.11+ では fromisoformat がタイムゾーン付きをサポート
        return datetime.fromisoformat(ts_str)
    except ValueError:
        # タイムゾーン形式が異なる場合、フォールバックパースを試みる
        pass

    # フォールバック: タイムゾーン部分を除去して試行
    try:
        # タイムゾーン部分（+09:00 / -05:00 など）を除去
        t_pos = ts_str.find("T")
        tz_cut_pos = None
        if t_pos != -1:
            plus_pos = ts_str.rfind("+")
            minus_pos = ts_str.rfind("-")
            # 日付部分の '-' ではなく、'T' 以降に現れる符号のみをタイムゾーン候補とみなす
            if plus_pos > t_pos:
                tz_cut_pos = plus_pos
            if minus_pos > t_pos and (tz_cut_pos is None or minus_pos > tz_cut_pos):
                tz_cut_pos = minus_pos
        if tz_cut_pos is not None:
            ts_str = ts_str[:tz_cut_pos]
        elif ts_str.endswith("Z"):
            ts_str = ts_str[:-1]
        return datetime.fromisoformat(ts_str)
    except ValueError:
        return None


def detect_consecutive_blocks(blocks: list[dict]) -> dict[str, list[tuple[str, str]]]:
    """連続ブロックパターンを検出する

    Returns:
        dict: フック名 -> [(timestamp1, timestamp2), ...] の連続ブロックペア
    """
    # フックごとにブロックをグループ化
    blocks_by_hook: dict[str, list[dict]] = defaultdict(list)
    for block in blocks:
        hook = block.get("hook")
        if hook:
            blocks_by_hook[hook].append(block)

    consecutive_patterns: dict[str, list[tuple[str, str]]] = {}

    for hook, hook_blocks in blocks_by_hook.items():
        # タイムスタンプが存在するブロックのみを対象にする
        timestamped_blocks = [b for b in hook_blocks if b.get("timestamp")]
        if len(timestamped_blocks) < 2:
            # 連続ブロックペアを構成できないためスキップ
            continue

        # タイムスタンプでソート
        sorted_blocks = sorted(timestamped_blocks, key=lambda b: b.get("timestamp"))

        pairs = []
        for i in range(len(sorted_blocks) - 1):
            ts1 = parse_timestamp(sorted_blocks[i].get("timestamp", ""))
            ts2 = parse_timestamp(sorted_blocks[i + 1].get("timestamp", ""))

            if ts1 and ts2:
                try:
                    diff = abs((ts2 - ts1).total_seconds())
                except TypeError:
                    # タイムゾーン形式の不一致（aware vs naive）の場合はスキップ
                    continue
                if diff <= CONSECUTIVE_BLOCK_THRESHOLD_SECONDS:
                    pairs.append(
                        (
                            sorted_blocks[i].get("timestamp", ""),
                            sorted_blocks[i + 1].get("timestamp", ""),
                        )
                    )

        if pairs:
            consecutive_patterns[hook] = pairs

    return consecutive_patterns


def format_warning_message(patterns: dict[str, list[tuple[str, str]]]) -> str:
    """警告メッセージをフォーマット"""
    lines = [
        "## 誤検知の可能性があるブロックパターンを検出",
        "",
        "以下のフックで短時間に連続ブロックが発生しました:",
        "",
    ]

    for hook, pairs in patterns.items():
        lines.append(f"### {hook}")
        lines.append(f"  連続ブロック: {len(pairs)}回")
        for ts1, ts2 in pairs[:3]:  # 最大3件まで表示
            lines.append(f"  - {ts1} → {ts2}")
        if len(pairs) > 3:
            lines.append(f"  - ... 他 {len(pairs) - 3}件")
        lines.append("")

    lines.extend(
        [
            "**推奨アクション**:",
            "1. 上記フックの検出ロジックを確認",
            "2. 誤検知であればIssueを作成:",
            "",
            "```bash",
            'gh issue create --title "フック誤検知: <フック名>" \\',
            '  --body "## 問題\\n<再現手順>\\n\\n## 期待動作\\n<期待動作>" \\',
            '  --label "bug,P2"',
            "```",
        ]
    )

    return "\n".join(lines)


def _output_result(result: dict) -> None:
    """結果を出力"""
    print(json.dumps(result))


def main() -> None:
    """メインエントリポイント"""
    data = parse_hook_input()

    ctx = create_hook_context(data)
    if not data:
        # パースエラー時は承認（fail open）
        log_hook_execution(HOOK_NAME, "approve", reason="parse_error")
        _output_result(make_approve_result(HOOK_NAME))
        return

    # セッションIDを取得
    session_id = ctx.get_session_id()
    if not session_id:
        log_hook_execution(HOOK_NAME, "approve", reason="no_session_id")
        _output_result(make_approve_result(HOOK_NAME))
        return

    # ブロックログを読み込み
    blocks = load_session_blocks(session_id)
    if not blocks:
        log_hook_execution(HOOK_NAME, "approve", reason="no_blocks")
        _output_result(make_approve_result(HOOK_NAME))
        return

    # 連続ブロックパターンを検出
    patterns = detect_consecutive_blocks(blocks)

    if patterns:
        # 警告を表示（ブロックはしない）
        warning_message = format_warning_message(patterns)
        log_hook_execution(
            HOOK_NAME,
            "approve",
            reason="patterns_detected",
            details={"patterns": {k: len(v) for k, v in patterns.items()}},
        )

        # 警告付きで承認（systemMessageにwarning_messageを含める）
        _output_result(make_approve_result(HOOK_NAME, warning_message))
    else:
        log_hook_execution(HOOK_NAME, "approve", reason="no_patterns")
        _output_result(make_approve_result(HOOK_NAME))


if __name__ == "__main__":
    main()
