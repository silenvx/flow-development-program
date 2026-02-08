/**
 * merge-checkフックのマージ条件チェックを集約・オーケストレーションする。
 *
 * Why:
 *   merge-check.pyが肥大化し、各チェックロジックが分散していた。
 *   チェック条件の追加・変更を容易にするため、条件ロジックを集約する。
 *
 * What:
 *   - BlockingReasonインターフェースで構造化されたエラー報告
 *   - runAllPrChecks関数で全PRチェックを一括実行
 *   - AIレビュー、dismissal、修正検証、受け入れ基準等のチェックを統合
 *
 * Remarks:
 *   - merge-check.tsから呼び出される補助モジュール
 *   - dry-runモードにも対応（副作用スキップ）
 *   - マージ済みPRはスキップ（Issue #890）
 *
 * Changelog:
 *   - silenvx/dekita#874: ブロック理由一括収集パターン導入
 *   - silenvx/dekita#890: マージ済みPRスキップ追加
 *   - silenvx/dekita#892: dry-runモード対応
 *   - silenvx/dekita#1458: 対象外条件のフォローアップチェック追加
 *   - silenvx/dekita#1661: コミットIssue番号の事前フェッチ最適化
 *   - silenvx/dekita#2457: 残タスクパターン検出追加
 *   - silenvx/dekita#2463: 完了率表示追加
 *   - silenvx/dekita#2710: Geminiセキュリティ指摘のIssue化強制チェック追加
 *   - silenvx/dekita#2775: Dependabot PRのボディ品質チェックスキップ追加
 *   - silenvx/dekita#3161: TypeScript移行
 */

import {
  QODO_FALSE_POSITIVE_PATTERN,
  checkAiReviewError,
  checkAiReviewing,
  checkQodoComplianceViolation,
  checkQodoFalsePositiveDeclarationFromComments,
  checkUnrespondedAiIssueCommentsFromComments,
  fetchAllIssueComments,
  requestCopilotReview,
} from "./ai_review_checker";
import { checkBodyQuality, truncateBody } from "./check_utils";
import { TIMEOUT_MEDIUM } from "./constants";
import {
  checkNumericClaimsVerified,
  checkResolvedWithoutVerification,
} from "./fix_verification_checker";
import { formatError } from "./format_error";
import { addRepoFlag, buildPrViewArgs, isPrMerged } from "./github";
import {
  extractIssueNumbersFromPrBody,
  fetchIssueAcceptanceCriteria,
  hasIssueReference,
} from "./issue_checker";
import {
  checkDismissalWithoutIssue,
  checkSecurityIssuesWithoutIssue,
  checkUnresolvedAiThreads,
  fetchAllAiReviewThreads,
} from "./review_checker";
import { asyncSpawn } from "./spawn";

// =============================================================================
// Types
// =============================================================================

/**
 * A blocking reason collected during merge checks (Issue #874).
 */
export interface BlockingReason {
  /** Short name for the check (e.g., "ai_reviewing", "dismissal"). */
  checkName: string;
  /** One-line summary of the problem. */
  title: string;
  /** Detailed description including items and remediation steps. */
  details: string;
}

interface IncompleteIssue {
  issueNumber: string;
  title: string;
  incompleteItems: string[];
  completedCount: number;
  totalCount: number;
}

interface ExcludedCriteriaIssue {
  issueNumber: string;
  title: string;
  excludedItems: string[];
}

interface BugIssue {
  issueNumber: string;
  title: string;
}

interface RemainingTaskIssue {
  issueNumber: string;
  title: string;
  patterns: string[];
}

// =============================================================================
// Helper Functions
// =============================================================================

/**
 * Check if PR is from Dependabot.
 */
async function isDependabotPr(prNumber: string, repo: string | null = null): Promise<boolean> {
  try {
    const args = buildPrViewArgs(prNumber, repo, ["--json", "author", "--jq", ".author.login"]);
    const result = await asyncSpawn("gh", args, { timeout: TIMEOUT_MEDIUM * 1000 });

    if (!result.success) {
      return false;
    }

    const author = result.stdout.trim().toLowerCase();
    return author === "dependabot[bot]" || author === "dependabot";
  } catch (error) {
    // Issue #3263: Fail-open with logging for Qodo compliance
    console.error(`[merge_conditions] Failed to check Dependabot PR: ${formatError(error)}`);
    return false;
  }
}

