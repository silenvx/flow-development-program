#!/usr/bin/env python3
"""連続Bash失敗を検知し、シェル破損時に引き継ぎプロンプト生成を提案する。

Why:
    worktreeの自己削除等でカレントディレクトリが消えると、シェルが破損状態になり
    全てのコマンドが失敗し続ける。この状態を早期検知して回復策を提示する。

What:
    - Bash失敗を連続カウント
    - シェル破損パターン（"No such file or directory"等）を検出
    - 閾値（3回連続）超過で警告と回復オプションを提示

State:
    - writes: /tmp/claude-hooks/bash-failures.json

Remarks:
    - 警告型フック（ブロックしない、回復策を提示）
    - PostToolUse:Bashで発火
    - シェル破損パターンはre.IGNORECASEでマッチング

Changelog:
    - silenvx/dekita#237: シェル破損時の自動回復メカニズム
"""

import json
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from lib.execution import log_hook_execution
from lib.hook_input import get_tool_result
from lib.session import parse_hook_input

# Consecutive failure threshold for warning
FAILURE_THRESHOLD = 3

# Error patterns that indicate potential shell corruption
SHELL_CORRUPTION_PATTERNS = [
    r"No such file or directory",
    r"Unable to read current working directory",
    r"cannot access",
    r"fatal: Unable to read",
]

# Tracking file location (use TMPDIR for sandbox compatibility)
TRACKING_DIR = Path(tempfile.gettempdir()) / "claude-hooks"
TRACKING_FILE = TRACKING_DIR / "bash-failures.json"


def load_tracking_data() -> dict:
    """Load existing tracking data."""
    if TRACKING_FILE.exists():
        try:
            return json.loads(TRACKING_FILE.read_text())
        except Exception:
            pass  # Best effort - corrupted tracking data is ignored
    return {"consecutive_failures": 0, "last_errors": [], "updated_at": None}


def save_tracking_data(data: dict) -> None:
    """Save tracking data."""
    TRACKING_DIR.mkdir(parents=True, exist_ok=True)
    TRACKING_FILE.write_text(json.dumps(data, indent=2))


def is_shell_corruption_error(output: str) -> bool:
    """Check if the output indicates potential shell corruption."""
    for pattern in SHELL_CORRUPTION_PATTERNS:
        if re.search(pattern, output, re.IGNORECASE):
            return True
    return False


def main():
    """PostToolUse hook for Bash commands.

    Tracks consecutive failures and suggests handoff when threshold exceeded.
    """
    result = {"continue": True}

    try:
        input_data = parse_hook_input()
        tool_result = get_tool_result(input_data) or {}

        exit_code = tool_result.get("exit_code", 0)
        stdout = tool_result.get("stdout", "")
        stderr = tool_result.get("stderr", "")
        output = f"{stdout}\n{stderr}"

        data = load_tracking_data()

        if exit_code != 0:
            # Bash command failed
            data["consecutive_failures"] += 1
            error_summary = stderr[:200] if stderr else stdout[:200]
            data["last_errors"].append(error_summary)
            # Keep only last 5 errors
            data["last_errors"] = data["last_errors"][-5:]
            data["updated_at"] = datetime.now(UTC).isoformat()
            save_tracking_data(data)

            # Check if we've hit the threshold with shell corruption patterns
            if data["consecutive_failures"] >= FAILURE_THRESHOLD:
                if is_shell_corruption_error(output):
                    result["systemMessage"] = (
                        f"⚠️ 連続 {data['consecutive_failures']} 回のBash失敗を検知。\n"
                        "シェル破損の可能性があります。\n"
                        "【対応オプション】\n"
                        "1. 引き継ぎプロンプトを生成して別セッションで継続\n"
                        "2. メインリポジトリに移動してから再実行"
                    )
                else:
                    result["systemMessage"] = (
                        f"⚠️ 連続 {data['consecutive_failures']} 回のBash失敗。\n"
                        "回復不能な場合は引き継ぎプロンプトの生成を検討してください。"
                    )
        else:
            # Bash command succeeded - reset counter
            if data["consecutive_failures"] > 0:
                data["consecutive_failures"] = 0
                data["last_errors"] = []
                data["updated_at"] = datetime.now(UTC).isoformat()
                save_tracking_data(data)

    except Exception:
        # フック実行の失敗でClaude Codeをブロックしない
        # 追跡失敗は致命的ではなく、次の実行で回復可能
        pass

    log_hook_execution("bash-failure-tracker", "approve")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
