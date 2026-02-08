#!/usr/bin/env bun
/**
 * 振り返りの観点網羅性を確認し、抜けがあればブロック。
 *
 * Why:
 *   振り返りで特定の観点（根本原因分析、見落とし確認等）が抜けると、
 *   表面的な振り返りになり改善につながらない。観点チェックを強制する。
 *
 * What:
 *   - トランスクリプトから振り返りキーワードを検出
 *   - PERSPECTIVESリストの各観点がカバーされているか確認
 *   - 抜けている観点があればブロック
 *   - セッション内の繰り返しブロックパターンを提示
 *
 * State:
 *   - reads: .claude/logs/metrics/block-patterns-{session_id}.jsonl
 *
 * Remarks:
 *   - ブロック型フック（Stopフック）
 *   - reflection-quality-checkは矛盾検出、本フックは観点網羅性
 *   - 振り返りなしの場合はスキップ
 *
 * Changelog:
 *   - silenvx/dekita#2242: フック追加（観点チェック）
 *   - silenvx/dekita#2251: 警告からブロックに変更
 *   - silenvx/dekita#2272: メタ評価（観点更新提案）追加
 *   - silenvx/dekita#2278: 7日分析からセッション分析に変更
 *   - silenvx/dekita#2289: already_handled_check観点を追加
 *   - silenvx/dekita#2290: meta_reflection観点を追加
 *   - silenvx/dekita#2582: implementation_verification観点を追加
 *   - silenvx/dekita#2771: inconsistency_reality_check観点を追加
 *   - silenvx/dekita#2779: followup_issue_check観点を追加
 *   - silenvx/dekita#2812: prompt_skill_check観点を追加
 *   - silenvx/dekita#2877: action_purpose_alignment観点を追加
 *   - silenvx/dekita#2992: validation_normal_flow_check観点を追加
 *   - silenvx/dekita#3052: duplicate_code_extraction観点を追加
 *   - silenvx/dekita#3161: TypeScript移行
 *   - silenvx/dekita#3487: problem_report_initial_action観点を追加
 *   - silenvx/dekita#3705: review_thread_resolve_check観点を追加
 *   - silenvx/dekita#3953: user_feedback_dismissal_check, fork_session_boundary_check観点を追加
 *   - silenvx/dekita#4004: block_continuation_check観点を追加
 */

