#!/usr/bin/env python3
"""ツール呼び出しパターンを追跡し非効率なパターンを検出。

Why:
    同じファイルの繰り返し読み書きや、同じ検索パターンの重複実行は非効率。
    これらのパターンを検出して警告することで、作業効率を向上させる。

What:
    - 全ツール実行後（PostToolUse）に発火
    - ツール呼び出し履歴をセッション単位で記録
    - 非効率パターンを検出して警告（Read→Edit繰り返し、検索重複等）
    - 検出結果をメトリクスログに記録

State:
    - reads/writes: /tmp/claude-hooks/tool-sequence.json（呼び出し履歴）
    - writes: .claude/logs/metrics/tool-efficiency-metrics.log

Remarks:
    - 非ブロック型（警告のみ）
    - 10分ウィンドウ内のパターンを検出
    - セッション変更時に履歴リセット

Changelog:
    - silenvx/dekita#xxx: フック追加
    - silenvx/dekita#1630: 高頻度Rework検出追加
    - silenvx/dekita#2607: HookContextパターン移行
"""

import json
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from common import METRICS_LOG_DIR
from lib.execution import log_hook_execution
from lib.hook_input import get_tool_result
from lib.results import print_continue_and_log_skip
from lib.session import create_hook_context, get_session_id, parse_hook_input

# Time window for pattern detection (minutes)
PATTERN_WINDOW_MINUTES = 10

# Tracking file location (use TMPDIR for sandbox compatibility)
TRACKING_DIR = Path(tempfile.gettempdir()) / "claude-hooks"
TOOL_TRACKING_FILE = TRACKING_DIR / "tool-sequence.json"

# Persistent log for analysis
TOOL_EFFICIENCY_LOG = METRICS_LOG_DIR / "tool-efficiency-metrics.log"

# Maximum number of tool calls to keep in history
MAX_HISTORY_SIZE = 50

# Inefficient patterns to detect
# Format: (pattern_name, description, detector_function)


def load_tool_history() -> dict:
    """Load tool call history."""
    if TOOL_TRACKING_FILE.exists():
        try:
            return json.loads(TOOL_TRACKING_FILE.read_text())
        except Exception:
            pass  # Best effort - corrupted tracking data is ignored
    return {"calls": [], "session_id": None}


def save_tool_history(data: dict) -> None:
    """Save tool call history."""
    TRACKING_DIR.mkdir(parents=True, exist_ok=True)
    TOOL_TRACKING_FILE.write_text(json.dumps(data, indent=2))


