#!/usr/bin/env python3
"""
レビュー返信で「別Issue対応」と約束した場合のIssue作成追跡フック。

Issue #1437: レビューコメントへの返信で「別Issue」「今後の改善」等と言いながら
Issue作成を忘れるケースを防止する。

動作:
1. PostToolUse: レビュースレッド返信で約束パターンを検出 → 記録
2. PostToolUse: gh issue create を検出 → 約束を解消
3. Stop: 未解消の約束があれば警告
"""

import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from common import SESSION_DIR
from lib.execution import log_hook_execution
from lib.results import make_approve_result
from lib.session import HookContext, create_hook_context, parse_hook_input

# 「別Issueで対応」を示唆するパターン
# Note: More specific patterns (e.g., "このPRの範囲外") must come before
# general patterns (e.g., "範囲外") to match correctly
PROMISE_PATTERNS = [
    r"別[Ii]ssue",
    r"今後の改善",
    r"将来対応",
    r"スコープ外",
    r"このPRの範囲外",
    r"範囲外",
    r"別途対応",
    r"後で対応",
]

# グローバルコンテキスト（Issue #2545: HookContextパターン移行）
_ctx: HookContext | None = None


def get_promise_file() -> Path:
    """Get the promise tracking file path for current session.

    Issue #2545: HookContextパターンに移行。グローバルの_ctxからsession_idを取得。
    """
    session_id = _ctx.get_session_id() if _ctx else None
    if not session_id:
        # session_idがない場合はファイル操作をスキップするための特別な値
        session_id = "unknown"
    # Sanitize session_id to prevent path traversal attacks
    safe_filename = Path(session_id).name
    return SESSION_DIR / f"review-promises-{safe_filename}.json"


def load_promises() -> list[dict]:
    """Load recorded promises from session file."""
    promise_file = get_promise_file()
    if promise_file.exists():
        try:
            return json.loads(promise_file.read_text())
        except (json.JSONDecodeError, OSError):
            # Corrupted or unreadable file - return empty list to start fresh
            pass
    return []


def save_promises(promises: list[dict]) -> None:
    """Save promises to session file."""
    promise_file = get_promise_file()
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    promise_file.write_text(json.dumps(promises, ensure_ascii=False, indent=2))


def detect_promise_in_text(text: str) -> str | None:
    """Detect if text contains a promise pattern.

    Returns:
        The matched pattern if found, None otherwise.
    """
    for pattern in PROMISE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return pattern
    return None


def is_review_thread_reply(command: str) -> tuple[bool, str | None]:
    """Check if command is a review thread reply.

    Issue #1444: Detect both GraphQL API and REST API reply patterns.

    Returns:
        Tuple of (is_reply, reply_body).
    """
    # Check for GraphQL API (addPullRequestReviewThreadReply mutation)
    if "addPullRequestReviewThreadReply" in command:
        # Extract body from the GraphQL mutation
        # Issue #1444: Handle escaped quotes in body content
        # Pattern handles: body: "text with \"escaped\" quotes"
        body_match = re.search(
            r'body:\s*"((?:[^"\\]|\\.)*)"|body:\s*\'((?:[^\'\\]|\\.)*)\'',
            command,
            re.DOTALL,
        )
        if body_match:
            # Return whichever group matched (double or single quotes)
            body = body_match.group(1) or body_match.group(2)
            # Unescape the content
            if body:
                body = body.replace('\\"', '"').replace("\\'", "'")
            return True, body
        return True, None

    # Check for REST API (review-respond.py uses /comments/.../replies)
    # Issue #1444: Also detect REST API reply patterns
    if "/comments/" in command and "/replies" in command:
        # Extract body from REST API call
        # Patterns: --body "...", -b "...", --field body="...", -f body="...", "body": "..."
        body_match = re.search(
            r'(?:--body|-b)\s+"((?:[^"\\]|\\.)*)"|(?:--body|-b)\s+\'((?:[^\'\\]|\\.)*)\''
            r'|(?:--field|-f)\s+body="((?:[^"\\]|\\.)*)"|(?:--field|-f)\s+body=\'((?:[^\'\\]|\\.)*)\''
            r'|"body":\s*"((?:[^"\\]|\\.)*)"',
            command,
            re.DOTALL,
        )
        if body_match:
            body = (
                body_match.group(1)
                or body_match.group(2)
                or body_match.group(3)
                or body_match.group(4)
                or body_match.group(5)
            )
            if body:
                body = body.replace('\\"', '"').replace("\\'", "'")
            return True, body
        return True, None

    return False, None


