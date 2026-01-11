#!/usr/bin/env python3
"""振り返り時に発見した教訓がIssue化されているか確認する。

Why:
    振り返りで発見した教訓をIssue化しないと、問題が放置され
    同じ失敗を繰り返す。教訓のIssue化を強制することで改善を促進する。

What:
    - 振り返りキーワード（五省、振り返り等）の存在を確認
    - [lesson]タグまたは教訓キーワードを検出
    - Issue参照がない教訓を発見したらブロック

Remarks:
    - ブロック型フック（[lesson]タグにIssue参照がない場合ブロック）
    - Stopで発火（transcript分析）
    - [lesson]タグ検出は高精度（ブロック）、キーワード検出は警告のみ
    - セッション継続/コードブロック/Read出力等は誤検知防止で除外
    - reflection-completion-checkは振り返り実施を確認（責務分離）

Changelog:
    - silenvx/dekita#2075: フック追加
    - silenvx/dekita#2094: 否定パターン除外（誤検知防止）
    - silenvx/dekita#2106: コードブロック除外
    - silenvx/dekita#2111: メタ議論パターン除外
    - silenvx/dekita#2120: セッション継続サマリ除外
    - silenvx/dekita#2137: フック自己参照ループ防止
    - silenvx/dekita#2155: Read出力除外
    - silenvx/dekita#2311: [lesson]タグベース検出に移行
"""

import json
import re
import sys
from pathlib import Path

from lib.execution import log_hook_execution
from lib.results import make_block_result
from lib.session import parse_hook_input

# Issue #2311: Tag-based detection (primary, high precision)
# [lesson] tag explicitly marks lessons that need Issue tracking
LESSON_TAG_PATTERN = re.compile(r"\[lesson\]", re.IGNORECASE)

# Keywords that indicate a lesson or improvement was found (fallback, lower precision)
# Note: 「課題」は通常の用法（「今回の課題は〜」）との誤検知リスクが高いため除外
# Issue #2311: キーワード検出は警告のみ（タグ検出がブロック）
LESSON_KEYWORDS = [
    r"教訓",
    r"反省点",
    r"改善点",
    r"次回への引き継ぎ",
    r"問題点",
    r"要改善",
]

# Compiled regex pattern for lesson keywords (for performance)
LESSON_PATTERN = re.compile("|".join(LESSON_KEYWORDS))

# Issue #2094: Patterns that indicate no actual lesson was found (negation context)
# These patterns near a keyword indicate it's a false positive
# Issue #2114: Added more negation patterns to reduce false positives
# Issue #2149: Added patterns for 五省 style responses
NEGATION_PATTERNS = [
    r"発見され(ませんでした|なかった)",
    r"発見して(いません|いない|おりません)",  # Issue #2114
    r"見つかりませんでした",
    r"見つからなかった",
    r"見当たり(ません|ませんでした)",  # Issue #2114 polite forms
    r"見当たら(ない|なかった)",  # Issue #2114 casual forms
    r"ありませんでした",
    r"特になし",
    r"なし(?:[\s。、．，\.\,]|$)",  # Issue #2149: 「なし」の後に空白、句読点、または文字列終端
    r"特にありません",
    r"確認され(ませんでした|なかった)",  # Issue #2114
    r"認められ(ませんでした|なかった)",  # Issue #2114
    # Issue #2149: 五省スタイルの「問題なし」判定パターン
    r"問題なし(?:[\s。、．，\.\,]|$)",  # 「問題なし」（後ろが空白・句読点・文字列終端の場合）
    r"問題(ない|ありません|はありません)(?:[\s。、．，\.\,]|$)",  # その他の否定形（疑問形除外）
]

