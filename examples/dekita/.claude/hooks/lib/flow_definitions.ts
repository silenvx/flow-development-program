/**
 * Flowの定義モジュール - 全フロー設定の単一真実源。
 *
 * Why:
 *   JSONでのフロー定義では動的パターンマッチングや型安全性が得られない。
 *   TypeScript化することでIDEサポート、テスト容易性、ロジックとの統合を実現。
 *
 * What:
 *   - FlowDefinitionクラスによる動的パターンマッチング
 *   - 13フェーズ開発ワークフローのフェーズ定義
 *   - ワークフロー検証用の期待フック動作定義
 *   - TaskType列挙によるセッション成果ベース評価
 *
 * Remarks:
 *   - flow-definitions.jsonを置き換え
 *   - getFlowDefinition()、getPhase()、getExpectedHooksForPhase()が主要API
 *
 * Changelog:
 *   - silenvx/dekita#3051: JSONからPython化
 *   - silenvx/dekita#3157: TypeScriptに移植
 */

// =============================================================================
// Task Type Definitions - for session outcome-based evaluation
// =============================================================================

/**
 * Task type for session outcome-based evaluation.
 *
 * Inferred from session outcomes (PRs, Issues, commits) at session end.
 */
export enum TaskType {
  /** PRs merged during session */
  IMPLEMENTATION = "implementation",
  /** PRs created but not merged */
  IMPLEMENTATION_WIP = "implementation_wip",
  /** Pushed to existing PRs (review comment response) */
  REVIEW_RESPONSE = "review_response",
  /** Only created Issues */
  ISSUE_CREATION = "issue_creation",
  /** No commits or code changes (research/investigation) */
  RESEARCH = "research",
  /** Cleanup, refactoring without new features */
  MAINTENANCE = "maintenance",
  /** Could not determine task type */
  UNKNOWN = "unknown",
}

/**
 * Session outcomes for task type estimation.
 */
export interface SessionOutcomes {
  prs_merged?: number[];
  prs_created?: number[];
  prs_pushed?: number[];
  issues_created?: number[];
  commits_count?: number;
}

/**
 * Estimate task type from session outcomes.
 */
export function estimateTaskType(outcomes: SessionOutcomes): TaskType {
  const prsMerged = outcomes.prs_merged ?? [];
  const prsCreated = outcomes.prs_created ?? [];
  const prsPushed = outcomes.prs_pushed ?? [];
  const issuesCreated = outcomes.issues_created ?? [];
  const commitsCount = outcomes.commits_count ?? 0;

  // PRs merged -> implementation complete
  if (prsMerged.length > 0) {
    return TaskType.IMPLEMENTATION;
  }

  // PRs created but not merged -> work in progress
  if (prsCreated.length > 0) {
    return TaskType.IMPLEMENTATION_WIP;
  }

  // Pushed to existing PRs -> review response
  if (prsPushed.length > 0) {
    return TaskType.REVIEW_RESPONSE;
  }

  // Only Issues created -> issue creation task
  if (issuesCreated.length > 0 && commitsCount === 0) {
    return TaskType.ISSUE_CREATION;
  }

  // No commits -> research/investigation
  if (commitsCount === 0) {
    return TaskType.RESEARCH;
  }

  // Commits but no PR -> maintenance/local work
  if (commitsCount > 0 && prsCreated.length === 0 && prsMerged.length === 0) {
    return TaskType.MAINTENANCE;
  }

  return TaskType.UNKNOWN;
}

// =============================================================================
// Phase Definitions - 13-phase development workflow
// =============================================================================

/**
 * A phase in the development workflow.
 *
 * Phases represent logical stages of the development process,
 * each with expected hook activations.
 */
export interface Phase {
  /** Unique identifier for the phase */
  id: string;
  /** Display name (Japanese) */
  name: string;
  /** Detailed description */
  description: string;
  /** Order index (0-based) */
  order: number;
  /** List of hook names expected to fire in this phase */
  expectedHooks: string[];
  /** FlowStep ID that triggers this phase (null = manual/automatic) */
  triggerStep: string | null;
  /** FlowStep ID that completes this phase */
  completionStep: string | null;
}

/**
 * 13 phases based on docs/development-flow.md
 */
