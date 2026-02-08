#!/usr/bin/env bun
/**
 * レビュースレッドResolve時に応答コメントを強制。
 *
 * Why:
 *   レビューコメントに返信せずにResolveすると、レビュアーへの説明責任が
 *   果たされず、対応内容が不明確になる。返信を強制する。
 *
 * What:
 *   - resolveReviewThread GraphQL mutationを検出
 *   - スレッド内にClaude Code応答コメントがあるか確認
 *   - 応答なしの場合はブロック
 *   - 修正主張には検証内容（Verified:）を要求
 *   - 範囲外発言にはIssue番号を要求
 *
 * Remarks:
 *   - ブロック型フック（PreToolUse:Bash）
 *   - batch_resolve_threads.tsの使用を推奨
 *   - REST APIも併用してコメント取得（GraphQLの遅延対策）
 *   - fail-open設計（APIエラー時は許可）
 *   - Python版: resolve_thread_guard.py
 *
 * Changelog:
 *   - silenvx/dekita#xxx: フック追加
 *   - silenvx/dekita#964: 修正主張の検証（Verified:）を追加
 *   - silenvx/dekita#1018: スレッドレベルの検証に変更
 *   - silenvx/dekita#1271: REST API併用でコメント取得
 *   - silenvx/dekita#2917: TypeScript版初期実装
 *   - silenvx/dekita#3068: コミットハッシュ必須チェックを追加
 */

import { execSync } from "node:child_process";
import { TIMEOUT_HEAVY, TIMEOUT_MEDIUM } from "../lib/constants";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { makeApproveResult, makeBlockResult } from "../lib/results";
import { createHookContext, parseHookInput } from "../lib/session";

const HOOK_NAME = "resolve-thread-guard";

// Verification patterns
const VERIFICATION_PATTERNS = ["verified:", "検証済み:", "確認済み:", "verified at"];

// Issue #3068: Fix claim patterns (shared between hasFixClaimWithoutVerification
// and hasFixClaimWithoutCommitHash). Aligned with Python version.
const FIX_CLAIM_PATTERNS = [
  "fixed:",
  "already addressed:",
  "added ",
  "updated ",
  "changed ",
  "implemented ",
  "修正済み",
  "対応済み",
];

// Issue #3068: Commit hash pattern for detecting commit references in fix claims
// Requires at least one digit (0-9) to exclude English words like "defaced", "feedback"
// Uses word boundaries to match standalone hex strings only
// Matches: abc1234, a1b2c3d, 0123456789abcdef (but NOT: defaced, abcdefg)
const COMMIT_HASH_PATTERN = /\b(?=[a-f0-9]*[0-9])[a-f0-9]{7,40}\b/i;

// Issue #1657: Keywords indicating out-of-scope response
// Issue #2821: 追加のキーワード
const OUT_OF_SCOPE_KEYWORDS = [
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
  "今後対応",
  "後回し",
  "次フェーズ",
  "対象外",
];

/**
 * Check if character is Japanese.
 *
 * Issue #1685: ord(c) > 127 では Latin-1 文字も誤判定されるため、
 * 正確なUnicode範囲チェックを使用する。
 */
function isJapaneseChar(c: string): boolean {
  if (c.length !== 1) {
    throw new Error("isJapaneseChar expects a single-character string");
  }
  const code = c.charCodeAt(0);
  return (
    (0x3040 <= code && code <= 0x309f) || // ひらがな
    (0x30a0 <= code && code <= 0x30ff) || // カタカナ
    (0x4e00 <= code && code <= 0x9fff) || // CJK統合漢字
    (0xff61 <= code && code <= 0xff9f) || // 半角カタカナ
    (0x3000 <= code && code <= 0x303f) // 和文記号・句読点
  );
}

/**
 * Get repository owner and name from git remote.
 */