# Issue #2111: Patterns indicating meta-discussion about the hook/keywords itself
# When discussing why a keyword was detected (false positive explanation), skip it
# Use non-greedy .*? to prevent ReDoS attacks (see Gemini review comment)
# Issue #2119: Added patterns for reflection template/instruction context
# Issue #2125: Added patterns for hook functionality description context
# Issue #2129: Added patterns for hook's own block message (self-feedback loop prevention)
# Issue #2137: Added unique marker pattern for robust self-feedback loop prevention
META_DISCUSSION_PATTERNS = [
    # Issue #2137: Universal hook block message marker (highest priority)
    # Any content near this marker should be skipped as it's from hook output
    r"<!-- HOOK_BLOCK_MESSAGE:[a-z-]+ -->",
    r"誤検知",
    r"フックが.*?トリガー",
    r"フックの.*?検知",
    r"キーワード.*?検出",
    # Limit to lesson keywords to avoid matching general negation like "これはバグではなく"
    r"これは.*?(?:教訓|反省点|改善点|問題点).*?ではなく",
    r"新しい(?:教訓|反省点|改善点|問題点).*?ではない",
    r"会話履歴",
    r"トランスクリプト",
    # Issue #2119: Reflection template/instruction patterns
    # Section headers and instruction phrases that discuss lesson identification
    # e.g., "## 4. 改善点の洗い出し" (L140 of execute.md)
    r"(?:教訓|反省点|改善点|問題点|要改善).{0,5}(?:や|を).{0,10}(?:発見したら|洗い出し)",
    r"(?:教訓|反省点|改善点|問題点|要改善).{0,5}の洗い出し",
    # Issue #2125: Hook functionality description patterns
    # More specific patterns to avoid false negatives for real lessons
    r"(?:教訓|反省点|改善点|問題点)キーワード",  # "教訓キーワードを検出する"
    r"発見されたキーワード",  # Hook output message: "発見されたキーワード: 教訓"
    # Issue #2129: Hook's own block message patterns (prevent self-feedback loop)
    # The hook's block message contains lesson keywords, which causes infinite loop
    # These patterns are specific to hook output to avoid false negatives
    r"lesson-issue-check",  # Hook name in block message
    r"振り返りで発見した(?:教訓|反省点|改善点|問題点)がIssue化されていません",
    # More specific pattern: only match hook's instruction phrase with full context
    # Issue #2133: Fixed to match "教訓や反省点を発見したら" (with や between keywords)
    r"教訓や反省点を発見したら",
    # Issue #2135: Additional template/markdown context patterns
    # Markdown section headers with numbers: "## 4. 改善点の洗い出し"
    r"##\s*\d+\.\s*(?:教訓|反省点|改善点|問題点|要改善)",
    # Markdown table header cells (bold only): "| **改善点** |"
    # Note: Non-bold table cells like "| 改善点 | 詳細 |" may contain real lessons
    # and should NOT be excluded to avoid false negatives (per Codex review)
    # Note: Pattern with spaces like "| ** 改善点 ** |" is uncommon in standard Markdown
    r"\|\s*\*\*(?:教訓|反省点|改善点|問題点|要改善)\*\*",
    # Note: "発見されたキーワード: ..." is already covered by L82 pattern
    # r"発見されたキーワード" which matches all strings containing this prefix
    # Issue #2141: Session continuation context patterns
    # When a session continues from a summarized conversation, keywords from the
    # original reflection may appear but the connection to created Issues is lost.
    # These patterns detect session continuation markers in the transcript.
    r"conversation is summarized",  # English summary marker
    r"session is being continued",  # English continuation marker
    r"summarized below",  # Summary section indicator
    r"Previous session",  # Context from previous session
    r"セッション継続",  # Japanese continuation marker
    r"要約され",  # Japanese summary marker
    # Patterns indicating reflection was already done and addressed
    r"/reflect.*?実行",  # /reflect command was executed
    r"振り返りを実行",  # Reflection was executed
    r"Issue #\d+.*?(?:完了|マージ|実装済)",  # Issue was completed/merged/implemented
    r"(?:完了|マージ|実装済).*?Issue #\d+",  # Completed/merged Issue reference (reverse order)
]

