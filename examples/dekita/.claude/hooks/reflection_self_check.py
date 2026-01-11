#!/usr/bin/env python3
"""振り返りの観点網羅性を確認し、抜けがあればブロック。

Why:
    振り返りで特定の観点（根本原因分析、見落とし確認等）が抜けると、
    表面的な振り返りになり改善につながらない。観点チェックを強制する。

What:
    - トランスクリプトから振り返りキーワードを検出
    - PERSPECTIVESリストの各観点がカバーされているか確認
    - 抜けている観点があればブロック
    - セッション内の繰り返しブロックパターンを提示

State:
    - reads: .claude/logs/metrics/block-patterns-{session_id}.jsonl

Remarks:
    - ブロック型フック（Stopフック）
    - reflection-quality-checkは矛盾検出、本フックは観点網羅性
    - 振り返りなしの場合はスキップ

Changelog:
    - silenvx/dekita#2242: フック追加（観点チェック）
    - silenvx/dekita#2251: 警告からブロックに変更
    - silenvx/dekita#2272: メタ評価（観点更新提案）追加
    - silenvx/dekita#2278: 7日分析からセッション分析に変更
    - silenvx/dekita#2289: already_handled_check観点を追加
    - silenvx/dekita#2290: meta_reflection観点を追加
    - silenvx/dekita#2582: implementation_verification観点を追加
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from lib.execution import log_hook_execution
from lib.path_validation import is_safe_transcript_path
from lib.results import make_approve_result, make_block_result
from lib.session import create_hook_context, parse_hook_input
from lib.session_validation import is_safe_session_id

# Perspectives to check in reflection
# Each perspective has keywords that indicate it was addressed
PERSPECTIVES = [
    {
        "id": "session_facts",
        "name": "セッション事実の確認",
        "description": "ログを確認し、客観的事象を把握したか",
        "keywords": [r"ログ", r"確認", r"事実", r"調査", r"分析結果"],
    },
    {
        "id": "anomaly_patterns",
        "name": "異常パターンの確認",
        "description": "通常と異なる動作を確認したか",
        "keywords": [r"異常", r"パターン", r"繰り返し", r"タイムアウト", r"連続", r"多発"],
    },
    {
        "id": "root_cause",
        "name": "根本原因分析",
        "description": "表面的な説明で終わらず、なぜなぜ分析をしたか",
        "keywords": [r"なぜ", r"原因", r"根本", r"本質", r"背景"],
    },
    {
        "id": "oversight_check",
        "name": "見落とし確認",
        "description": "「他にないか？」を自問したか",
        "keywords": [r"他にないか", r"3回自問", r"見落とし", r"漏れ"],
    },
    {
        "id": "hasty_judgment",
        "name": "安易な判断の回避",
        "description": "「問題なし」と判断する前に十分検討したか",
        "keywords": [r"十分.*検討", r"深掘り", r"掘り下げ", r"詳細.*分析"],
    },
    {
        "id": "issue_creation",
        "name": "Issue化の確認",
        "description": "発見した問題をIssue化したか（または不要な理由を明記したか）",
        "keywords": [r"Issue", r"#\d+", r"作成", r"不要", r"Issue化"],
    },
    # Issue #2289: Prevent false "already handled" judgments
    {
        "id": "already_handled_check",
        "name": "「対応済み」判断の検証",
        "description": "「対応済み」と判断した場合、その仕組みの実行タイミング（Pre/Post/Stop）を確認し、実際に有効か検証したか",
        "keywords": [
            r"対応済み.*検証",
            r"実行タイミング",
            r"(Pre|Post|Stop)",
            r"フック.*確認",
            r"仕組み.*有効",
            r"対応済み.*なし",  # "「対応済み」判断なし" も許容
        ],
    },
    # Issue #2290: Meta-reflection to ensure reflection quality
    {
        "id": "meta_reflection",
        "name": "振り返り自体の評価",
        "description": "この振り返り自体に改善点はないか、形式的なチェックリスト消化になっていないか",
        "keywords": [
            r"振り返り自体",
            r"メタ.*振り返り",
            r"形式的",
            r"チェックリスト.*消化",
            r"振り返り.*改善",
            r"振り返り.*品質",
        ],
    },
    # Issue #2582: Dogfooding verification to ensure implementation is tested
    {
        "id": "implementation_verification",
        "name": "実装後の動作確認",
        "description": "実装後（マージ前）に動作を確認したか（正常系、異常系、Dogfooding）",
        "keywords": [
            r"動作確認",
            r"Dogfooding",
            r"正常系.*確認",
            r"異常系.*確認",
            r"自分で使",
            r"実際.*テスト",
            r"実データ.*確認",
            r"動作確認.*不要",  # "動作確認不要"（ドキュメント変更など）も許容
        ],
    },
]

# Keywords indicating reflection was performed
REFLECTION_KEYWORDS = [r"五省", r"振り返り", r"反省", r"教訓", r"改善点"]
COMPILED_REFLECTION_PATTERN = re.compile("|".join(REFLECTION_KEYWORDS))


def has_reflection(transcript_content: str) -> bool:
    """Check if reflection was performed in the transcript."""
    return bool(COMPILED_REFLECTION_PATTERN.search(transcript_content))


def check_perspective(transcript_content: str, keywords: list[str]) -> bool:
    """Check if a perspective was addressed based on keyword presence.

    Returns True if any keyword is found in the transcript.
    """
    for keyword in keywords:
        if re.search(keyword, transcript_content):
            return True
    return False


def get_missing_perspectives(transcript_content: str) -> list[dict]:
    """Get list of perspectives not addressed in the reflection.

    Returns list of perspective dicts that were not found.
    """
    missing = []
    for perspective in PERSPECTIVES:
        if not check_perspective(transcript_content, perspective["keywords"]):
            missing.append(perspective)
    return missing


def build_checklist_message(missing_perspectives: list[dict]) -> str:
    """Build a user-friendly checklist message for missing perspectives."""
    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "📋 振り返り観点チェック",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "以下の観点について確認しましたか？",
        "",
    ]

    for p in missing_perspectives:
        lines.append(f"❓ {p['name']}")
        lines.append(f"   → {p['description']}")
        lines.append("")

    lines.extend(
        [
            "上記の観点が抜けている場合、振り返りを補完してください。",
            "意図的にスキップした場合は問題ありません。",
        ]
    )

    return "\n".join(lines)


# =============================================================================
# Session Block Pattern Analysis (Issue #2278)
# =============================================================================

# Minimum block count to consider as "repeated" pattern
MIN_REPEAT_COUNT = 2


def get_session_block_patterns(session_id: str) -> dict[str, int]:
    """Get block pattern counts for the current session.

    Issue #2278: Changed from 7-day analysis to session-scoped analysis.
    Only analyzes blocks from the current session to detect repeated patterns.

    Args:
        session_id: Current session ID from hook input.

    Returns:
        A dict of hook_name -> count for this session.
    """
    # Validate session_id to prevent path traversal (Issue #2278, #2282)
    if not is_safe_session_id(session_id):
        return {}

    logs_dir = Path(__file__).parent.parent / "logs" / "metrics"
    log_file = logs_dir / f"block-patterns-{session_id}.jsonl"

    if not log_file.exists():
        return {}

    hook_counts: dict[str, int] = {}

    try:
        content = log_file.read_text(encoding="utf-8")
        for line in content.strip().split("\n"):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                if entry.get("type") != "block":
                    continue
                hook = entry.get("hook", "")
                if hook:
                    hook_counts[hook] = hook_counts.get(hook, 0) + 1
            except json.JSONDecodeError:
                continue
    except OSError:
        pass  # Log file may not exist or be inaccessible - return empty dict

    return hook_counts


def analyze_session_reflection_hints(block_patterns: dict[str, int]) -> list[dict]:
    """Analyze session block patterns to suggest reflection points.

    Issue #2278: Redesigned to focus on repeated blocks in the current session.
    Instead of mapping hooks to perspectives (which was incorrect),
    this function detects patterns that suggest things to reflect on.

    Args:
        block_patterns: Dict of hook_name -> count from current session.

    Returns:
        List of reflection hint dicts with 'hook', 'count', and 'hint'.
    """
    hints = []

    # Find hooks that blocked multiple times (repeated patterns)
    repeated = [
        (hook, count) for hook, count in block_patterns.items() if count >= MIN_REPEAT_COUNT
    ]

    # Sort by count descending
    repeated.sort(key=lambda x: x[1], reverse=True)

    # Generate hints for top repeated patterns (limit to 3 to avoid noise)
    for hook, count in repeated[:3]:
        hints.append(
            {
                "hook": hook,
                "count": count,
                "hint": f"'{hook}' が{count}回ブロック → なぜ繰り返したか振り返る",
            }
        )

    return hints


def build_session_hints_message(hints: list[dict]) -> str:
    """Build a message for session-based reflection hints.

    Issue #2278: Changed from "perspective meta-evaluation" to
    "session reflection hints" - simpler and more actionable.
    """
    if not hints:
        return ""

    lines = [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "💡 このセッションの振り返りポイント",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "同じブロックが繰り返し発生しています:",
        "",
    ]

    for hint in hints:
        lines.append(f"  🔄 {hint['hint']}")

    lines.append("")
    lines.append("繰り返しの原因を振り返り、改善策を検討してください。")

    return "\n".join(lines)


def main():
    """Main hook logic for Stop event."""
    result = make_approve_result("reflection-self-check")

    try:
        input_data = parse_hook_input()

        ctx = create_hook_context(input_data)

        # Get transcript content
        transcript_path = input_data.get("transcript_path", "")
        transcript_content = ""
        if transcript_path and is_safe_transcript_path(transcript_path):
            try:
                transcript_content = Path(transcript_path).read_text()
            except Exception:
                pass  # Best effort - transcript read failure should not break hook

        # Only check if reflection was performed
        if not has_reflection(transcript_content):
            log_hook_execution(
                "reflection-self-check",
                "approve",
                "No reflection detected, skipping perspective check",
            )
            print(json.dumps(result))
            return

        # Get missing perspectives
        missing = get_missing_perspectives(transcript_content)

        # Analyze current session's block patterns (Issue #2278)
        session_id = ctx.get_session_id()
        block_patterns = get_session_block_patterns(session_id)
        hints = analyze_session_reflection_hints(block_patterns)
        hints_message = build_session_hints_message(hints)

        if missing:
            # Block when perspectives are missing (Issue #2251)
            message = build_checklist_message(missing)
            if hints_message:
                message += "\n" + hints_message
            # make_block_result内でlog_hook_executionが自動呼び出しされる
            result = make_block_result("reflection-self-check", message, ctx)
            print(json.dumps(result))
            sys.exit(2)
        else:
            # All perspectives covered, but show session hints if any
            if hints_message:
                # Warn but don't block
                print(hints_message, file=sys.stderr)
            log_hook_execution(
                "reflection-self-check",
                "approve",
                f"All perspectives addressed. Session hints: {len(hints)}",
            )

    except Exception as e:
        log_hook_execution("reflection-self-check", "error", f"Hook error: {e}")

    print(json.dumps(result))


if __name__ == "__main__":
    main()
