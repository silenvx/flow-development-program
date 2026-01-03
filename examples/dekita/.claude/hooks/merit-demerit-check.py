#!/usr/bin/env python3
"""AskUserQuestionの選択肢にメリット/デメリット分析が含まれているか確認する。

Why:
    選択肢を提示する際、メリット/デメリット/コストの説明がないと
    ユーザーが適切な判断を下せない。十分な情報提供を強制する。

What:
    - AskUserQuestionツールの呼び出しを検出
    - 各選択肢のlabel/descriptionにメリット・デメリット・コストを確認
    - 3つのうち2つ以上がない場合はブロック

Remarks:
    - ブロック型フック（説明不足時はブロック）
    - PreToolUse:AskUserQuestionで発火
    - [fact-check]/[事実確認]タグで事実確認質問はスキップ可能
    - 2選択肢未満の場合は判定せずスキップ

Changelog:
    - silenvx/dekita#1894: フック追加
    - silenvx/dekita#2237: ブロック型に変更
    - silenvx/dekita#2305: 事実確認タグでスキップ機能追加
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Add hooks directory to path for common imports
sys.path.insert(0, str(Path(__file__).parent))

from lib.execution import log_hook_execution
from lib.results import make_approve_result, make_block_result
from lib.session import parse_hook_input

HOOK_NAME = "merit-demerit-check"

# Keywords indicating merit/demerit analysis is present
MERIT_KEYWORDS_JA = [
    "メリット",
    "利点",
    "長所",
    "良い点",
    "利便性",
    "強み",
]

DEMERIT_KEYWORDS_JA = [
    "デメリット",
    "欠点",
    "短所",
    "問題点",
    "リスク",
    "弱み",
    "懸念",
]

COST_KEYWORDS_JA = [
    "コスト",
    "実装コスト",
    "運用コスト",
    "工数",
    "負担",
    # "複雑" is too general - use specific patterns instead
    "実装が複雑",
    "構成が複雑",
    "複雑性",
    "複雑になる",
]

MERIT_KEYWORDS_EN = [
    "merit",
    "advantage",
    "benefit",
    "pros",
    "strength",
    "upside",
]

DEMERIT_KEYWORDS_EN = [
    "demerit",
    "disadvantage",
    "drawback",
    "cons",
    "weakness",
    "downside",
    "risk",
    "concern",
]

COST_KEYWORDS_EN = [
    "cost",
    # "implementation cost" removed - "cost" alone is sufficient
    "maintenance",
    "complexity",
    "overhead",
    "effort",
]

# Minimum number of options to trigger the check
MIN_OPTIONS_FOR_CHECK = 2

# Regex pattern to skip merit/demerit check (Issue #2305)
# Only matches tags at the beginning or end of the question text
# to prevent unintentional bypasses via embedded tag strings.
FACT_CHECK_REGEX = re.compile(
    r"^\s*(?:\[fact-check\]|\[事実確認\])|(?:\[fact-check\]|\[事実確認\])\s*$",
    re.IGNORECASE,
)


def is_fact_check_question(question_text: str) -> bool:
    """Check if question contains fact-check skip tag at start or end.

    Issue #2305: Allow skipping merit/demerit check for fact-checking questions.
    Only matches tags at the beginning or end of the question to prevent
    unintentional bypasses (security improvement per Gemini review).

    Args:
        question_text: The question text to check.

    Returns:
        True if the question has a fact-check tag at the start or end.
    """
    return bool(FACT_CHECK_REGEX.search(question_text))


def _match_any_word_boundary(keywords: list[str], text: str) -> bool:
    """Check if any keyword exists as a whole word in text.

    Uses word boundary matching to prevent false positives like
    'pros' matching 'prospective' or 'cons' matching 'consider'.
    Combines all keywords into a single regex pattern for efficiency.

    Args:
        keywords: List of keywords to search for.
        text: Text to search in (matched case-insensitively).

    Returns:
        True if any keyword found as a whole word.
    """
    if not keywords:
        return False
    # Combine keywords into single pattern with | for efficiency
    pattern = r"\b(" + "|".join(map(re.escape, keywords)) + r")\b"
    return bool(re.search(pattern, text, re.IGNORECASE))


def has_merit_context(text: str) -> bool:
    """Check if text contains merit-related keywords.

    Args:
        text: Text to check (option label + description).

    Returns:
        True if merit context is present.
    """
    # Japanese keywords: substring match (no case concept, no word boundaries)
    if any(keyword in text for keyword in MERIT_KEYWORDS_JA):
        return True

    # English keywords: word boundary match to prevent false positives
    return _match_any_word_boundary(MERIT_KEYWORDS_EN, text)


def has_demerit_context(text: str) -> bool:
    """Check if text contains demerit-related keywords.

    Args:
        text: Text to check (option label + description).

    Returns:
        True if demerit context is present.
    """
    # Japanese keywords: substring match (no case concept, no word boundaries)
    if any(keyword in text for keyword in DEMERIT_KEYWORDS_JA):
        return True

    # English keywords: word boundary match to prevent false positives
    return _match_any_word_boundary(DEMERIT_KEYWORDS_EN, text)


def has_cost_context(text: str) -> bool:
    """Check if text contains cost-related keywords.

    Args:
        text: Text to check (option label + description).

    Returns:
        True if cost context is present.
    """
    # Japanese keywords: substring match (no case concept, no word boundaries)
    if any(keyword in text for keyword in COST_KEYWORDS_JA):
        return True

    # English keywords: word boundary match to prevent false positives
    return _match_any_word_boundary(COST_KEYWORDS_EN, text)


def analyze_options(options: list[dict]) -> dict:
    """Analyze options for merit/demerit/cost coverage.

    Args:
        options: List of option dictionaries with 'label' and 'description'.

    Returns:
        Analysis result with coverage status.
    """
    result = {
        "total_options": len(options),
        "has_merit": False,
        "has_demerit": False,
        "has_cost": False,
        "options_without_context": [],
    }

    for opt in options:
        label = opt.get("label", "")
        description = opt.get("description", "")
        combined_text = f"{label} {description}"

        opt_has_merit = has_merit_context(combined_text)
        opt_has_demerit = has_demerit_context(combined_text)
        opt_has_cost = has_cost_context(combined_text)

        result["has_merit"] |= opt_has_merit
        result["has_demerit"] |= opt_has_demerit
        result["has_cost"] |= opt_has_cost

        # Track options without any context
        if not (opt_has_merit or opt_has_demerit or opt_has_cost):
            truncated_label = label[:30] + "..." if len(label) > 30 else label
            result["options_without_context"].append(truncated_label)

    return result


def format_block_message(analysis: dict, question: str) -> str:
    """Format block message for missing context.

    Args:
        analysis: Analysis result from analyze_options.
        question: The question being asked.

    Returns:
        Formatted block message.
    """
    missing = []
    if not analysis["has_merit"]:
        missing.append("メリット/利点")
    if not analysis["has_demerit"]:
        missing.append("デメリット/欠点")
    if not analysis["has_cost"]:
        missing.append("コスト/工数")

    options_info = ""
    if analysis["options_without_context"]:
        options_info = "\n詳細不足の選択肢: " + ", ".join(analysis["options_without_context"])

    truncated_question = question[:50] + "..." if len(question) > 50 else question

    return f"""🚫 選択肢の説明が不十分なためブロックしました。