# Compiled regex for meta-discussion patterns
# Use re.DOTALL so that .* can match across newlines in multi-line transcripts
META_DISCUSSION_PATTERN = re.compile("|".join(META_DISCUSSION_PATTERNS), re.DOTALL)

# Compiled regex for negation patterns
NEGATION_PATTERN = re.compile("|".join(NEGATION_PATTERNS))

# Pattern to detect Issue references (e.g., #1234, Issue #1234, Issue#1234)
ISSUE_REFERENCE_PATTERN = re.compile(r"(?:Issue\s*)?#(\d+)", re.IGNORECASE)

# Issue #2100: Keywords indicating resolved/completed status
# When combined with Issue reference, indicates false positive
RESOLVED_KEYWORDS = [
    r"済み",
    r"完了",
    r"解決",
    r"クローズ",
    r"マージ",
    r"実装済",
    r"対応済",
    r"仕組み化済",
]

# Compiled regex for resolved keywords
RESOLVED_PATTERN = re.compile("|".join(RESOLVED_KEYWORDS))

# Keywords that indicate reflection was performed
REFLECTION_KEYWORDS = [
    r"五省",
    r"振り返り",
    r"要件理解.*悖",
    r"実装.*恥",
    r"検証.*欠",
    r"対応.*憾",
    r"効率.*欠",
]

# Compiled regex pattern for reflection keywords (for performance)
REFLECTION_PATTERN = re.compile("|".join(REFLECTION_KEYWORDS))

# Issue #2130: Patterns that indicate reflection keyword is used in work/discussion context
# not as an actual reflection being performed
# Note: Use limited distance (.{0,30}) to avoid over-matching across paragraphs
REFLECTION_EXCLUSION_PATTERNS = [
    r"振り返り(?:プロンプト|テンプレート|フォーマット|ツール|機能|フック)",
    r"振り返り.{0,20}誤検知",
    r"振り返り.{0,20}(?:Issue|PR|修正|対応)",
    r"(?:Issue|PR).{0,30}振り返り(?:プロンプト|テンプレート|フォーマット|ツール|機能|フック)",
    r"「振り返り」",  # Quoted reference to the keyword itself
]

# Compiled regex for reflection exclusion patterns
REFLECTION_EXCLUSION_PATTERN = re.compile("|".join(REFLECTION_EXCLUSION_PATTERNS))

# Issue #2144: Global session continuation patterns
# These patterns indicate the session was continued from a summarized conversation.
# When detected ANYWHERE in the transcript, skip the entire lesson check.
# This addresses the limitation of context-based detection (200-char window).
#
# Note: Some patterns overlap with META_DISCUSSION_PATTERNS and SUMMARY_SECTION_PATTERN.
# This is intentional:
# - META_DISCUSSION_PATTERNS: Context-based exclusion (200-char window around keywords)
# - GLOBAL_SESSION_CONTINUATION_PATTERN: Early exit (skip entire check if marker exists)
# - SUMMARY_SECTION_PATTERN: Historical fallback (strip specific sections)
#
# The global check runs first and provides the most aggressive filtering.
# When it matches, the entire lesson check is skipped (no false positives).
GLOBAL_SESSION_CONTINUATION_PATTERNS = [
    r"session is being continued",  # English continuation marker
    r"conversation is summarized",  # English summary marker
    r"summarized below",  # Summary section indicator
    r"<!-- HOOK_BLOCK_MESSAGE:",  # Hook's own output marker
]

# Compiled regex for global session continuation check
GLOBAL_SESSION_CONTINUATION_PATTERN = re.compile(
    "|".join(GLOBAL_SESSION_CONTINUATION_PATTERNS), re.IGNORECASE
)

# Context extraction range (characters before/after match)
CONTEXT_CHARS = 200

# Issue #2106: Pattern to match code blocks (```...```)
# These should be excluded from keyword search as they contain test code
# Note: re.MULTILINE is not needed since [\s\S] already matches newlines
CODE_BLOCK_PATTERN = re.compile(r"```[\s\S]*?```")

