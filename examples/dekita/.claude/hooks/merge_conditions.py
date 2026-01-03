#!/usr/bin/env python3
"""merge-checkフックのマージ条件チェックを集約・オーケストレーションする。

Why:
    merge-check.pyが肥大化し、各チェックロジックが分散していた。
    チェック条件の追加・変更を容易にするため、条件ロジックを集約する。

What:
    - BlockingReasonデータクラスで構造化されたエラー報告
    - run_all_pr_checks関数で全PRチェックを一括実行
    - AIレビュー、dismissal、修正検証、受け入れ基準等のチェックを統合

Remarks:
    - merge-check.pyから呼び出される補助モジュール
    - dry-runモードにも対応（副作用スキップ）
    - マージ済みPRはスキップ（Issue #890）

Changelog:
    - silenvx/dekita#874: ブロック理由一括収集パターン導入
    - silenvx/dekita#890: マージ済みPRスキップ追加
    - silenvx/dekita#892: dry-runモード対応
    - silenvx/dekita#1458: 対象外条件のフォローアップチェック追加
    - silenvx/dekita#1661: コミットIssue番号の事前フェッチ最適化
    - silenvx/dekita#2457: 残タスクパターン検出追加
    - silenvx/dekita#2463: 完了率表示追加
"""

import sys
from dataclasses import dataclass

from ai_review_checker import (
    check_ai_review_error,
    check_ai_reviewing,
    request_copilot_review,
)
from check_utils import check_body_quality, truncate_body
from fix_verification_checker import (
    check_numeric_claims_verified,
    check_resolved_without_verification,
)
from issue_checker import (
    check_bug_issue_from_review,
    check_excluded_criteria_without_followup,
    check_incomplete_acceptance_criteria,
    check_remaining_task_patterns,
    extract_issue_numbers_from_commits,
    get_pr_body,
)
from lib.github import is_pr_merged
from review_checker import (
    check_dismissal_without_issue,
    check_resolved_without_response,
    check_unresolved_ai_threads,
)


@dataclass
class BlockingReason:
    """A blocking reason collected during merge checks (Issue #874).

    Attributes:
        check_name: Short name for the check (e.g., "ai_reviewing", "dismissal").
        title: One-line summary of the problem.
        details: Detailed description including items and remediation steps.
    """

    check_name: str
    title: str
    details: str


