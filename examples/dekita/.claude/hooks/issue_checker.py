#!/usr/bin/env python3
"""merge-checkフックのIssue・受け入れ基準チェック機能。

Why:
    未完了の受け入れ基準を持つIssueがCloseされると、品質管理が形骸化する。
    PRからIssue参照を抽出し、受け入れ基準の完了状態を確認する。

What:
    - PRボディからIssue参照を抽出（Closes #xxx等）
    - 受け入れ基準の完了チェック
    - レビューコメントからのバグIssue検出

Remarks:
    - review_checker.py: レビュースレッド確認
    - ai_review_checker.py: AIレビュアーステータス
    - 本モジュールはIssueと受け入れ基準に特化

Changelog:
    - silenvx/dekita#xxx: モジュール分割
"""

import json
import re
import subprocess

from check_utils import (
    ISSUE_REFERENCE_PATTERN,
    get_repo_owner_and_name,
    strip_code_blocks,
    truncate_body,
)
from lib.constants import TIMEOUT_HEAVY, TIMEOUT_MEDIUM


def get_pr_body(pr_number: str) -> str | None:
    """Get the PR body text.

    Args:
        pr_number: The PR number.

    Returns:
        PR body text, or None on error.
    """
    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                f"repos/:owner/:repo/pulls/{pr_number}",
                "--jq",
                ".body",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()
    except Exception:
        return None


def extract_issue_numbers_from_pr_body(body: str) -> list[str]:
    """Extract issue numbers from Closes/Fixes keywords in PR body.

    Args:
        body: The PR body text.

    Returns:
        List of issue numbers found.
    """
    if not body:
        return []

    # Find blocks starting with closing keywords (close/closes/closed, fix/fixes/fixed, resolve/resolves/resolved)
    # Handles comma-separated issues: "Closes #123, #456" and "Closes #123, Fixes #456"
    # Case insensitive, allows optional colon
    block_pattern = r"(?:close[sd]?|fix(?:es|ed)?|resolve[sd]?):?\s+#\d+(?:\s*,\s*#\d+)*"
    blocks = re.findall(block_pattern, body, re.IGNORECASE)

    # Extract all issue numbers from matched blocks
    all_numbers = []
    for block in blocks:
        numbers = re.findall(r"#(\d+)", block)
        all_numbers.extend(numbers)

    return list(set(all_numbers))


def extract_issue_numbers_from_commits(pr_number: str) -> list[str]:
    """Extract issue numbers from Fixes/Closes keywords in PR commit messages.

    GitHub auto-closes issues when commits with "Fixes #XXX" or "Closes #XXX"
    are merged. This function extracts those issue numbers so we can consider
    them as "will be auto-closed on merge".

    Issue #1638: Added to handle the case where an issue is created and fixed
    in the same PR. The issue is still open when merging, but will be auto-closed.

    Args:
        pr_number: The PR number to get commits from.

    Returns:
        List of issue numbers found in commit messages.
    """
    try:
        # Get commit messages for the PR
        result = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                pr_number,
                "--json",
                "commits",
                "--jq",
                '.commits[] | .messageHeadline + " " + .messageBody',
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode != 0:
            return []

        commit_text = result.stdout

        # Use the same pattern as extract_issue_numbers_from_pr_body
        block_pattern = r"(?:close[sd]?|fix(?:es|ed)?|resolve[sd]?):?\s+#\d+(?:\s*,\s*#\d+)*"
        blocks = re.findall(block_pattern, commit_text, re.IGNORECASE)

        all_numbers = []
        for block in blocks:
            numbers = re.findall(r"#(\d+)", block)
            all_numbers.extend(numbers)

        return list(set(all_numbers))
    except Exception:
        return []