/**
 * Get PR body.
 */
async function getPrBody(prNumber: string, repo: string | null = null): Promise<string | null> {
  try {
    const args = buildPrViewArgs(prNumber, repo, ["--json", "body", "--jq", ".body"]);
    const result = await asyncSpawn("gh", args, { timeout: TIMEOUT_MEDIUM * 1000 });

    if (!result.success) {
      return null;
    }

    return result.stdout;
  } catch (error) {
    // Issue #3263: Fail-open with logging for Qodo compliance
    console.error(`[merge_conditions] Failed to get PR body: ${formatError(error)}`);
    return null;
  }
}

/**
 * Extract issue numbers from commit messages.
 */
async function extractIssueNumbersFromCommits(
  prNumber: string,
  repo: string | null = null,
): Promise<string[]> {
  try {
    const args = buildPrViewArgs(prNumber, repo, [
      "--json",
      "commits",
      "--jq",
      ".commits[].messageHeadline",
    ]);
    const result = await asyncSpawn("gh", args, { timeout: TIMEOUT_MEDIUM * 1000 });

    if (!result.success) {
      return [];
    }

    const issues: string[] = [];
    const pattern = /#(\d+)/g;

    for (const line of result.stdout.split("\n")) {
      // Issue #3161: Reset lastIndex to avoid skipping matches when regex is reused
      pattern.lastIndex = 0;
      let match = pattern.exec(line);
      while (match) {
        issues.push(match[1]);
        match = pattern.exec(line);
      }
    }

    return [...new Set(issues)];
  } catch (error) {
    // Issue #3263: Fail-open with logging for Qodo compliance
    console.error(
      `[merge_conditions] Failed to extract issue numbers from commits: ${formatError(error)}`,
    );
    return [];
  }
}

/**
 * Check for bug issues created from review comments.
 *
 * Issue #1130: Detects the anti-pattern where:
 * 1. AI reviewer points out a bug in the PR code
 * 2. Claude Code creates a separate Issue instead of fixing in-PR
 * 3. PR gets merged with the bug still present
 * 4. Bug Issue remains open
 *
 * Note: This is a simplified implementation compared to Python version.
 * Python version only blocks issues explicitly referenced from review comments
 * and created after the PR. This version searches for any bug issue mentioning
 * the PR, which may be more broad. If false positives occur, consider
 * implementing the full review-comment-based check (Issue #1152).
 */
async function checkBugIssueFromReview(
  prNumber: string,
  repo: string | null = null,
): Promise<BugIssue[]> {
  // Simplified implementation - check if there are open bug issues referencing this PR
  // in the search text. This may catch more issues than intended, but provides
  // basic protection against the anti-pattern described above.
  try {
    const args = [
      "issue",
      "list",
      "--state",
      "open",
      "--label",
      "bug",
      "--search",
      `PR #${prNumber}`,
      "--json",
      "number,title",
      "--limit",
      "10",
    ];
    addRepoFlag(args, repo);
    const result = await asyncSpawn("gh", args, { timeout: TIMEOUT_MEDIUM * 1000 });

    if (!result.success || !result.stdout.trim()) {
      return [];
    }

    const issues = JSON.parse(result.stdout) as Array<{
      number: number;
      title: string;
    }>;

    return issues.map((i) => ({
      issueNumber: String(i.number),
      title: i.title,
    }));
  } catch (error) {
    // Issue #3263: Fail-open with logging for Qodo compliance
    console.error(
      `[merge_conditions] Failed to check bug issues from review: ${formatError(error)}`,
    );
    return [];
  }
}

/**
 * Check for incomplete acceptance criteria in linked issues.
 */