def is_issue_create(command: str) -> bool:
    """Check if command creates an issue.

    Issue #1444: Only match when gh issue create appears as an actual command
    execution at a recognized command boundary. The regex handles:
    - Start of command (^)
    - After shell operators (&&, ||, ;, |)
    - Inside subshell or command substitution ($(, ()
    - After control flow keywords (if, then, else, do, {)

    This correctly rejects:
    - echo "gh issue create" (literal string in echo)
    - VAR="gh issue create" (literal string in assignment)
    - # gh issue create (comment)

    And correctly accepts:
    - gh issue create --title foo
    - echo done && gh issue create
    - TOKEN="secret" gh issue create (env var prefix)
    - if gh issue create; then ...
    """
    stripped = command.strip()
    # Skip comments
    if stripped.startswith("#"):
        return False

    # Match gh issue create at command boundaries
    # - Start of command
    # - After shell operators: &&, ||, ;, |
    # - Inside subshell: $(, (
    # - After control flow keywords: if, then, else, do, etc.
    # - After env var assignment: VAR="value" or VAR=value followed by space
    return bool(
        re.search(
            r"(?:"
            r"(?:^|&&|\|\||;|\||\$\(|\(|\bif\s+|\bthen\s+|\belse\s+|\bdo\s+|\b\{\s*)\s*"
            r"|"
            # Env var assignment: VAR="val" or VAR='val' or VAR=val followed by space
            r'[A-Za-z_][A-Za-z0-9_]*=(?:"[^"]*"|\'[^\']*\'|[^\s"\']+)\s+'
            r")"
            r"gh\s+issue\s+create\b",
            command,
        )
    )


def record_promise(reply_body: str, pattern: str) -> None:
    """Record a promise made in a review reply."""
    promises = load_promises()
    promises.append(
        {
            "timestamp": datetime.now(UTC).isoformat(),
            "pattern": pattern,
            "excerpt": reply_body[:100] if reply_body else "",
            "resolved": False,
        }
    )
    save_promises(promises)
    log_hook_execution(
        "review-promise-tracker",
        "info",
        f"Promise recorded: {pattern}",
    )


def resolve_promise() -> None:
    """Mark the most recent unresolved promise as resolved."""
    promises = load_promises()
    for promise in reversed(promises):
        if not promise.get("resolved"):
            promise["resolved"] = True
            promise["resolved_at"] = datetime.now(UTC).isoformat()
            save_promises(promises)
            log_hook_execution(
                "review-promise-tracker",
                "info",
                "Promise resolved by issue creation",
            )
            return


def get_unresolved_promises() -> list[dict]:
    """Get all unresolved promises."""
    promises = load_promises()
    return [p for p in promises if not p.get("resolved")]


def main():
    """PostToolUse/Stop hook for tracking review promises."""
    global _ctx
    try:
        data = parse_hook_input()
        # Issue #2545: HookContextパターンでsession_idを取得
        _ctx = create_hook_context(data)

        hook_type = data.get("hook_type", "")
        tool_name = data.get("tool_name", "")
        tool_input = data.get("tool_input", {})

        # Stop hook: Check for unresolved promises
        if hook_type == "Stop":
            unresolved = get_unresolved_promises()
            if unresolved:
                patterns = [p["pattern"] for p in unresolved]
                log_hook_execution(
                    "review-promise-tracker",
                    "warn",
                    f"Unresolved promises: {patterns}",
                )
                # Output warning via systemMessage
                result = {
                    "decision": "approve",
                    "systemMessage": (
                        f"⚠️ レビュー返信で「別Issue対応」と約束しましたが、"
                        f"Issue作成が確認できません（{len(unresolved)}件）。\n\n"
                        "約束したパターン:\n"
                        + "\n".join(f"- {p['pattern']}" for p in unresolved)
                        + "\n\n`gh issue create` でIssueを作成してください。"
                    ),
                }
                print(json.dumps(result))
                sys.exit(0)
            # No unresolved promises
            sys.exit(0)

        # PostToolUse: Track promises and resolutions
        if hook_type == "PostToolUse" and tool_name == "Bash":
            command = tool_input.get("command", "")

            # Check for review thread reply with promise
            is_reply, reply_body = is_review_thread_reply(command)
            if is_reply and reply_body:
                pattern = detect_promise_in_text(reply_body)
                if pattern:
                    record_promise(reply_body, pattern)
            # Issue #1444: Use elif for mutual exclusivity - a command is either
            # a review reply or an issue creation, not both
            elif is_issue_create(command):
                resolve_promise()

        # Default: approve
        result = make_approve_result("review-promise-tracker", "OK")
        print(json.dumps(result))
        sys.exit(0)

    except Exception as e:
        # Fail open - log details but don't leak to output
        log_hook_execution("review-promise-tracker", "error", str(e))
        result = make_approve_result("review-promise-tracker", "Error: An internal error occurred")
        print(json.dumps(result))
        sys.exit(0)


if __name__ == "__main__":
    main()