# Issue #2139: Pattern to match system-reminder tags
# These contain system-generated context (AGENTS.md, CLAUDE.md, etc.)
# and should be excluded from lesson keyword detection
SYSTEM_REMINDER_PATTERN = re.compile(r"<system-reminder>[\s\S]*?</system-reminder>")

# Issue #2155: Pattern to match Read tool output
# Read tool output contains file content which may include lesson keywords
# as part of code/documentation, not actual lessons
# Format: "Result of calling the Read tool: \"..." followed by file content
# Note: lookahead allows: double newline, newline+tag, single newline, or EOF
# Single newline case (\n[^<\n]) handles common transcript format where next
# message immediately follows with just one line break
READ_TOOL_OUTPUT_PATTERN = re.compile(
    r'Result of calling the Read tool: "[\s\S]*?"(?=\n\n|\n<|\n[^<\n]|\n$|$)'
)

# Issue #2120: Pattern to match session continuation summary
# This summary contains keywords from previous sessions and should be excluded
# The summary starts with "This session is being continued..." and ends with
# "Please continue the conversation..."
# Note: re.MULTILINE is not needed since [\s\S] already matches newlines
SUMMARY_SECTION_PATTERN = re.compile(
    r"This session is being continued from a previous conversation[\s\S]*?"
    r"Please continue the conversation[^\n]*"
)

# Issue #2113: Patterns that indicate work/review context (not actual lessons)
# These patterns near a keyword indicate it's being discussed as work, not discovered
WORK_CONTEXT_PATTERNS = [
    r"を修正",
    r"に対応",
    r"対応済",
    r"のレビュー",
    r"レビューコメント",
    r"コメント対応",
    r"を実装",
    r"として登録",
    r"をIssue化(?!していな|してな|しな|してませ|していませ|しておりませ|しておら)",  # Exclude all negation forms
    r"誤検知",
]

# Compiled regex for work context patterns
WORK_CONTEXT_PATTERN = re.compile("|".join(WORK_CONTEXT_PATTERNS))


def strip_summary_section(text: str) -> str:
    """Remove session continuation summary from text to avoid false positives.

    Issue #2120: When a session is continued from a previous conversation,
    the summary contains keywords from the previous session which triggers
    false positives. This function removes the summary section.

    Note: With the addition of should_skip_lesson_check_globally() (Issue #2144),
    this function is now a historical fallback. The global check runs first and
    skips the entire lesson check when continuation markers are detected.
    This function is only reached when the global check does not match,
    providing additional safety for edge cases.
    """
    return SUMMARY_SECTION_PATTERN.sub("", text)


def strip_system_reminders(text: str) -> str:
    """Remove system-reminder tags from text to avoid false positives.

    Issue #2139: System reminders contain system-generated context like
    AGENTS.md, CLAUDE.md content which includes lesson keywords as part of
    documentation. These should not be checked for actual lessons.
    """
    return SYSTEM_REMINDER_PATTERN.sub("", text)


def strip_code_blocks(text: str) -> str:
    """Remove code blocks from text to avoid false positives from test code.

    Issue #2106: When working on lesson-issue-check itself, the test code
    contains lesson keywords which trigger false positives.
    """
    return CODE_BLOCK_PATTERN.sub("", text)


def strip_read_tool_output(text: str) -> str:
    """Remove Read tool output from text to avoid false positives from file content.

    Issue #2155: When Read tool displays file content (especially hook source code),
    lesson keywords in the file trigger false positives. The pattern matches:
    - 'Result of calling the Read tool: "...' followed by file content
    - Content is enclosed in quotes and ends at double newline, newline+tag, or end
    """
    return READ_TOOL_OUTPUT_PATTERN.sub("", text)