export const DEVELOPMENT_PHASES: Phase[] = [
  {
    id: "session_start",
    name: "セッション開始",
    description: "セッション開始時の自動フック発動",
    order: 0,
    expectedHooks: [
      "date_context_injector",
      "check-lefthook.sh",
      "session_handoff_reader",
      "open_pr_warning",
      "branch_check",
    ],
    triggerStep: null,
    completionStep: null,
  },
  {
    id: "pre_check",
    name: "事前確認",
    description: "Issue/worktree/PRの確認",
    order: 1,
    expectedHooks: [
      "open_issue_reminder",
      "task_start_checklist",
      "research_requirement_check",
      "planning_enforcement",
      "locked_worktree_guard",
      "plan_ai_review",
    ],
    triggerStep: null,
    completionStep: "worktree_created",
  },
  {
    id: "worktree_create",
    name: "Worktree作成",
    description: "Issue用worktreeの作成",
    order: 2,
    expectedHooks: [
      "worktree_path_guard",
      "orphan_worktree_check",
      "merged_worktree_check",
      "active_worktree_check",
      "issue_auto_assign",
      "development_workflow_tracker",
      "git_operations_tracker",
      "flow_progress_tracker",
      "worktree_main_freshness_check",
    ],
    triggerStep: "worktree_created",
    completionStep: "worktree_created",
  },
  {
    id: "implementation",
    name: "実装",
    description: "コード編集・変更",
    order: 3,
    expectedHooks: [
      "worktree_warning",
      "empty_return_check",
      "ui_check_reminder",
      "dependency_check_reminder",
      "hooks_design_check",
      "rework_tracker",
      "tool_efficiency_tracker",
      "exploration_tracker",
      "research_tracker",
    ],
    triggerStep: "worktree_created",
    completionStep: "committed",
  },
  {
    id: "pre_commit_check",
    name: "コミット前検証",
    description: "lint/test/typecheckの実行",
    order: 4,
    expectedHooks: [
      "existing_impl_check",
      "bash_failure_tracker",
      "e2e_test_check",
      "e2e_test_recorder",
    ],
    triggerStep: "implementation",
    completionStep: "committed",
  },
  {
    id: "local_ai_review",
    name: "ローカルAIレビュー",
    description: "codex reviewの実行",
    order: 5,
    expectedHooks: ["codex_review_logger", "codex_review_output_logger"],
    triggerStep: "committed",
    completionStep: "pushed",
  },
  {
    id: "pr_create",
    name: "PR作成",
    description: "PRの作成とレビュー依頼",
    order: 6,
    expectedHooks: [
      "codex_review_check",
      "pr_scope_check",
      "closes_keyword_check",
      "closes_validation",
      "pr_issue_assign_check",
      "pr_overlap_check",
      "pr_issue_alignment_check",
      "pr_metrics_collector",
    ],
    triggerStep: "pushed",
    completionStep: "pr_created",
  },
  {
    id: "issue_work",
    name: "Issue作成",
    description: "問題発見時のIssue作成",
    order: 7,
    expectedHooks: ["issue_label_check", "issue_scope_check", "issue_creation_tracker"],
    triggerStep: null,
    completionStep: null,
  },
  {
    id: "ci_review",
    name: "CI監視+レビュー対応",
    description: "CI完了待ちとレビューコメント対応",
    order: 8,
    expectedHooks: [
      "ci_wait_check",
      "ci_recovery_tracker",
      "copilot_review_retry_suggestion",
      "issue_comments_check",
      "issue_review_response_check",
      "recurring_problem_block",
      "reflection_reminder",
    ],
    triggerStep: "pr_created",
    completionStep: "ci_passed",
  },
  {
    id: "merge",
    name: "マージ",
    description: "PRのマージ",
    order: 9,
    expectedHooks: [
      "merge_check",
      "reviewer_removal_check",
      "force_push_guard",
      "issue_incomplete_close_check",
      "worktree_auto_cleanup",
      "pr_merge_pull_reminder",
      "resolve_thread_guard",
    ],
    triggerStep: "ci_passed",
    completionStep: "merged",
  },
  {
    id: "cleanup",
    name: "クリーンアップ",
    description: "worktree削除とmain pull",
    order: 10,
    expectedHooks: ["worktree_removal_check"],
    triggerStep: "merged",
    completionStep: "cleaned_up",
  },
  {
    id: "production",
    name: "本番確認",
    description: "本番環境でのデプロイ確認",
    order: 11,
    expectedHooks: ["production_url_warning", "secret_deploy_trigger"],
    triggerStep: "merged",
    completionStep: null,
  },
  {
    id: "session_end",
    name: "セッション終了",
    description: "セッション終了時の評価・振り返り",
    order: 12,
    expectedHooks: [
      "hook_effectiveness_evaluator",
      "hook_behavior_evaluator",
      "session_metrics_collector",
      "session_handoff_writer",
      "secret_deploy_check",
      "cwd_check",
      "git_status_check",
      "related_task_check",
      "problem_report_check",
      "systematization_check",
      "flow_effect_verifier",
      "reflection_prompt",
      "worktree_cleanup_suggester",
      "session_end_worktree_cleanup",
    ],
    triggerStep: null,
    completionStep: null,
  },
];

/**
 * Get a phase definition by ID.
 */
export function getPhase(phaseId: string): Phase | null {
  return DEVELOPMENT_PHASES.find((p) => p.id === phaseId) ?? null;
}

/**
 * Get all phase definitions in order.
 */
export function getAllPhases(): Phase[] {
  return [...DEVELOPMENT_PHASES].sort((a, b) => a.order - b.order);
}

/**
 * Get list of expected hooks for a phase.
 */
export function getExpectedHooksForPhase(phaseId: string): string[] {
  const phase = getPhase(phaseId);
  return phase?.expectedHooks ?? [];
}

// =============================================================================
// Expected Hook Behavior Definitions
// =============================================================================

/**
 * Expected behavior definition for a hook.
 */
export interface ExpectedHookBehavior {
  /** Hook identifier (file name without extension) */
  hookName: string;
  /** Primary phase where this hook fires */
  phaseId: string;
  /** SessionStart, PreToolUse, PostToolUse, Stop */
  triggerType: string;
  /** Tool matcher (Bash, Edit, Write, etc.) */
  triggerTool: string | null;
  /** Expected decision (approve, block, either) */
  expectedDecision: "approve" | "block" | "either";
  /** What this hook does */
  description: string;
  /** Whether this hook can block operations */
  canBlock: boolean;
}

/**
 * All hooks from settings.json with their expected behaviors.
 */
