#!/usr/bin/env python3
"""セッション終了時に統合レポートを生成する。

Why:
    セッション単位の活動サマリーを生成し、
    成果と改善点を把握するため。

What:
    - collect_session_data(): セッションデータを収集
    - generate_report(): 統合レポートを生成

State:
    - reads: .claude/logs/session/*/hook-execution-*.jsonl
    - writes: .claude/logs/reports/session-{session_id}.md

Remarks:
    - Stop hookから呼び出される
    - worktree内でも本体のログを参照

Changelog:
    - silenvx/dekita#1367: セッションレポート生成機能を追加
    - silenvx/dekita#2496: get_claude_session_id削除に対応
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ログディレクトリ
SCRIPT_DIR = Path(__file__).parent
HOOKS_DIR = SCRIPT_DIR.parent / "hooks"

# Add hooks directory to path for importing common module
# Issue #2496: get_claude_session_id を削除し、handle_session_id_arg の戻り値を使用
sys.path.insert(0, str(HOOKS_DIR))
from lib.logging import read_session_log_entries
from lib.session import handle_session_id_arg


def _get_main_project_root() -> Path:
    """メインプロジェクトルートを取得

    worktree環境でも正しくメインリポジトリルートを検出する。
    git rev-parse --git-common-dir の出力:
    - 通常リポジトリ: .git
    - worktree: /path/to/main/.git/worktrees/<name>
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=SCRIPT_DIR,
        )
        if result.returncode == 0:
            git_common_dir = Path(result.stdout.strip()).resolve()
            # worktree環境: .git/worktrees/<name> -> .git -> parent がルート
            if "worktrees" in git_common_dir.parts:
                # .git/worktrees/<name> から .git を見つけてその親を返す
                parts = git_common_dir.parts
                worktrees_idx = parts.index("worktrees")
                # worktrees の前が .git、その前がルート
                if worktrees_idx >= 2:
                    return Path(*parts[: worktrees_idx - 1])
            # 通常リポジトリ: .git -> parent がルート
            if git_common_dir.name == ".git":
                return git_common_dir.parent
    except Exception:
        # Git command failed or timeout; fall back to relative path
        pass
    return SCRIPT_DIR.parent.parent


PROJECT_ROOT = _get_main_project_root()
LOGS_DIR = PROJECT_ROOT / ".claude" / "logs"
EXECUTION_LOG_DIR = LOGS_DIR / "execution"
METRICS_LOG_DIR = LOGS_DIR / "metrics"
FLOW_LOG_DIR = LOGS_DIR / "flow"
REPORTS_DIR = LOGS_DIR / "reports"
REFLECTIONS_DIR = LOGS_DIR / "reflections"

SESSION_METRICS_LOG = METRICS_LOG_DIR / "session-metrics.log"
REWORK_LOG = METRICS_LOG_DIR / "rework-metrics.log"
EFFICIENCY_LOG = METRICS_LOG_DIR / "tool-efficiency-metrics.log"
FLOW_EVENTS_LOG = FLOW_LOG_DIR / "events.jsonl"
# Note: Reflections are now stored in session-specific files (Issue #2194)
# Use read_all_session_log_entries(REFLECTIONS_DIR, "session-reflections") if needed


def get_fallback_session_id() -> str:
    """Get fallback session ID based on PPID.

    Issue #2496: Simplified to use PPID-based fallback only.
    The session_id should be provided via --session-id argument.
    """
    return f"ppid-{os.getppid()}"