def has_reflection_keywords(text: str) -> bool:
    """Check if text contains reflection-related keywords.

    Issue #2130: Also checks that reflection keywords are not used in
    work/discussion context (e.g., "振り返りプロンプト", "振り返りIssue").
    """
    # First check if any reflection keyword exists
    match = REFLECTION_PATTERN.search(text)
    if not match:
        return False

    # Pre-compute all exclusion pattern matches
    exclusion_ranges: list[tuple[int, int]] = []
    for excl_match in REFLECTION_EXCLUSION_PATTERN.finditer(text):
        exclusion_ranges.append((excl_match.start(), excl_match.end()))

    # Check if ALL occurrences are within exclusion pattern ranges
    # If at least one occurrence is NOT within an exclusion range, it's genuine
    for m in REFLECTION_PATTERN.finditer(text):
        matched_pos = m.start()

        # Check if this specific match position is within any exclusion pattern
        is_excluded = any(start <= matched_pos < end for start, end in exclusion_ranges)

        if not is_excluded:
            return True

    # All occurrences were within exclusion patterns
    return False


def find_lesson_tags(text: str) -> list[tuple[str, str]]:
    """Find [lesson] tags in text and extract surrounding context.

    Issue #2311: Tag-based detection for high-precision lesson identification.
    Tags are explicitly placed by Claude during reflection, making them
    more reliable than keyword detection.

    Returns:
        List of ("[lesson]", context) tuples where context is the surrounding text.
    """
    lessons: list[tuple[str, str]] = []

    for match in LESSON_TAG_PATTERN.finditer(text):
        # Extract context around the match
        start = max(0, match.start() - CONTEXT_CHARS)
        end = min(len(text), match.end() + CONTEXT_CHARS)
        context = text[start:end]
        lessons.append((match.group(), context))

    return lessons


def find_lesson_mentions(text: str) -> list[tuple[str, str]]:
    """Find lesson keyword mentions in text and extract surrounding context.

    Issue #2311: This is now the fallback detection method (lower precision).
    Keyword detection triggers warnings, while tag detection triggers blocks.

    Returns:
        List of (keyword, context) tuples where context is the surrounding text.
    """
    lessons: list[tuple[str, str]] = []

    for match in LESSON_PATTERN.finditer(text):
        # Extract context around the match
        start = max(0, match.start() - CONTEXT_CHARS)
        end = min(len(text), match.end() + CONTEXT_CHARS)
        context = text[start:end]
        lessons.append((match.group(), context))

    return lessons


def has_issue_reference(context: str) -> bool:
    """Check if context contains an Issue reference."""
    return bool(ISSUE_REFERENCE_PATTERN.search(context))


def has_resolved_issue_reference(context: str) -> bool:
    """Check if context contains Issue reference with resolved keyword (Issue #2100).

    Returns True if Issue reference (e.g., #1234) and resolved keyword (e.g., 済み, 完了)
    appear in proximity (within 30 characters of each other).

    This indicates the lesson was already addressed and issued,
    so it's a false positive for the lesson check.
    The proximity check prevents false associations between unrelated keywords
    in the same context window.
    """
    # Pattern to match Issue reference and resolved keyword within 30 chars
    combined_pattern_str = (
        f"({ISSUE_REFERENCE_PATTERN.pattern}).{{0,30}}({RESOLVED_PATTERN.pattern})|"
        f"({RESOLVED_PATTERN.pattern}).{{0,30}}({ISSUE_REFERENCE_PATTERN.pattern})"
    )
    combined_pattern = re.compile(combined_pattern_str, re.IGNORECASE | re.DOTALL)
    return bool(combined_pattern.search(context))


def has_negation_context(context: str) -> bool:
    """Check if context contains negation patterns (Issue #2094).

    Returns True if the context indicates no actual lesson was found,
    e.g., "改善点は発見されませんでした" or "問題点: 特になし"
    """
    return bool(NEGATION_PATTERN.search(context))


def has_meta_discussion_context(context: str) -> bool:
    """Check if context is discussing the hook or keywords meta-ly (Issue #2111).

    Returns True if the context indicates discussion ABOUT the detection itself,
    e.g., "誤検知です", "フックがトリガーされている", "これは新しい教訓ではなく..."

    This prevents false positives when explaining why a previous detection was wrong.
    """
    return bool(META_DISCUSSION_PATTERN.search(context))