function getRepoOwnerAndName(): { owner: string; name: string } | null {
  try {
    const result = execSync("gh repo view --json owner,name", {
      encoding: "utf-8",
      timeout: TIMEOUT_MEDIUM * 1000,
      stdio: ["pipe", "pipe", "pipe"],
    });

    const data = JSON.parse(result);
    const owner = data?.owner?.login;
    const name = data?.name;

    if (!owner || !name) {
      return null;
    }

    return { owner, name };
  } catch {
    return null;
  }
}

/**
 * Extract thread ID from resolveReviewThread mutation.
 */
function extractThreadId(command: string): string | null {
  const patterns = [
    /-[Ff]\s+threadId=([^\s"']+)/,
    /-[Ff]\s+threadId=["']([^"']+)["']/,
    /threadId:\s*["']([^"']+)["']/,
    /threadId:\s*\\"([^"\\]+)\\"/,
    /"threadId"\s*:\s*"([^"]+)"/,
  ];

  for (const pattern of patterns) {
    const match = command.match(pattern);
    if (match) {
      return match[1];
    }
  }

  return null;
}

/**
 * Check if comment body contains Claude Code signature.
 */
function hasClaudeCodeSignature(body: string): boolean {
  for (const line of body.split("\n")) {
    const stripped = line.trim();
    if (stripped === "-- Claude Code") {
      return true;
    }
  }
  return false;
}

/**
 * Check if comment claims a fix but lacks verification.
 */
function hasFixClaimWithoutVerification(body: string): boolean {
  const bodyLower = body.toLowerCase();

  const hasFixClaim = FIX_CLAIM_PATTERNS.some((p) => bodyLower.includes(p));
  if (!hasFixClaim) {
    return false;
  }

  const hasVerification = VERIFICATION_PATTERNS.some((p) => bodyLower.includes(p));
  return !hasVerification;
}

/**
 * Check if comment body contains a commit hash reference.
 * Issue #3068: Added to detect commit hash in fix claims.
 */
export function hasCommitHash(body: string): boolean {
  return COMMIT_HASH_PATTERN.test(body.toLowerCase());
}

/**
 * Check if comment claims a fix but lacks commit hash reference.
 * Issue #3068: Each fix claim must include its own commit hash.
 */
export function hasFixClaimWithoutCommitHash(body: string): boolean {
  const bodyLower = body.toLowerCase();

  const hasFixClaim = FIX_CLAIM_PATTERNS.some((p) => bodyLower.includes(p));
  if (!hasFixClaim) {
    return false;
  }

  return !hasCommitHash(body);
}

/**
 * Check if comment body contains verification.
 */
function hasVerification(body: string): boolean {
  const bodyLower = body.toLowerCase();
  return VERIFICATION_PATTERNS.some((p) => bodyLower.includes(p));
}

/**
 * Check if comment has out-of-scope keyword without Issue reference.
 */
