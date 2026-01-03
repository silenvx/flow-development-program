#!/usr/bin/env python3
"""
セッションメトリクス収集フック（Stop）

セッション終了時に自動でメトリクスを収集・記録する。
"""

# SRP: セッション終了時のメトリクス収集のみを担当（単一責任）
# 既存フックとの重複なし（新規機能）
# ブロックなし（情報収集のみのため）

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# 共通モジュール
HOOKS_DIR = Path(__file__).parent
sys.path.insert(0, str(HOOKS_DIR))
from lib.constants import TIMEOUT_HEAVY
from lib.execution import log_hook_execution
from lib.logging import get_error_context_manager
from lib.session import create_hook_context, parse_hook_input

SCRIPT_DIR = HOOKS_DIR.parent / "scripts"


def collect_session_metrics(session_id: str) -> bool:
    """セッションメトリクスを収集

    Args:
        session_id: Claude Codeから提供されたセッションID
    """
    collect_script = SCRIPT_DIR / "collect-session-metrics.py"
    if not collect_script.exists():
        return False

    try:
        # Issue #2317: 環境変数ではなくコマンドライン引数でsession_idを渡す
        result = subprocess.run(
            ["python3", str(collect_script), "--session-id", session_id],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_HEAVY,
        )
        return result.returncode == 0
    except Exception:
        return False


def generate_session_report(session_id: str) -> bool:
    """セッションレポートを生成

    Issue #1367: セッション終了時に統合レポートを生成

    Args:
        session_id: Claude Codeから提供されたセッションID
    """
    report_script = SCRIPT_DIR / "session-report-generator.py"
    if not report_script.exists():
        return False

    try:
        # Issue #2317: 環境変数ではなくコマンドライン引数でsession_idを渡す
        result = subprocess.run(
            ["python3", str(report_script), "--session-id", session_id],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_HEAVY,
        )
        return result.returncode == 0
    except Exception:
        return False


def main():
    """Collect session metrics on session end."""
    # Stop hookはstdinからJSON入力を受け取る
    hook_input = parse_hook_input()

    ctx = create_hook_context(hook_input)

    # Stop hookが既にアクティブな場合は即座にapprove
    if hook_input.get("stop_hook_active"):
        print(json.dumps({"decision": "approve"}))
        return

    # セッションIDを取得（Issue #1308: hook入力から直接取得を優先）
    # hook入力にsession_idがある場合はそれを使用し、
    # ない場合（None、空文字列含む）のみctx.get_session_id()にフォールバック
    # 注: 空文字列も無効なsession_idとして扱い、フォールバックする（意図的な動作）
    session_id = hook_input.get("session_id") or ctx.get_session_id()

    # エラーコンテキストをフラッシュ（Issue #1636）
    # セッション終了時に pending のエラーコンテキストを保存
    error_context_manager = get_error_context_manager()
    context_flushed = error_context_manager.flush_pending(session_id) is not None

    # メトリクス収集（非ブロッキング）
    metrics_success = collect_session_metrics(session_id)

    # レポート生成（Issue #1367）
    report_success = generate_session_report(session_id)

    log_hook_execution(
        "session_metrics_collector",
        "approve",
        f"Session metrics {'collected' if metrics_success else 'collection failed'}, "
        f"report {'generated' if report_success else 'generation failed'}, "
        f"error context {'flushed' if context_flushed else 'no pending'}",
        {
            "metrics_success": metrics_success,
            "report_success": report_success,
            "context_flushed": context_flushed,
        },
    )

    # メトリクス収集・レポート生成の成否に関わらずapprove
    print(json.dumps({"decision": "approve"}))


if __name__ == "__main__":
    main()