def analyze_hook_executions(session_id: str) -> dict[str, Any]:
    """フック実行ログからセッション情報を分析"""
    result = {
        "total_executions": 0,
        "blocks": 0,
        "approves": 0,
        "skips": 0,
        "warns": 0,
        "triggered_hooks": [],
        "block_reasons": [],
        "first_timestamp": None,
        "last_timestamp": None,
    }

    hooks_counter: Counter[str] = Counter()
    block_reasons: list[str] = []

    # Read from session-specific log file
    entries = read_session_log_entries(EXECUTION_LOG_DIR, "hook-execution", session_id)

    for entry in entries:
        result["total_executions"] += 1
        hooks_counter[entry.get("hook", "unknown")] += 1

        decision = entry.get("decision", "approve")
        if decision == "approve":
            result["approves"] += 1
        elif decision == "block":
            result["blocks"] += 1
            if reason := entry.get("reason"):
                block_reasons.append(reason[:100])
        elif decision == "skip":
            result["skips"] += 1
        elif decision in ("warn", "warning"):
            result["warns"] += 1

        ts_str = entry.get("timestamp")
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str)
                if result["first_timestamp"] is None or ts < result["first_timestamp"]:
                    result["first_timestamp"] = ts
                if result["last_timestamp"] is None or ts > result["last_timestamp"]:
                    result["last_timestamp"] = ts
            except ValueError:
                continue

    result["triggered_hooks"] = [hook for hook, _ in hooks_counter.most_common(10)]
    result["block_reasons"] = block_reasons[:10]  # 上位10件まで

    return result


def analyze_git_operations(session_id: str) -> dict[str, Any]:
    """Git操作ログからセッション情報を分析"""
    result = {
        "commits": 0,
        "merges": 0,
        "branches_touched": [],
        "worktree_count": 0,
    }

    branches: set[str] = set()

    # Read from session-specific log file
    entries = read_session_log_entries(EXECUTION_LOG_DIR, "hook-execution", session_id)

    for entry in entries:
        if branch := entry.get("branch"):
            branches.add(branch)

        # worktree関連のフックをカウント
        hook = entry.get("hook", "")
        if "worktree" in hook.lower() and entry.get("decision") == "approve":
            result["worktree_count"] += 1

    result["branches_touched"] = list(branches)

    return result


