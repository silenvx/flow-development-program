#!/usr/bin/env python3
"""修正主張と検証チェックの関数群（merge-check用モジュール）。

Why:
    Claude Codeが「Fixed:」「修正済み」と主張しても、実際にコードが
    修正されているとは限らない。検証パターンの存在を確認することで、
    未検証の修正主張がマージされることを防ぐ。

What:
    - 修正主張キーワード検出（Fixed:, Added, 修正済み等）
    - 検証パターン検出（Verified:, 検証済み等）
    - 数値主張の検証（AIレビュアーの数値誤りを検出）
    - 具体的な修正箇所参照（ファイル:行番号）の検出

Remarks:
    - review_checker.pyは却下・応答チェック担当
    - ai_review_checker.pyはAIレビュアーステータスチェック担当
    - 本モジュールは修正主張と検証に特化

Changelog:
    - silenvx/dekita#462: FixClaimKeyword構造体でキーワード管理
    - silenvx/dekita#856: 具体的なファイル参照を「自己検証」として認識
    - silenvx/dekita#858: 数値主張の検証チェック追加
    - silenvx/dekita#1679: Issue参照を有効な応答として認識
"""

import json
import re
import subprocess
from dataclasses import dataclass

from check_utils import get_repo_owner_and_name, truncate_body
from lib.constants import TIMEOUT_HEAVY

# Keywords that indicate a fix claim (case-insensitive)
#
# Design note: These keywords are checked ONLY in comments with "-- Claude Code" signature.
# This significantly reduces false positives from broad keywords like "added " or "updated ".
# The trailing space in action words helps match verb usage ("added error handling") while
# avoiding partial matches in longer words. Since we only check Claude Code's own responses,
# the risk of false positives from casual language is minimal.
#
# Uses a dataclass for structured keyword management (Issue #462):
# - pattern: The text to match in comment body (case-insensitive)
# - display_name: Human-readable name shown in error messages
# - trailing_char: Character to strip from pattern when extracting display name (':' or ' ')


@dataclass(frozen=True)
class FixClaimKeyword:
    """Structured fix claim keyword with display metadata."""

    pattern: str
    display_name: str
    trailing_char: str = ""  # ':' or ' ', empty if display_name is explicit


FIX_CLAIM_KEYWORDS: list[FixClaimKeyword] = [
    FixClaimKeyword("fixed:", "Fixed", ":"),
    FixClaimKeyword("already addressed:", "Already addressed", ":"),
    FixClaimKeyword("added ", "Added", " "),
    FixClaimKeyword("updated ", "Updated", " "),
    FixClaimKeyword("changed ", "Changed", " "),
    FixClaimKeyword("implemented ", "Implemented", " "),
    FixClaimKeyword("修正済み", "修正済み"),
    FixClaimKeyword("対応済み", "対応済み"),
]

# Pattern that indicates verification (case-insensitive)
#
# Design note (Issue #462): Instead of negative lookbehinds (which have fixed-width
# limitations), we use two patterns:
# - VERIFICATION_POSITIVE_PATTERN: Matches "verified:" anywhere
# - VERIFICATION_NEGATION_PATTERN: Matches negated forms like "not verified:",
#   "haven't verified:", "could not verify:", etc.
#
# IMPORTANT: The negation check must be position-aware. A comment like
# "Previously unverified: pending. Verified: confirmed." should count as verified
# because there's at least one "verified:" that is NOT negated.
VERIFICATION_POSITIVE_PATTERN = re.compile(r"\bverified:", re.IGNORECASE)
# Note: Word boundary \b is added before negation words to avoid false positives
# like "run verified:" matching "un\s*verified:". The pattern ensures the negation
# word starts at a word boundary (start of string or after non-word character).
VERIFICATION_NEGATION_PATTERN = re.compile(
    r"\b(?:not|un|never|haven't|couldn't|could not|did not|didn't|won't|will not|cannot|can't)\s*verified:",
    re.IGNORECASE,
)


