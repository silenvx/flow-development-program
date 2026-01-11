#!/usr/bin/env python3
"""レビュースレッドResolve時に応答コメントを強制。

Why:
    レビューコメントに返信せずにResolveすると、レビュアーへの説明責任が
    果たされず、対応内容が不明確になる。返信を強制する。

What:
    - resolveReviewThread GraphQL mutationを検出
    - スレッド内にClaude Code応答コメントがあるか確認
    - 応答なしの場合はブロック
    - 修正主張には検証内容（Verified:）を要求
    - 範囲外発言にはIssue番号を要求

Remarks:
    - ブロック型フック（PreToolUse:Bash）
    - batch_resolve_threads.pyの使用を推奨
    - REST APIも併用してコメント取得（GraphQLの遅延対策）
    - fail-open設計（APIエラー時は許可）

Changelog:
    - silenvx/dekita#xxx: フック追加
    - silenvx/dekita#964: 修正主張の検証（Verified:）を追加
    - silenvx/dekita#1018: スレッドレベルの検証に変更
    - silenvx/dekita#1271: REST API併用でコメント取得
    - silenvx/dekita#1332: レビュー品質ログ追加
    - silenvx/dekita#1657: 範囲外発言のIssue番号要求
    - silenvx/dekita#1685: 日本語文字判定を正確化
    - silenvx/dekita#1917: スレッドレベルのIssue参照チェック
    - silenvx/dekita#2023: make_block_result内でlog_hook_execution
"""

import json
import re
import subprocess
from subprocess import TimeoutExpired
from typing import Any

from common import log_review_comment
from lib.constants import TIMEOUT_HEAVY, TIMEOUT_MEDIUM
from lib.execution import log_hook_execution
from lib.results import make_approve_result, make_block_result
from lib.review import identify_reviewer
from lib.session import parse_hook_input

HOOK_NAME = "resolve-thread-guard"


def is_japanese_char(c: str) -> bool:
    """文字が日本語かどうかを判定する。

    Issue #1685: ord(c) > 127 では Latin-1 文字（é, ñ, ü等）も
    日本語として誤判定されるため、正確なUnicode範囲チェックを使用する。

    Args:
        c: 判定する1文字

    Returns:
        日本語文字の場合True

    Raises:
        ValueError: cが長さ1の文字列でない場合
    """
    if len(c) != 1:
        raise ValueError("is_japanese_char expects a single-character string")
    code = ord(c)
    return (
        0x3040 <= code <= 0x309F  # ひらがな
        or 0x30A0 <= code <= 0x30FF  # カタカナ（長音記号ーを含む）
        or 0x4E00 <= code <= 0x9FFF  # CJK統合漢字
        or 0xFF61 <= code <= 0xFF9F  # 半角カタカナ
        or 0x3000 <= code <= 0x303F  # 和文記号・句読点（々を含む）
    )


# Verification patterns (shared between _has_fix_claim_without_verification and _has_verification)
VERIFICATION_PATTERNS = [
    "verified:",
    "検証済み:",
    "確認済み:",
    "verified at",
]

# Issue #1657: Keywords indicating out-of-scope response
# When these keywords are used, an Issue reference is required
OUT_OF_SCOPE_KEYWORDS = [
    "範囲外",
    "スコープ外",
    "将来対応",
    "後でフォローアップ",
    "フォローアップとして",
    "今後の改善",
    "別途対応",
    "out of scope",
    "future improvement",
    "follow-up",
    "follow up",
]