export const EXPECTED_HOOK_BEHAVIORS: Record<string, ExpectedHookBehavior> = {
  // SessionStart hooks
  date_context_injector: {
    hookName: "date_context_injector",
    phaseId: "session_start",
    triggerType: "SessionStart",
    triggerTool: null,
    expectedDecision: "approve",
    description: "現在日時をコンテキストに注入",
    canBlock: false,
  },
  "check-lefthook.sh": {
    hookName: "check-lefthook.sh",
    phaseId: "session_start",
    triggerType: "SessionStart",
    triggerTool: null,
    expectedDecision: "approve",
    description: "lefthookインストール状態確認",
    canBlock: false,
  },
  session_handoff_reader: {
    hookName: "session_handoff_reader",
    phaseId: "session_start",
    triggerType: "SessionStart",
    triggerTool: null,
    expectedDecision: "approve",
    description: "前セッションの引き継ぎ情報読み取り",
    canBlock: false,
  },
  open_pr_warning: {
    hookName: "open_pr_warning",
    phaseId: "session_start",
    triggerType: "SessionStart",
    triggerTool: null,
    expectedDecision: "approve",
    description: "オープンPRの警告表示",
    canBlock: false,
  },
  branch_check: {
    hookName: "branch_check",
    phaseId: "session_start",
    triggerType: "SessionStart",
    triggerTool: null,
    expectedDecision: "approve",
    description: "セッション開始時のブランチ確認",
    canBlock: false,
  },
  // PreToolUse - Navigation
  production_url_warning: {
    hookName: "production_url_warning",
    phaseId: "production",
    triggerType: "PreToolUse",
    triggerTool: "mcp__chrome-devtools__navigate_page|new_page",
    expectedDecision: "either",
    description: "本番URL操作時に警告",
    canBlock: true,
  },
  // PreToolUse - Edit/Write
  task_start_checklist: {
    hookName: "task_start_checklist",
    phaseId: "pre_check",
    triggerType: "PreToolUse",
    triggerTool: "Edit|Write|Bash",
    expectedDecision: "approve",
    description: "タスク開始時の要件確認チェックリスト",
    canBlock: false,
  },
  worktree_warning: {
    hookName: "worktree_warning",
    phaseId: "implementation",
    triggerType: "PreToolUse",
    triggerTool: "Edit|Write",
    expectedDecision: "either",
    description: "mainブランチでの編集をブロック、worktree外を警告",
    canBlock: true,
  },
  empty_return_check: {
    hookName: "empty_return_check",
    phaseId: "implementation",
    triggerType: "PreToolUse",
    triggerTool: "Edit|Write",
    expectedDecision: "either",
    description: "空文字列返却をブロック",
    canBlock: true,
  },
  // PreToolUse - Bash (alphabetical order)
  active_worktree_check: {
    hookName: "active_worktree_check",
    phaseId: "worktree_create",
    triggerType: "PreToolUse",
    triggerTool: "Bash",
    expectedDecision: "approve",
    description: "アクティブworktreeの状態確認",
    canBlock: false,
  },
  ci_wait_check: {
    hookName: "ci_wait_check",
    phaseId: "ci_review",
    triggerType: "PreToolUse",
    triggerTool: "Bash",
    expectedDecision: "either",
    description: "CI監視をci-monitor.pyに一元化",
    canBlock: true,
  },
  closes_keyword_check: {
    hookName: "closes_keyword_check",
    phaseId: "pr_create",
    triggerType: "PreToolUse",
    triggerTool: "Bash",
    expectedDecision: "either",
    description: "Closes/Fixes記法の確認",
    canBlock: true,
  },
  closes_validation: {
    hookName: "closes_validation",
    phaseId: "pr_create",
    triggerType: "PreToolUse",
    triggerTool: "Bash",
    expectedDecision: "either",
    description: "Closes参照先の妥当性検証",
    canBlock: true,
  },
  codex_review_check: {
    hookName: "codex_review_check",
    phaseId: "pr_create",
    triggerType: "PreToolUse",
    triggerTool: "Bash",
    expectedDecision: "either",
    description: "PR作成時にレビュー済みか確認",
    canBlock: true,
  },
  codex_review_logger: {
    hookName: "codex_review_logger",
    phaseId: "local_ai_review",
    triggerType: "PreToolUse",
    triggerTool: "Bash",
    expectedDecision: "approve",
    description: "codex review実行をログ記録",
    canBlock: false,
  },
  dependency_check_reminder: {
    hookName: "dependency_check_reminder",
    phaseId: "implementation",
    triggerType: "PreToolUse",
    triggerTool: "Bash",
    expectedDecision: "approve",
    description: "依存関係追加時にContext7確認を促す",
    canBlock: false,
  },
  e2e_test_check: {
    hookName: "e2e_test_check",
    phaseId: "pre_commit_check",
    triggerType: "PreToolUse",
    triggerTool: "Bash",
    expectedDecision: "approve",
    description: "E2Eテスト実行必要性チェック",
    canBlock: false,
  },
  existing_impl_check: {
    hookName: "existing_impl_check",
    phaseId: "pre_commit_check",
    triggerType: "PreToolUse",
    triggerTool: "Bash",
    expectedDecision: "either",
    description: "既存実装との重複チェック",
    canBlock: true,
  },
  force_push_guard: {
    hookName: "force_push_guard",
    phaseId: "merge",
    triggerType: "PreToolUse",
    triggerTool: "Bash",
    expectedDecision: "either",
    description: "強制pushをブロック/警告",
    canBlock: true,
  },
  hooks_design_check: {
    hookName: "hooks_design_check",
    phaseId: "implementation",
    triggerType: "PreToolUse",
    triggerTool: "Bash",
    expectedDecision: "either",
    description: "新規フックに設計レビュー日付があるか確認",
    canBlock: true,
  },
  issue_auto_assign: {
    hookName: "issue_auto_assign",
    phaseId: "worktree_create",
    triggerType: "PreToolUse",
    triggerTool: "Bash",
    expectedDecision: "either",
    description: "worktree作成時にIssue自動assign（クローズ済み/重複/オープンPR時ブロック）",
    canBlock: true,
  },
  issue_comments_check: {
    hookName: "issue_comments_check",
    phaseId: "ci_review",
    triggerType: "PreToolUse",
    triggerTool: "Bash",
    expectedDecision: "approve",
    description: "Issueコメントの確認促進",
    canBlock: false,
  },
  issue_label_check: {
    hookName: "issue_label_check",
    phaseId: "issue_work",
    triggerType: "PreToolUse",
    triggerTool: "Bash",
    expectedDecision: "either",
    description: "Issue作成時のラベル付与確認",
    canBlock: true,
  },
  issue_review_response_check: {
    hookName: "issue_review_response_check",
    phaseId: "ci_review",
    triggerType: "PreToolUse",
    triggerTool: "Bash",
    expectedDecision: "either",
    description: "Issueレビューへの返答確認（未対応時ブロック）",
    canBlock: true,
  },
  issue_incomplete_close_check: {
    hookName: "issue_incomplete_close_check",
    phaseId: "merge",
    triggerType: "PreToolUse",
    triggerTool: "Bash",
    expectedDecision: "either",
    description: "Issue部分完了でのクローズ防止（未完了チェックボックス検出）",
    canBlock: true,
  },
  issue_scope_check: {
    hookName: "issue_scope_check",
    phaseId: "issue_work",
    triggerType: "PreToolUse",
    triggerTool: "Bash",
    expectedDecision: "either",
    description: "Issueスコープの適切性確認",
    canBlock: true,
  },
  locked_worktree_guard: {
    hookName: "locked_worktree_guard",
    phaseId: "pre_check",
    triggerType: "PreToolUse",
    triggerTool: "Bash",
    expectedDecision: "either",
    description: "ロック中worktreeへの操作ブロック",
    canBlock: true,
  },
  merge_check: {
    hookName: "merge_check",
    phaseId: "merge",
    triggerType: "PreToolUse",
    triggerTool: "Bash",
    expectedDecision: "either",
    description: "マージ安全性チェック",
    canBlock: true,
  },
  merged_worktree_check: {
    hookName: "merged_worktree_check",
    phaseId: "worktree_create",
    triggerType: "PreToolUse",
    triggerTool: "Bash",
    expectedDecision: "approve",
    description: "マージ済みworktree検出→削除促進",
    canBlock: false,
  },
  open_issue_reminder: {
    hookName: "open_issue_reminder",
    phaseId: "pre_check",
    triggerType: "PreToolUse",
    triggerTool: "Bash",
    expectedDecision: "approve",
    description: "未アサインIssue表示",
    canBlock: false,
  },
  orphan_worktree_check: {
    hookName: "orphan_worktree_check",
    phaseId: "worktree_create",
    triggerType: "PreToolUse",
    triggerTool: "Bash",
    expectedDecision: "approve",
    description: "ブランチ削除済みworktree検出",
    canBlock: false,
  },
  planning_enforcement: {
    hookName: "planning_enforcement",
    phaseId: "pre_check",
    triggerType: "PreToolUse",
    triggerTool: "Bash",
    expectedDecision: "either",
    description: "計画が必要なタスクのプランニング強制",
    canBlock: true,
  },
  pr_issue_alignment_check: {
    hookName: "pr_issue_alignment_check",
    phaseId: "pr_create",
    triggerType: "PreToolUse",
    triggerTool: "Bash",
    expectedDecision: "either",
    description: "PRとIssueの整合性確認",
    canBlock: true,
  },
  pr_issue_assign_check: {
    hookName: "pr_issue_assign_check",
    phaseId: "pr_create",
    triggerType: "PreToolUse",
    triggerTool: "Bash",
    expectedDecision: "either",
    description: "参照Issueがアサインされているか確認",
    canBlock: true,
  },
  pr_overlap_check: {
    hookName: "pr_overlap_check",
    phaseId: "pr_create",
    triggerType: "PreToolUse",
    triggerTool: "Bash",
    expectedDecision: "either",
    description: "同一Issueに対する複数PR検出",
    canBlock: true,
  },
  pr_scope_check: {
    hookName: "pr_scope_check",
    phaseId: "pr_create",
    triggerType: "PreToolUse",
    triggerTool: "Bash",
    expectedDecision: "either",
    description: "1 Issue = 1 PRルール強制",
    canBlock: true,
  },
  recurring_problem_block: {
    hookName: "recurring_problem_block",
    phaseId: "ci_review",
    triggerType: "PreToolUse",
    triggerTool: "Bash",
    expectedDecision: "either",
    description: "繰り返し発生する問題をブロック",
    canBlock: true,
  },
  research_requirement_check: {
    hookName: "research_requirement_check",
    phaseId: "pre_check",
    triggerType: "PreToolUse",
    triggerTool: "Bash",
    expectedDecision: "either",
    description: "調査が必要なタスクの事前調査確認",
    canBlock: true,
  },
  resolve_thread_guard: {
    hookName: "resolve_thread_guard",
    phaseId: "merge",
    triggerType: "PreToolUse",
    triggerTool: "Bash",
    expectedDecision: "either",
    description: "マージ前に未解決レビュースレッドをチェック",
    canBlock: true,
  },
  reviewer_removal_check: {
    hookName: "reviewer_removal_check",
    phaseId: "merge",
    triggerType: "PreToolUse",
    triggerTool: "Bash",
    expectedDecision: "either",
    description: "レビュアー削除操作を検出・警告",
    canBlock: true,
  },
  ui_check_reminder: {
    hookName: "ui_check_reminder",
    phaseId: "implementation",
    triggerType: "PreToolUse",
    triggerTool: "Bash",
    expectedDecision: "approve",
    description: "UI変更後の目視確認リマインド",
    canBlock: false,
  },
  worktree_main_freshness_check: {
    hookName: "worktree_main_freshness_check",
    phaseId: "worktree_create",
    triggerType: "PreToolUse",
    triggerTool: "Bash",
    expectedDecision: "either",
    description: "git worktree add時にmainブランチの新鮮さをチェック",
    canBlock: true,
  },
  worktree_path_guard: {
    hookName: "worktree_path_guard",
    phaseId: "worktree_create",
    triggerType: "PreToolUse",
    triggerTool: "Bash",
    expectedDecision: "either",
    description: "worktreeパスの妥当性確認",
    canBlock: true,
  },
  worktree_removal_check: {
    hookName: "worktree_removal_check",
    phaseId: "cleanup",
    triggerType: "PreToolUse",
    triggerTool: "Bash",
    expectedDecision: "either",
    description: "worktree削除前のアクティブ作業検出",
    canBlock: true,
  },
  // PostToolUse - Bash
  bash_failure_tracker: {
    hookName: "bash_failure_tracker",
    phaseId: "pre_commit_check",
    triggerType: "PostToolUse",
    triggerTool: "Bash",
    expectedDecision: "approve",
    description: "コマンド失敗を追跡",
    canBlock: false,
  },
  ci_recovery_tracker: {
    hookName: "ci_recovery_tracker",
    phaseId: "ci_review",
    triggerType: "PostToolUse",
    triggerTool: "Bash",
    expectedDecision: "approve",
    description: "CI失敗→復旧の追跡",
    canBlock: false,
  },
  codex_review_output_logger: {
    hookName: "codex_review_output_logger",
    phaseId: "local_ai_review",
    triggerType: "PostToolUse",
    triggerTool: "Bash",
    expectedDecision: "approve",
    description: "Codex CLIレビュー結果をログ記録",
    canBlock: false,
  },
  copilot_review_retry_suggestion: {
    hookName: "copilot_review_retry_suggestion",
    phaseId: "ci_review",
    triggerType: "PostToolUse",
    triggerTool: "Bash",
    expectedDecision: "approve",
    description: "Copilotレビュー失敗時リトライ提案",
    canBlock: false,
  },
  development_workflow_tracker: {
    hookName: "development_workflow_tracker",
    phaseId: "worktree_create",
    triggerType: "PostToolUse",
    triggerTool: "Bash",
    expectedDecision: "approve",
    description: "ワークフロー進捗記録",
    canBlock: false,
  },
  e2e_test_recorder: {
    hookName: "e2e_test_recorder",
    phaseId: "pre_commit_check",
    triggerType: "PostToolUse",
    triggerTool: "Bash",
    expectedDecision: "approve",
    description: "E2Eテスト実行を記録",
    canBlock: false,
  },
  flow_progress_tracker: {
    hookName: "flow_progress_tracker",
    phaseId: "worktree_create",
    triggerType: "PostToolUse",
    triggerTool: "Bash",
    expectedDecision: "approve",
    description: "フロー進捗追跡",
    canBlock: false,
  },
  git_operations_tracker: {
    hookName: "git_operations_tracker",
    phaseId: "worktree_create",
    triggerType: "PostToolUse",
    triggerTool: "Bash",
    expectedDecision: "approve",
    description: "Git操作を記録",
    canBlock: false,
  },
  plan_ai_review: {
    hookName: "plan_ai_review",
    phaseId: "pre_check",
    triggerType: "PostToolUse",
    triggerTool: "ExitPlanMode",
    expectedDecision: "approve",
    description: "Plan段階でのGemini AIレビュー",
    canBlock: false,
  },
  issue_creation_tracker: {
    hookName: "issue_creation_tracker",
    phaseId: "issue_work",
    triggerType: "PostToolUse",
    triggerTool: "Bash",
    expectedDecision: "approve",
    description: "Issue作成を記録",
    canBlock: false,
  },
  pr_metrics_collector: {
    hookName: "pr_metrics_collector",
    phaseId: "pr_create",
    triggerType: "PostToolUse",
    triggerTool: "Bash",
    expectedDecision: "approve",
    description: "PRメトリクス収集",
    canBlock: false,
  },
  pr_merge_pull_reminder: {
    hookName: "pr_merge_pull_reminder",
    phaseId: "merge",
    triggerType: "PostToolUse",
    triggerTool: "Bash",
    expectedDecision: "approve",
    description: "マージ後のpullリマインダー",
    canBlock: false,
  },
  reflection_reminder: {
    hookName: "reflection_reminder",
    phaseId: "ci_review",
    triggerType: "PostToolUse",
    triggerTool: "Bash",
    expectedDecision: "approve",
    description: "振り返りリマインダー",
    canBlock: false,
  },
  secret_deploy_trigger: {
    hookName: "secret_deploy_trigger",
    phaseId: "production",
    triggerType: "PostToolUse",
    triggerTool: "Bash",
    expectedDecision: "approve",
    description: "秘密情報デプロイのトリガー検出",
    canBlock: false,
  },
  worktree_auto_cleanup: {
    hookName: "worktree_auto_cleanup",
    phaseId: "merge",
    triggerType: "PostToolUse",
    triggerTool: "Bash",
    expectedDecision: "approve",
    description: "マージ後のworktree自動クリーンアップ提案",
    canBlock: false,
  },
  // PostToolUse - Edit
  rework_tracker: {
    hookName: "rework_tracker",
    phaseId: "implementation",
    triggerType: "PostToolUse",
    triggerTool: "Edit",
    expectedDecision: "approve",
    description: "同一ファイル再編集（手戻り）追跡",
    canBlock: false,
  },
  tool_efficiency_tracker: {
    hookName: "tool_efficiency_tracker",
    phaseId: "implementation",
    triggerType: "PostToolUse",
    triggerTool: "Bash|Edit|Read|Glob|Grep",
    expectedDecision: "approve",
    description: "ツール使用効率の追跡",
    canBlock: false,
  },
  // PostToolUse - Read/Glob/Grep
  exploration_tracker: {
    hookName: "exploration_tracker",
    phaseId: "implementation",
    triggerType: "PostToolUse",
    triggerTool: "Read|Glob|Grep",
    expectedDecision: "approve",
    description: "コード探索パターンを追跡",
    canBlock: false,
  },
  // PostToolUse - WebSearch/WebFetch
  research_tracker: {
    hookName: "research_tracker",
    phaseId: "implementation",
    triggerType: "PostToolUse",
    triggerTool: "WebSearch|WebFetch",
    expectedDecision: "approve",
    description: "Web調査を追跡",
    canBlock: false,
  },
  // Stop hooks
  cwd_check: {
    hookName: "cwd_check",
    phaseId: "session_end",
    triggerType: "Stop",
    triggerTool: null,
    expectedDecision: "either",
    description: "カレントディレクトリ消失検知",
    canBlock: true,
  },
  flow_effect_verifier: {
    hookName: "flow_effect_verifier",
    phaseId: "session_end",
    triggerType: "Stop",
    triggerTool: null,
    expectedDecision: "either",
    description: "フロー効果検証",
    canBlock: true,
  },
  git_status_check: {
    hookName: "git_status_check",
    phaseId: "session_end",
    triggerType: "Stop",
    triggerTool: null,
    expectedDecision: "either",
    description: "mainの未コミット変更検知",
    canBlock: true,
  },
  hook_behavior_evaluator: {
    hookName: "hook_behavior_evaluator",
    phaseId: "session_end",
    triggerType: "Stop",
    triggerTool: null,
    expectedDecision: "approve",
    description: "期待動作と実際の動作のギャップ検知",
    canBlock: false,
  },
  hook_effectiveness_evaluator: {
    hookName: "hook_effectiveness_evaluator",
    phaseId: "session_end",
    triggerType: "Stop",
    triggerTool: null,
    expectedDecision: "approve",
    description: "フック有効性の評価",
    canBlock: false,
  },
  problem_report_check: {
    hookName: "problem_report_check",
    phaseId: "session_end",
    triggerType: "Stop",
    triggerTool: null,
    expectedDecision: "either",
    description: "発見した問題のIssue化確認",
    canBlock: true,
  },
  related_task_check: {
    hookName: "related_task_check",
    phaseId: "session_end",
    triggerType: "Stop",
    triggerTool: null,
    expectedDecision: "either",
    description: "関連タスク検知",
    canBlock: true,
  },
  reflection_prompt: {
    hookName: "reflection_prompt",
    phaseId: "session_end",
    triggerType: "Stop",
    triggerTool: null,
    expectedDecision: "either",
    description: "五省ベースの自己評価",
    canBlock: true,
  },
  secret_deploy_check: {
    hookName: "secret_deploy_check",
    phaseId: "session_end",
    triggerType: "Stop",
    triggerTool: null,
    expectedDecision: "either",
    description: "秘密情報デプロイ漏れチェック",
    canBlock: true,
  },
  session_metrics_collector: {
    hookName: "session_metrics_collector",
    phaseId: "session_end",
    triggerType: "Stop",
    triggerTool: null,
    expectedDecision: "approve",
    description: "セッションメトリクス収集",
    canBlock: false,
  },
  session_handoff_writer: {
    hookName: "session_handoff_writer",
    phaseId: "session_end",
    triggerType: "Stop",
    triggerTool: null,
    expectedDecision: "approve",
    description: "次セッションへの引き継ぎ情報記録",
    canBlock: false,
  },
  systematization_check: {
    hookName: "systematization_check",
    phaseId: "session_end",
    triggerType: "Stop",
    triggerTool: null,
    expectedDecision: "either",
    description: "教訓の仕組み化確認",
    canBlock: true,
  },
  worktree_cleanup_suggester: {
    hookName: "worktree_cleanup_suggester",
    phaseId: "session_end",
    triggerType: "Stop",
    triggerTool: null,
    expectedDecision: "approve",
    description: "マージ済みworktreeのクリーンアップ提案",
    canBlock: false,
  },
  session_end_worktree_cleanup: {
    hookName: "session_end_worktree_cleanup",
    phaseId: "session_end",
    triggerType: "Stop",
    triggerTool: null,
    expectedDecision: "approve",
    description: "セッション終了時のworktreeクリーンアップ確認",
    canBlock: false,
  },
};

