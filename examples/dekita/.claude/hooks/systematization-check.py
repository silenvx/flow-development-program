#!/usr/bin/env python3
"""セッション終了時に教訓が仕組み化されたか確認する。

Why:
    教訓をドキュメント化しただけでは、同じ問題が再発する。
    フック/CI/ツールによる仕組み化を強制することで、再発を防止する。

What:
    - 教訓パターン検出（教訓、反省点、学び、気づき、lesson learned等）
    - 仕組み化ファイル変更検出（.claude/hooks/*.py、.github/workflows/*.yml等）
    - 教訓あり＆仕組み化なしの場合にブロック
    - 誤検知緩和（複数インジケータ要求、明示的スキップ許可）

Remarks:
    - problem-report-checkはIssue作成を確認、本フックは仕組み化を確認
    - 強パターン（「仕組み化が必要」等）は単独でもトリガー
    - 「仕組み化しました」等の完了パターンは誤検知として除外

Changelog:
    - silenvx/dekita#468: フック追加
"""

import json
import re
import sys

from lib.execution import log_hook_execution
from lib.session import parse_hook_input
from lib.transcript import load_transcript

# Lesson/learning patterns in Japanese
LESSON_PATTERNS_JA = [
    r"教訓として",
    r"反省点として",
    r"反省点は",
    r"学びとして",
    r"学んだこと",
    r"気づいた(?:こと|点)",
    r"今後は.*(?:する|しない)(?:べき|必要)",
    r"(?:再発)?防止(?:策)?(?:として|は)",
    r"次回(?:から)?は",
    r"改善(?:点|策|が必要)",
]

# Lesson/learning patterns in English
LESSON_PATTERNS_EN = [
    r"lesson(?:s)?\s+learned",
    r"key\s+takeaway",
    r"should\s+have\s+(?!been\s+(?:done|completed))",  # "should have" but not completion
    r"in\s+the\s+future",
    r"next\s+time",
    r"going\s+forward",
    r"to\s+prevent\s+this",
    r"to\s+avoid\s+this",
]

ALL_LESSON_PATTERNS = LESSON_PATTERNS_JA + LESSON_PATTERNS_EN

# Strong indicators that definitely need systematization
STRONG_LESSON_PATTERNS = [
    r"仕組み化(?:する|が必要|すべき)",
    r"hook(?:を|で|が)",
    r"フック(?:を|で|が)",
    r"CI(?:で|に|を)",
    r"自動化(?:する|が必要|すべき)",
]

# Patterns that indicate false positives
FALSE_POSITIVE_PATTERNS = [
    r"仕組み化(?:しました|済み|完了)",
    r"hook(?:を)?(?:作成|追加|実装)(?:しました|済み|完了)",
    r"フック(?:を)?(?:作成|追加|実装)(?:しました|済み|完了)",
    r"対応不要",
    r"アクション不要",
    r"no\s+action\s+needed",
    r"already\s+(?:implemented|done|addressed)",
]

# Systematization file patterns
SYSTEMATIZATION_PATTERNS = [
    r"\.claude/hooks/.*\.py$",
    r"\.github/workflows/.*\.ya?ml$",
    r"\.claude/scripts/.*\.(?:py|sh)$",
]


def extract_claude_messages(transcript: list[dict]) -> list[str]:
    """Extract text from Claude's messages in the transcript."""
    messages = []
    for entry in transcript:
        if entry.get("role") == "assistant":
            content = entry.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "")
                        if text:
                            messages.append(text)
            elif isinstance(content, str):
                messages.append(content)
    return messages


def extract_file_operations(transcript: list[dict]) -> list[str]:
    """Extract file paths from Edit/Write tool uses."""
    files = []
    for entry in transcript:
        if entry.get("role") == "assistant":
            content = entry.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tool_name = block.get("name", "")
                        if tool_name in ("Edit", "Write"):
                            file_path = block.get("input", {}).get("file_path", "")
                            if file_path:
                                files.append(file_path)
    return files


def has_false_positive(messages: list[str]) -> bool:
    """Check if any message indicates a false positive."""
    for msg in messages:
        for pattern in FALSE_POSITIVE_PATTERNS:
            if re.search(pattern, msg, re.IGNORECASE):
                return True
    return False