def fetch_issue_acceptance_criteria(
    issue_number: str,
) -> tuple[bool, str, list[tuple[bool, bool, str]]]:
    """Fetch issue and extract acceptance criteria (checkbox items).

    Args:
        issue_number: The issue number.

    Returns:
        Tuple of (success, title, criteria).
        criteria is a list of (is_completed, is_strikethrough, text) tuples.
        is_strikethrough indicates the item was marked with ~~text~~ (excluded).
    """
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "view",
                issue_number,
                "--json",
                "title,body,state",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode != 0:
            return (False, "", [])

        data = json.loads(result.stdout)
        title = data.get("title") or ""
        body = data.get("body") or ""
        state = data.get("state") or ""

        # Skip closed Issues (they were closed by another PR or manually)
        if state == "CLOSED":
            return (False, "", [])

        # Strip code blocks before extracting checkboxes (Issue #830)
        # This prevents false positives from checkbox examples in code blocks
        body_without_code = strip_code_blocks(body)

        # Extract checkbox items: - [ ] or - [x] or * [ ] format
        # Issue #823: Treat strikethrough checkboxes (- [ ] ~~text~~) as completed
        # since they indicate items that are no longer applicable
        criteria = []
        pattern = r"^[\s]*[-*]\s*\[([ xX])\]\s*(.+)$"
        # Pattern to detect strikethrough: text starting with ~~...~~
        # Matches both "~~text~~" and "~~text~~（explanation）"
        # Uses non-greedy (.+?) to match the first closing ~~ only
        strikethrough_pattern = re.compile(r"^~~.+?~~")
        for line in body_without_code.split("\n"):
            match = re.match(pattern, line)
            if match:
                checkbox_mark = match.group(1).lower()
                criteria_text = match.group(2).strip()
                # Checkbox is completed if:
                # 1. Marked with [x] or [X]
                # 2. Text starts with strikethrough (~~text~~) - indicates no longer applicable
                is_strikethrough = bool(strikethrough_pattern.match(criteria_text))
                is_completed = checkbox_mark == "x" or is_strikethrough
                criteria.append((is_completed, is_strikethrough, criteria_text))

        return (True, title, criteria)
    except Exception:
        return (False, "", [])


# Pattern to detect bug-related keywords in Issue titles (Issue #1130)
BUG_ISSUE_TITLE_KEYWORDS = [
    "fix:",
    "fix(",
    "bug:",
    "bug(",
    "バグ",
    "修正",
    "不具合",
]

# Labels that indicate an Issue is NOT a bug (Issue #1142)
# If an Issue has any of these labels, it should not be treated as a bug
# even if the title contains bug-related keywords like "fix:".
BUG_ISSUE_EXCLUSION_LABELS = [
    "enhancement",
    "improvement",
    "documentation",
    "refactor",
    "feature",
]

# Labels that indicate an Issue IS a bug (Issue #1142)
# If an Issue has any of these labels, it should be treated as a bug.
BUG_ISSUE_LABELS = [
    "bug",
    "bugfix",
    "バグ",
]

# Pattern to detect Issue references in review comments (Issue #1130)
ISSUE_CREATION_PATTERN = re.compile(
    r"issue\s*#?(\d+)\s*(?:を|として)?(?:作成|登録)|"  # "Issue #123 を作成"
    r"#(\d+)\s*(?:を|として)?(?:作成|登録)|"  # "#123 として登録"
    r"\(issue\s*#(\d+)\)|"  # "(Issue #123)"
    r"\(#(\d+)\)",  # "(#123)"
    re.IGNORECASE,
)