/**
 * Get expected behavior for a hook.
 */
export function getHookBehavior(hookName: string): ExpectedHookBehavior | null {
  return EXPECTED_HOOK_BEHAVIORS[hookName] ?? null;
}

/**
 * Get list of all defined hook names.
 */
export function getAllHookNames(): string[] {
  return Object.keys(EXPECTED_HOOK_BEHAVIORS);
}

/**
 * Get all hooks for a specific phase.
 */
export function getHooksByPhase(phaseId: string): ExpectedHookBehavior[] {
  return Object.values(EXPECTED_HOOK_BEHAVIORS).filter((h) => h.phaseId === phaseId);
}

/**
 * Get all hooks by trigger type.
 */
export function getHooksByTriggerType(triggerType: string): ExpectedHookBehavior[] {
  return Object.values(EXPECTED_HOOK_BEHAVIORS).filter((h) => h.triggerType === triggerType);
}

// =============================================================================
// FlowStep and FlowDefinition
// =============================================================================

/**
 * A single step in a flow.
 */
export interface FlowStep {
  /** Unique identifier for the step */
  id: string;
  /** Display name */
  name: string;
  /** Detailed description */
  description: string;
  /** Order index (0-based) for basic sequencing */
  order: number;
  /** If True, step must be completed (cannot be skipped) */
  required: boolean;
  /** If True, next steps cannot start until this completes */
  blocking: boolean;
  /** If True, step can be completed multiple times */
  repeatable: boolean;
  /** List of step IDs that can run in parallel with this step */
  parallelWith: string[];
  /** List of step IDs that must be completed before this step */
  dependsOn: string[];
  /** Optional condition key for conditional steps (e.g., "has_review_comments") */
  condition: string | null;
  /** Phase name for hierarchical display (e.g., "setup", "implementation", "review") */
  phase: string | null;
}