def run_all_pr_checks(
    pr_number: str, *, dry_run: bool = False
) -> tuple[list[BlockingReason], list[str]]:
    """Run all PR state checks and return blocking reasons and warnings.

    This function extracts the core check logic from main() to enable reuse
    in both hook mode and dry-run mode (Issue #892).

    Args:
        pr_number: The PR number to check.
        dry_run: If True, skip side effects like re-requesting reviews.

    Returns:
        Tuple of (blocking_reasons, warnings):
        - blocking_reasons: List of BlockingReason objects for any failed checks.
        - warnings: List of warning messages (non-blocking but should be logged).
    """
    # Issue #890: Skip all checks if PR is already merged
    # This prevents false positives when another hook (e.g., locked-worktree-guard)
    # has already completed the merge before this hook runs.
    if is_pr_merged(pr_number):
        # Note: Logging is handled by the caller (merge-check.py)
        return [], []

    blocking_reasons: list[BlockingReason] = []
    warnings: list[str] = []

    # Check 3: AI review status
    ai_reviewers = check_ai_reviewing(pr_number)
    if ai_reviewers:
        reviewers_str = ", ".join(ai_reviewers)
        blocking_reasons.append(
            BlockingReason(
                check_name="ai_reviewing",
                title=f"AIレビューが進行中です（レビュアー: {reviewers_str}）",
                details=(
                    "レビュー完了を待ってからマージしてください。\n\n"
                    "確認コマンド:\n"
                    f"gh api repos/:owner/:repo/pulls/{pr_number} "
                    "--jq '.requested_reviewers[].login'\n"
                    "# 空なら完了、'Copilot'や'codex'を含む名前があれば進行中"
                ),
            )
        )

    # Check 3.5: AI review error (Copilot encountered error)
    ai_error = check_ai_review_error(pr_number)
    if ai_error:
        # Issue #630: Allow merge with warning if consecutive errors after successful review
        if ai_error.get("allow_with_warning"):
            # Record warning for caller to log (Issue #630)
            warnings.append(
                f"[WARNING] AIレビューが連続でエラー（レビュアー: {ai_error['reviewer']}）。"
                "以前のレビューが成功しているためマージを許可しますが、確認を推奨します。"
            )
            # Continue to next checks instead of blocking
        else:
            # Issue #642: Try to automatically re-request Copilot review
            # Skip side effects in dry-run mode
            retry_requested = False
            if not dry_run:
                retry_requested = request_copilot_review(pr_number)

            if retry_requested:
                blocking_reasons.append(
                    BlockingReason(
                        check_name="ai_review_error",
                        title="AIレビューがエラーで失敗（自動で再リクエスト済み）",
                        details=(
                            f"レビュアー: {ai_error['reviewer']}\n\n"
                            "対処方法:\n"
                            "1. Copilotレビューの完了を待つ（1-2分程度）\n"
                            "2. レビューコメントに対応\n"
                            "3. 再度マージを実行\n\n"
                            "注: 再リクエストは自動で行われました。"
                        ),
                    )
                )
            else:
                blocking_reasons.append(
                    BlockingReason(
                        check_name="ai_review_error",
                        title="AIレビューがエラーで失敗しました",
                        details=(
                            f"レビュアー: {ai_error['reviewer']}\n\n"
                            "対処方法:\n"
                            "1. GitHubのPRページでCopilotレビューをRe-request\n"
                            "2. レビュー完了を待つ\n"
                            "3. 再度マージを実行"
                        ),
                    )
                )

    # Check 4: Review dismissal without Issue
    dismissals = check_dismissal_without_issue(pr_number)
    if dismissals:
        dismissal_details = "\n".join(
            f"  - {d['path']}:{d['line']}: {d['body']}" for d in dismissals
        )
        blocking_reasons.append(
            BlockingReason(
                check_name="dismissal_without_issue",
                title=f"Issueを作成せずにDismissしたレビューがあります（{len(dismissals)}件）",
                details=(
                    f"該当レビュー:\n{dismissal_details}\n\n"
                    "対処方法:\n"
                    "1. 各dismissに対応するIssueを作成（Issueを作成しないでdismissはNG）\n"
                    '2. dismissコメントに "Issue #番号 を作成" と追記\n'
                    "3. 再度マージを実行\n\n"
                    "理由: AIレビュー指摘を記録なしに却下すると、\n"
                    "問題が見落とされるリスクがあります。"
                ),
            )
        )

    # Check 5: Resolved without Claude Code response
    unresponded = check_resolved_without_response(pr_number)
    if unresponded:
        thread_details = "\n".join(f"  - [{t['author']}] {t['body']}" for t in unresponded)
        blocking_reasons.append(
            BlockingReason(
                check_name="resolved_without_response",
                title=f"Claude Code回答なしでResolveされたスレッドがあります（{len(unresponded)}件）",
                details=(
                    f"該当スレッド:\n{thread_details}\n\n"
                    "対処方法:\n"
                    "1. 各スレッドにClaude Codeで回答を追加\n"
                    '   署名: "-- Claude Code" を末尾に追加\n'
                    "2. 再度マージを実行\n\n"
                    "理由: AIレビューの指摘に対して、\n"
                    "Claude Codeが対応した記録が必要です（トレーサビリティ）。"
                ),
            )
        )

    # Check 6: Fix claims without verification
    unverified = check_resolved_without_verification(pr_number)
    if unverified:
        thread_details = "\n".join(f"  - [{t['author']}] {t['fix_claim']}" for t in unverified)
        blocking_reasons.append(
            BlockingReason(
                check_name="unverified_fix_claim",
                title=f"修正済みの主張が検証されていません（{len(unverified)}件）",
                details=(
                    f"該当スレッド:\n{thread_details}\n\n"
                    "対処方法:\n"
                    "1. 実際にコードが修正されているか確認\n"
                    "2. **該当スレッドに返信として** 'Verified: 確認済み' を追加\n"
                    "   署名: '-- Claude Code' を末尾に追加\n"
                    "3. 再度マージを実行\n\n"
                    "⚠️ 注意:\n"
                    "- PR一般コメント（gh pr comment）は**無効**です\n"
                    "- 指摘スレッドへの返信のみ有効です\n"
                    "- 「Verified:」または「検証済み:」キーワードが必須です\n\n"
                    "例（GraphQL API）:\n"
                    "gh api graphql -f query='mutation {\n"
                    "  addPullRequestReviewThreadReply(input: {\n"
                    '    pullRequestReviewThreadId: "PRRT_xxx",\n'
                    '    body: "Verified: 修正を確認\\n\\n-- Claude Code"\n'
                    "  }) { comment { id } }\n"
                    "}'\n\n"
                    "理由: 「修正済み」と主張してResolveしても、実際には\n"
                    "修正が反映されていないケースがありました（Issue #457）。"
                ),
            )
        )

    # Check 7: Unresolved AI review threads
    unresolved = check_unresolved_ai_threads(pr_number)
    if unresolved:
        thread_details = "\n".join(
            f"  - [{t['author']}] {truncate_body(t['body'])}" for t in unresolved
        )
        blocking_reasons.append(
            BlockingReason(
                check_name="unresolved_ai_threads",
                title=f"未解決のAIレビュースレッドがあります（{len(unresolved)}件）",
                details=(
                    f"該当スレッド:\n{thread_details}\n\n"
                    "対処方法:\n"
                    "1. 各スレッドに対応（修正、回答、または却下理由を説明）\n"
                    "2. スレッドをResolve\n"
                    "3. 再度マージを実行\n\n"
                    "注: AIレビューの全指摘に対応してからマージしてください。"
                ),
            )
        )

    # Check 8: Numeric claims without verification
    unverified_numeric = check_numeric_claims_verified(pr_number)
    if unverified_numeric:
        thread_details = "\n".join(
            f"  - [{t['author']}] {truncate_body(t['body'])}" for t in unverified_numeric
        )
        blocking_reasons.append(
            BlockingReason(
                check_name="unverified_numeric_claim",
                title=f"数値を含むAI指摘への検証コメントがありません（{len(unverified_numeric)}件）",
                details=(
                    f"該当スレッド:\n{thread_details}\n\n"
                    "対処方法:\n"
                    "1. AIが指摘した数値を自分で確認（文字数、行数など）\n"
                    "2. **該当スレッドに返信として** 検証結果を追加:\n"
                    "   「検証済み: 実際は32文字」「Verified: counted 32 chars」\n"
                    '3. 必ず末尾に署名を追加: "-- Claude Code"\n'
                    "4. 再度マージを実行\n\n"
                    "⚠️ 注意:\n"
                    "- PR一般コメント（gh pr comment）は**無効**です\n"
                    "- 指摘スレッドへの返信のみ有効です\n"
                    "- 「Verified:」または「検証済み:」キーワードが必須です\n\n"
                    "例（GraphQL API）:\n"
                    "gh api graphql -f query='mutation {\n"
                    "  addPullRequestReviewThreadReply(input: {\n"
                    '    pullRequestReviewThreadId: "PRRT_xxx",\n'
                    '    body: "Verified: 実際は32文字（自分でカウント）\\n\\n-- Claude Code"\n'
                    "  }) { comment { id } }\n"
                    "}'\n\n"
                    "背景: PR #851でCopilotが「33文字」と指摘したが実際は32文字。\n"
                    "AIの数値指摘を盲信して修正→テスト失敗（Issue #858）。"
                ),
            )
        )

    # Issue #1661: Pre-fetch commit issue numbers once for Check 9 and Check 9.5
    # This reduces API calls from 2 to 1
    # Wrap in try/except to maintain fail-open behavior on API errors
    try:
        commit_issue_numbers = set(extract_issue_numbers_from_commits(pr_number))
    except Exception as e:
        print(f"⚠️ Warning: Failed to fetch commit issue numbers: {e}", file=sys.stderr)
        commit_issue_numbers = None  # Fall back to None, each check will re-fetch independently

    # Check 9: Incomplete acceptance criteria
    # Issue #2463: Display completion ratio (X/Y タスク完了) for awareness
    incomplete_issues = check_incomplete_acceptance_criteria(pr_number, commit_issue_numbers)
    if incomplete_issues:
        issue_details = "\n".join(
            f"  ⚠️ Issue #{i['issue_number']}: {i['completed_count']}/{i['total_count']} タスク対応済み\n"
            f"    {i['title']}\n"
            f"    未完了: {', '.join(f'「{item}」' for item in i['incomplete_items'])}"
            for i in incomplete_issues
        )
        blocking_reasons.append(
            BlockingReason(
                check_name="incomplete_acceptance_criteria",
                title=f"Closes対象のIssueに未完了の受け入れ条件があります（{len(incomplete_issues)}件）",
                details=(
                    f"該当Issue:\n{issue_details}\n\n"
                    "対処方法:\n"
                    "1. Issueの受け入れ条件を全て実装したか確認\n"
                    "2. 実装済みの場合、Issueのチェックボックスを更新\n"
                    '   gh issue edit {Issue番号} --body "..."\n'
                    "3. 意図的に一部を対象外とする場合、Issueの条件を更新\n"
                    "4. 再度マージを実行\n\n"
                    "理由: 受け入れ条件が未完了のままクローズすると、\n"
                    "Issueが不完全な状態でクローズされます（Issue #598）。"
                ),
            )
        )

    # Check 9.5: Excluded criteria without follow-up Issue (Issue #1458)
    excluded_without_ref = check_excluded_criteria_without_followup(pr_number, commit_issue_numbers)
    if excluded_without_ref:

        def format_excluded_items(items: list[str]) -> str:
            """Format excluded items with truncation indicator."""
            displayed = ", ".join(f"「{item}」" for item in items[:3])
            if len(items) > 3:
                displayed += f" 他{len(items) - 3}件"
            return displayed

        issue_details = "\n".join(
            f"  - Issue #{i['issue_number']}: {i['title']}\n"
            f"    対象外: {format_excluded_items(i['excluded_items'])}"
            for i in excluded_without_ref
        )
        blocking_reasons.append(
            BlockingReason(
                check_name="excluded_criteria_without_followup",
                title=(
                    f"対象外にした受け入れ条件にフォローアップIssueがありません"
                    f"（{len(excluded_without_ref)}件）"
                ),
                details=(
                    f"該当Issue:\n{issue_details}\n\n"
                    "対処方法:\n"
                    "1. 対象外とした条件それぞれについてフォローアップIssueを作成\n"
                    "2. Issueの条件テキストにIssue番号を追加\n"
                    "   例: ~~対象外機能~~ -> #123 で対応\n"
                    "3. 再度マージを実行\n\n"
                    "理由: 受け入れ条件を対象外にする場合、追跡可能性のため\n"
                    "フォローアップIssueが必要です（Issue #1251事例）。"
                ),
            )
        )

    # Check 10: Bug Issues created from review comments (Issue #1130)
    bug_issues = check_bug_issue_from_review(pr_number)
    if bug_issues:
        issue_details = "\n".join(
            f"  - Issue #{i['issue_number']}: {i['title']}" for i in bug_issues
        )
        blocking_reasons.append(
            BlockingReason(
                check_name="bug_issue_from_review",
                title=f"レビューで発見されたバグが別Issueとしてオープンのままです（{len(bug_issues)}件）",
                details=(
                    f"該当Issue:\n{issue_details}\n\n"
                    "⚠️ 問題:\n"
                    "レビューで指摘されたバグを別Issueにしてマージすると、\n"
                    "バグ込みでマージされ、修正が後回しになります。\n\n"
                    "対処方法:\n"
                    "1. このPRで導入したバグなら、同じPRで修正する\n"
                    "2. 既存コードのバグ（偶然発見）なら、Issueをクローズせずマージ可\n"
                    "3. 修正完了後、再度マージを実行\n\n"
                    "背景: PR #1126でレビュー指摘を別Issue化してマージし、\n"
                    "バグ入りコードがマージされた（Issue #1125, #1127, #1128）。"
                ),
            )
        )

    # Check 11: PR body quality (Issue #2439)
    # This check runs in CI regardless of Claude Code session state,
    # providing a safety net when the pr-body-quality-check hook is not loaded.
    pr_body = get_pr_body(pr_number)
    if pr_body is not None:
        is_valid, missing = check_body_quality(pr_body)
        if not is_valid:
            missing_details = "\n".join(f"  - {item}" for item in missing)
            blocking_reasons.append(
                BlockingReason(
                    check_name="pr_body_quality",
                    title="PRボディに必須項目がありません",
                    details=(
                        f"不足している項目:\n{missing_details}\n\n"
                        "**PRボディの推奨フォーマット:**\n"
                        "```markdown\n"
                        "## なぜ\n"
                        "この変更が必要になった背景・動機を記述\n"
                        "\n"
                        "## 何を\n"
                        "変更内容の概要\n"
                        "\n"
                        "Closes #XXX\n"
                        "```\n\n"
                        "対処方法:\n"
                        f'1. `gh pr edit {pr_number} --body "..."` でPRボディを更新\n'
                        "2. 再度マージを実行\n\n"
                        "背景: Issue #2439 - フックのセッション依存性を回避するため、\n"
                        "CIでもPRボディ品質チェックを実行。"
                    ),
                )
            )

    # Check 12: Remaining task patterns without Issue references (Issue #2457)
    remaining_tasks = check_remaining_task_patterns(pr_number, commit_issue_numbers)
    if remaining_tasks:
        issue_details = "\n".join(
            f"  - Issue #{i['issue_number']}: {i['title']}\n"
            f"    検出パターン: {', '.join(f'「{p}」' for p in i['patterns'])}"
            for i in remaining_tasks
        )
        blocking_reasons.append(
            BlockingReason(
                check_name="remaining_task_patterns",
                title=f"Issue参照なしの残タスクパターンが検出されました（{len(remaining_tasks)}件）",
                details=(
                    f"該当Issue:\n{issue_details}\n\n"
                    "⚠️ 問題:\n"
                    "「第2段階」「別PR」「残タスク」等のパターンが検出されましたが、\n"
                    "フォローアップ用のIssue番号（#XXX）が見つかりません。\n\n"
                    "対処方法:\n"
                    "1. 残タスク用の新Issueを作成\n"
                    "   例: `gh issue create --title '残タスク: 第2段階の実装' --body '元のIssue #123 の第2段階として実装予定'`\n"
                    "2. Issue本文に作成したIssue番号を追記\n"
                    "   例: 「第2段階として #1234 で対応予定」\n"
                    "3. 再度マージを実行\n\n"
                    "背景: Issue #2449で残タスクがIssue化されずにクローズされ、\n"
                    "タスクが放置されました。この問題を防止するため、Issue #2457で\n"
                    "残タスクパターン検出機能を実装しました。"
                ),
            )
        )

    return blocking_reasons, warnings