def _collect_issue_refs_from_review(
    pr_number: str, owner: str, name: str
) -> tuple[dict[str, str], str]:
    """Collect Issue references from Claude Code review comments.

    Issue #1152: Extracted from check_bug_issue_from_review for clarity.

    Args:
        pr_number: The PR number.
        owner: Repository owner.
        name: Repository name.

    Returns:
        Tuple of (issue_refs dict, pr_created_at string).
        issue_refs maps issue_number -> comment snippet.
    """
    query = """
    query($owner: String!, $name: String!, $pr: Int!) {
      repository(owner: $owner, name: $name) {
        pullRequest(number: $pr) {
          createdAt
          reviewThreads(first: 50) {
            nodes {
              comments(last: 10) {
                nodes {
                  body
                  author { login }
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

    if result.returncode != 0 or not result.stdout.strip():
        return {}, ""

    data = json.loads(result.stdout)
    pr_data = data.get("data", {}).get("repository", {}).get("pullRequest", {})
    threads = pr_data.get("reviewThreads", {}).get("nodes", [])
    pr_created_at = pr_data.get("createdAt", "")

    # Collect Issue numbers from Claude Code comments
    issue_refs: dict[str, str] = {}
    for thread in threads:
        comments = thread.get("comments", {}).get("nodes", [])
        for comment in comments:
            comment_body = comment.get("body", "")
            # Issue #1135: Strip code blocks to avoid matching examples
            comment_body_stripped = strip_code_blocks(comment_body)

            # Only check Claude Code comments (signature at end)
            if not comment_body_stripped.strip().endswith("-- Claude Code"):
                continue

            # Find Issue references in stripped body
            for match in ISSUE_CREATION_PATTERN.finditer(comment_body_stripped):
                issue_num = next((g for g in match.groups() if g is not None), None)
                if issue_num and issue_num not in issue_refs:
                    issue_refs[issue_num] = truncate_body(comment_body_stripped, 80)

    return issue_refs, pr_created_at


def _is_bug_issue(title: str, labels: list[str]) -> bool:
    """Determine if an Issue is a bug based on labels and title.

    Issue #1152: Extracted from check_bug_issue_from_review for clarity.
    Issue #1142: Label-based filtering with priority.

    Priority:
    1. Exclusion labels (enhancement, etc.) → NOT a bug
    2. Bug labels (bug, bugfix, バグ) → IS a bug
    3. Title keywords → IS a bug (fallback)

    Args:
        title: Issue title.
        labels: List of lowercase label names.

    Returns:
        True if the Issue is considered a bug.
    """
    # Check exclusion labels first (highest priority)
    if any(lbl in BUG_ISSUE_EXCLUSION_LABELS for lbl in labels):
        return False

    # Check bug labels
    if any(lbl in BUG_ISSUE_LABELS for lbl in labels):
        return True

    # Fall back to title keywords
    title_lower = title.lower()
    return any(keyword.lower() in title_lower for keyword in BUG_ISSUE_TITLE_KEYWORDS)


def _references_pr(issue_body: str, pr_number: str) -> bool:
    """Check if Issue body references the given PR.

    Issue #1152: Extracted from check_bug_issue_from_review for clarity.
    Issue #1135: Only match explicit PR references, not #{number} patterns.

    Args:
        issue_body: The Issue body text.
        pr_number: The PR number to check for.

    Returns:
        True if the Issue body references the PR.
    """
    # Match "PR #123" or "pull request 123" with word boundaries
    pr_ref_pattern = rf"\bPR\s*#?{pr_number}\b|\bpull\s*request\s*#?{pr_number}\b"
    return bool(re.search(pr_ref_pattern, issue_body, re.IGNORECASE))


def check_bug_issue_from_review(pr_number: str) -> list[dict]:
    """Check if review-related bug Issues are still open (Issue #1130).

    Issue #1152: Refactored to use helper functions for clarity.

    Detects the anti-pattern where:
    1. AI reviewer points out a bug in the PR code
    2. Claude Code creates a separate Issue instead of fixing in-PR
    3. PR gets merged with the bug still present
    4. Bug Issue remains open

    This catches cases where bugs introduced in this PR are incorrectly
    deferred to separate Issues instead of being fixed before merge.

    Returns list of open bug Issues that were created from this PR's review.
    Each item contains: issue_number, title, referenced_in (thread body snippet)
    """
    try:
        repo_info = get_repo_owner_and_name()
        if not repo_info:
            return []

        owner, name = repo_info

        # Collect Issue references from review comments
        issue_refs, pr_created_at = _collect_issue_refs_from_review(pr_number, owner, name)
        if not issue_refs:
            return []

        # Check each referenced Issue
        bug_issues: list[dict] = []
        for issue_num, comment_snippet in issue_refs.items():
            try:
                # Get Issue details
                issue_result = subprocess.run(
                    [
                        "gh",
                        "issue",
                        "view",
                        issue_num,
                        "--json",
                        "title,state,labels,body,createdAt",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=TIMEOUT_MEDIUM,
                )

                if issue_result.returncode != 0:
                    continue

                issue_data = json.loads(issue_result.stdout)
                title = issue_data.get("title", "")
                state = issue_data.get("state", "")
                labels = [lbl.get("name", "").lower() for lbl in issue_data.get("labels", [])]
                issue_body = issue_data.get("body", "")
                issue_created_at = issue_data.get("createdAt", "")

                # Skip closed Issues or merged PRs (gh issue view returns MERGED for PRs)
                if state in ("CLOSED", "MERGED"):
                    continue

                # Issue #1135: Skip Issues created before the PR
                if pr_created_at and issue_created_at and issue_created_at < pr_created_at:
                    continue

                # Check if bug Issue that references this PR
                if _is_bug_issue(title, labels) and _references_pr(issue_body, pr_number):
                    bug_issues.append(
                        {
                            "issue_number": issue_num,
                            "title": title,
                            "referenced_in": comment_snippet,
                        }
                    )

            except Exception:
                continue

        return bug_issues

    except Exception:
        # On error, don't block (fail open)
        return []


def check_incomplete_acceptance_criteria(
    pr_number: str,
    commit_issue_numbers: set[str] | None = None,
) -> list[dict]:
    """Check if Issues being closed by this PR have incomplete acceptance criteria.

    This catches the case where a PR closes an Issue but doesn't actually
    fulfill all acceptance criteria (checkbox items).

    Issue #1638: Issues referenced with "Fixes #XXX" or "Closes #XXX" in commit
    messages are skipped, as they will be auto-closed by GitHub on merge.
    This handles the case where an issue is created and fixed in the same PR.

    Issue #1986: Unchecked items that contain Issue references (e.g., "→ #123")
    are considered "properly deferred" and are not counted as incomplete.
    This prevents the workaround of just marking items as "スコープ外" without
    creating a follow-up Issue.

    Args:
        pr_number: The PR number.
        commit_issue_numbers: Pre-fetched set of issue numbers from commits (Issue #1661).
            If None, will be fetched within this function.

    Returns:
        List of issues with incomplete criteria.
        Each item contains:
            - issue_number: Issue number
            - title: Issue title
            - incomplete_count: Number of incomplete items (excluding deferred ones)
            - incomplete_items: List of incomplete item texts (max 3)
            - total_count: Total number of checklist items
            - completed_count: Number of handled items (completed + deferred with Issue refs) (Issue #2463)
    """
    try:
        pr_body = get_pr_body(pr_number)
        if not pr_body:
            return []

        issue_numbers = extract_issue_numbers_from_pr_body(pr_body)
        if not issue_numbers:
            return []

        # Issue #1638: Get issues that will be auto-closed from commit messages
        # These issues are skipped because they'll be auto-closed on merge
        # Issue #1661: Use pre-fetched data if provided to reduce API calls
        if commit_issue_numbers is None:
            commit_issue_numbers = set(extract_issue_numbers_from_commits(pr_number))

        issues_with_incomplete = []

        for issue_num in issue_numbers:
            # Issue #1638: Skip issues that are referenced in commits with Fixes/Closes
            # They will be auto-closed by GitHub on merge
            if issue_num in commit_issue_numbers:
                continue

            success, title, criteria = fetch_issue_acceptance_criteria(issue_num)
            if not success or not criteria:
                continue

            # Issue #1986: Check for incomplete criteria, but allow items with Issue references
            # Items with Issue references (e.g., "→ #123", "Issue #456 で対応") are considered
            # "properly deferred" and don't count as incomplete.
            # Issue #2463: Count completed items for display (X/Y タスク完了)
            incomplete_items = []
            completed_count = 0
            total_count = len(criteria)

            for is_completed, _is_strikethrough, text in criteria:
                if is_completed:
                    completed_count += 1
                else:
                    # Check if this item has an Issue reference (properly deferred)
                    if not ISSUE_REFERENCE_PATTERN.search(text):
                        incomplete_items.append(text)
                    else:
                        # Issue #2463: Properly deferred items are considered "handled"
                        # Include them in completed_count to avoid overestimating severity
                        completed_count += 1

            if incomplete_items:
                issues_with_incomplete.append(
                    {
                        "issue_number": issue_num,
                        "title": title,
                        "incomplete_count": len(incomplete_items),
                        "incomplete_items": incomplete_items[:3],  # Show max 3
                        "total_count": total_count,
                        "completed_count": completed_count,
                    }
                )

        return issues_with_incomplete
    except Exception:
        return []


# Patterns that indicate remaining tasks to be done in another PR (Issue #2457)
# These patterns suggest work is being deferred but may not have Issue references
REMAINING_TASK_PATTERNS = [
    # Phase/Stage indicators (exclude 1 - likely refers to current work)
    r"第(?:[2-9]|[1-9]\d+)段階",  # 第2段階, 第10段階 (第1段階は現在の作業を示す可能性が高いため除外)
    r"phase\s*(?:[2-9]|[1-9]\d+)",  # Phase 2, Phase 10 (exclude phase 1)
    r"stage\s*(?:[2-9]|[1-9]\d+)",  # Stage 2, Stage 10 (exclude stage 1)
    # Deferred work indicators (Japanese)
    r"別(?:の)?PR(?:で|として|に)?",  # 別PR, 別のPR, 別PRで, 別PRとして, 別PRに
    r"残タスク",  # 残タスク
    r"後続(?:の)?PR",  # 後続PR, 後続のPR
    r"今後(?:の)?対応",  # 今後の対応
    r"将来(?:の)?対応",  # 将来の対応
    r"次(?:の)?ステップ",  # 次のステップ
    r"次フェーズ",  # 次フェーズ
    r"後で(?:対応|実装)",  # 後で対応, 後で実装
    # Deferred work indicators (English)
    r"follow[\s-]?up",  # follow-up, followup
    r"future\s+(?:work|PR|task)",  # future work, future PR
    r"next\s+(?:step|phase|PR)",  # next step, next phase
    r"separate\s+PR",  # separate PR
    r"another\s+PR",  # another PR
    r"TODO:\s*(?:別|次|後)",  # TODO: 別PR, TODO: 次のステップ
]

# Compiled pattern for remaining task detection
REMAINING_TASK_PATTERN = re.compile(
    "|".join(REMAINING_TASK_PATTERNS),
    re.IGNORECASE,
)


def _get_issue_details(issue_number: str) -> tuple[bool, str, str]:
    """Get the Issue title and body for open Issues only.

    Closed Issues are skipped because remaining task patterns in closed Issues
    are historical information and should not trigger warnings.

    Args:
        issue_number: The Issue number.

    Returns:
        Tuple of (success, title, body). On error or closed Issue, returns (False, "", "").
    """
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "view",
                issue_number,
                "--json",
                "title,body,state",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode != 0:
            return (False, "", "")

        data = json.loads(result.stdout)
        state = data.get("state", "")

        # Skip closed Issues
        if state == "CLOSED":
            return (False, "", "")

        title = data.get("title") or ""
        body = data.get("body") or ""
        return (True, title, body)
    except Exception:
        return (False, "", "")


def _find_remaining_task_patterns_without_issue_ref(
    body: str,
) -> list[str]:
    """Find remaining task patterns that don't have Issue references nearby.

    Args:
        body: The Issue body text.

    Returns:
        List of matched patterns that lack Issue references.
    """
    if not body:
        return []

    # Strip code blocks to avoid false positives
    body_without_code = strip_code_blocks(body)

    patterns_without_ref = []

    # Find all remaining task pattern matches
    for match in REMAINING_TASK_PATTERN.finditer(body_without_code):
        matched_text = match.group()
        start_pos = match.start()
        end_pos = match.end()

        # Get surrounding context (100 chars before and after)
        # 100 chars is chosen as a reasonable window that typically covers:
        # - Japanese: 2-3 sentences (average 30-50 chars per sentence)
        # - English: 1-2 sentences (average 80-100 chars per sentence)
        # Too short: miss nearby Issue references; Too long: false matches from distant paragraphs
        context_start = max(0, start_pos - 100)
        context_end = min(len(body_without_code), end_pos + 100)
        context = body_without_code[context_start:context_end]

        # Check if there's an Issue reference in the context
        if not ISSUE_REFERENCE_PATTERN.search(context):
            patterns_without_ref.append(matched_text)

    return patterns_without_ref


def check_remaining_task_patterns(
    pr_number: str,
    commit_issue_numbers: set[str] | None = None,
) -> list[dict]:
    """Check if Issues have remaining task patterns without Issue references (Issue #2457).

    This detects patterns like "第2段階で対応", "別PRで実装", "残タスク" in Issue bodies
    that indicate work being deferred but without a follow-up Issue reference.

    Args:
        pr_number: The PR number.
        commit_issue_numbers: Pre-fetched set of issue numbers from commits.
            If None, will be fetched within this function.

    Returns:
        List of issues with remaining task patterns lacking Issue references.
        Each item contains: issue_number, title, patterns
    """
    try:
        pr_body = get_pr_body(pr_number)
        if not pr_body:
            return []

        issue_numbers = extract_issue_numbers_from_pr_body(pr_body)
        if not issue_numbers:
            return []

        # Use pre-fetched data if provided
        if commit_issue_numbers is None:
            commit_issue_numbers = set(extract_issue_numbers_from_commits(pr_number))

        issues_with_patterns = []

        for issue_num in issue_numbers:
            # Skip issues referenced in commits with Fixes/Closes
            if issue_num in commit_issue_numbers:
                continue

            # Get Issue details (single API call instead of two)
            success, title, issue_body = _get_issue_details(issue_num)
            if not success:
                continue

            # Find patterns without Issue references
            patterns = _find_remaining_task_patterns_without_issue_ref(issue_body)

            if patterns:
                # Deduplicate patterns
                unique_patterns = list(set(patterns))
                issues_with_patterns.append(
                    {
                        "issue_number": issue_num,
                        "title": title,
                        "patterns": unique_patterns[:5],  # Show max 5
                    }
                )

        return issues_with_patterns
    except Exception:
        return []


def check_excluded_criteria_without_followup(
    pr_number: str,
    commit_issue_numbers: set[str] | None = None,
) -> list[dict[str, str | list[str]]]:
    """Check if strikethrough criteria have follow-up Issue references (Issue #1458).

    When acceptance criteria are marked as "out of scope" using strikethrough (~~text~~),
    they must include a follow-up Issue reference (e.g., "#123") to ensure traceability.

    Issue #1638: Issues referenced with "Fixes #XXX" in commit messages are skipped.

    Args:
        pr_number: The PR number.
        commit_issue_numbers: Pre-fetched set of issue numbers from commits (Issue #1661).
            If None, will be fetched within this function.

    Returns:
        List of issues with excluded criteria lacking Issue references.
        Each item contains: issue_number, title, excluded_items
    """
    try:
        pr_body = get_pr_body(pr_number)
        if not pr_body:
            return []

        issue_numbers = extract_issue_numbers_from_pr_body(pr_body)
        if not issue_numbers:
            return []

        # Issue #1638: Get issues that will be auto-closed from commit messages
        # Issue #1661: Use pre-fetched data if provided to reduce API calls
        if commit_issue_numbers is None:
            commit_issue_numbers = set(extract_issue_numbers_from_commits(pr_number))

        issues_with_missing_refs = []

        for issue_num in issue_numbers:
            # Issue #1638: Skip issues referenced in commits with Fixes/Closes
            if issue_num in commit_issue_numbers:
                continue
            success, title, criteria = fetch_issue_acceptance_criteria(issue_num)
            if not success or not criteria:
                continue

            # Find strikethrough criteria without Issue references
            missing_ref_items = []
            for _is_completed, is_strikethrough, text in criteria:
                # Only check strikethrough items (excluded from this PR)
                if is_strikethrough:
                    # Check if text contains Issue reference
                    if not ISSUE_REFERENCE_PATTERN.search(text):
                        missing_ref_items.append(text)

            if missing_ref_items:
                issues_with_missing_refs.append(
                    {
                        "issue_number": issue_num,
                        "title": title,
                        "excluded_items": missing_ref_items,
                    }
                )

        return issues_with_missing_refs
    except Exception:
        return []