/**
 * Flow context for pattern matching.
 */
export interface FlowContext {
  issue_number?: number;
  branch_name?: string;
  [key: string]: unknown;
}

/**
 * Base interface for flow definitions.
 */
export interface FlowDefinition {
  /** Unique identifier for the flow */
  id: string;
  /** Display name */
  name: string;
  /** Detailed description */
  description: string;
  /** List of FlowStep instances */
  steps: FlowStep[];
  /** If true, session cannot end with this flow incomplete */
  blockingOnSessionEnd: boolean;
  /** Step ID that marks the flow as complete when finished */
  completionStep: string | null;

  /**
   * Return ordered list of step IDs.
   */
  getStepIds(): string[];

  /**
   * Get a step by its ID.
   */
  getStep(stepId: string): FlowStep | null;

  /**
   * Get the order of a step (-1 if not found).
   */
  getStepOrder(stepId: string): number;

  /**
   * Check if a command matches a step with the given context.
   */
  matchesStep(stepId: string, command: string, context: FlowContext): boolean;

  /**
   * Convert to dictionary (for backward compatibility).
   */
  toDict(): Record<string, unknown>;
}

/**
 * Create a default FlowStep.
 */
function createFlowStep(partial: Partial<FlowStep> & { id: string; name: string }): FlowStep {
  return {
    description: "",
    order: 0,
    required: true,
    blocking: true,
    repeatable: false,
    parallelWith: [],
    dependsOn: [],
    condition: null,
    phase: null,
    ...partial,
  };
}