def analyze_efficiency_metrics(session_id: str) -> dict[str, Any]:
    """効率メトリクスを分析"""
    result = {
        "read_edit_loop_count": 0,
        "repeated_search_count": 0,
        "bash_retry_count": 0,
        "rework_file_count": 0,
        "top_rework_files": [],
        "efficiency_score": 100,
    }

    # tool-efficiency-metrics.log から分析
    if EFFICIENCY_LOG.exists():
        with open(EFFICIENCY_LOG, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if entry.get("session_id") != session_id:
                        continue

                    pattern = entry.get("pattern_name", "")
                    if pattern == "read_edit_loop":
                        result["read_edit_loop_count"] += 1
                    elif pattern == "repeated_search":
                        result["repeated_search_count"] += 1
                    elif pattern == "bash_retry":
                        result["bash_retry_count"] += 1

                except (json.JSONDecodeError, KeyError):
                    continue

    # rework-metrics.log から分析
    rework_files: Counter[str] = Counter()
    if REWORK_LOG.exists():
        with open(REWORK_LOG, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if entry.get("session_id") != session_id:
                        continue

                    if entry.get("type") == "rework_detected":
                        file_path = entry.get("file_path", "")
                        if file_path:
                            # ファイル名のみを取得
                            rework_files[Path(file_path).name] += 1

                except (json.JSONDecodeError, KeyError):
                    continue

    result["rework_file_count"] = len(rework_files)
    result["top_rework_files"] = [f for f, _ in rework_files.most_common(5)]

    # 効率スコア計算（100点満点、減点方式）
    score = 100
    score -= result["read_edit_loop_count"] * 5
    score -= result["repeated_search_count"] * 3
    score -= result["bash_retry_count"] * 3
    score -= min(result["rework_file_count"] * 2, 20)  # 上限20点減点
    result["efficiency_score"] = max(0, score)

    return result


def analyze_phases(session_id: str) -> dict[str, Any]:
    """フェーズ分析を行う"""
    result: dict[str, dict[str, Any]] = {}

    if not FLOW_EVENTS_LOG.exists():
        return result

    phase_entries: dict[str, list[datetime]] = {}

    with open(FLOW_EVENTS_LOG, encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
                if entry.get("session_id") != session_id:
                    continue

                phase = entry.get("current_phase") or entry.get("new_phase")
                if not phase:
                    continue

                ts_str = entry.get("ts")
                if ts_str:
                    ts = datetime.fromisoformat(ts_str)
                    if phase not in phase_entries:
                        phase_entries[phase] = []
                    phase_entries[phase].append(ts)

            except (json.JSONDecodeError, KeyError, ValueError):
                continue

    # 各フェーズの滞在時間を計算
    for phase, timestamps in phase_entries.items():
        if len(timestamps) >= 2:
            timestamps.sort()
            duration = (timestamps[-1] - timestamps[0]).total_seconds() / 60
            result[phase] = {
                "duration_minutes": round(duration, 1),
                "event_count": len(timestamps),
            }
        else:
            result[phase] = {
                "duration_minutes": 0,
                "event_count": len(timestamps),
            }

    return result


def get_gosei_evaluation(session_id: str) -> dict[str, Any]:
    """五省評価を取得

    Issue #2194: Reflections are now stored in session-specific files.
    Reads from session-specific reflection files if they exist.
    """
    # Default evaluation structure
    default_result = {
        "requirements": {"score": "unknown", "indicators": []},
        "quality": {"score": "unknown", "indicators": []},
        "verification": {"score": "unknown", "indicators": []},
        "responsiveness": {"score": "unknown", "indicators": []},
        "efficiency": {"score": "unknown", "indicators": []},
    }

    # Try to read from session-specific reflection file
    entries = read_session_log_entries(REFLECTIONS_DIR, "session-reflections", session_id)
    if not entries:
        return default_result

    # Find the latest gosei evaluation entry
    for entry in reversed(entries):
        if entry.get("type") == "gosei_evaluation" and "evaluation" in entry:
            return entry["evaluation"]

    return default_result


def enrich_gosei_indicators(
    gosei: dict[str, Any],
    hooks_data: dict[str, Any],
    efficiency_data: dict[str, Any],
) -> dict[str, Any]:
    """五省評価のindicatorsを充実させる"""
    enriched = gosei.copy()

    # requirements indicators
    if hooks_data.get("blocks", 0) == 0:
        enriched["requirements"]["indicators"].append("ブロックなし: 全フロー遵守")
    if hooks_data.get("blocks", 0) > 0:
        enriched["requirements"]["indicators"].append(
            f"ブロック{hooks_data['blocks']}回: 誤操作防止が機能"
        )

    # quality indicators
    if efficiency_data.get("rework_file_count", 0) == 0:
        enriched["quality"]["indicators"].append("手戻りなし")
    else:
        enriched["quality"]["indicators"].append(
            f"手戻り{efficiency_data['rework_file_count']}ファイル"
        )

    # verification indicators
    if hooks_data.get("total_executions", 0) > 0:
        enriched["verification"]["indicators"].append(
            f"フック実行{hooks_data['total_executions']}回"
        )

    # efficiency indicators
    score = efficiency_data.get("efficiency_score", 100)
    if score >= 90:
        enriched["efficiency"]["score"] = "good"
        enriched["efficiency"]["indicators"].append(f"効率スコア: {score}/100")
    elif score >= 70:
        enriched["efficiency"]["score"] = "needs_improvement"
        enriched["efficiency"]["indicators"].append(f"効率スコア: {score}/100")
    else:
        enriched["efficiency"]["score"] = "poor"
        enriched["efficiency"]["indicators"].append(f"効率スコア: {score}/100 (要改善)")

    if efficiency_data.get("read_edit_loop_count", 0) > 0:
        enriched["efficiency"]["indicators"].append(
            f"Read→Editループ {efficiency_data['read_edit_loop_count']}回検出"
        )

    return enriched


def generate_improvement_candidates(
    efficiency_data: dict[str, Any],
) -> list[dict[str, Any]]:
    """改善候補を生成"""
    candidates = []

    if efficiency_data.get("read_edit_loop_count", 0) > 0:
        candidates.append(
            {
                "type": "efficiency",
                "source": "read_edit_loop",
                "count": efficiency_data["read_edit_loop_count"],
                "suggestion": "事前調査を十分に行い、編集内容を確定させてから実行",
            }
        )

    if efficiency_data.get("rework_file_count", 0) >= 3:
        candidates.append(
            {
                "type": "efficiency",
                "source": "rework",
                "count": efficiency_data["rework_file_count"],
                "suggestion": "実装前に設計を見直し、手戻りを減らす",
            }
        )

    return candidates


def generate_session_report(session_id: str) -> dict[str, Any]:
    """セッションレポートを生成"""
    hooks_data = analyze_hook_executions(session_id)
    git_data = analyze_git_operations(session_id)
    efficiency_data = analyze_efficiency_metrics(session_id)
    phases_data = analyze_phases(session_id)
    gosei_data = get_gosei_evaluation(session_id)

    # 時間計算
    duration_minutes = 0
    if hooks_data["first_timestamp"] and hooks_data["last_timestamp"]:
        duration = hooks_data["last_timestamp"] - hooks_data["first_timestamp"]
        duration_minutes = round(duration.total_seconds() / 60, 1)

    # 五省indicatorsを充実
    enriched_gosei = enrich_gosei_indicators(gosei_data, hooks_data, efficiency_data)

    # レポート生成
    report = {
        "session_id": session_id,
        "timestamp": datetime.now(UTC).isoformat(),
        "duration_minutes": duration_minutes,
        "branch": git_data["branches_touched"][0] if git_data["branches_touched"] else "unknown",
        "workflows": git_data["branches_touched"],
        "summary": {
            "hooks": {
                "total_executions": hooks_data["total_executions"],
                "blocks": hooks_data["blocks"],
                "approves": hooks_data["approves"],
                "skips": hooks_data["skips"],
                "warns": hooks_data["warns"],
                "triggered_hooks": hooks_data["triggered_hooks"],
            },
            "git": {
                "branches_touched": git_data["branches_touched"],
                "worktree_count": git_data["worktree_count"],
            },
        },
        "efficiency": {
            "read_edit_loop_count": efficiency_data["read_edit_loop_count"],
            "repeated_search_count": efficiency_data["repeated_search_count"],
            "rework_file_count": efficiency_data["rework_file_count"],
            "top_rework_files": efficiency_data["top_rework_files"],
            "efficiency_score": efficiency_data["efficiency_score"],
        },
        "phases": phases_data,
        "gosei_evaluation": enriched_gosei,
        "improvement_candidates": generate_improvement_candidates(efficiency_data),
    }

    return report


def save_report(report: dict[str, Any]) -> Path:
    """レポートをファイルに保存"""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    # ファイル名生成（UTCを使用して一貫性を保つ）
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d-%H-%M-%S")
    session_short = report["session_id"][:8] if report["session_id"] else "unknown"
    filename = f"session-{timestamp}-{session_short}.json"
    filepath = REPORTS_DIR / filename

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)

    return filepath


def main() -> None:
    """メイン処理"""
    import argparse

    # Issue #2317: 引数ベースのsession_id伝播に対応
    parser = argparse.ArgumentParser(description="Generate session report")
    parser.add_argument(
        "--session-id",
        type=str,
        default=None,
        help="Claude session ID for log tracking",
    )
    # 後方互換性: 位置引数でもsession_idを受け取る
    parser.add_argument(
        "session_id_positional",
        nargs="?",
        type=str,
        default=None,
        help="Session ID (positional, deprecated)",
    )
    args = parser.parse_args()

    # Issue #2496: handle_session_id_arg の戻り値を使用
    # --session-idまたは位置引数からsession_idを取得
    session_id_arg = args.session_id or args.session_id_positional
    validated_session_id = handle_session_id_arg(session_id_arg)
    session_id = validated_session_id or get_fallback_session_id()
    if not session_id:
        print("Error: セッションIDが取得できません", file=sys.stderr)
        sys.exit(1)

    # レポート生成
    report = generate_session_report(session_id)

    # 保存
    filepath = save_report(report)
    print(f"Session report saved: {filepath}")


if __name__ == "__main__":
    main()
