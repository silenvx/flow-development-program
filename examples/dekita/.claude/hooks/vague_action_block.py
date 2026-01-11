#!/usr/bin/env python3
"""曖昧な対策表現（精神論）を検出してブロック。

Why:
    「注意する」「気をつける」「徹底する」といった精神論は再発防止策にならない。
    具体的な仕組み化（フック/CI/スクリプト）を強制することで実効性を担保する。

What:
    - セッション終了時（Stop）に発火
    - transcriptから対策文脈の曖昧表現を検出
    - 「守る」「注意」「心がけ」等のパターンをマッチング
    - 曖昧表現があればACTION_REQUIREDで警告

Remarks:
    - 警告型フック（ACTION_REQUIRED、ブロックはしない）
    - 具体的アクション（Issue作成、フック実装等）があればスキップ
    - systematization-checkは教訓→仕組み化要求、本hookは表現自体を検出

Changelog:
    - silenvx/dekita#1959: フック追加
    - silenvx/dekita#2026: exit code 0でACTION_REQUIRED形式に変更
"""

from __future__ import annotations

import json
import re
import sys

from lib.execution import log_hook_execution
from lib.session import parse_hook_input
from lib.transcript import load_transcript

# Vague action patterns in countermeasure context
# Note: Japanese verb conjugations handled with flexible patterns
VAGUE_ACTION_PATTERNS = [
    # 対策/改善/今後 + 守る/遵守/徹底 (守り/守ります等も含む)
    r"(?:対策|改善|今後).*(?:守[るりっ]|遵守|徹底)",
    # 対策/改善/今後 + 注意/気をつけ/意識/心がけ
    r"(?:対策|改善|今後).*(?:注意|気をつけ|意識|心がけ)",
    # ガイド/ルール + 守る/遵守/徹底/従う (守り/守ります等も含む)
    r"(?:ガイド|ルール|規約|方針).*(?:守[るりっ]|遵守|徹底|従[うい])",
    # 〜を意識する/心がける (standalone)
    r"(?:を|に)(?:意識|心がけ|注意)(?:する|します|していく)",
    # 確認を徹底する
    r"確認.*徹底",
]

# Compiled patterns for performance
COMPILED_VAGUE_PATTERNS = [re.compile(p) for p in VAGUE_ACTION_PATTERNS]

# Countermeasure context indicators
COUNTERMEASURE_CONTEXT_PATTERNS = [
    r"対策",
    r"改善(?:点|策)?",
    r"今後(?:は|の)",
    r"再発防止",
    r"防止策",
    r"反省点",
]