def find_lesson_patterns(messages: list[str]) -> tuple[list[str], bool]:
    """Find lesson patterns in Claude's messages.

    Returns:
        Tuple of (list of lesson excerpts, has_strong_pattern)
    """
    lessons = []
    has_strong = False

    for msg in messages:
        # Check strong patterns first
        for pattern in STRONG_LESSON_PATTERNS:
            if re.search(pattern, msg, re.IGNORECASE):
                has_strong = True

        # Check normal lesson patterns
        for pattern in ALL_LESSON_PATTERNS:
            match = re.search(pattern, msg, re.IGNORECASE)
            if match:
                # Extract surrounding context
                start = max(0, match.start() - 30)
                end = min(len(msg), match.end() + 30)
                excerpt = msg[start:end].strip()
                if start > 0:
                    excerpt = "..." + excerpt
                if end < len(msg):
                    excerpt = excerpt + "..."
                lessons.append(excerpt)
                break  # Only count once per message

    return lessons, has_strong


def find_systematization_files(files: list[str]) -> list[str]:
    """Find files that indicate systematization."""
    systematized = []
    for file_path in files:
        for pattern in SYSTEMATIZATION_PATTERNS:
            if re.search(pattern, file_path):
                systematized.append(file_path)
                break
    return systematized


def main():
    """Stop hook to detect lessons learned and request systematization.

    Uses exit code 0 with ACTION_REQUIRED message to allow continuation.
    Claude Code will see the message and take action autonomously.
    """
    result = {"decision": "approve"}

    try:
        input_data = parse_hook_input()

        # Skip if stop hook is already active (prevents infinite loops)
        # When stop_hook_active=True, exit 0 to allow stop and prevent infinite retry
        if input_data.get("stop_hook_active"):
            log_hook_execution("systematization-check", "approve", "stop_hook_active")
            print(json.dumps(result))
            return

        transcript_path = input_data.get("transcript_path")
        if not transcript_path:
            log_hook_execution("systematization-check", "approve", "no transcript path")
            print(json.dumps(result))
            return

        transcript = load_transcript(transcript_path)
        if not transcript:
            log_hook_execution("systematization-check", "approve", "transcript load failed")
            print(json.dumps(result))
            return

        claude_messages = extract_claude_messages(transcript)
        file_operations = extract_file_operations(transcript)

        # Check for false positives first
        if has_false_positive(claude_messages):
            log_hook_execution("systematization-check", "approve", "false positive detected")
            print(json.dumps(result))
            return

        # Find lessons and systematization
        lessons, has_strong = find_lesson_patterns(claude_messages)
        systematized_files = find_systematization_files(file_operations)

        # Decision logic:
        # - If strong lesson pattern AND no systematization -> force continue with exit 2
        # - If 2+ normal lesson patterns AND no systematization -> force continue with exit 2
        # - Otherwise -> approve with warning if lessons detected

        should_block = False
        if has_strong and not systematized_files:
            should_block = True
        elif len(lessons) >= 2 and not systematized_files:
            should_block = True

        if should_block:
            excerpts = lessons[:3]
            excerpt_text = "\n".join(f"  - {e[:80]}" for e in excerpts)
            if len(lessons) > 3:
                excerpt_text += f"\n  - ... 他 {len(lessons) - 3} 件"

            # ACTION_REQUIRED format for Claude Code to take autonomous action
            reason = (
                f"[ACTION_REQUIRED: SYSTEMATIZATION]\n"
                f"教訓が見つかりましたが、仕組み化されていません。\n\n"
                f"**検出された教訓** ({len(lessons)}件):\n{excerpt_text}\n\n"
                "Claude Codeは以下のいずれかを実行してください:\n"
                "1. `.claude/hooks/` にフックを作成してブロック機構を実装\n"
                "2. `.github/workflows/` にCIチェックを追加\n"
                "3. `.claude/scripts/` にスクリプトを作成\n"
                "4. 上記が不要な場合はIssueを作成して理由を記録\n\n"
                "ドキュメント（AGENTS.md等）への追記だけでは不十分です。"
            )
            log_hook_execution(
                "systematization-check",
                "warn",
                f"lessons: {len(lessons)}, strong: {has_strong}, files: {len(systematized_files)}",
            )
            # Issue #2026: Use exit code 0 to allow continuation
            # Print ACTION_REQUIRED to stderr for Claude Code to see and act on
            print(f"[systematization-check] {reason}", file=sys.stderr)
            # Continue with approve instead of blocking
        elif lessons:
            # Lessons detected but systematization also detected
            result["systemMessage"] = (
                f"✅ [systematization-check] 教訓検出: {len(lessons)}件, "
                f"仕組み化ファイル: {len(systematized_files)}件"
            )
            log_hook_execution(
                "systematization-check",
                "approve",
                f"lessons: {len(lessons)}, files: {len(systematized_files)}",
            )
        else:
            log_hook_execution("systematization-check", "approve", "no lessons detected")

    except Exception as e:
        log_hook_execution("systematization-check", "approve", f"error: {e}")
        print(f"[systematization-check] Error: {e}", file=sys.stderr)

    print(json.dumps(result))


if __name__ == "__main__":
    main()