# Pattern to detect AI review comments containing numeric claims (Issue #858)
#
# Design note: AI reviewers (Copilot/Codex) sometimes make incorrect claims about
# numbers (e.g., "should be 33 characters" when it's actually 32). When Claude Code
# responds to such numeric claims, it should include verification that confirms
# the actual count/measurement.
NUMERIC_CLAIM_PATTERN = re.compile(
    r"(?:should be|は|を|から)\s*\d+|"  # "should be 10", "は10", "を10に"
    # "10文字", "10 characters" - but NOT line references like "10行目" or "10行付近"
    r"\d+\s*(?:文字|行(?!目|付近)|個|件|要素|bytes?|characters?|lines?|items?)",
    re.IGNORECASE,
)

# Pattern to detect verification of numeric claims (Issue #858)
#
# When responding to numeric claims, include verification like:
# "検証済み: 実際は32文字" or "Verified: counted 32 characters"
#
# Issue #1679: Also recognize Issue references as valid responses.
# When a numeric claim is deferred to a separate Issue (e.g., "Issue #1652に記録済み"),
# it means the claim has been acknowledged and tracked for follow-up.
NUMERIC_VERIFICATION_PATTERN = re.compile(
    r"検証済み:|verified:|確認済み:|counted\s*\d|"
    r"実際[はに]\d+|actually\s*\d+|"
    # Issue #1679: Require tracking context for Issue references to avoid false positives
    # e.g., "see Issue #123" should NOT match, but "Issue #123 に記録" should
    r"issue\s*#\d+\s*(?:に記録|として追跡|for\s*follow-?up)|"
    # Issue #1738, #1744: Add negative lookbehinds to prevent negated patterns
    # Issue #1735: Support "recorded in issue #123" variation
    r"#\d+\s*(?:に記録|として追跡)|(?<!not )(?<!never )\brecorded\s*in\s*(?:issue\s*)?#\d+",
    re.IGNORECASE,
)


def has_valid_verification(text: str) -> bool:
    """Check if text contains at least one valid (non-negated) verification.

    This function finds all "verified:" occurrences and checks each one to see
    if it's preceded by a negation word. Returns True if at least one occurrence
    is NOT negated.

    Args:
        text: The comment text to check

    Returns:
        True if there's at least one valid verification, False otherwise
    """
    # Find all positive matches
    positive_matches = list(VERIFICATION_POSITIVE_PATTERN.finditer(text))
    if not positive_matches:
        return False

    # Check if any positive match is NOT at a negated position
    # Note: negation pattern includes "verified:" so we compare with
    # the position where "verified:" starts in each negation match
    for pos_match in positive_matches:
        pos_start = pos_match.start()
        # Check if this position is NOT part of a negated match
        # A negation match's "verified:" starts after the negation word
        is_negated = False
        for neg_match in VERIFICATION_NEGATION_PATTERN.finditer(text):
            # The "verified:" in the negation match is at the end
            neg_verified_pos = neg_match.end() - len("verified:")
            if pos_start == neg_verified_pos:
                is_negated = True
                break
        if not is_negated:
            return True

    return False


# Known source file extensions for file reference detection (Issue #856, #887)
# Comprehensive list to avoid false negatives while preventing URL/IP false positives
_SOURCE_FILE_EXTENSIONS = (
    # Scripting / interpreted
    r"py|rb|pl|php|lua|r|R"
    r"|"
    # JavaScript / TypeScript ecosystem
    r"js|jsx|ts|tsx|mjs|cjs|vue|svelte"
    r"|"
    # Compiled languages
    r"go|rs|c|h|cpp|hpp|cc|cxx|java|kt|kts|scala|cs|fs|swift|m|mm"
    r"|"
    # Web / markup / styling
    r"html|htm|css|scss|sass|less"
    r"|"
    # Data / config
    r"json|yml|yaml|xml|toml|ini|cfg|env|properties"
    r"|"
    # Database
    r"sql"
    r"|"
    # Documentation / text
    r"md|mdx|txt|rst"
    r"|"
    # Shell / scripts
    r"sh|bash|zsh|fish|ps1|bat|cmd"
    r"|"
    # Build / package management
    r"gradle|gemspec|bazel|bzl|cmake|make|ninja|sbt|pom"
    r"|"
    # Lock / dependency files
    r"lock"
    r"|"
    # Other
    r"graphql|proto|tf|hcl"
)