質問: {truncated_question}

不足している観点: {", ".join(missing)}{options_info}

【必須】各選択肢のdescriptionに以下を追記してください:
- メリット/利点（例: 確実に対応される、フローを止めずに改善を促せる）
- デメリット/リスク（例: 軽微なケースでも止まる、強制力がない）
- コスト/工数（例: 実装不要、Claude側の対応ロジックが必要）

💡 ブロック後も作業を継続してください。
   AskUserQuestionを修正して再度呼び出してください。"""


def main() -> None:
    """Main entry point for the hook."""
    try:
        input_data = parse_hook_input()
    except json.JSONDecodeError:
        # Invalid input - approve silently
        print(json.dumps({"decision": "approve"}))
        return

    tool_name = input_data.get("tool_name", "")

    # Only check AskUserQuestion
    if tool_name != "AskUserQuestion":
        print(json.dumps({"decision": "approve"}))
        return

    tool_input = input_data.get("tool_input", {})
    questions = tool_input.get("questions", [])

    if not questions:
        print(json.dumps({"decision": "approve"}))
        return

    # Check each question's options
    block_messages = []
    fact_check_skip_count = 0
    sufficient_context_count = 0

    for q in questions:
        options = q.get("options", [])
        question_text = q.get("question", "")

        # Skip if fewer than 2 options (not a real choice)
        if len(options) < MIN_OPTIONS_FOR_CHECK:
            continue

        # Issue #2305: Skip fact-check questions
        if is_fact_check_question(question_text):
            fact_check_skip_count += 1
            continue

        analysis = analyze_options(options)

        # Check if sufficient context is provided
        # Require at least 2 of 3 categories to be covered
        coverage_count = sum(
            [
                analysis["has_merit"],
                analysis["has_demerit"],
                analysis["has_cost"],
            ]
        )

        if coverage_count < 2:
            block_messages.append(format_block_message(analysis, question_text))
        else:
            sufficient_context_count += 1

    # Block if options lack sufficient context
    if block_messages:
        # Combine all block messages
        # Note: make_block_result calls log_hook_execution internally (Issue #2023)
        combined_message = "\n\n".join(block_messages)
        result = make_block_result(HOOK_NAME, combined_message)
    else:
        # Build accurate log message (Issue #2305: Copilot review feedback)
        if fact_check_skip_count > 0 and sufficient_context_count > 0:
            reason = "一部事実確認タグでスキップ、残りは選択肢に十分な説明あり"
        elif fact_check_skip_count > 0:
            reason = "事実確認タグでスキップ"
        else:
            reason = "選択肢に十分な説明あり"
        log_hook_execution(
            HOOK_NAME,
            "approve",
            reason=reason,
        )
        result = make_approve_result(HOOK_NAME)

    print(json.dumps(result))


if __name__ == "__main__":
    main()