def has_work_context(context: str) -> bool:
    """Check if context contains work/review context patterns (Issue #2113).

    Returns True if the context indicates the keyword is being discussed
    in the context of work or review, not as an actual lesson discovery.
    e.g., "改善点を修正しました" or "問題点のレビューコメント"
    """
    return bool(WORK_CONTEXT_PATTERN.search(context))


def should_skip_lesson_check_globally(text: str) -> bool:
    """Check if lesson check should be skipped entirely (Issue #2144).

    This is a global check that searches the ENTIRE transcript, not just
    a context window around each keyword. When session continuation markers
    are detected anywhere in the text, skip the lesson check entirely.

    This addresses the limitation of context-based detection where markers
    may be more than 200 characters away from lesson keywords.

    Returns:
        True if session continuation markers are found (skip lesson check).
    """
    return bool(GLOBAL_SESSION_CONTINUATION_PATTERN.search(text))


def get_tags_without_issues(text: str) -> list[str]:
    """Get list of [lesson] tags that don't have Issue references nearby.

    Issue #2311: Tag-based detection (high precision).
    Tags are explicitly placed by Claude, but still apply false positive filters
    for consistency with keyword detection.

    Returns:
        List of "[lesson]" tags that lack Issue references.
    """
    tags = find_lesson_tags(text)
    unissued: list[str] = []

    for tag, context in tags:
        # Issue #2094: Skip if context indicates no actual lesson
        if has_negation_context(context):
            continue
        # Issue #2111: Skip if context is discussing the hook meta-ly
        if has_meta_discussion_context(context):
            continue
        # Issue #2100: Skip if context shows lesson was already resolved and issued
        if has_resolved_issue_reference(context):
            continue
        # Issue #2113: Skip if context shows tag is being discussed as work
        if has_work_context(context):
            continue
        if not has_issue_reference(context):
            unissued.append(tag)

    return unissued


def get_lessons_without_issues(text: str) -> list[str]:
    """Get list of lesson keywords that don't have Issue references nearby.

    Issue #2094: Skip keywords in negation context (e.g., "改善点は発見されませんでした")
    Issue #2100: Skip keywords with resolved Issue reference (e.g., "Issue #2090 として仕組み化済み")
    Issue #2111: Skip keywords in meta-discussion context (e.g., "誤検知です", "フックがトリガー")
    Issue #2113: Skip keywords in work/review context (e.g., "改善点を修正しました")
    Issue #2311: This is now fallback detection (triggers warning, not block).

    Returns:
        List of lesson keywords that lack Issue references.
    """
    lessons = find_lesson_mentions(text)
    unissued: set[str] = set()

    for keyword, context in lessons:
        # Issue #2094: Skip if context indicates no actual lesson
        if has_negation_context(context):
            continue
        # Issue #2100: Skip if context shows lesson was already resolved and issued
        if has_resolved_issue_reference(context):
            continue
        # Issue #2111: Skip if context is discussing the hook/keywords meta-ly
        if has_meta_discussion_context(context):
            continue
        # Issue #2113: Skip if context shows keyword is being discussed as work
        if has_work_context(context):
            continue
        if not has_issue_reference(context):
            unissued.add(keyword)

    return list(unissued)