/**
 * Base implementation for flow definitions.
 */
abstract class BaseFlowDefinition implements FlowDefinition {
  id: string;
  name: string;
  description: string;
  steps: FlowStep[];
  blockingOnSessionEnd: boolean;
  completionStep: string | null;

  constructor(config: {
    id: string;
    name: string;
    description?: string;
    steps: FlowStep[];
    blockingOnSessionEnd?: boolean;
    completionStep?: string | null;
  }) {
    this.id = config.id;
    this.name = config.name;
    this.description = config.description ?? "";
    this.steps = config.steps;
    this.blockingOnSessionEnd = config.blockingOnSessionEnd ?? false;
    this.completionStep = config.completionStep ?? null;
  }

  getStepIds(): string[] {
    return [...this.steps].sort((a, b) => a.order - b.order).map((s) => s.id);
  }

  getStep(stepId: string): FlowStep | null {
    return this.steps.find((s) => s.id === stepId) ?? null;
  }

  getStepOrder(stepId: string): number {
    const step = this.getStep(stepId);
    return step?.order ?? -1;
  }

  abstract matchesStep(stepId: string, command: string, context: FlowContext): boolean;

  toDict(): Record<string, unknown> {
    return {
      id: this.id,
      name: this.name,
      description: this.description,
      steps: [...this.steps]
        .sort((a, b) => a.order - b.order)
        .map((s) => ({
          id: s.id,
          name: s.name,
          description: s.description,
        })),
      blocking: { on_session_end: this.blockingOnSessionEnd },
    };
  }
}