# Special build/config files without extensions (e.g., Makefile, Dockerfile)
_SPECIAL_BUILD_FILES = r"Makefile|Dockerfile|Jenkinsfile|Vagrantfile|Gemfile|Rakefile"

# Pattern to detect specific file path references (Issue #856, #887)
# Matches: file.py:10, src/utils.ts:25-30, common.py, ./path/to/file.tsx, Makefile:5
# Issue #887: Requires known source file extensions to avoid false positives
# from URLs (example.com:8080), IPs (192.168.1.1:8080), and versions (v1.2.3:4567).
SPECIFIC_FILE_REFERENCE_PATTERN = re.compile(
    r"(?:"
    # Relative paths: file.py:10, ./path/file.tsx:25-30 (not starting with /)
    # (?<![a-zA-Z0-9/.]) blocks matches within URLs (after hostname/domain chars)
    rf"(?<![a-zA-Z0-9/.])[a-zA-Z0-9_.\-][a-zA-Z0-9_\-./]*\.(?:{_SOURCE_FILE_EXTENSIONS}):\d+(?:-\d+)?"
    r"|"
    # Absolute paths: /app/main.py:10 (starting with /)
    # (?<![a-zA-Z0-9:/]) blocks URLs (after domain) and :// patterns
    rf"(?<![a-zA-Z0-9:/])/[a-zA-Z0-9_\-./]+\.(?:{_SOURCE_FILE_EXTENSIONS}):\d+(?:-\d+)?"
    r"|"
    # common.py, utils.ts, config.toml (bare filenames without line numbers)
    # (?<![a-zA-Z0-9/.]) blocks matches within URLs
    rf"(?<![a-zA-Z0-9/.])[a-zA-Z0-9_\-]+\.(?:{_SOURCE_FILE_EXTENSIONS})\b"
    r"|"
    # Makefile:10, Dockerfile:5 (special files without extensions)
    # Note: URLs with build files (e.g., https://example.com/Dockerfile) are rare
    rf"\b(?:{_SPECIAL_BUILD_FILES})(?::\d+(?:-\d+)?)?\b"
    r")",
    re.IGNORECASE,
)

# Pattern to detect commit hash references (Issue #856)
# Matches: "in abc1234", "commit abc1234def", "Fixed in abc1234def5678"
# IMPORTANT: Requires "in " or "commit " prefix with word boundary to avoid
# false positives (e.g., "resubmit abc1234" should not match)
COMMIT_HASH_REFERENCE_PATTERN = re.compile(
    r"(?:\bin\s+|\bcommit\s+)[0-9a-f]{7,40}\b",
    re.IGNORECASE,
)

# Pattern to detect explicit "not verified/verify" statements (Issue #856)
# Matches: "not verified", "unverified", "haven't verified", "not yet verified",
#          "couldn't verify", "didn't verify locally", etc.
# Used to override self-verification from specific fix claims.
# Example: "Fixed: merge-check.py:50. Couldn't verify locally." should NOT
# be treated as self-verified because it explicitly says it wasn't verified.
# The pattern allows up to 2 intermediate words like "yet", "fully", "actually"
# between the negation and "verified/verify" (e.g., "not yet verified").
# Limiting to 2 words prevents false positives from long sentences like:
# "I haven't reviewed the code changes and verified the implementation"
EXPLICIT_NOT_VERIFIED_PATTERN = re.compile(
    r"\b(?:"
    r"unverified"  # Single word form
    r"|"
    r"(?:not|never|haven't|hasn't|couldn't|could not|"
    r"did not|didn't|won't|will not|cannot|can't)(?:\s+\w+){0,2}\s+verif(?:y|ied)"
    r")\b",
    re.IGNORECASE,
)


def is_specific_fix_claim(text: str) -> bool:
    """Check if a fix claim comment contains specific evidence.

    Issue #856: When a fix claim includes specific file paths or commit hashes,
    it's considered "self-verifying" because the reviewer can easily verify
    the claim by checking the referenced location.

    Args:
        text: The comment text to check

    Returns:
        True if the comment contains specific file references or commit hashes
    """
    # Check for file path references
    if SPECIFIC_FILE_REFERENCE_PATTERN.search(text):
        return True

    # Check for commit hash references
    if COMMIT_HASH_REFERENCE_PATTERN.search(text):
        return True

    return False