def get_repo_owner_and_name() -> tuple[str, str] | None:
    """Get repository owner and name from git remote.

    Returns:
        Tuple of (owner, name) on success, None on failure.
        Fails open (returns None) on any error to avoid blocking operations.
    """
    try:
        result = subprocess.run(
            ["gh", "repo", "view", "--json", "owner,name"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        return data.get("owner", {}).get("login"), data.get("name")
    except TimeoutExpired:
        return None
    except json.JSONDecodeError:
        return None
    except OSError:
        return None
    except (AttributeError, TypeError, KeyError):
        # Handle unexpected data shapes (e.g., json.loads returns non-dict)
        return None


def extract_thread_id(command: str) -> str | None:
    """Extract thread ID from resolveReviewThread mutation.

    Handles various formats:
    - threadId: "PRRT_xxx"
    - threadId: \\"PRRT_xxx\\"
    - {threadId: "PRRT_xxx"}
    - -F threadId=PRRT_xxx (gh CLI standard)
    - -f threadId=PRRT_xxx (gh CLI standard)
    """
    # Pattern to match threadId in various quote styles
    patterns = [
        r"-[Ff]\s+threadId=([^\s\"']+)",  # -F threadId=xxx or -f threadId=xxx
        r'-[Ff]\s+threadId=["\']([^"\']+)["\']',  # -F threadId="xxx" or -f threadId='xxx'
        r'threadId:\s*["\']([^"\']+)["\']',  # threadId: "xxx" or threadId: 'xxx'
        r'threadId:\s*\\"([^"\\]+)\\"',  # threadId: \"xxx\"
        r'"threadId"\s*:\s*"([^"]+)"',  # "threadId": "xxx" (JSON style)
    ]

    for pattern in patterns:
        match = re.search(pattern, command)
        if match:
            return match.group(1)

    return None


def _has_claude_code_signature(body: str) -> bool:
    """Check if comment body contains Claude Code signature.

    Uses line-level matching to avoid false positives in code blocks or quotes.
    The signature must appear at the start of a line (possibly with leading whitespace).

    Args:
        body: The comment body text to check.

    Returns:
        True if the signature is found in a valid position.
    """
    for line in body.splitlines():
        # Check if line is exactly the signature (with optional leading/trailing whitespace)
        stripped = line.strip()
        # Only match exact signature, not "-- Claude Code is awesome" etc.
        if stripped == "-- Claude Code":
            return True
    return False


def _has_fix_claim_without_verification(body: str) -> bool:
    """Check if comment claims a fix but lacks verification.

    A "fix claim" is when the comment contains phrases like:
    - 修正済み / 対応済み (Japanese)
    - Fixed: / Added / Updated / Changed / Implemented (English)

    A "verification" is when the comment contains:
    - Verified: / 検証済み: / 確認済み:

    Note: Patterns aligned with merge-check.py FIX_CLAIM_KEYWORDS.

    Args:
        body: The comment body text to check.

    Returns:
        True if there's a fix claim without verification.
    """
    body_lower = body.lower()

    # Check for fix claims (aligned with merge-check.py FIX_CLAIM_KEYWORDS)
    # Japanese patterns work with body_lower since .lower() doesn't change them
    fix_patterns = [
        "fixed:",
        "already addressed:",
        "added ",
        "updated ",
        "changed ",
        "implemented ",
        "修正済み",
        "対応済み",
    ]
    has_fix_claim = any(pattern in body_lower for pattern in fix_patterns)

    if not has_fix_claim:
        return False  # No fix claim, no need for verification

    # Check for verification (using shared constant)
    has_verification = any(pattern in body_lower for pattern in VERIFICATION_PATTERNS)

    return not has_verification  # True if fix claim but no verification


def _has_verification(body: str) -> bool:
    """Check if comment body contains verification.

    Args:
        body: The comment body text to check.

    Returns:
        True if verification pattern is found.
    """
    body_lower = body.lower()
    return any(pattern in body_lower for pattern in VERIFICATION_PATTERNS)


def _has_out_of_scope_without_issue(body: str) -> tuple[bool, str | None]:
    """Check if comment has out-of-scope keyword without Issue reference.

    Issue #1657: When Claude marks something as "out of scope", it must
    create a follow-up Issue first. This prevents the common mistake of
    deferring work without proper tracking.

    Args:
        body: The comment body text to check.

    Returns:
        Tuple of (has_problem, detected_keyword):
        - has_problem: True if out-of-scope keyword found without Issue reference
        - detected_keyword: The keyword that was detected (for error message)
    """
    body_lower = body.lower()

    # Find which out-of-scope keyword is present
    # Use word boundary matching for English keywords to avoid false positives
    # e.g., "follow up" should not match "following update"
    detected_keyword = None
    for keyword in OUT_OF_SCOPE_KEYWORDS:
        keyword_lower = keyword.lower()
        # Japanese keywords: use simple substring matching (no word boundaries in Japanese)
        # English keywords: use word boundary regex
        if any(is_japanese_char(c) for c in keyword):
            # Japanese: simple substring match
            if keyword_lower in body_lower:
                detected_keyword = keyword
                # NOTE: 最初にマッチしたキーワードのみ報告する設計。
                # 複数報告はノイズとなるため意図的にbreakで終了。
                break
        else:
            # English: word boundary match
            pattern = r"\b" + re.escape(keyword_lower) + r"\b"
            if re.search(pattern, body_lower):
                detected_keyword = keyword
                # NOTE: 最初にマッチしたキーワードのみ報告する設計。
                # 複数報告はノイズとなるため意図的にbreakで終了。
                break

    if not detected_keyword:
        return False, None  # No out-of-scope keyword, no problem

    # Check for Issue reference patterns
    # Patterns: #123, Issue #123, Issue#123, Issue 123
    # Use boundary-aware matching to avoid false positives from:
    # - URL fragments (e.g., https://example.com/page#123)
    # - Markdown headings (e.g., ### 123 Steps)
    issue_pattern = r"(?:^|[^\w#])#(\d+)|[Ii]ssue\s*#?(\d+)"
    has_issue_ref = re.search(issue_pattern, body, re.MULTILINE) is not None

    if has_issue_ref:
        return False, None  # Has Issue reference, no problem

    return True, detected_keyword


def _check_rest_api_replies(
    owner: str, repo: str, pr_number: int, original_comment_id: int
) -> list[dict[str, Any]]:
    """Check for replies via REST API (Issue #1271).

    This function supplements GraphQL query which may not immediately show
    comments added via REST API. By checking both APIs, we ensure consistency.

    Args:
        owner: Repository owner
        repo: Repository name
        pr_number: Pull request number
        original_comment_id: Database ID of the original comment in the thread

    Returns:
        List of reply comments (with 'body' key) found via REST API.
        Empty list on any error (fail-open).
    """
    try:
        # Get all review comments on the PR
        # Note: --paginate outputs multiple JSON arrays (one per page) separated by newlines
        result = subprocess.run(
            [
                "gh",
                "api",
                f"/repos/{owner}/{repo}/pulls/{pr_number}/comments",
                "--paginate",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_HEAVY,
        )

        if result.returncode != 0:
            return []

        # Parse multi-page output: each page is a separate JSON array
        all_comments: list[dict[str, Any]] = []
        for line in result.stdout.strip().split("\n"):
            if line:
                try:
                    page_comments = json.loads(line)
                    if isinstance(page_comments, list):
                        all_comments.extend(page_comments)
                except json.JSONDecodeError:
                    continue  # Skip invalid lines

        # Filter to find replies to the original comment
        replies = [
            comment
            for comment in all_comments
            if comment.get("in_reply_to_id") == original_comment_id
        ]

        return replies

    except TimeoutExpired:
        return []
    except OSError:
        return []
    except (AttributeError, TypeError, KeyError):
        return []


def check_thread_has_response(thread_id: str) -> dict[str, Any]:
    """Check if the thread has a Claude Code response comment.

    This function follows the fail-open principle: any error condition results
    in allowing the operation to proceed (has_response=True).

    Issue #1271: Also checks REST API for replies, as GraphQL may not
    immediately reflect comments added via REST API.

    Returns:
        dict with the following keys:
        - has_response (bool): True if Claude Code response found OR on any error
        - has_unverified_fix (bool): True if there's a fix claim without verification
        - thread_found (bool): True if thread was successfully retrieved
        - original_comment (str): First 100 chars of original comment (only if thread_found)
        - author (str): Author of original comment (only if thread_found)

    Note:
        - Returns {has_response: True, thread_found: False} on API/network errors
        - Returns {has_response: True, thread_found: True} if thread has no comments (edge case)
    """
    # Verify GitHub CLI is working by checking repo access
    repo_info = get_repo_owner_and_name()
    if not repo_info:
        # Fail open
        return {"has_response": True, "has_unverified_fix": False, "thread_found": False}

    # Query to get thread comments
    # Note: Pagination limit of 30 comments is intentional.
    # Review threads rarely exceed 30 comments, and if they do,
    # the Claude Code response is likely within the first 30.
    # This avoids pagination complexity while covering 99%+ of cases.
    # Issue #1332: Added pullRequest.number and databaseId for review quality logging
    query = """
    query($id: ID!) {
      node(id: $id) {
        ... on PullRequestReviewThread {
          id
          isResolved
          pullRequest {
            number
          }
          comments(first: 30) {
            nodes {
              databaseId
              body
              author { login }
            }
          }
        }
      }
    }
    """

    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                "graphql",
                "-f",
                f"query={query}",
                "-F",
                f"id={thread_id}",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_HEAVY,
        )

        if result.returncode != 0:
            # Fail open
            return {"has_response": True, "has_unverified_fix": False, "thread_found": False}

        data = json.loads(result.stdout)
        node = data.get("data", {}).get("node")

        if not node:
            # Fail open
            return {"has_response": True, "has_unverified_fix": False, "thread_found": False}

        comments = node.get("comments", {}).get("nodes", [])

        # Issue #1332: Extract PR number for review quality logging
        pr_number = node.get("pullRequest", {}).get("number")

        if not comments:
            # Edge case: thread exists but has no comments (should not happen normally)
            # Fail open to avoid blocking legitimate operations due to data inconsistency
            return {"has_response": True, "has_unverified_fix": False, "thread_found": True}

        # Get original comment info
        first_comment = comments[0]
        original_body = first_comment.get("body", "")[:100]
        original_author = first_comment.get("author", {}).get("login", "unknown")
        # Issue #1332: Get comment database ID for review quality logging
        comment_id = first_comment.get("databaseId")

        # Issue #1271: Also check REST API for replies
        # GraphQL may not immediately reflect comments added via REST API
        owner, repo = repo_info
        rest_replies: list[dict[str, Any]] = []
        if pr_number and comment_id:
            rest_replies = _check_rest_api_replies(owner, repo, pr_number, comment_id)

        # Combine GraphQL comments with REST API replies for comprehensive check
        all_comments = list(comments) + rest_replies

        # Check if any comment has Claude Code signature using line-level matching
        has_response = any(
            _has_claude_code_signature(comment.get("body", "")) for comment in all_comments
        )

        # Check if any Claude Code comment claims a fix without verification
        # Fix for Issue #1018: Check thread-level verification, not per-comment
        # If ANY comment in the thread has verification, the fix claims are considered verified
        has_fix_claim = any(
            _has_claude_code_signature(comment.get("body", ""))
            and _has_fix_claim_without_verification(comment.get("body", ""))
            for comment in all_comments
        )
        thread_has_verification = any(
            _has_verification(comment.get("body", "")) for comment in all_comments
        )
        has_unverified_fix = has_fix_claim and not thread_has_verification

        # Issue #1657: Check for out-of-scope keywords without Issue reference
        # Only check Claude Code comments (comments with signature)
        # Issue #1917: Check thread-level Issue reference first
        # If ANY Claude Code comment has an Issue reference, all keywords are covered
        thread_has_issue_ref = False
        issue_pattern = r"(?:^|[^\w#])#(\d+)|[Ii]ssue\s*#?(\d+)"
        for comment in all_comments:
            if _has_claude_code_signature(comment.get("body", "")):
                if re.search(issue_pattern, comment.get("body", ""), re.MULTILINE):
                    thread_has_issue_ref = True
                    break

        out_of_scope_keyword = None
        if not thread_has_issue_ref:
            for comment in all_comments:
                if _has_claude_code_signature(comment.get("body", "")):
                    has_problem, keyword = _has_out_of_scope_without_issue(comment.get("body", ""))
                    if has_problem:
                        out_of_scope_keyword = keyword
                        # NOTE: 最初の違反コメントで処理を終了する設計。
                        # 複数報告はノイズとなり、1件の修正で他も解決することが多いため。
                        break

        return {
            "has_response": has_response,
            "has_unverified_fix": has_unverified_fix,
            "out_of_scope_keyword": out_of_scope_keyword,  # Issue #1657
            "thread_found": True,
            "original_comment": original_body,
            "author": original_author,
            # Issue #1332: Include PR number and comment ID for review quality logging
            "pr_number": pr_number,
            "comment_id": comment_id,
        }

    except TimeoutExpired:
        # Fail open
        return {"has_response": True, "has_unverified_fix": False, "thread_found": False}
    except json.JSONDecodeError:
        # Fail open
        return {"has_response": True, "has_unverified_fix": False, "thread_found": False}
    except OSError:
        # Fail open
        return {"has_response": True, "has_unverified_fix": False, "thread_found": False}
    except (AttributeError, TypeError, KeyError):
        # Handle unexpected data shapes (e.g., json.loads returns non-dict)
        return {"has_response": True, "has_unverified_fix": False, "thread_found": False}


def main() -> None:
    """Main hook entry point."""
    data = parse_hook_input()
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})

    # Only process Bash commands
    if tool_name != "Bash":
        result = make_approve_result(HOOK_NAME)
        print(json.dumps(result))
        return

    command = tool_input.get("command", "")

    # Check if this is a resolveReviewThread GraphQL mutation
    if "gh" not in command or "graphql" not in command:
        result = make_approve_result(HOOK_NAME)
        print(json.dumps(result))
        return

    if "resolveReviewThread" not in command:
        result = make_approve_result(HOOK_NAME)
        print(json.dumps(result))
        return

    # Extract thread ID
    thread_id = extract_thread_id(command)
    if not thread_id:
        # Can't extract thread ID, allow the command
        log_hook_execution(HOOK_NAME, "approve", "Could not extract thread ID, allowing")
        result = make_approve_result(HOOK_NAME)
        print(json.dumps(result))
        return

    # Check if thread has a response
    check_result = check_thread_has_response(thread_id)

    if check_result["has_response"]:
        # Check for unverified fix claims (Issue #964)
        if check_result.get("has_unverified_fix"):
            author = check_result.get("author", "unknown")
            snippet = check_result.get("original_comment", "")[:80]

            block_reason = f"""「修正済み」と書いていますが、検証内容がありません。

**問題:**
「修正済み」と主張していますが、「Verified:」による具体的な検証内容が含まれていません。
実際にコードを読んで確認したことを証明してください。

**正しい形式:**
```
修正済み: コミット xxx で修正

Verified: [ファイル名]:[行番号] で [具体的に確認した内容]

-- Claude Code
```

**対象スレッド:** {thread_id}
**投稿者:** {author}
**コメント抜粋:** {snippet}..."""

            # Note: make_block_result calls log_hook_execution internally (Issue #2023)
            result = make_block_result(HOOK_NAME, block_reason)
            print(json.dumps(result))
            return

        # Issue #1657: Check for out-of-scope keyword without Issue reference
        out_of_scope_keyword = check_result.get("out_of_scope_keyword")
        if out_of_scope_keyword:
            author = check_result.get("author", "unknown")
            snippet = check_result.get("original_comment", "")[:80]

            block_reason = f"""範囲外発言にIssue番号がありません。

**まず確認してください:**
- 本当にスコープ外ですか？
- 5分以内で修正できるなら、このPRで対応すべきです
- Issueを作成しても、このセッションで着手する必要があります

**スコープ外が妥当な場合のみ:**
1. `gh issue create --title "..." --label "enhancement" --body "..."`
2. コメントに Issue番号を含める（例: "Issue #1234 を作成しました"）
3. 再度Resolveを実行

**注:** 作成したIssueにはこのセッションで着手してください。

**検出されたキーワード:** {out_of_scope_keyword}
**対象スレッド:** {thread_id}
**投稿者:** {author}
**コメント抜粋:** {snippet}..."""

            # Note: make_block_result calls log_hook_execution internally (Issue #2023)
            result = make_block_result(HOOK_NAME, block_reason)
            print(json.dumps(result))
            return

        # Issue #1332: Log review comment resolution for quality tracking
        pr_number = check_result.get("pr_number")
        comment_id = check_result.get("comment_id")
        if pr_number and comment_id:
            try:
                # Normalize reviewer name using identify_reviewer
                raw_author = check_result.get("author", "unknown")
                reviewer = identify_reviewer(raw_author)
                log_review_comment(
                    pr_number=pr_number,
                    comment_id=comment_id,
                    reviewer=reviewer,
                    resolution="accepted",
                )
            except (OSError, ValueError, TypeError):
                # Don't block resolution if logging fails
                # OSError: file system errors
                # ValueError/TypeError: data format issues
                pass

        log_hook_execution(
            HOOK_NAME,
            "approve",
            f"Thread {thread_id} has Claude Code response",
        )
        result = make_approve_result(HOOK_NAME)
        print(json.dumps(result))
        return

    # Block: No Claude Code response found
    author = check_result.get("author", "unknown")
    snippet = check_result.get("original_comment", "")[:80]
    pr_number = check_result.get("pr_number") or "<PR番号>"

    block_reason = f"""コメントなしでResolveしようとしています。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📌 推奨: batch_resolve_threads.py を使用
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
python3 .claude/scripts/batch_resolve_threads.py {pr_number} "対応しました"

このコマンドで:
✓ 全未解決スレッドに「対応しました」と返信
✓ 返信後に自動でResolve
✓ 署名 (-- Claude Code) も自動追加

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**対象スレッド:** {thread_id}
**投稿者:** {author}
**コメント抜粋:** {snippet}...

**手動で対応する場合:**
1. スレッドに返信を追加（末尾に「-- Claude Code」必須）
2. 返信後にResolveを実行"""

    # Note: make_block_result calls log_hook_execution internally (Issue #2023)
    result = make_block_result(HOOK_NAME, block_reason)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