/**
 * Flow for tracking the entire development workflow.
 *
 * Tracks the complete Issue-to-merge lifecycle:
 * 1. worktree_created - Worktree created for the Issue
 * 2. implementation - Implementation started (free phase, repeatable)
 * 3. committed - Changes committed
 * 4. pushed - Changes pushed to remote
 * 5. pr_created - PR created
 * 6. ci_passed - CI checks passed (can run parallel with review)
 * 7. review_addressed - Review comments addressed (optional, conditional)
 * 8. merged - PR merged
 * 9. cleaned_up - Worktree cleaned up (optional)
 */
class DevelopmentWorkflow extends BaseFlowDefinition {
  constructor() {
    super({
      id: "development-workflow",
      name: "開発ワークフロー",
      description: "Issue対応の開発ワークフロー全体を追跡",
      steps: [
        // Phase: setup (strict order)
        createFlowStep({
          id: "worktree_created",
          name: "Worktree作成",
          description: "Issue用のworktreeを作成",
          order: 0,
          required: true,
          blocking: true,
          phase: "setup",
        }),
        // Phase: implementation (free phase)
        createFlowStep({
          id: "implementation",
          name: "実装",
          description: "機能の実装（自由区間、繰り返し可）",
          order: 1,
          required: true,
          blocking: false, // Non-blocking to allow flexible work
          repeatable: true,
          phase: "implementation",
        }),
        // Phase: implementation (Commit-Push loop, repeatable)
        createFlowStep({
          id: "committed",
          name: "コミット",
          description: "変更をコミット",
          order: 2,
          required: true,
          blocking: true,
          repeatable: true,
          phase: "implementation",
        }),
        createFlowStep({
          id: "pushed",
          name: "プッシュ",
          description: "変更をリモートにプッシュ",
          order: 3,
          required: true,
          blocking: true,
          repeatable: true,
          dependsOn: ["committed"],
          phase: "implementation",
        }),
        // Phase: review (PR and CI, parallel possible)
        createFlowStep({
          id: "pr_created",
          name: "PR作成",
          description: "プルリクエストを作成",
          order: 4,
          required: true,
          blocking: true,
          phase: "review",
        }),
        createFlowStep({
          id: "ci_passed",
          name: "CI通過",
          description: "CIチェックが全てパス",
          order: 5,
          required: true,
          blocking: false, // Can wait asynchronously
          parallelWith: ["review_addressed"],
          phase: "review",
        }),
        createFlowStep({
          id: "review_addressed",
          name: "レビュー対応",
          description: "レビューコメントに対応",
          order: 5, // Same order as ci_passed (parallel)
          required: false, // Optional if no review comments
          blocking: false,
          parallelWith: ["ci_passed"],
          condition: "has_review_comments",
          phase: "review",
        }),
        // Phase: complete (Merge is the completion step)
        createFlowStep({
          id: "merged",
          name: "マージ",
          description: "PRをマージ",
          order: 6,
          required: true,
          blocking: true,
          dependsOn: ["ci_passed"],
          phase: "complete",
        }),
        // Phase: complete (Cleanup is optional, after completion)
        createFlowStep({
          id: "cleaned_up",
          name: "クリーンアップ",
          description: "worktreeを削除",
          order: 7,
          required: false,
          blocking: false,
          phase: "complete",
        }),
      ],
      blockingOnSessionEnd: false, // Don't block session end
      completionStep: "merged", // Flow is complete when merged
    });
  }