async function checkIncompleteAcceptanceCriteria(
  prNumber: string,
  _commitIssueNumbers: Set<string> | null,
  repo: string | null = null,
): Promise<IncompleteIssue[]> {
  const incomplete: IncompleteIssue[] = [];

  // Get PR body
  const prBody = await getPrBody(prNumber, repo);
  if (!prBody) {
    return [];
  }

  // Extract issue numbers from PR body
  // Issue #1638/#2834: Only check issues from PR body, skip commit-only issues.
  // Commit-only issues (those referenced only in commit messages via "Fixes #XYZ")
  // should not gate merges unless the PR body explicitly closes the issue.
  const issueNumbers = extractIssueNumbersFromPrBody(prBody);

  // Check each issue's acceptance criteria
  for (const issueNumber of issueNumbers) {
    const { success, title, criteria } = await fetchIssueAcceptanceCriteria(issueNumber, repo);

    if (!success || criteria.length === 0) {
      continue;
    }

    const incompleteItems = criteria.filter((c) => !c.isCompleted).map((c) => c.text);

    const completedCount = criteria.filter((c) => c.isCompleted).length;

    if (incompleteItems.length > 0) {
      incomplete.push({
        issueNumber,
        title,
        incompleteItems,
        completedCount,
        totalCount: criteria.length,
      });
    }
  }

  return incomplete;
}

/**
 * Check for excluded criteria without follow-up issues.
 */
async function checkExcludedCriteriaWithoutFollowup(
  prNumber: string,
  _commitIssueNumbers: Set<string> | null,
  repo: string | null = null,
): Promise<ExcludedCriteriaIssue[]> {
  const excluded: ExcludedCriteriaIssue[] = [];

  // Get PR body
  const prBody = await getPrBody(prNumber, repo);
  if (!prBody) {
    return [];
  }

  // Extract issue numbers from PR body
  // Issue #1638: Only check issues from PR body, skip commit-only issues.
  const issueNumbers = extractIssueNumbersFromPrBody(prBody);

  // Check each issue for excluded criteria
  for (const issueNumber of issueNumbers) {
    const { success, title, criteria } = await fetchIssueAcceptanceCriteria(issueNumber, repo);

    if (!success) {
      continue;
    }

    // Find strikethrough items without issue references
    const excludedItems = criteria
      .filter((c) => c.isStrikethrough && !hasIssueReference(c.text))
      .map((c) => c.text);

    if (excludedItems.length > 0) {
      excluded.push({
        issueNumber,
        title,
        excludedItems,
      });
    }
  }

  return excluded;
}

/**
 * Check for remaining task patterns without issue references.
 */
async function checkRemainingTaskPatterns(
  prNumber: string,
  _commitIssueNumbers: Set<string> | null,
  repo: string | null = null,
): Promise<RemainingTaskIssue[]> {
  const remaining: RemainingTaskIssue[] = [];

  // Get PR body
  const prBody = await getPrBody(prNumber, repo);
  if (!prBody) {
    return [];
  }

  // Extract issue numbers from PR body
  // Issue #1638: Only check issues from PR body, skip commit-only issues.
  const issueNumbers = extractIssueNumbersFromPrBody(prBody);

  // Patterns that indicate remaining tasks
  const remainingPatterns = [/第[2-9２-９]段階/, /別PR/, /残タスク/, /将来的に/, /後で対応/];

  for (const issueNumber of issueNumbers) {
    try {
      const args = ["issue", "view", issueNumber, "--json", "title,body"];
      addRepoFlag(args, repo);
      const result = await asyncSpawn("gh", args, { timeout: TIMEOUT_MEDIUM * 1000 });

      if (!result.success) {
        continue;
      }

      const data = JSON.parse(result.stdout) as { title: string; body: string };
      const content = `${data.title}\n${data.body}`;

      const matchedPatterns: string[] = [];
      for (const pattern of remainingPatterns) {
        const match = content.match(pattern);
        if (match && !hasIssueReference(content.slice(match.index))) {
          matchedPatterns.push(match[0]);
        }
      }

      if (matchedPatterns.length > 0) {
        remaining.push({
          issueNumber,
          title: data.title,
          patterns: matchedPatterns,
        });
      }
    } catch (error) {
      // Fail open: log error but continue checking other issues
      console.error(
        `[merge_conditions] Failed to fetch issue #${issueNumber}: ${formatError(error)}`,
      );
    }
  }

  return remaining;
}