def log_efficiency_event(pattern_name: str, description: str, details: dict) -> None:
    """Log efficiency event for later analysis."""
    try:
        METRICS_LOG_DIR.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "session_id": get_session_id(),
            "type": "inefficiency_detected",
            "pattern_name": pattern_name,
            "description": description,
            "details": details,
        }
        with open(TOOL_EFFICIENCY_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass  # ログ書き込み失敗はサイレントに無視（メトリクスは必須ではない）


def extract_target(tool_name: str, tool_input: dict) -> str | None:
    """Extract the target (file/pattern) from tool input."""
    if tool_name in ("Read", "Edit", "Write"):
        return tool_input.get("file_path")
    elif tool_name == "Glob":
        return tool_input.get("pattern")
    elif tool_name == "Grep":
        return tool_input.get("pattern")
    elif tool_name == "Bash":
        return tool_input.get("command", "")[:100]  # First 100 chars
    return None


def detect_read_edit_loop(calls: list[dict]) -> dict | None:
    """Detect Read → Edit → Read → Edit pattern on same file.

    This pattern suggests the edit wasn't complete on first try.
    """
    # Need at least 4 calls for this pattern
    if len(calls) < 4:
        return None

    # Look at last 6 calls
    recent = calls[-6:]

    # Find Read-Edit pairs on the same file
    file_edit_counts: dict[str, int] = {}
    for i, call in enumerate(recent):
        if call["tool"] == "Edit" and call.get("target"):
            target = call["target"]
            # Check if preceded by Read on same file
            for j in range(max(0, i - 2), i):
                if recent[j]["tool"] == "Read" and recent[j].get("target") == target:
                    file_edit_counts[target] = file_edit_counts.get(target, 0) + 1
                    break

    # Report if any file had 2+ Read-Edit cycles
    for file_path, count in file_edit_counts.items():
        if count >= 2:
            return {
                "pattern": "read_edit_loop",
                "file": file_path,
                "cycles": count,
            }

    return None


def detect_repeated_search(calls: list[dict]) -> dict | None:
    """Detect repeated Glob/Grep with similar patterns.

    This suggests the search strategy could be improved.
    """
    # Look at last 10 calls
    recent = calls[-10:]

    search_patterns: dict[str, int] = {}
    for call in recent:
        if call["tool"] in ("Glob", "Grep") and call.get("target"):
            # Normalize pattern for comparison
            pattern = call["target"].lower()
            search_patterns[pattern] = search_patterns.get(pattern, 0) + 1

    # Report if any pattern was searched 3+ times
    for pattern, count in search_patterns.items():
        if count >= 3:
            return {
                "pattern": "repeated_search",
                "search_pattern": pattern,
                "count": count,
            }

    return None


def detect_bash_retry(calls: list[dict]) -> dict | None:
    """Detect repeated Bash command failures.

    This suggests the command or approach needs reconsideration.
    """
    # Look at last 5 Bash calls
    bash_calls = [c for c in calls[-10:] if c["tool"] == "Bash"]

    if len(bash_calls) < 3:
        return None

    # Count failures
    failures = [c for c in bash_calls if not c.get("success", True)]
    if len(failures) >= 3:
        return {
            "pattern": "bash_retry",
            "failure_count": len(failures),
            "commands": [c.get("target", "")[:50] for c in failures[-3:]],
        }

    return None


def detect_high_frequency_rework(calls: list[dict], now: datetime) -> dict | None:
    """Detect high-frequency rework on the same file.

    Issue #1630: 5分間で3回以上の同一ファイル編集を検出。

    呼び出し元の main() では、履歴全体から直近 PATTERN_WINDOW_MINUTES 分の
    コールだけを抽出してからこの関数に calls を渡している。
    この関数ではさらに、その中から直近 5 分間のコールだけを対象としているため、
    「グローバルな 10 分ウィンドウに対する、より厳しめの 5 分ローカルウィンドウ」
    という二重フィルタリングになっている。

    Args:
        calls (list[dict]): ツール呼び出し履歴
        now (datetime): 現在時刻（main()から渡される、タイムスタンプの一貫性のため）

    This suggests the changes weren't well-planned.
    """
    # 直近 5 分のコールのみを対象とする（呼び出し元の 10 分ウィンドウに対する追加フィルタ）
    window_5min = now - timedelta(minutes=5)
    recent_5min = [c for c in calls if datetime.fromisoformat(c["timestamp"]) > window_5min]

    # Filter to Edit calls with targets
    edit_calls = [c for c in recent_5min if c["tool"] == "Edit" and c.get("target")]

    if len(edit_calls) < 3:
        return None

    # Count edits per file
    file_edit_counts: dict[str, int] = {}
    for call in edit_calls:
        target = call["target"]
        file_edit_counts[target] = file_edit_counts.get(target, 0) + 1

    # Find files with 3+ edits
    for file_path, count in file_edit_counts.items():
        if count >= 3:
            return {
                "pattern": "high_frequency_rework",
                "file": file_path,
                "edit_count": count,
            }

    return None


def main():
    """PostToolUse hook for all tools.

    Tracks tool calls and detects inefficient patterns.
    """
    result = {"continue": True}

    try:
        input_data = parse_hook_input()
        # Issue #2607: Create context for session_id logging
        ctx = create_hook_context(input_data)
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {})
        tool_result = get_tool_result(input_data) or {}

        # Skip if no tool name
        if not tool_name:
            print_continue_and_log_skip("tool-efficiency-tracker", "no tool name", ctx=ctx)
            return

        now = datetime.now(UTC)
        current_session = get_session_id()

        # Load history
        history = load_tool_history()

        # Reset if session changed
        if history.get("session_id") != current_session:
            history = {"calls": [], "session_id": current_session}

        # Determine success (for Bash, check exit code)
        success = True
        if tool_name == "Bash":
            exit_code = tool_result.get("exit_code", 0)
            success = exit_code == 0

        # Create call record
        call_record = {
            "timestamp": now.isoformat(),
            "tool": tool_name,
            "target": extract_target(tool_name, tool_input),
            "success": success,
        }

        # Add to history
        history["calls"].append(call_record)

        # Trim history to max size
        if len(history["calls"]) > MAX_HISTORY_SIZE:
            history["calls"] = history["calls"][-MAX_HISTORY_SIZE:]

        # Save updated history
        save_tool_history(history)

        # Filter to recent calls within window
        window_start = now - timedelta(minutes=PATTERN_WINDOW_MINUTES)
        recent_calls = [
            c for c in history["calls"] if datetime.fromisoformat(c["timestamp"]) > window_start
        ]

        # Detect patterns
        patterns_detected = []

        read_edit = detect_read_edit_loop(recent_calls)
        if read_edit:
            patterns_detected.append(read_edit)

        repeated = detect_repeated_search(recent_calls)
        if repeated:
            patterns_detected.append(repeated)

        bash_retry = detect_bash_retry(recent_calls)
        if bash_retry:
            patterns_detected.append(bash_retry)

        # Issue #1630: Add high-frequency rework detection
        rework = detect_high_frequency_rework(recent_calls, now)
        if rework:
            patterns_detected.append(rework)

        # Log and report patterns
        if patterns_detected:
            for pattern in patterns_detected:
                pattern_name = pattern["pattern"]
                if pattern_name == "read_edit_loop":
                    log_efficiency_event(
                        pattern_name,
                        f"ファイル {pattern['file']} で Read→Edit が {pattern['cycles']} 回繰り返し",
                        pattern,
                    )
                elif pattern_name == "repeated_search":
                    log_efficiency_event(
                        pattern_name,
                        f"パターン '{pattern['search_pattern']}' を {pattern['count']} 回検索",
                        pattern,
                    )
                elif pattern_name == "bash_retry":
                    log_efficiency_event(
                        pattern_name,
                        f"Bashコマンドが {pattern['failure_count']} 回失敗",
                        pattern,
                    )
                elif pattern_name == "high_frequency_rework":
                    log_efficiency_event(
                        pattern_name,
                        f"ファイル {pattern['file']} を {pattern['edit_count']} 回編集（高頻度Rework）",
                        pattern,
                    )

            # Show message for first pattern only
            first = patterns_detected[0]
            if first["pattern"] == "read_edit_loop":
                result["systemMessage"] = (
                    f"📊 効率性: {Path(first['file']).name} の "
                    f"Read→Edit が {first['cycles']} 回繰り返し。\n"
                    "事前調査で編集内容を確定させると効率的です。"
                )
            elif first["pattern"] == "repeated_search":
                result["systemMessage"] = (
                    f"📊 効率性: 同じパターンを {first['count']} 回検索。\n"
                    "検索結果を活用するか、Task toolで探索すると効率的です。"
                )
            elif first["pattern"] == "bash_retry":
                result["systemMessage"] = (
                    f"📊 効率性: Bashコマンドが {first['failure_count']} 回失敗。\n"
                    "アプローチの見直しを検討してください。"
                )
            elif first["pattern"] == "high_frequency_rework":
                result["systemMessage"] = (
                    f"📊 効率性: {Path(first['file']).name} を "
                    f"{first['edit_count']} 回編集（高頻度Rework）。\n"
                    "編集前に変更内容を確定させると効率的です。"
                )

            # Issue #1630: 即時フィードバック強化 - stderrにも出力
            if "systemMessage" in result:
                stderr_msg = f"[tool-efficiency-tracker] {result['systemMessage']}"
                print(stderr_msg, file=sys.stderr)

    except Exception:
        # フック実行の失敗でClaude Codeをブロックしない
        pass

    log_hook_execution(
        "tool-efficiency-tracker",
        "approve",
        details={"type": "tool_tracked"},
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