import { existsSync, readFileSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";
import { formatError } from "../lib/format_error";
import { logHookExecution } from "../lib/logging";
import { isSafeTranscriptPath } from "../lib/path_validation";
import { makeApproveResult, makeBlockResult } from "../lib/results";
import { createHookContext, isSafeSessionId, parseHookInput } from "../lib/session";

const HOOK_NAME = "reflection_self_check";

// =============================================================================
// Types
// =============================================================================

interface Perspective {
  id: string;
  name: string;
  description: string;
  keywords: string[];
}

interface ReflectionHint {
  hook: string;
  count: number;
  hint: string;
}

// =============================================================================
// Perspectives
// =============================================================================

// Perspectives to check in reflection
// Each perspective has keywords that indicate it was addressed
// Exported for testing
export const PERSPECTIVES: Perspective[] = [
  {
    id: "session_facts",
    name: "セッション事実の確認",
    description: "ログを確認し、客観的事象を把握したか",
    keywords: ["ログ", "確認", "事実", "調査", "分析結果"],
  },
  {
    id: "anomaly_patterns",
    name: "異常パターンの確認",
    description: "通常と異なる動作を確認したか",
    keywords: ["異常", "パターン", "繰り返し", "タイムアウト", "連続", "多発"],
  },
  {
    id: "root_cause",
    name: "根本原因分析",
    description: "表面的な説明で終わらず、なぜなぜ分析をしたか",
    keywords: ["なぜ", "原因", "根本", "本質", "背景"],
  },
  {
    id: "oversight_check",
    name: "見落とし確認",
    description: "「他にないか？」を自問したか",
    keywords: ["他にないか", "3回自問", "見落とし", "漏れ"],
  },
  {
    id: "hasty_judgment",
    name: "安易な判断の回避",
    description: "「問題なし」と判断する前に十分検討したか",
    keywords: ["十分.*検討", "深掘り", "掘り下げ", "詳細.*分析"],
  },
  {
    id: "issue_creation",
    name: "Issue化の確認",
    description: "発見した問題をIssue化したか（または不要な理由を明記したか）",
    keywords: ["Issue", "#\\d+", "作成", "不要", "Issue化"],
  },
  // Issue #2289: Prevent false "already handled" judgments
  {
    id: "already_handled_check",
    name: "「対応済み」判断の検証",
    description:
      "「対応済み」と判断した場合、その仕組みの実行タイミング（Pre/Post/Stop）を確認し、実際に有効か検証したか",
    keywords: [
      "対応済み.*検証",
      "実行タイミング",
      "(Pre|Post|Stop)",
      "フック.*確認",
      "仕組み.*有効",
      "対応済み.*なし", // "「対応済み」判断なし" も許容
    ],
  },
  // Issue #2290: Meta-reflection to ensure reflection quality
  {
    id: "meta_reflection",
    name: "振り返り自体の評価",
    description: "この振り返り自体に改善点はないか、形式的なチェックリスト消化になっていないか",
    keywords: [
      "振り返り自体",
      "メタ.*振り返り",
      "形式的",
      "チェックリスト.*消化",
      "振り返り.*改善",
      "振り返り.*品質",
    ],
  },
  // Issue #2582: Dogfooding verification to ensure implementation is tested
  {
    id: "implementation_verification",
    name: "実装後の動作確認",
    description: "実装後（マージ前）に動作を確認したか（正常系、異常系、Dogfooding）",
    keywords: [
      "動作確認",
      "Dogfooding",
      "正常系.*確認",
      "異常系.*確認",
      "自分で使",
      "実際.*テスト",
      "実データ.*確認",
      "動作確認.*不要", // "動作確認不要"（ドキュメント変更など）も許容
    ],
  },
  // Issue #2771: 不整合発見時に実態を確認して正解を判断する
  {
    id: "inconsistency_reality_check",
    name: "不整合発見時の実態確認",
    description:
      "不整合・矛盾を発見した際、テストや既存コードの期待値を鵜呑みにせず、実態（ファイル名、過去のリファクタリング等）を確認して正解を判断したか",
    keywords: [
      "不整合.*実態",
      "矛盾.*確認",
      "ファイル名.*確認",
      "過去.*変更.*確認",
      "リファクタ.*確認",
      "正しい.*状態",
      "実態.*判断",
      "実際.*ファイル",
      "不整合.*なし", // "不整合なし" も許容
    ],
  },
  // Issue #2779: セッション中の「後で対応」発言がIssue化されているか確認
  {
    id: "followup_issue_check",
    name: "「後で対応」発言のIssue化確認",
    description:
      "セッション中に「別途対応」「後で」「将来的に」等と発言した問題が、Issue化されているか確認したか",
    keywords: [
      "別途対応.*Issue",
      "後で.*Issue",
      "将来.*Issue",
      "フォローアップ.*#\\d+",
      "スコープ外.*#\\d+",
      "別途.*#\\d+",
      "後で.*#\\d+", // "後で #123 で対応" も許容
      "将来.*#\\d+", // "将来 #456 で対応" も許容
      "後で.*なし", // "「後で」発言なし" も許容
      "フォローアップ.*なし",
      "別途対応.*なし",
    ],
  },
  // Issue #2812: 既存プロンプト/Skillを確認せずに操作を実行した問題
  {
    id: "prompt_skill_check",
    name: "既存プロンプト/Skill確認",
    description: "操作実行前に、関連するプロンプト/Skillの手順を確認したか",
    keywords: [
      "プロンプト.*確認",
      "Skill.*確認",
      "prompts/.*読",
      "prompts/.*確認", // "prompts/export-to-fdp.md を確認" も許容
      "手順.*確認.*実行",
      "既存.*ドキュメント.*確認",
      "プロンプト.*なし", // "該当プロンプトなし" も許容
      "Skill.*なし", // "該当Skillなし" も許容
    ],
  },
  // Issue #2877: 「やったこと」と「背景・目的」の整合性確認
  {
    id: "action_purpose_alignment",
    name: "「やったこと」と「背景・目的」の整合性",
    description:
      "報告した「やったこと」が当初の背景・目的と整合しているか確認したか（コマンド単位、sub-agent単位、セッション単位）",
    keywords: [
      "やったこと.*目的",
      "背景.*整合",
      "目的.*一致",
      "目的.*達成",
      "当初の目的",
      "目的.*整合性.*確認",
      "背景.*整合性.*確認",
      "目的.*乖離",
      "目的.*相違",
      "背景.*目的.*確認",
      "目的.*整合.*なし", // "目的との整合性に問題なし" も許容
      "背景.*相違.*なし", // "背景との相違なし" も許容
    ],
  },
  // Issue #2952: Issue作成後に確認を求めていないか
  {
    id: "issue_auto_start_check",
    name: "Issue作成後の自動着手確認",
    description:
      "Issue作成後に「次は何をしますか？」と確認を求めていないか。AGENTS.md原則「セッション内で作成したIssueは確認を求めずに即座に着手」を遵守しているか",
    keywords: [
      "Issue作成後.*確認.*求め",
      "確認を求めず.*着手",
      "即座に着手",
      "自動着手",
      "issue_creation_tracker",
      "次は何をしますか.*違反",
      "確認.*求め.*なし", // "確認を求めることなし" も許容
      "Issue作成後.*即座.*実装",
      "セッション内.*Issue.*完遂",
    ],
  },
  // Issue #2992: バリデーション追加時の正常系確認
  {
    id: "validation_normal_flow_check",
    name: "バリデーション追加時の正常系確認",
    description:
      "新しいチェック/バリデーションを追加する際、正常系フロー（プレースホルダー、特殊パターン等）を壊さないか事前確認したか",
    keywords: [
      "バリデーション.*正常系",
      "チェック.*正常.*フロー",
      "新規.*既存.*検証",
      "セキュリティ.*修正.*正常",
      "プレースホルダー.*確認",
      "例外.*パターン.*確認",
      "正常系.*壊",
      "バリデーション.*追加.*なし", // "バリデーション追加なし" も許容
      "新規チェック.*なし", // "新規チェック追加なし" も許容
    ],
  },
  // Issue #3052: 類似ロジックの重複によるバグを共通ライブラリ化で防止
  {
    id: "duplicate_code_extraction",
    name: "重複コードパターンの抽出",
    description:
      "類似ロジックが複数箇所に存在する場合、再利用可能なモジュール/ライブラリに抽出したか。特にパーサー・イテレーション処理は共通化でバグを防げる",
    keywords: [
      "重複.*コード",
      "重複.*パターン",
      "再利用可能",
      "共通化",
      "ライブラリ.*抽出",
      "モジュール.*作成",
      "トークン.*処理",
      "パーサー.*統一",
      "オプション.*パーサー",
      "lib/.*追加",
      "重複.*なし", // "重複パターンなし" も許容
    ],
  },
  // Issue #3487: 問題報告時の初動でユーザーへの質問を優先する
  {
    id: "problem_report_initial_action",
    name: "問題報告時の初動確認",
    description:
      "問題報告を受けた際、ログ調査より先にユーザーへ「具体的に何が起きたか」を質問したか。ユーザーが既に具体例を知っている可能性を考慮したか",
    keywords: [
      "具体.*質問",
      "具体的.*何.*起き",
      "ユーザー.*質問.*先",
      "ユーザーから.*情報収集",
      "具体例.*確認",
      "ログ調査.*前.*質問",
      "問題報告.*質問",
      "何.*起き.*質問",
      "問題報告.*なし", // "問題報告なし" も許容
      "ユーザーに.*確認",
    ],
  },
  // Issue #3705: レビュースレッドに返信しただけでResolveしなかった問題
  {
    id: "review_thread_resolve_check",
    name: "レビュースレッドのResolve完遂確認",
    description:
      "AIレビュー（Copilot/Codex/greptile等）のスレッドに返信した後、Resolveまで実行したか。返信だけでは完了ではなく、Resolveして初めて対応完了となる",
    keywords: [
      "スレッド.*Resolve",
      "Resolve.*実行",
      "resolveReviewThread",
      "isResolved.*true",
      "スレッド.*解決",
      "Resolve.*完了",
      "返信.*Resolve",
      "スレッド.*対応.*なし", // "レビュースレッド対応なし" も許容
      "AIレビュー.*なし", // "AIレビュー対応なし" も許容
    ],
  },
  // Issue #3953: ユーザー指摘を「不要」と自己判断して無視・却下した問題
  {
    id: "user_feedback_dismissal_check",
    name: "ユーザー指摘の自己判断による却下",
    description:
      "ユーザーが問題を指摘した際、「不要」「問題ない」と自己判断して無視・却下せず、適切に対応（Issue化・修正）したか",
    keywords: [
      "ユーザー.*指摘",
      "指摘.*対応",
      "指摘.*Issue",
      "フィードバック.*対応",
      "指摘.*無視.*なし",
      "却下.*なし",
      "理由.*却下", // 正当な理由を持って却下した場合の報告も許容
    ],
  },
  // Issue #3953: fork-sessionが親セッションの作業に介入した問題
  {
    id: "fork_session_boundary_check",
    name: "fork-sessionの境界遵守",
    description:
      "fork-sessionとして親セッションの作業に介入（ファイル編集、作業継続）しなかったか。独立したIssueのみに着手したか",
    keywords: [
      "fork.*session.*境界",
      "fork.*session.*独立",
      "親.*セッション.*介入.*なし",
      "fork.*session.*該当.*なし",
      "fork.*session.*ではない",
    ],
  },
  // Issue #4004: ブロック後にテキストのみで停止しAskUserQuestionを使わなかった問題
  {
    id: "block_continuation_check",
    name: "ブロック後のツール呼び出し継続確認",
    description:
      "ブロック後にテキストのみで停止せず、AskUserQuestion等のツール呼び出しを継続したか。「進みますか？」等のテキスト質問でエージェントループを停止させなかったか",
    keywords: [
      "ブロック後.*ツール",
      "ブロック後.*継続",
      "AskUserQuestion.*使",
      "ブロック後.*AskUserQuestion",
      "ブロック後.*停止.*なし", // "ブロック後の停止なし" も許容
      "テキスト.*停止.*なし", // "テキスト停止なし" も許容
    ],
  },
];

// Keywords indicating reflection was performed
const REFLECTION_KEYWORDS = ["五省", "振り返り", "反省", "教訓", "改善点"];
const COMPILED_REFLECTION_PATTERN = new RegExp(REFLECTION_KEYWORDS.join("|"));

// Minimum block count to consider as "repeated" pattern
const MIN_REPEAT_COUNT = 2;

// =============================================================================
// Helper Functions
// =============================================================================

/**
 * Check if reflection was performed in the transcript.
 */
function hasReflection(transcriptContent: string): boolean {
  return COMPILED_REFLECTION_PATTERN.test(transcriptContent);
}

/**
 * Check if a perspective was addressed based on keyword presence.
 */
function checkPerspective(transcriptContent: string, keywords: string[]): boolean {
  for (const keyword of keywords) {
    const pattern = new RegExp(keyword);
    if (pattern.test(transcriptContent)) {
      return true;
    }
  }
  return false;
}

/**
 * Get list of perspectives not addressed in the reflection.
 * Exported for testing.
 */
export function getMissingPerspectives(transcriptContent: string): Perspective[] {
  const missing: Perspective[] = [];
  for (const perspective of PERSPECTIVES) {
    if (!checkPerspective(transcriptContent, perspective.keywords)) {
      missing.push(perspective);
    }
  }
  return missing;
}

/**
 * Build a user-friendly checklist message for missing perspectives.
 */
function buildChecklistMessage(missingPerspectives: Perspective[]): string {
  const lines = [
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    "📋 振り返り観点チェック",
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    "",
    "以下の観点について確認しましたか？",
    "",
  ];

  for (const p of missingPerspectives) {
    lines.push(`❓ ${p.name}`);
    lines.push(`   → ${p.description}`);
    lines.push("");
  }

  lines.push("上記の観点が抜けている場合、振り返りを補完してください。");
  lines.push("意図的にスキップした場合は問題ありません。");

  return lines.join("\n");
}

// =============================================================================
// Session Block Pattern Analysis (Issue #2278)
// =============================================================================

/**
 * Get block pattern counts for the current session.
 */
function getSessionBlockPatterns(sessionId: string | null | undefined): Map<string, number> {
  if (!sessionId || !isSafeSessionId(sessionId)) {
    return new Map();
  }

  // Issue #3161: Path should be .claude/logs/metrics (not .claude/hooks/logs/metrics)
  // __dirname = .claude/hooks/handlers, so need 2 levels up to reach .claude
  const claudeDir = resolve(dirname(dirname(__dirname)));
  const safeSessionId = basename(sessionId);
  const logFile = join(claudeDir, "logs", "metrics", `block-patterns-${safeSessionId}.jsonl`);

  if (!existsSync(logFile)) {
    return new Map();
  }

  const hookCounts = new Map<string, number>();

  try {
    const content = readFileSync(logFile, "utf-8");
    for (const line of content.trim().split("\n")) {
      if (!line.trim()) continue;

      try {
        const entry = JSON.parse(line) as { type?: string; hook?: string };
        if (entry.type !== "block") continue;

        const hook = entry.hook ?? "";
        if (hook) {
          hookCounts.set(hook, (hookCounts.get(hook) ?? 0) + 1);
        }
      } catch {
        // 無効なJSON行、スキップ
      }
    }
  } catch {
    // Log file may not exist or be inaccessible
  }

  return hookCounts;
}

/**
 * Analyze session block patterns to suggest reflection points.
 */
function analyzeSessionReflectionHints(blockPatterns: Map<string, number>): ReflectionHint[] {
  const hints: ReflectionHint[] = [];

  // Find hooks that blocked multiple times (repeated patterns)
  const repeated: Array<[string, number]> = [];
  for (const [hook, count] of blockPatterns.entries()) {
    if (count >= MIN_REPEAT_COUNT) {
      repeated.push([hook, count]);
    }
  }

  // Sort by count descending
  repeated.sort((a, b) => b[1] - a[1]);

  // Generate hints for top repeated patterns (limit to 3 to avoid noise)
  for (const [hook, count] of repeated.slice(0, 3)) {
    hints.push({
      hook,
      count,
      hint: `'${hook}' が${count}回ブロック → なぜ繰り返したか振り返る`,
    });
  }

  return hints;
}

/**
 * Build a message for session-based reflection hints.
 */
function buildSessionHintsMessage(hints: ReflectionHint[]): string {
  if (hints.length === 0) {
    return "";
  }

  const lines = [
    "",
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    "💡 このセッションの振り返りポイント",
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    "",
    "同じブロックが繰り返し発生しています:",
    "",
  ];

  for (const hint of hints) {
    lines.push(`  🔄 ${hint.hint}`);
  }

  lines.push("");
  lines.push("繰り返しの原因を振り返り、改善策を検討してください。");

  return lines.join("\n");
}

// =============================================================================
// Main
// =============================================================================

async function main(): Promise<void> {
  let result = makeApproveResult(HOOK_NAME);

  try {
    const input = await parseHookInput();
    const ctx = createHookContext(input);
    const sessionId = ctx.sessionId;

    // Get transcript content
    const transcriptPath = input.transcript_path ?? "";
    let transcriptContent = "";

    if (transcriptPath && isSafeTranscriptPath(transcriptPath)) {
      try {
        transcriptContent = readFileSync(transcriptPath, "utf-8");
      } catch {
        // Best effort - transcript read failure should not break hook
      }
    }

    // Only check if reflection was performed
    if (!hasReflection(transcriptContent)) {
      await logHookExecution(
        HOOK_NAME,
        "approve",
        "No reflection detected, skipping perspective check",
        undefined,
        { sessionId: sessionId ?? undefined },
      );
      console.log(JSON.stringify(result));
      return;
    }

    // Get missing perspectives
    const missing = getMissingPerspectives(transcriptContent);

    // Analyze current session's block patterns (Issue #2278)
    const blockPatterns = getSessionBlockPatterns(sessionId);
    const hints = analyzeSessionReflectionHints(blockPatterns);
    const hintsMessage = buildSessionHintsMessage(hints);

    if (missing.length > 0) {
      // Block when perspectives are missing (Issue #2251)
      let message = buildChecklistMessage(missing);
      if (hintsMessage) {
        message += `\n${hintsMessage}`;
      }
      result = makeBlockResult(HOOK_NAME, message, ctx);
      console.log(JSON.stringify(result));
      process.exit(2);
    } else {
      // All perspectives covered, but show session hints if any
      if (hintsMessage) {
        // Warn but don't block
        console.error(hintsMessage);
      }
      await logHookExecution(
        HOOK_NAME,
        "approve",
        `All perspectives addressed. Session hints: ${hints.length}`,
        undefined,
        { sessionId: sessionId ?? undefined },
      );
    }
  } catch (e) {
    const error = e instanceof Error ? e.message : String(e);
    await logHookExecution(HOOK_NAME, "error", `Hook error: ${formatError(error)}`);
  }

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main().catch((e) => {
    console.error(`[${HOOK_NAME}] Fatal error: ${formatError(e)}`);
    console.log(JSON.stringify(makeApproveResult(HOOK_NAME)));
  });
}