# Patterns that indicate concrete actions (NOT vague)
CONCRETE_ACTION_PATTERNS = [
    r"Issue\s*(?:#|\d|を)",  # Issue作成 (Issue #123, Issue 123, Issueを)
    r"フック(?:を|作成|追加)",  # フック作成
    r"hook(?:を|作成|追加)",
    r"CI(?:を|に|で)(?:追加|チェック)",  # CI追加
    r"スクリプト(?:を|作成)",  # スクリプト作成
    r"テスト(?:を|追加|作成)",  # テスト追加
    r"コード(?:を)?(?:修正|変更)",  # コード修正
    r"実装(?:する|します|しました)",  # 実装
    r"作成(?:する|します|しました)",  # 作成
    r"修正(?:する|します|しました)",  # 修正
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


def is_in_countermeasure_context(text: str, match_start: int, match_end: int) -> bool:
    """Check if the match is within countermeasure context.

    Looks for countermeasure indicators:
    1. Within 100 characters before the match, OR
    2. Within the matched text itself (for patterns that include context)
    """
    # Check context before the match
    context_start = max(0, match_start - 100)
    context_before = text[context_start:match_start]
    for pattern in COUNTERMEASURE_CONTEXT_PATTERNS:
        if re.search(pattern, context_before):
            return True

    # Check within the matched text (patterns may include context indicators)
    matched_text = text[match_start:match_end]
    for pattern in COUNTERMEASURE_CONTEXT_PATTERNS:
        if re.search(pattern, matched_text):
            return True

    return False


def has_concrete_action(text: str) -> bool:
    """Check if the text also contains concrete action patterns."""
    for pattern in CONCRETE_ACTION_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def find_vague_patterns(messages: list[str]) -> list[str]:
    """Find vague action patterns in messages within countermeasure context.

    Only detects patterns when they appear in countermeasure context
    (対策, 改善, 反省点, etc.) to avoid false positives.

    Returns list of excerpts containing vague patterns.
    """
    vague_excerpts = []

    for msg in messages:
        # Skip if message contains concrete actions
        if has_concrete_action(msg):
            continue

        for pattern in COMPILED_VAGUE_PATTERNS:
            match = pattern.search(msg)
            if match:
                # Only block if in countermeasure context
                if not is_in_countermeasure_context(msg, match.start(), match.end()):
                    continue

                # Extract surrounding context
                start = max(0, match.start() - 20)
                end = min(len(msg), match.end() + 20)
                excerpt = msg[start:end].strip()
                if start > 0:
                    excerpt = "..." + excerpt
                if end < len(msg):
                    excerpt = excerpt + "..."
                vague_excerpts.append(excerpt)
                break  # Only count once per message

    return vague_excerpts


def main():
    """Stop hook to detect vague action expressions.

    Uses exit code 0 with ACTION_REQUIRED message to allow continuation.
    Claude Code will see the message and take action autonomously.
    """
    result = {"decision": "approve"}

    try:
        input_data = parse_hook_input()

        # Skip if stop hook is already active (prevents infinite loops)
        if input_data.get("stop_hook_active"):
            log_hook_execution("vague-action-block", "approve", "stop_hook_active")
            print(json.dumps(result))
            return

        transcript_path = input_data.get("transcript_path")
        if not transcript_path:
            log_hook_execution("vague-action-block", "approve", "no transcript path")
            print(json.dumps(result))
            return

        transcript = load_transcript(transcript_path)
        if not transcript:
            log_hook_execution("vague-action-block", "approve", "transcript load failed")
            print(json.dumps(result))
            return

        claude_messages = extract_claude_messages(transcript)
        vague_excerpts = find_vague_patterns(claude_messages)

        if vague_excerpts:
            excerpt_text = "\n".join(f"  - {e[:80]}" for e in vague_excerpts[:3])
            if len(vague_excerpts) > 3:
                excerpt_text += f"\n  - ... 他 {len(vague_excerpts) - 3} 件"

            # ACTION_REQUIRED format for Claude Code to take autonomous action
            reason = (
                f"[ACTION_REQUIRED: CONCRETE_ACTION]\n"
                f"曖昧な対策表現を検出しました。\n\n"
                f"**検出された表現** ({len(vague_excerpts)}件):\n{excerpt_text}\n\n"
                "「ガイドを守る」「注意する」は対策ではありません。\n"
                "Claude Codeは以下のいずれかを実行してください:\n"
                "1. フック作成（違反をブロック）\n"
                "2. CI追加（自動チェック）\n"
                "3. スクリプト作成（自動化）\n"
                "4. コード修正（根本対応）\n"
                "5. Issue作成（追跡可能な形で記録）\n\n"
                "精神論（「注意する」「気をつける」「徹底する」）は禁止です。"
            )
            log_hook_execution(
                "vague-action-block",
                "warn",
                f"vague patterns: {len(vague_excerpts)}",
            )
            # Issue #2026: Use exit code 0 to allow continuation
            # Print ACTION_REQUIRED to stderr for Claude Code to see and act on
            print(f"[vague-action-block] {reason}", file=sys.stderr)
            # Continue with approve instead of blocking
        else:
            log_hook_execution("vague-action-block", "approve", "no vague patterns")

    except Exception as e:
        log_hook_execution("vague-action-block", "approve", f"error: {e}")
        print(f"[vague-action-block] Error: {e}", file=sys.stderr)

    print(json.dumps(result))


if __name__ == "__main__":
    main()