def check_resolved_without_verification(pr_number: str) -> list[dict]:
    """Check if resolved threads have fix claims without verification.

    When a Claude Code comment claims a fix (e.g., "Fixed:", "Already addressed:"),
    there should be a corresponding "Verified:" comment to confirm the fix was actually applied.

    This catches the case where Claude Code claims to have fixed something but didn't
    actually verify the fix in the code.

    GraphQL Limitations (Issue #561, Issue #1215):
        - reviewThreads(first: 50): Only the first 50 threads per PR are checked.
          PRs with >50 threads will have later threads unchecked.
        - comments(last: 100): Only the last 100 comments per thread are checked
          for fix claims and verifications (increased from 30 in Issue #1215).

    Returns list of threads with unverified fix claims.
    Each item contains: thread_id, author, fix_claim, body (snippet of original review comment)
    """
    try:
        repo_info = get_repo_owner_and_name()
        if not repo_info:
            return []

        owner, name = repo_info

        # GraphQL query - see docstring for limitations (Issue #561, Issue #1215)
        # - firstComment: Original AI review comment for author identification
        # - recentComments: Last 100 comments to find fix claims and verifications
        query = """
        query($owner: String!, $name: String!, $pr: Int!) {
          repository(owner: $owner, name: $name) {
            pullRequest(number: $pr) {
              reviewThreads(first: 50) {
                nodes {
                  id
                  isResolved
                  firstComment: comments(first: 1) {
                    nodes {
                      body
                      author { login }
                    }
                  }
                  recentComments: comments(last: 100) {
                    nodes {
                      body
                    }
                  }
                }
              }
            }
          }
        }
        """

        result = subprocess.run(
            [
                "gh",
                "api",
                "graphql",
                "-f",
                f"query={query}",
                "-F",
                f"owner={owner}",
                "-F",
                f"name={name}",
                "-F",
                f"pr={pr_number}",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_HEAVY,
        )

        # Issue #1026: Check for empty stdout before JSON parsing
        # gh api may return 200 OK with empty body in edge cases
        if result.returncode != 0 or not result.stdout.strip():
            return []

        data = json.loads(result.stdout)
        threads = (
            data.get("data", {})
            .get("repository", {})
            .get("pullRequest", {})
            .get("reviewThreads", {})
            .get("nodes", [])
        )

        threads_without_verification = []

        for thread in threads:
            if not thread.get("isResolved"):
                continue

            # Get the first comment (original AI review comment) for author identification
            first_comments = thread.get("firstComment", {}).get("nodes", [])
            if not first_comments:
                continue

            first_comment = first_comments[0]
            first_body = first_comment.get("body", "")
            author = first_comment.get("author", {}).get("login", "unknown")

            # Skip threads not started by AI reviewer
            author_lower = author.lower()
            if "copilot" not in author_lower and "codex" not in author_lower:
                continue

            # Check for fix claims and verifications in recent comments
            # Only check Claude Code comments (identified by signature) for fix claims
            recent_comments = thread.get("recentComments", {}).get("nodes", [])
            has_fix_claim = False
            fix_claim_text = ""
            has_verification = False

            for comment in recent_comments:
                body = comment.get("body", "")
                body_lower = body.lower()

                # Only check comments with Claude Code signature for fix claims
                if "-- Claude Code" in body:
                    # Check for fix claim keywords using structured FixClaimKeyword
                    for keyword in FIX_CLAIM_KEYWORDS:
                        if keyword.pattern.lower() in body_lower:
                            has_fix_claim = True
                            # Use display_name directly (Issue #462: rstrip improvement)
                            if not fix_claim_text:
                                fix_claim_text = keyword.display_name
                            # Issue #856: Check if THIS fix claim comment has specific
                            # evidence (file path or commit hash). Only the comment
                            # containing the fix claim is checked, not later comments.
                            # But don't count as verified if the comment explicitly
                            # states it's not verified (e.g., "Not verified yet").
                            if is_specific_fix_claim(body):
                                if not EXPLICIT_NOT_VERIFIED_PATTERN.search(body):
                                    has_verification = True
                            break

                    # Check for verification pattern (Issue #462: improved negation handling)
                    # Uses position-aware check to handle comments with both
                    # negated and non-negated verification statements
                    if has_valid_verification(body):
                        has_verification = True

            # If there's a fix claim but no verification, flag it
            if has_fix_claim and not has_verification:
                threads_without_verification.append(
                    {
                        "thread_id": thread.get("id", "unknown"),
                        "author": author,
                        "fix_claim": fix_claim_text,
                        "body": truncate_body(first_body),
                    }
                )

        return threads_without_verification
    except Exception:
        # On error, don't block (fail open)
        return []


def check_numeric_claims_verified(pr_number: str) -> list[dict]:
    """Check if AI review comments with numeric claims have verification.

    When an AI reviewer (Copilot/Codex) makes a claim involving numbers
    (e.g., "should be 33 characters"), Claude Code's response should include
    verification that the number was actually confirmed.

    Background (Issue #858): In PR #851, Copilot claimed "33 characters" but it was
    actually 32. Blindly trusting the AI led to test failures.

    GraphQL Limitations (Issue #561, Issue #1215):
        - reviewThreads(first: 50): Only the first 50 threads per PR are checked.
        - comments(last: 100): Only the last 100 comments per thread are checked.

    Returns list of threads with numeric claims lacking verification.
    Each item contains: thread_id, author, body (snippet of AI comment)
    """
    try:
        repo_info = get_repo_owner_and_name()
        if not repo_info:
            return []

        owner, name = repo_info

        query = """
        query($owner: String!, $name: String!, $pr: Int!) {
          repository(owner: $owner, name: $name) {
            pullRequest(number: $pr) {
              reviewThreads(first: 50) {
                nodes {
                  id
                  isResolved
                  firstComment: comments(first: 1) {
                    nodes {
                      body
                      author { login }
                    }
                  }
                  recentComments: comments(last: 100) {
                    nodes {
                      body
                    }
                  }
                }
              }
            }
          }
        }
        """

        result = subprocess.run(
            [
                "gh",
                "api",
                "graphql",
                "-f",
                f"query={query}",
                "-F",
                f"owner={owner}",
                "-F",
                f"name={name}",
                "-F",
                f"pr={pr_number}",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_HEAVY,
        )

        # Issue #1026: Check for empty stdout before JSON parsing
        # gh api may return 200 OK with empty body in edge cases
        if result.returncode != 0 or not result.stdout.strip():
            return []

        data = json.loads(result.stdout)
        threads = (
            data.get("data", {})
            .get("repository", {})
            .get("pullRequest", {})
            .get("reviewThreads", {})
            .get("nodes", [])
        )

        threads_without_verification = []

        for thread in threads:
            if not thread.get("isResolved"):
                continue

            first_comments = thread.get("firstComment", {}).get("nodes", [])
            if not first_comments:
                continue

            first_comment = first_comments[0]
            first_body = first_comment.get("body", "")
            author = first_comment.get("author", {}).get("login", "unknown")

            # Only check threads started by AI reviewers (Copilot/Codex)
            author_lower = author.lower()
            if "copilot" not in author_lower and "codex" not in author_lower:
                continue

            # Check if the AI comment contains numeric claims
            if not NUMERIC_CLAIM_PATTERN.search(first_body):
                continue

            # Check if any Claude Code response has numeric verification
            recent_comments = thread.get("recentComments", {}).get("nodes", [])
            has_verification = False

            for comment in recent_comments:
                body = comment.get("body", "")
                if "-- Claude Code" not in body:
                    continue
                if NUMERIC_VERIFICATION_PATTERN.search(body):
                    has_verification = True
                    break

            if not has_verification:
                threads_without_verification.append(
                    {
                        "thread_id": thread.get("id", "unknown"),
                        "author": author,
                        "body": truncate_body(first_body),
                    }
                )

        return threads_without_verification
    except Exception:
        # On error, don't block (fail open)
        return []
