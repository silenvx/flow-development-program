#!/usr/bin/env python3
"""セッション開始時に現在日時とセッションIDをコンテキストに注入する。

Why:
    Claude Codeは明示的な日付コンテキストがない場合、知識カットオフ時点の
    日時にデフォルトしやすい。現在日時を注入することで正確な時間認識を保証する。

What:
    - 現在日時をISO8601形式で出力
    - セッションIDを出力
    - fork-session検出時はsourceを"fork"に設定

Remarks:
    - TZ環境変数でタイムゾーン設定可能（デフォルト: Asia/Tokyo）
    - 参考: https://www.nathanonn.com/the-claude-code-date-bug-thats-sabotaging-your-web-searches-and-the-3-minute-fix/

Changelog:
    - silenvx/dekita#1797: セッションID出力を追加
    - silenvx/dekita#2279: sourceフィールドを追加
    - silenvx/dekita#2288: fork-session時の表示改善
    - silenvx/dekita#2308: transcript-based fork-session検出
    - silenvx/dekita#2322: デバッグログ追加
    - silenvx/dekita#2496: グローバル状態を削除
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Import fork-session detection from lib/session
HOOKS_DIR = Path(__file__).parent
sys.path.insert(0, str(HOOKS_DIR))
from lib.session import (
    get_parent_session_id,
    is_fork_session,
)

# タイムゾーン設定（環境変数またはデフォルト）
TZ = os.environ.get("TZ", "Asia/Tokyo")


def get_session_info_from_input() -> dict:
    """フック入力からセッション情報を取得する。

    SessionStartフックはClaude Codeから JSON形式で入力を受け取る。
    入力がない場合（テスト時など）は空の辞書を返す。

    Issue #2279: session_idだけでなくsource等の情報も取得できるように変更。

    Returns:
        セッション情報を含む辞書。session_id, source等のフィールドを含む。
    """
    # stdinが端末の場合は入力なしとみなす
    if sys.stdin.isatty():
        return {}

    try:
        raw_input = sys.stdin.read()
        if not raw_input.strip():
            return {}
        return json.loads(raw_input)
    except (json.JSONDecodeError, OSError):
        return {}


def build_output(
    human_readable: str,
    iso_format: str,
    session_id: str | None,
    source: str | None,
    error: str | None = None,
) -> str:
    """出力文字列を構築する。

    Args:
        human_readable: 人間が読みやすい日時形式
        iso_format: ISO8601形式の日時
        session_id: セッションID（Noneの場合は出力しない）
        source: セッションソース（Noneの場合は出力しない）
        error: エラーメッセージ（Noneの場合は出力しない）

    Returns:
        構築された出力文字列
    """
    parts = [f"[CONTEXT] 現在日時: {human_readable} | ISO: {iso_format}"]
    if session_id:
        parts.append(f"Session: {session_id}")
    if source:
        parts.append(f"Source: {source}")
    output = " | ".join(parts)
    if error:
        output += f" (TZエラー: {error})"
    return output


def _debug_fork_detection(
    session_id: str | None,
    source: str | None,
    transcript_path: str | None,
    is_fork: bool,
    parent_session_id: str | None = None,
) -> None:
    """デバッグ用: fork-session検出の詳細をログ出力する。

    Issue #2322: fork-session検出のデバッグ情報を出力。
    環境変数 CLAUDE_DEBUG_FORK=1 で有効化。
    """
    if os.environ.get("CLAUDE_DEBUG_FORK") != "1":
        return

    debug_log_dir = HOOKS_DIR / "logs" / "debug"
    debug_log_dir.mkdir(parents=True, exist_ok=True)
    debug_log_file = debug_log_dir / "fork-detection.jsonl"

    entry = {
        "timestamp": datetime.now().isoformat(),
        "hook_session_id": session_id,
        "source": source,
        "transcript_path": transcript_path,
        "parent_session_id": parent_session_id,
        "is_fork": is_fork,
        "pid": os.getpid(),
        "ppid": os.getppid(),
    }

    try:
        with debug_log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        # Debug logging is best-effort; failures should not affect main functionality
        pass


def main() -> None:
    """現在日時とセッション情報をSTDOUTに出力する。

    通常は、指定されたタイムゾーンに基づく現在日時を次の形式で出力する:
    [CONTEXT] 現在日時: <YYYY-MM-DD 曜日 HH:MM:SS TZ> | ISO: <ISO8601>

    セッション情報が利用可能な場合は末尾に追加:
    [CONTEXT] 現在日時: ... | Session: <session_id> | Source: <source>

    Issue #2279: sourceフィールドも出力し、fork-session時の挙動を調査可能にする。
    Issue #2288: source="resume"でもfork-sessionの場合は"fork"と表示する。

    タイムゾーン解決に失敗した場合は、システムローカル時間を用いて同様の形式で出力し、
    末尾に「(TZエラー: <例外メッセージ>)」を付加したフォールバック形式となる。
    """
    session_info = get_session_info_from_input()
    session_id = session_info.get("session_id")
    source = session_info.get("source")
    transcript_path = session_info.get("transcript_path")

    # Issue #2308: transcript-based fork-session検出
    # transcript の最初のユーザーメッセージの sessionId と比較
    # Issue #2496: Removed set_hook_session_id() - no longer using global state
    is_fork = False
    parent_session_id = None
    if session_id:
        parent_session_id = get_parent_session_id(transcript_path)
        is_fork = is_fork_session(session_id, source or "", transcript_path)
        if is_fork:
            source = "fork"
            # Note: fork時のsession_id更新はUserPromptSubmitフックで行う
            # SessionStart時点ではfork用のtranscriptファイルがまだ存在しないため

    # Issue #2322: デバッグログ出力
    _debug_fork_detection(session_id, source, transcript_path, is_fork, parent_session_id)

    # 出力を構築
    try:
        tz = ZoneInfo(TZ)
        now = datetime.now(tz)
        human_readable = now.strftime("%Y-%m-%d %A %H:%M:%S %Z")
        iso_format = now.isoformat()
        context_output = build_output(human_readable, iso_format, session_id, source)
    except ZoneInfoNotFoundError as e:
        now = datetime.now()
        human_readable = now.strftime("%Y-%m-%d %A %H:%M:%S")
        iso_format = now.isoformat()
        context_output = build_output(human_readable, iso_format, session_id, source, str(e))

    # Issue #2350: fork-session時のhook入力を調査するためのデバッグ出力
    # 環境変数 CLAUDE_DEBUG_FORK=1 で有効化
    # session_infoにはsession_id, source, transcript_path, cwd, hook_event_nameのみが含まれ
    # 機密情報（APIキー、トークン等）は含まれない（Claude Codeのhook仕様による）
    # マークダウンのコードブロック形式はClaude Codeの出力で可読性が高いため採用
    if os.environ.get("CLAUDE_DEBUG_FORK") == "1":
        output = "[DEBUG] SessionStart hook input:\n```json\n"
        output += json.dumps(session_info, indent=2, ensure_ascii=False, default=str)
        output += "\n```\n\n"
        output += context_output
    else:
        output = context_output

    print(output)


if __name__ == "__main__":
    main()