function hasOutOfScopeWithoutIssue(body: string): { hasProblem: boolean; keyword: string | null } {
  const bodyLower = body.toLowerCase();

  let detectedKeyword: string | null = null;
  for (const keyword of OUT_OF_SCOPE_KEYWORDS) {
    const keywordLower = keyword.toLowerCase();

    // Japanese keywords: simple substring match
    // English keywords: word boundary match
    const hasJapanese = [...keyword].some((c) => {
      try {
        return isJapaneseChar(c);
      } catch {
        return false;
      }
    });

    if (hasJapanese) {
      if (bodyLower.includes(keywordLower)) {
        detectedKeyword = keyword;
        break;
      }
    } else {
      const pattern = new RegExp(`\\b${escapeRegex(keywordLower)}\\b`);
      if (pattern.test(bodyLower)) {
        detectedKeyword = keyword;
        break;
      }
    }
  }

  if (!detectedKeyword) {
    return { hasProblem: false, keyword: null };
  }

  // Check for Issue reference patterns
  const issuePattern = /(?:^|[^\w#])#(\d+)|[Ii]ssue\s*#?(\d+)/m;
  const hasIssueRef = issuePattern.test(body);

  if (hasIssueRef) {
    return { hasProblem: false, keyword: null };
  }

  return { hasProblem: true, keyword: detectedKeyword };
}

/**
 * Escape string for use in regex.
 */
function escapeRegex(str: string): string {
  return str.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

interface Comment {
  body?: string;
  author?: { login?: string };
  databaseId?: number;
  in_reply_to_id?: number;
}

/**
 * Check for replies via REST API (Issue #1271).
 */
function checkRestApiReplies(
  owner: string,
  repo: string,
  prNumber: number,
  originalCommentId: number,
): Comment[] {
  try {
    const result = execSync(
      `gh api /repos/${owner}/${repo}/pulls/${prNumber}/comments --paginate`,
      {
        encoding: "utf-8",
        timeout: TIMEOUT_HEAVY * 1000,
        stdio: ["pipe", "pipe", "pipe"],
      },
    );

    const allComments: Comment[] = [];
    for (const line of result.trim().split("\n")) {
      if (line) {
        try {
          const pageComments = JSON.parse(line);
          if (Array.isArray(pageComments)) {
            allComments.push(...pageComments);
          }
        } catch {
          // 無効なJSON行、スキップ
        }
      }
    }

    // Filter to find replies to the original comment
    return allComments.filter((comment: Comment) => comment.in_reply_to_id === originalCommentId);
  } catch {
    return [];
  }
}

interface ThreadCheckResult {
  hasResponse: boolean;
  hasUnverifiedFix: boolean;
  hasFixWithoutCommitHash: boolean; // Issue #3068
  outOfScopeKeyword: string | null;
  threadFound: boolean;
  originalComment: string;
  author: string;
  prNumber: number | null;
  commentId: number | null;
}

/**
 * Check if the thread has a Claude Code response comment.
 */
function checkThreadHasResponse(threadId: string): ThreadCheckResult {
  const failOpen: ThreadCheckResult = {
    hasResponse: true,
    hasUnverifiedFix: false,
    hasFixWithoutCommitHash: false, // Issue #3068
    outOfScopeKeyword: null,
    threadFound: false,
    originalComment: "",
    author: "unknown",
    prNumber: null,
    commentId: null,
  };

  const repoInfo = getRepoOwnerAndName();
  if (!repoInfo) {
    return failOpen;
  }

  const query = `
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
  `;

  try {
    const result = execSync(
      `gh api graphql -f query='${query.replace(/'/g, "'\\''")}' -F id=${threadId}`,
      {
        encoding: "utf-8",
        timeout: TIMEOUT_HEAVY * 1000,
        stdio: ["pipe", "pipe", "pipe"],
      },
    );

    const data = JSON.parse(result);
    const node = data?.data?.node;

    if (!node) {
      return failOpen;
    }

    const comments: Comment[] = node.comments?.nodes || [];
    const prNumber = node.pullRequest?.number || null;

    if (comments.length === 0) {
      return { ...failOpen, threadFound: true };
    }

    const firstComment = comments[0];
    const originalBody = (firstComment.body || "").slice(0, 100);
    const originalAuthor = firstComment.author?.login || "unknown";
    const commentId = firstComment.databaseId || null;

    // Check REST API for replies
    let restReplies: Comment[] = [];
    if (prNumber && commentId) {
      restReplies = checkRestApiReplies(repoInfo.owner, repoInfo.name, prNumber, commentId);
    }

    const allComments = [...comments, ...restReplies];

    // Check if any comment has Claude Code signature
    const hasResponse = allComments.some((c) => hasClaudeCodeSignature(c.body || ""));

    // Check for unverified fix claims
    const hasFixClaim = allComments.some(
      (c) => hasClaudeCodeSignature(c.body || "") && hasFixClaimWithoutVerification(c.body || ""),
    );
    const threadHasVerification = allComments.some((c) => hasVerification(c.body || ""));
    const hasUnverifiedFix = hasFixClaim && !threadHasVerification;

    // Issue #3068: Check if any Claude Code comment claims a fix without commit hash
    // Comment-level check: each fix claim must include its own commit hash
    const hasFixWithoutCommitHash = allComments.some(
      (c) => hasClaudeCodeSignature(c.body || "") && hasFixClaimWithoutCommitHash(c.body || ""),
    );

    // Check for out-of-scope keywords
    const issuePattern = /(?:^|[^\w#])#(\d+)|[Ii]ssue\s*#?(\d+)/m;
    let threadHasIssueRef = false;
    for (const comment of allComments) {
      if (hasClaudeCodeSignature(comment.body || "")) {
        if (issuePattern.test(comment.body || "")) {
          threadHasIssueRef = true;
          break;
        }
      }
    }

    let outOfScopeKeyword: string | null = null;
    if (!threadHasIssueRef) {
      for (const comment of allComments) {
        if (hasClaudeCodeSignature(comment.body || "")) {
          const result = hasOutOfScopeWithoutIssue(comment.body || "");
          if (result.hasProblem) {
            outOfScopeKeyword = result.keyword;
            break;
          }
        }
      }
    }

    return {
      hasResponse,
      hasUnverifiedFix,
      hasFixWithoutCommitHash, // Issue #3068
      outOfScopeKeyword,
      threadFound: true,
      originalComment: originalBody,
      author: originalAuthor,
      prNumber,
      commentId,
    };
  } catch {
    return failOpen;
  }
}

async function main(): Promise<void> {
  const data = await parseHookInput();
  const ctx = createHookContext(data);
  const sessionId = ctx.sessionId;
  const toolName = (data.tool_name as string) || "";
  const toolInput = (data.tool_input as Record<string, unknown>) || {};

  // Only process Bash commands
  if (toolName !== "Bash") {
    const result = makeApproveResult(HOOK_NAME);
    console.log(JSON.stringify(result));
    return;
  }

  const command = (toolInput.command as string) || "";

  // Check if this is a resolveReviewThread GraphQL mutation
  if (!command.includes("gh") || !command.includes("graphql")) {
    const result = makeApproveResult(HOOK_NAME);
    console.log(JSON.stringify(result));
    return;
  }

  if (!command.includes("resolveReviewThread")) {
    const result = makeApproveResult(HOOK_NAME);
    console.log(JSON.stringify(result));
    return;
  }

  // Extract thread ID
  const threadId = extractThreadId(command);
  if (!threadId) {
    await logHookExecution(
      HOOK_NAME,
      "approve",
      "Could not extract thread ID, allowing",
      undefined,
      { sessionId },
    );
    const result = makeApproveResult(HOOK_NAME);
    console.log(JSON.stringify(result));
    return;
  }

  // Check if thread has a response
  const checkResult = checkThreadHasResponse(threadId);

  if (checkResult.hasResponse) {
    // Check for unverified fix claims
    if (checkResult.hasUnverifiedFix) {
      const { author, originalComment } = checkResult;
      const snippet = originalComment.slice(0, 80);

      const blockReason = `「修正済み」と書いていますが、検証内容がありません。

**問題:**
「修正済み」と主張していますが、「Verified:」による具体的な検証内容が含まれていません。
実際にコードを読んで確認したことを証明してください。

**正しい形式:**
\`\`\`
修正済み: コミット xxx で修正

Verified: [ファイル名]:[行番号] で [具体的に確認した内容]

-- Claude Code
\`\`\`

**対象スレッド:** ${threadId}
**投稿者:** ${author}
**コメント抜粋:** ${snippet}...`;

      await logHookExecution(
        HOOK_NAME,
        "block",
        `Unverified fix claim in thread ${threadId}`,
        undefined,
        { sessionId },
      );
      const result = makeBlockResult(HOOK_NAME, blockReason);
      console.log(JSON.stringify(result));
      process.exit(2);
    }

    // Issue #3068: Check for fix claims without commit hash reference
    if (checkResult.hasFixWithoutCommitHash) {
      const { author, originalComment } = checkResult;
      const snippet = originalComment.slice(0, 80);

      const blockReason = `「修正済み」と書いていますが、コミットハッシュがありません。

**問題:**
「修正済み」「対応済み」などの修正主張には、対応コミットハッシュが必要です。
どのコミットで対応したかを明示してください。

**正しい形式:**
\`\`\`
対応済み: コミット abc1234 で修正しました。

Verified: [ファイル名]:[行番号] で [具体的に確認した内容]

-- Claude Code
\`\`\`

**対象スレッド:** ${threadId}
**投稿者:** ${author}
**コメント抜粋:** ${snippet}...`;

      await logHookExecution(
        HOOK_NAME,
        "block",
        `Fix claim without commit hash in thread ${threadId}`,
        undefined,
        { sessionId },
      );
      const result = makeBlockResult(HOOK_NAME, blockReason);
      console.log(JSON.stringify(result));
      process.exit(2);
    }

    // Check for out-of-scope keyword without Issue reference
    if (checkResult.outOfScopeKeyword) {
      const { author, originalComment, outOfScopeKeyword } = checkResult;
      const snippet = originalComment.slice(0, 80);

      const blockReason = `範囲外発言にIssue番号がありません。

**まず確認してください:**
- 本当にスコープ外ですか？
- 5分以内で修正できるなら、このPRで対応すべきです
- Issueを作成しても、このセッションで着手する必要があります

**スコープ外が妥当な場合のみ:**
1. \`gh issue create --title "..." --label "enhancement" --body "..."\`
2. コメントに Issue番号を含める（例: "Issue #1234 を作成しました"）
3. 再度Resolveを実行

**注:** 作成したIssueにはこのセッションで着手してください。

**検出されたキーワード:** ${outOfScopeKeyword}
**対象スレッド:** ${threadId}
**投稿者:** ${author}
**コメント抜粋:** ${snippet}...`;

      await logHookExecution(
        HOOK_NAME,
        "block",
        `Out-of-scope without Issue in thread ${threadId}`,
        undefined,
        { sessionId },
      );
      const result = makeBlockResult(HOOK_NAME, blockReason);
      console.log(JSON.stringify(result));
      process.exit(2);
    }

    // Log review comment resolution (best-effort, don't block on failure)
    // Note: Simplified logging compared to Python version

    await logHookExecution(
      HOOK_NAME,
      "approve",
      `Thread ${threadId} has Claude Code response`,
      undefined,
      { sessionId },
    );
    const result = makeApproveResult(HOOK_NAME);
    console.log(JSON.stringify(result));
    return;
  }

  // Block: No Claude Code response found
  const { author, originalComment, prNumber } = checkResult;
  const snippet = originalComment.slice(0, 80);
  const prNum = prNumber || "<PR番号>";

  const blockReason = `コメントなしでResolveしようとしています。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📌 推奨: batch_resolve_threads.ts を使用
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
bun run .claude/scripts/batch_resolve_threads.ts ${prNum} "対応しました"

このコマンドで:
✓ 全未解決スレッドに「対応しました」と返信
✓ 返信後に自動でResolve
✓ 署名 (-- Claude Code) も自動追加

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**対象スレッド:** ${threadId}
**投稿者:** ${author}
**コメント抜粋:** ${snippet}...

**手動で対応する場合:**
1. スレッドに返信を追加（末尾に「-- Claude Code」必須）
2. 返信後にResolveを実行`;

  await logHookExecution(
    HOOK_NAME,
    "block",
    `No Claude Code response in thread ${threadId}`,
    undefined,
    { sessionId },
  );
  const result = makeBlockResult(HOOK_NAME, blockReason);
  console.log(JSON.stringify(result));
  process.exit(2);
}

if (import.meta.main) {
  main().catch((e) => {
    console.error(`[${HOOK_NAME}] Fatal error: ${formatError(e)}`);
    console.log(JSON.stringify({}));
  });
}