// =============================================================================
// Main Function
// =============================================================================

/**
 * Run all PR state checks and return blocking reasons and warnings.
 *
 * This function extracts the core check logic from main() to enable reuse
 * in both hook mode and dry-run mode (Issue #892).
 *
 * When merging a PR from a different repo (gh pr merge -R other/repo 123),
 * the repo parameter is used to query the correct repository.
 *
 * @param prNumber - The PR number to check.
 * @param dryRun - If true, skip side effects like re-requesting reviews.
 * @param repo - Repository in owner/repo format, or null for current repo.
 * @returns Tuple of [blocking_reasons, warnings].
 */
export async function runAllPrChecks(
  prNumber: string,
  dryRun = false,
  repo: string | null = null,
): Promise<[BlockingReason[], string[]]> {
  // Issue #890: Skip all checks if PR is already merged
  if (await isPrMerged(prNumber, repo)) {
    return [[], []];
  }

  const blockingReasons: BlockingReason[] = [];
  const warnings: string[] = [];

  // Issue #3627: Pre-fetch issue comments once for reuse in multiple checks.
  // Both checkQodoFalsePositiveDeclaration and checkUnrespondedAiIssueComments
  // require issue comments, so we fetch them once and share across checks.
  const issueComments = await fetchAllIssueComments(prNumber, repo);

  // Check 3: AI review status
  const aiReviewers = await checkAiReviewing(prNumber, repo);
  if (aiReviewers.length > 0) {
    const reviewersStr = aiReviewers.join(", ");
    blockingReasons.push({
      checkName: "ai_reviewing",
      title: `AIレビューが進行中です（レビュアー: ${reviewersStr}）`,
      details: `レビュー完了を待ってからマージしてください。\n\n確認コマンド:\ngh api repos/:owner/:repo/pulls/${prNumber} --jq '.requested_reviewers[].login'\n# 空なら完了、'Copilot'や'codex'を含む名前があれば進行中`,
    });
  }

  // Check 3.5: AI review error (Copilot encountered error)
  const aiError = await checkAiReviewError(prNumber, repo);
  if (aiError) {
    if (aiError.allowWithWarning) {
      warnings.push(
        `[WARNING] AIレビューが連続でエラー（レビュアー: ${aiError.reviewer}）。以前のレビューが成功しているためマージを許可しますが、確認を推奨します。`,
      );
    } else {
      let retryRequested = false;
      if (!dryRun) {
        retryRequested = await requestCopilotReview(prNumber, repo);
      }

      if (retryRequested) {
        blockingReasons.push({
          checkName: "ai_review_error",
          title: "AIレビューがエラーで失敗（自動で再リクエスト済み）",
          details: `レビュアー: ${aiError.reviewer}\n\n対処方法:\n1. Copilotレビューの完了を待つ（1-2分程度）\n2. レビューコメントに対応\n3. 再度マージを実行\n\n注: 再リクエストは自動で行われました。`,
        });
      } else {
        blockingReasons.push({
          checkName: "ai_review_error",
          title: "AIレビューがエラーで失敗しました",
          details: `レビュアー: ${aiError.reviewer}\n\n対処方法:\n1. GitHubのPRページでCopilotレビューをRe-request\n2. レビュー完了を待つ\n3. 再度マージを実行`,
        });
      }
    }
  }

  // Check 3.6: Qodo compliance violations (Issue #3196)
  const qodoViolations = await checkQodoComplianceViolation(prNumber, repo);
  if (qodoViolations) {
    // Issue #3620: Check for false positive declaration to skip blocking
    // Only honor declarations that are newer than the latest compliance report
    // Issue #3627: Use pre-fetched comments to avoid redundant API calls.
    // Copilot review: Only fetch PR author if a false positive declaration pattern exists.
    // This avoids unnecessary API calls when no declaration is present.
    const hasPossibleDeclaration = issueComments.some((comment) =>
      comment.body?.match(QODO_FALSE_POSITIVE_PATTERN),
    );
    let prAuthor: string | null = null;
    if (hasPossibleDeclaration) {
      const prArgs = buildPrViewArgs(prNumber, repo, ["--json", "author", "--jq", ".author.login"]);
      const prResult = await asyncSpawn("gh", prArgs, { timeout: TIMEOUT_MEDIUM * 1000 });
      if (prResult.success) {
        prAuthor = prResult.stdout.trim() || null;
      }
    }
    const falsePositiveDecl = checkQodoFalsePositiveDeclarationFromComments(
      issueComments,
      prAuthor,
    );
    const reportTime = new Date(qodoViolations.reportTimestamp).getTime();
    const declaredTime = falsePositiveDecl
      ? new Date(falsePositiveDecl.declaredAt).getTime()
      : Number.NaN;
    // Skip if: declaration exists, both timestamps are valid, and declaration is newer
    const shouldSkip =
      falsePositiveDecl &&
      !Number.isNaN(declaredTime) &&
      !Number.isNaN(reportTime) &&
      declaredTime > reportTime;

    if (shouldSkip) {
      console.log(
        `[merge-check] Qodo false positive declared by ${falsePositiveDecl.author}: ${falsePositiveDecl.reason}`,
      );
      // Skip blocking when a valid (non-stale) false positive declaration exists
    } else {
      const violationList = qodoViolations.violations.map((v) => `  - ${v}`).join("\n");
      blockingReasons.push({
        checkName: "qodo_compliance_violation",
        title: `Qodoがコンプライアンス違反を検出しました（${qodoViolations.count}件）`,
        details: `🔴 Not Compliant の指摘があります:\n${violationList}\n\n対処方法:\n1. 各違反項目の詳細をPRコメントで確認\n2. コードを修正して違反を解消\n3. 再度マージを実行\n\nヒント: Qodoの指摘がfalse positiveの場合、PRコメントに\n「Qodo false positive: 理由」と記載してスキップできます（Issue #3620）\n\n参照: Qodo Code Reviewのコンプライアンスチェック`,
      });
    }
  }

  // Check 4: Review dismissal without Issue
  const dismissals = await checkDismissalWithoutIssue(prNumber, repo);
  if (dismissals.length > 0) {
    const dismissalDetails = dismissals
      .map((d) => `  - ${d.path}:${d.line ?? "?"}: ${d.body}`)
      .join("\n");
    blockingReasons.push({
      checkName: "dismissal_without_issue",
      title: `Issueを作成せずにDismissしたレビューがあります（${dismissals.length}件）`,
      details: `該当レビュー:\n${dismissalDetails}\n\n対処方法:\n1. 各dismissに対応するIssueを作成（Issueを作成しないでdismissはNG）\n2. dismissコメントに "Issue #番号 を作成" と追記\n3. 再度マージを実行\n\n理由: AIレビュー指摘を記録なしに却下すると、\n問題が見落とされるリスクがあります。`,
    });
  }

  // Check 5 + 7.6: Unified AI review thread fetch (Issue #3432)
  // Fetches all review threads once and classifies them into:
  // - resolvedWithoutResponse (Check 5): RESOLVED threads without response
  // - unrespondedAiReviewComments (Check 7.6): UNRESOLVED threads without response
  const aiReviewThreadResults = await fetchAllAiReviewThreads(prNumber, repo);

  // Check 5: Resolved without Claude Code response
  const unresponded = aiReviewThreadResults.resolvedWithoutResponse;
  if (unresponded.length > 0) {
    const threadDetails = unresponded.map((t) => `  - [${t.author}] ${t.body}`).join("\n");
    blockingReasons.push({
      checkName: "resolved_without_response",
      title: `Claude Code回答なしでResolveされたスレッドがあります（${unresponded.length}件）`,
      details: `該当スレッド:\n${threadDetails}\n\n対処方法:\n1. 各スレッドにClaude Codeで回答を追加\n   署名: "-- Claude Code" を末尾に追加\n2. 再度マージを実行\n\n理由: AIレビューの指摘に対して、\nClaude Codeが対応した記録が必要です（トレーサビリティ）。`,
    });
  }

  // Check 6: Fix claims without verification
  const unverified = await checkResolvedWithoutVerification(prNumber, repo);
  if (unverified.length > 0) {
    // Issue #3679: Show matched pattern for debugging
    // Use JSON.stringify to escape special characters in pattern (Gemini review suggestion)
    const threadDetails = unverified
      .map((t) => `  - [${t.author}] ${t.fixClaim} (pattern: ${JSON.stringify(t.matchedPattern)})`)
      .join("\n");
    blockingReasons.push({
      checkName: "unverified_fix_claim",
      title: `修正済みの主張が検証されていません（${unverified.length}件）`,
      details: `該当スレッド:\n${threadDetails}\n\n対処方法:\n1. 実際にコードが修正されているか確認\n2. **該当スレッドに返信として** 'Verified: 確認済み' を追加\n   署名: '-- Claude Code' を末尾に追加\n3. 再度マージを実行\n\n⚠️ 注意:\n- PR一般コメント（gh pr comment）は**無効**です\n- 指摘スレッドへの返信のみ有効です\n- 「Verified:」または「検証済み:」キーワードが必須です`,
    });
  }

  // Check 7: Unresolved AI review threads
  const unresolved = await checkUnresolvedAiThreads(prNumber, repo);
  if (unresolved.length > 0) {
    const threadDetails = unresolved
      .map((t) => `  - [${t.author}] ${truncateBody(t.body)}`)
      .join("\n");
    blockingReasons.push({
      checkName: "unresolved_ai_threads",
      title: `未解決のAIレビュースレッドがあります（${unresolved.length}件）`,
      details: `該当スレッド:\n${threadDetails}\n\n対処方法:\n1. 各スレッドに対応（修正、回答、または却下理由を説明）\n2. スレッドをResolve\n3. 再度マージを実行\n\n注: AIレビューの全指摘に対応してからマージしてください。`,
    });
  }

  // Check 7.5: Unresponded AI issue comments (Issue #3391)
  // Issue #3627: Use pre-fetched comments to avoid redundant API calls.
  const unrespondedIssueComments = checkUnrespondedAiIssueCommentsFromComments(issueComments);
  if (unrespondedIssueComments.length > 0) {
    const commentDetails = unrespondedIssueComments
      .map((c) => `  - [${c.author}] ${c.body}`)
      .join("\n");
    blockingReasons.push({
      checkName: "unresponded_ai_issue_comments",
      title: `AIイシューコメントへの返信がありません（${unrespondedIssueComments.length}件）`,
      details: `該当コメント:\n${commentDetails}\n\n対処方法:\n1. 各AIコメントに対してPRコメントで返信\n2. 修正した場合は「修正しました」と記載\n3. 対応不要の場合は「確認済み」または「False positive」と記載\n4. 再度マージを実行`,
    });
  }

  // Check 7.6: Unresponded AI review comments (Issue #3429)
  // Role: Handles UNRESOLVED threads to ensure response before merge.
  // Note: Check 7 (unresolved_ai_threads) already blocks any unresolved AI threads.
  // This check provides specific guidance on the required Claude Code signature.
  // Issue #3432: Data shared with Check 5 via fetchAllAiReviewThreads.
  const unrespondedReviewComments = aiReviewThreadResults.unrespondedAiReviewComments;
  if (unrespondedReviewComments.length > 0) {
    const commentDetails = unrespondedReviewComments
      .map((c) => `  - [${c.author}] ${c.path}:${c.line ?? "?"}: ${c.body}`)
      .join("\n");
    blockingReasons.push({
      checkName: "unresponded_ai_review_comments",
      title: `AIレビューコメントにClaude Code返信がありません（${unrespondedReviewComments.length}件）`,
      details: `該当スレッド:\n${commentDetails}\n\n対処方法:\n1. 各AIレビューコメントに返信を追加\n2. 末尾に署名を追加: "-- Claude Code"\n3. 再度マージを実行\n\n理由: 全てのAIレビュー指摘に対して、\nClaude Codeが対応した記録が必要です。`,
    });
  }

  // Check 8: Numeric claims without verification
  const unverifiedNumeric = await checkNumericClaimsVerified(prNumber, repo);
  if (unverifiedNumeric.length > 0) {
    const threadDetails = unverifiedNumeric
      .map((t) => `  - [${t.author}] ${truncateBody(t.body)}`)
      .join("\n");
    blockingReasons.push({
      checkName: "unverified_numeric_claim",
      title: `数値を含むAI指摘への検証コメントがありません（${unverifiedNumeric.length}件）`,
      details: `該当スレッド:\n${threadDetails}\n\n対処方法:\n1. AIが指摘した数値を自分で確認（文字数、行数など）\n2. **該当スレッドに返信として** 検証結果を追加:\n   「検証済み: 実際は32文字」「Verified: counted 32 chars」\n3. 必ず末尾に署名を追加: "-- Claude Code"\n4. 再度マージを実行`,
    });
  }

  // Pre-fetch commit issue numbers for Check 9 and Check 9.5
  let commitIssueNumbers: Set<string> | null = null;
  try {
    commitIssueNumbers = new Set(await extractIssueNumbersFromCommits(prNumber, repo));
  } catch (e) {
    console.error(`⚠️ Warning: Failed to fetch commit issue numbers: ${formatError(e)}`);
    commitIssueNumbers = null;
  }

  // Check 9: Incomplete acceptance criteria
  const incompleteIssues = await checkIncompleteAcceptanceCriteria(
    prNumber,
    commitIssueNumbers,
    repo,
  );
  if (incompleteIssues.length > 0) {
    const issueDetails = incompleteIssues
      .map(
        (i) =>
          `  ⚠️ Issue #${i.issueNumber}: ${i.completedCount}/${i.totalCount} タスク対応済み\n` +
          `    ${i.title}\n` +
          `    未完了: ${i.incompleteItems
            .slice(0, 3)
            .map((item) => `「${item}」`)
            .join(
              ", ",
            )}${i.incompleteItems.length > 3 ? ` 他${i.incompleteItems.length - 3}件` : ""}`,
      )
      .join("\n");
    blockingReasons.push({
      checkName: "incomplete_acceptance_criteria",
      title: `Closes対象のIssueに未完了の受け入れ条件があります（${incompleteIssues.length}件）`,
      details: `該当Issue:\n${issueDetails}\n\n対処方法:\n1. Issueの受け入れ条件を全て実装したか確認\n2. 実装済みの場合、Issueのチェックボックスを更新\n   gh issue edit {Issue番号} --body "..."\n3. 意図的に一部を対象外とする場合、Issueの条件を更新\n4. 再度マージを実行`,
    });
  }

  // Check 9.5: Excluded criteria without follow-up Issue
  const excludedWithoutRef = await checkExcludedCriteriaWithoutFollowup(
    prNumber,
    commitIssueNumbers,
    repo,
  );
  if (excludedWithoutRef.length > 0) {
    const issueDetails = excludedWithoutRef
      .map(
        (i) =>
          `  - Issue #${i.issueNumber}: ${i.title}\n` +
          `    対象外: ${i.excludedItems
            .slice(0, 3)
            .map((item) => `「${item}」`)
            .join(", ")}${i.excludedItems.length > 3 ? ` 他${i.excludedItems.length - 3}件` : ""}`,
      )
      .join("\n");
    blockingReasons.push({
      checkName: "excluded_criteria_without_followup",
      title: `対象外にした受け入れ条件にフォローアップIssueがありません（${excludedWithoutRef.length}件）`,
      details: `該当Issue:\n${issueDetails}\n\n対処方法:\n1. 対象外とした条件それぞれについてフォローアップIssueを作成\n2. Issueの条件テキストにIssue番号を追加\n   例: ~~対象外機能~~ -> #123 で対応\n3. 再度マージを実行`,
    });
  }

  // Check 10: Bug Issues created from review comments
  const bugIssues = await checkBugIssueFromReview(prNumber, repo);
  if (bugIssues.length > 0) {
    const issueDetails = bugIssues.map((i) => `  - Issue #${i.issueNumber}: ${i.title}`).join("\n");
    blockingReasons.push({
      checkName: "bug_issue_from_review",
      title: `レビューで発見されたバグが別Issueとしてオープンのままです（${bugIssues.length}件）`,
      details: `該当Issue:\n${issueDetails}\n\n⚠️ 問題:\nレビューで指摘されたバグを別Issueにしてマージすると、\nバグ込みでマージされ、修正が後回しになります。\n\n対処方法:\n1. このPRで導入したバグなら、同じPRで修正する\n2. 既存コードのバグ（偶然発見）なら、Issueをクローズせずマージ可\n3. 修正完了後、再度マージを実行`,
    });
  }

  // Check 11: PR body quality
  if (!(await isDependabotPr(prNumber, repo))) {
    const prBody = await getPrBody(prNumber, repo);
    if (prBody !== null) {
      const [isValid, missing] = checkBodyQuality(prBody);
      if (!isValid) {
        const missingDetails = missing.map((item) => `  - ${item}`).join("\n");
        blockingReasons.push({
          checkName: "pr_body_quality",
          title: "PRボディに必須項目がありません",
          details: `不足している項目:\n${missingDetails}\n\n**PRボディの推奨フォーマット:**\n\`\`\`markdown\n## なぜ\nこの変更が必要になった背景・動機を記述\n\n## 何を\n変更内容の概要\n\nCloses #XXX\n\`\`\`\n\n対処方法:\n1. \`gh pr edit ${prNumber} --body "..."\` でPRボディを更新\n2. 再度マージを実行`,
        });
      }
    }
  }

  // Check 12: Remaining task patterns without Issue references
  const remainingTasks = await checkRemainingTaskPatterns(prNumber, commitIssueNumbers, repo);
  if (remainingTasks.length > 0) {
    const issueDetails = remainingTasks
      .map(
        (i) =>
          `  - Issue #${i.issueNumber}: ${i.title}\n` +
          `    検出パターン: ${i.patterns.map((p) => `「${p}」`).join(", ")}`,
      )
      .join("\n");
    blockingReasons.push({
      checkName: "remaining_task_patterns",
      title: `Issue参照なしの残タスクパターンが検出されました（${remainingTasks.length}件）`,
      details: `該当Issue:\n${issueDetails}\n\n⚠️ 問題:\n「第2段階」「別PR」「残タスク」等のパターンが検出されましたが、\nフォローアップ用のIssue番号（#XXX）が見つかりません。\n\n対処方法:\n1. 残タスク用の新Issueを作成\n2. Issue本文に作成したIssue番号を追記\n3. 再度マージを実行`,
    });
  }

  // Check 13: Gemini security warnings without Issue reference
  const securityWarnings = await checkSecurityIssuesWithoutIssue(prNumber, repo);
  if (securityWarnings.length > 0) {
    const warningDetails = securityWarnings
      .map((w) => `  - [${w.severity}] ${w.path}:${w.line ?? "?"}: ${w.body}`)
      .join("\n");
    blockingReasons.push({
      checkName: "security_issues_without_issue",
      title: `Geminiのセキュリティ指摘にIssue参照がありません（${securityWarnings.length}件）`,
      details: `該当スレッド:\n${warningDetails}\n\n⚠️ 問題:\nGeminiがセキュリティ問題（medium以上）を検出しましたが、\n対応するIssueが作成されていません。\n\n対処方法:\n1. 各セキュリティ指摘に対応するIssueを作成\n2. 該当スレッドにIssue番号を追記\n3. 再度マージを実行`,
    });
  }

  return [blockingReasons, warnings];
}