def main():
    """Main hook logic for Stop event."""
    result = {"decision": "approve"}

    try:
        input_data = parse_hook_input()

        # Get transcript content
        transcript_path = input_data.get("transcript_path", "")
        if not transcript_path:
            log_hook_execution(
                "lesson-issue-check",
                "approve",
                "No transcript path provided",
            )
            print(json.dumps(result))
            return

        try:
            transcript_content = Path(transcript_path).read_text()
        except (OSError, FileNotFoundError):
            log_hook_execution(
                "lesson-issue-check",
                "approve",
                "Could not read transcript",
            )
            print(json.dumps(result))
            return

        # Issue #2144: Global check for session continuation markers
        # Check the RAW transcript before any stripping, because markers may
        # be anywhere in the text (not just near lesson keywords)
        if should_skip_lesson_check_globally(transcript_content):
            log_hook_execution(
                "lesson-issue-check",
                "approve",
                "Session continuation detected (global check)",
            )
            print(json.dumps(result))
            return

        # Issue #2139: Strip system-reminder tags to avoid false positives from documentation
        transcript_content = strip_system_reminders(transcript_content)

        # Issue #2106: Strip code blocks to avoid false positives from test code
        transcript_content = strip_code_blocks(transcript_content)

        # Issue #2120: Strip summary section to avoid false positives from previous session
        transcript_content = strip_summary_section(transcript_content)

        # Issue #2155: Strip Read tool output to avoid false positives from file content
        transcript_content = strip_read_tool_output(transcript_content)

        # Only check if reflection was performed
        if not has_reflection_keywords(transcript_content):
            log_hook_execution(
                "lesson-issue-check",
                "approve",
                "No reflection detected in session",
            )
            print(json.dumps(result))
            return

        # Issue #2311: Hybrid detection approach
        # 1. Tag detection ([lesson]) -> Block (high precision, explicit marking)
        # 2. Keyword detection -> Warning only (low precision, learning promotion)

        # Check for [lesson] tags without Issue references (high precision)
        unissued_tags = get_tags_without_issues(transcript_content)

        if unissued_tags:
            # Found tags without Issue references - block
            # Issue #2137: Add unique marker to prevent self-feedback loop
            reason = (
                f"**振り返りで発見した教訓がIssue化されていません**\n"
                f"<!-- HOOK_BLOCK_MESSAGE:lesson-issue-check -->\n\n"
                f"検出: `[lesson]` タグ（{len(unissued_tags)}件）にIssue参照がありません\n\n"
                f"教訓を発見したら、必ずIssue化してください:\n"
                f"```bash\n"
                f'gh issue create --title "fix: [教訓の内容]" --label "bug,P2" --body "[詳細]"\n'
                f"```\n\n"
                f"**重要**: AGENTS.mdの「仕組み化 = ドキュメント + 強制機構」に従い、\n"
                f"教訓はドキュメント追加だけでなく、フック/CI等の強制機構まで実装してください。"
            )

            result = make_block_result("lesson-issue-check", reason)
            log_hook_execution(
                "lesson-issue-check",
                "block",
                f"[lesson] tags without Issue: {len(unissued_tags)}",
            )
            print(json.dumps(result))
            return

        # Check for keyword mentions without Issue references (low precision)
        # This only produces a warning, not a block
        unissued_lessons = get_lessons_without_issues(transcript_content)

        if unissued_lessons:
            # Found keywords without Issue references - warn only (not block)
            keywords_str = "、".join(unissued_lessons[:3])  # Show first 3
            if len(unissued_lessons) > 3:
                keywords_str += f" 他{len(unissued_lessons) - 3}件"

            # Issue #2311: Warning message (not blocking)
            # Keyword detection is low precision, so we only warn to avoid false positive blocks
            warning_msg = (
                f"[lesson-issue-check] 警告: キーワード検出（{keywords_str}）\n"
                f"教訓がある場合は振り返りで `[lesson]` タグを使用してください。\n"
                f"例: [lesson] 根本原因を調査せずに表面的な対応をした → Issue #xxx"
            )
            print(warning_msg, file=sys.stderr)

            log_hook_execution(
                "lesson-issue-check",
                "approve",
                f"Keyword warning (not block): {keywords_str}",
            )
            print(json.dumps(result))
            return

        log_hook_execution(
            "lesson-issue-check",
            "approve",
            "All lessons have Issue references",
        )
        print(json.dumps(result))
        return

    except Exception as e:
        print(f"[lesson-issue-check] Error: {e}", file=sys.stderr)
        log_hook_execution("lesson-issue-check", "error", f"Hook error: {e}")
        # Don't block on errors
        result = {"decision": "approve"}

    print(json.dumps(result))


if __name__ == "__main__":
    main()