  matchesStep(stepId: string, command: string, context: FlowContext): boolean {
    const issueNumber = context.issue_number;

    if (stepId === "worktree_created") {
      // Pattern: git worktree add ... issue-<number>
      // Support both `git worktree add` and `cd /path && git worktree add` patterns
      // Issue #2534: Use (?:^|&&\s*) to match start of line or after &&
      if (issueNumber) {
        const issueNumberStr = String(Number(issueNumber));
        const pattern = new RegExp(
          `(?:^|&&\\s*)\\s*git\\s+worktree\\s+add\\b.*\\bissue-${issueNumberStr}\\b`,
        );
        return pattern.test(command);
      }
      return /(?:^|&&\s*)\s*git\s+worktree\s+add\b/.test(command);
    }

    if (stepId === "committed") {
      // Pattern: git commit
      return /^git\s+commit\b/.test(command);
    }

    if (stepId === "pushed") {
      // Pattern: git push
      return /^git\s+push\b/.test(command);
    }

    if (stepId === "pr_created") {
      // Pattern: gh pr create
      return /^gh\s+pr\s+create\b/.test(command);
    }

    if (stepId === "ci_passed") {
      // This is typically marked programmatically after CI completes
      // Could match: gh run watch, gh pr checks
      return /^gh\s+(run\s+watch|pr\s+checks)\b/.test(command);
    }

    if (stepId === "merged") {
      // Pattern: gh pr merge
      return /^gh\s+pr\s+merge\b/.test(command);
    }

    if (stepId === "cleaned_up") {
      // Pattern: git worktree remove
      return /^git\s+worktree\s+remove\b/.test(command);
    }

    // implementation and review_addressed are typically marked programmatically
    return false;
  }
}

// =============================================================================
// Flow Registry and Helper Functions
// =============================================================================

/**
 * Registry of all flow definitions.
 */
export const FLOW_REGISTRY: Record<string, FlowDefinition> = {
  "development-workflow": new DevelopmentWorkflow(),
};

/**
 * Get a flow definition by its ID.
 */
export function getFlowDefinition(flowId: string): FlowDefinition | null {
  return FLOW_REGISTRY[flowId] ?? null;
}

/**
 * Get all registered flow definitions.
 */
export function getAllFlowDefinitions(): Record<string, FlowDefinition> {
  return { ...FLOW_REGISTRY };
}

/**
 * Validate that a step is being completed in the correct order.
 *
 * Considers:
 * - Basic order (order field)
 * - Explicit dependencies (dependsOn field)
 * - Parallel execution (parallelWith field)
 * - Blocking steps (blocking field)
 *
 * @returns Tuple of [is_valid, error_message]. If valid, error_message is empty.
 */
export function validateStepOrder(
  flowId: string,
  completedSteps: string[],
  newStepId: string,
): [boolean, string] {
  const flow = getFlowDefinition(flowId);
  if (!flow) {
    return [false, `Unknown flow: ${flowId}`];
  }

  const newStep = flow.getStep(newStepId);
  if (!newStep) {
    return [false, `Unknown step: ${newStepId}`];
  }

  const newStepOrder = newStep.order;

  // Check explicit dependencies first (dependsOn takes priority)
  for (const depId of newStep.dependsOn) {
    if (!completedSteps.includes(depId)) {
      return [false, `Step '${newStepId}' depends on '${depId}' which is not completed`];
    }
  }

  // Check order-based dependencies (blocking steps must complete first)
  for (const step of flow.steps) {
    if (step.order < newStepOrder) {
      // Skip if this step can run in parallel with the new step
      if (newStep.parallelWith.includes(step.id) || step.parallelWith.includes(newStepId)) {
        continue;
      }

      // Skip optional (non-required) steps
      if (!step.required) {
        continue;
      }

      // Blocking steps must be completed
      if (step.blocking && !completedSteps.includes(step.id)) {
        return [
          false,
          `Step '${newStepId}' cannot be completed before blocking step '${step.id}' (order: ${step.order} < ${newStepOrder})`,
        ];
      }
    }
  }

  return [true, ""];
}

/**
 * Check if a step can be skipped based on its characteristics.
 *
 * @returns True if the step can be skipped.
 */
export function canSkipStep(flowId: string, stepId: string, context: FlowContext): boolean {
  const flow = getFlowDefinition(flowId);
  if (!flow) {
    return false;
  }

  const step = flow.getStep(stepId);
  if (!step) {
    return false;
  }

  // Required steps cannot be skipped
  if (step.required) {
    return false;
  }

  // Conditional steps can be skipped if condition is not met
  if (step.condition) {
    // Condition key should be in context as a boolean
    return !context[step.condition];
  }

  // Optional (non-required) steps can be skipped
  return true;
}

/**
 * Get list of required steps that are not yet completed.
 *
 * @returns List of pending required step IDs in order.
 */
export function getPendingRequiredSteps(flowId: string, completedSteps: string[]): string[] {
  const flow = getFlowDefinition(flowId);
  if (!flow) {
    return [];
  }

  const pending: string[] = [];
  const sortedSteps = [...flow.steps].sort((a, b) => a.order - b.order);

  for (const step of sortedSteps) {
    if (step.required && !completedSteps.includes(step.id)) {
      pending.push(step.id);
    }
  }

  return pending;
}
