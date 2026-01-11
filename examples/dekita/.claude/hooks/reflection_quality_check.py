#!/usr/bin/env python3
"""振り返りの形式的評価を防ぐ（ブロック回数との矛盾検出、改善点Issue化強制）。

Why:
    形式的・表面的な振り返りは実質的な改善につながらない。
    客観的メトリクス（ブロック回数）と主観的評価（「問題なし」）の
    矛盾を検出し、根本原因分析を強制する。また、改善点を発見しても
    Issue化しなければ忘れられるため、Issue参照を強制する。

What:
    - フック実行ログからブロック回数をカウント
    - トランスクリプトから振り返り内容を分析
    - 矛盾検出（3回以上ブロックなのに「問題なし」）時にブロック
    - 改善点があるのにIssue参照がない場合にブロック

Remarks:
    - reflection-completion-checkはキーワード存在確認、本フックは品質検証
    - ブロック型フック（警告ではなくブロック）
    - 根本原因パターン（なぜ、根本原因、原因は）があれば通過

Changelog:
    - silenvx/dekita#1945: フック追加（形式的振り返り防止）
    - silenvx/dekita#1958: 設計簡素化（レビューコメント数チェック削除）
    - silenvx/dekita#2005: 警告からブロックに変更
    - silenvx/dekita#2354: 改善点Issue化強制を追加
    - silenvx/dekita#2362: Issue参照パターン改善
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from common import EXECUTION_LOG_DIR
from lib.execution import log_hook_execution
from lib.logging import read_session_log_entries
from lib.path_validation import is_safe_transcript_path
from lib.results import make_approve_result, make_block_result
from lib.session import create_hook_context, parse_hook_input

# Threshold for contradiction detection
# Warn if blocked 3+ times but claims "no problem"
BLOCK_WARNING_THRESHOLD = 3

# Patterns indicating "no problem" in reflection
# Note: Japanese negation forms include ない/なし/ありません/なかった
NO_PROBLEM_PATTERNS = [
    r"問題なし",
    r"特に問題.*(ない|なし|ありません|なかった)",  # "特に問題がある"を誤検知しないように否定形を含める
    r"問題は.*(ない|なし|ありません)",
    r"改善点.*(なし|ない|ありません)",
    r"反省点.*(なし|ない|ありません)",
    r"[45]/5.*全項目",
    r"全項目.*[45]/5",
]

# Pre-compiled pattern for performance (Issue #1945 review)
COMPILED_NO_PROBLEM_PATTERN = re.compile("|".join(NO_PROBLEM_PATTERNS))

# Patterns indicating root cause analysis (Issue #2005)
# If these patterns are present, the reflection is considered substantive
# Note: Uses .{0,50} instead of .* to prevent overly broad matching (Copilot review)
ROOT_CAUSE_PATTERNS = [
    r"なぜ.{0,50}?(した|ブロック|忘れ|スキップ)",  # "なぜブロックされたか" etc.
    r"根本原因",
    r"原因は",
    r"原因として",
    r"原因が",
    r"問題の本質",
    r"本質的な問題",
    r"構造的な問題",
    r"パターン.{0,50}(検出|発見)",  # "パターンを検出"
    r"3回自問",
    r"他にないか",
]

# Pre-compiled pattern for root cause detection
COMPILED_ROOT_CAUSE_PATTERN = re.compile("|".join(ROOT_CAUSE_PATTERNS))

# Issue #2354: Patterns indicating improvement points that need Issue creation
# These patterns suggest Claude found something to improve but may not have created an Issue
# Note: Uses negative lookahead to exclude negation forms like "改善点なし" (Copilot review)
IMPROVEMENT_PATTERNS = [
    r"改善点(?!.*(?:なし|ない|ありません))",  # Exclude "改善点なし" etc.
    r"問題点(?!.*(?:なし|ない|ありません))",  # Exclude "問題点なし" etc.
    r"すべきだった",
    r"べきだった",
    # Note: "必要がある" removed - too broad, causes false positives (Copilot review)
    r"対策.{0,20}(必要|検討)",
    r"今後.{0,20}(対応|改善|検討)",
    r"次回.{0,20}(注意|気をつけ|確認)",
]

# Pre-compiled pattern for improvement detection
COMPILED_IMPROVEMENT_PATTERN = re.compile("|".join(IMPROVEMENT_PATTERNS))

# Pattern for Issue references (e.g., #123, Issue #123, issue-123)
# Note: Differs from check_utils.py which includes "issue作成" - this pattern focuses on
# actual Issue references (numbers) rather than statements of intent (Copilot review)
ISSUE_REFERENCE_PATTERN = re.compile(r"#\d+|Issue\s*#?\d+|issue[/-]\d+", re.IGNORECASE)

# Issue #2362: Patterns indicating an improvement has been addressed without Issue
# These patterns indicate Claude explicitly stated no Issue is needed
ISSUE_NOT_NEEDED_PATTERNS = [
    r"Issue不要",
    r"Issue化不要",
    r"issue不要",
    r"ルール再確認で対応",
    r"対応済み",
    r"解決済み",
    r"クローズ済み",
    r"軽微.{0,20}対応可能",
    r"仕組み.{0,20}既存",
]

# Pre-compiled pattern for "Issue not needed" detection
COMPILED_ISSUE_NOT_NEEDED_PATTERN = re.compile("|".join(ISSUE_NOT_NEEDED_PATTERNS))


def get_block_count(session_id: str) -> int:
    """Get block count for the current session from local logs.

    This is the only objective metric used - no external API calls needed.
    """
    count = 0

    # Read from session-specific log file
    entries = read_session_log_entries(EXECUTION_LOG_DIR, "hook-execution", session_id)

    for entry in entries:
        if entry.get("decision") == "block":
            count += 1

    return count


def check_transcript_for_no_problem(transcript_content: str) -> bool:
    """Check if transcript contains "no problem" patterns in reflection context.

    Returns True if Claude said "no problem" during reflection.
    """
    # Only check assistant responses that contain reflection keywords
    reflection_context = False
    for line in transcript_content.split("\n"):
        # Check if this line is a reflection header or a different section header
        is_reflection_line = re.search(r"五省|振り返り|反省", line)
        is_section_header = re.search(r"^#{1,3}\s", line)

        # Start reflection context
        if is_reflection_line:
            reflection_context = True

        # If in reflection context, check for "no problem" patterns using compiled regex
        if reflection_context:
            if COMPILED_NO_PROBLEM_PATTERN.search(line):
                return True

            # Reset context on new section header (unless it's the reflection header itself)
            if is_section_header and not is_reflection_line:
                reflection_context = False

    return False


def check_root_cause_analysis(transcript_content: str) -> bool:
    """Check if transcript contains root cause analysis patterns.

    Returns True if Claude performed substantive root cause analysis.
    This indicates the reflection is not superficial even if other metrics suggest otherwise.

    Note: Context detection includes "問題" and "分析" in addition to the base reflection
    keywords. This is intentional because root cause analysis often appears in problem
    analysis sections, not just formal reflection sections. (Copilot review clarification)
    """
    reflection_context = False
    for line in transcript_content.split("\n"):
        # Broader context than check_transcript_for_no_problem because root cause
        # analysis may appear in problem analysis sections, not just reflection
        is_reflection_line = re.search(r"五省|振り返り|反省|問題|分析", line)
        is_section_header = re.search(r"^#{1,3}\s", line)

        if is_reflection_line:
            reflection_context = True

        if reflection_context:
            if COMPILED_ROOT_CAUSE_PATTERN.search(line):
                return True

            if is_section_header and not is_reflection_line:
                reflection_context = False

    return False


def check_high_scores(transcript_content: str) -> bool:
    """Check if reflection contains high scores (4-5) for all items.

    Only counts scores within reflection context to avoid false positives
    from unrelated content (e.g., "5/5 tests passing").
    """
    score_pattern = re.compile(r"[45]\s*/\s*5")
    reflection_context = False
    score_count = 0

    for line in transcript_content.split("\n"):
        # Check if this line is a reflection header or a different section header
        is_reflection_line = re.search(r"五省|振り返り|反省", line)
        is_section_header = re.search(r"^#{1,3}\s", line)

        # Start reflection context
        if is_reflection_line:
            reflection_context = True

        if reflection_context:
            # Count high scores in this line
            score_count += len(score_pattern.findall(line))

            # Reset context on new section header (unless it's the reflection header itself)
            if is_section_header and not is_reflection_line:
                reflection_context = False

    # If we found 5 or more high scores in reflection context, flag it
    return score_count >= 5


def check_improvements_without_issues(transcript_content: str) -> list[str]:
    """Check if transcript contains improvement points without Issue references.

    Returns list of improvement lines that don't have Issue references.
    Issue #2354: Enforces that all improvement points must be Issue-ized.
    Issue #2362: Also checks for follow-up responses that address improvements.
    """
    improvements_without_issues: list[str] = []
    reflection_context = False

    # Issue #2362: Track when first improvement is found to count follow-up responses
    # Only count Issue refs/not-needed statements AFTER improvements are detected
    first_improvement_found = False
    followup_addressed_count = 0

    lines = transcript_content.split("\n")
    for line in lines:
        # Check if this line is a reflection header or a different section header
        is_reflection_line = re.search(r"五省|振り返り|反省|改善|問題", line)
        is_section_header = re.search(r"^\s*#{1,3}\s", line)  # Allow leading whitespace

        # Issue #2362: Count follow-up Issue refs/not-needed AFTER first improvement found
        # This is done BEFORE updating reflection_context to correctly detect
        # follow-up responses outside the original reflection section.
        #
        # DESIGN NOTE (Codex review response):
        # This intentionally counts ANY Issue ref/not-needed AFTER the first improvement,
        # even if not directly related to a specific improvement. This is by design because:
        # 1. The hook blocks on "unaddressed improvement count > 0"
        # 2. Follow-up responses often address multiple improvements at once
        # 3. Requiring 1:1 matching between improvements and Issue refs is too strict
        # 4. Test coverage ensures this doesn't cause false negatives:
        #    - test_ignores_issue_refs_before_improvements
        #    - test_inline_issue_on_improvement_does_not_count_as_followup
        #    - test_blocks_when_followup_is_insufficient
        is_improvement_line = COMPILED_IMPROVEMENT_PATTERN.search(line)
        has_issue_ref = ISSUE_REFERENCE_PATTERN.search(line)
        has_not_needed = COMPILED_ISSUE_NOT_NEEDED_PATTERN.search(line)

        if first_improvement_found:
            # Case 1: Pure follow-up line (not an improvement pattern)
            # Note: Use `or` to count only once even if both patterns match (Copilot review)
            if not is_improvement_line:
                if has_issue_ref or has_not_needed:
                    followup_addressed_count += 1
            # Case 2: Improvement line with Issue ref/not-needed OUTSIDE reflection context
            # This handles "## 対応状況" section where improvements are re-listed with Issue refs
            # NOTE: Use reflection_context BEFORE update to detect section changes correctly
            elif not reflection_context and (has_issue_ref or has_not_needed):
                followup_addressed_count += 1

        # Start reflection context (AFTER followup counting to avoid false negatives)
        if is_reflection_line:
            reflection_context = True

        if reflection_context:
            # Skip section headers themselves - they don't need Issue refs (Copilot review)
            if is_section_header:
                if not is_reflection_line:
                    reflection_context = False
                continue

            # Check if line contains improvement pattern
            # Note: Reuse is_improvement_line from line 275 (Copilot review)
            if is_improvement_line:
                first_improvement_found = True
                # Reuse has_issue_ref/has_not_needed from lines 276-277 (Copilot review)
                if not has_issue_ref and not has_not_needed:
                    # Truncate long lines for display
                    display_line = line.strip()[:80]
                    if len(line.strip()) > 80:
                        display_line += "..."
                    improvements_without_issues.append(display_line)

    # Issue #2362: If follow-up addressed count >= unaddressed improvement count,
    # consider all improvements addressed (allows follow-up responses)
    # Key difference from original: only counts Issue refs AFTER improvements detected
    if (
        followup_addressed_count >= len(improvements_without_issues)
        and len(improvements_without_issues) > 0
    ):
        return []

    return improvements_without_issues


def should_block_reflection(
    block_count: int, has_no_problem: bool, has_high_scores: bool, has_root_cause: bool
) -> str | None:
    """Determine if reflection should be blocked due to contradiction.

    Returns block message or None if no blocking needed.

    Issue #2005: Changed from warning to blocking.
    - Contradiction + no root cause analysis = BLOCK
    - Contradiction + root cause analysis = PASS (substantive reflection)
    """
    # Early return: No contradiction possible without subjective "no problem" claim
    has_positive_subjective = has_no_problem or has_high_scores
    if not has_positive_subjective:
        return None

    # Check for contradiction with block count
    if block_count < BLOCK_WARNING_THRESHOLD:
        return None

    # If root cause analysis is present, allow pass (substantive reflection)
    if has_root_cause:
        return None

    return (
        "[reflection-quality-check] 振り返りの矛盾を検出 - セッション終了をブロック\n\n"
        f"ブロック {block_count}回 なのに「問題なし」/高評価で、根本原因の分析がありません。\n\n"
        "**「本当に問題なかったですか？」**\n\n"
        "以下のいずれかを実行してください:\n"
        "  1. 各ブロックについて「なぜその行動をしたか」を分析する\n"
        "  2. 根本原因を特定してIssue化する\n"
        "  3. 「他にないか？」を3回自問した結果を記述する\n\n"
        "ヒント: execute.md の「0. 必須チェック」セクションを参照\n"
    )


def main():
    """Main hook logic for Stop event."""
    result = make_approve_result("reflection-quality-check")

    try:
        input_data = parse_hook_input()

        ctx = create_hook_context(input_data)
        session_id = ctx.get_session_id()

        # Get objective metric (block count only - no external API)
        block_count = get_block_count(session_id)

        # Get transcript content
        transcript_path = input_data.get("transcript_path", "")
        transcript_content = ""
        if transcript_path and is_safe_transcript_path(transcript_path):
            try:
                transcript_content = Path(transcript_path).read_text()
            except Exception:
                pass  # Best effort - transcript read failure should not break hook

        # Check for contradictions and root cause analysis
        has_no_problem = check_transcript_for_no_problem(transcript_content)
        has_high_scores = check_high_scores(transcript_content)
        has_root_cause = check_root_cause_analysis(transcript_content)

        block_message = should_block_reflection(
            block_count, has_no_problem, has_high_scores, has_root_cause
        )

        # Issue #2354: Check for improvements without Issue references
        improvements_without_issues = check_improvements_without_issues(transcript_content)

        if block_message:
            # BLOCK session end if contradiction without root cause analysis
            result = make_block_result("reflection-quality-check", block_message, ctx)
            log_hook_execution(
                "reflection-quality-check",
                "block",
                f"Contradiction detected without root cause: {block_count} blocks",
            )
        elif improvements_without_issues:
            # BLOCK session end if improvements found without Issue references
            improvement_list = "\n".join(f"  - {line}" for line in improvements_without_issues[:5])
            if len(improvements_without_issues) > 5:
                improvement_list += f"\n  ... 他 {len(improvements_without_issues) - 5} 件"

            block_msg = (
                "[reflection-quality-check] 改善点のIssue化漏れを検出 - セッション終了をブロック\n\n"
                "振り返りで改善点を発見しましたが、Issue参照がありません。\n\n"
                "**該当箇所:**\n"
                f"{improvement_list}\n\n"
                "**execute.md原則**: 「改善点が見つかった場合、severity に関わらず全てIssue化が必須」\n\n"
                "以下のいずれかを実行してください:\n"
                "  1. 各改善点についてIssueを作成し、振り返りに Issue #番号 を追記\n"
                "  2. 改善点が軽微でルール再確認で対応可能な場合、その旨を明記\n\n"
            )
            result = make_block_result("reflection-quality-check", block_msg, ctx)
            log_hook_execution(
                "reflection-quality-check",
                "block",
                f"Improvements without Issue references: {len(improvements_without_issues)} found",
            )
        else:
            log_hook_execution(
                "reflection-quality-check",
                "approve",
                f"No contradiction or substantive analysis: blocks={block_count}, root_cause={has_root_cause}",
            )

    except Exception as e:
        log_hook_execution("reflection-quality-check", "error", f"Hook error: {e}")

    print(json.dumps(result))


if __name__ == "__main__":
    main()
