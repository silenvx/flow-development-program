#!/usr/bin/env python3
"""Flowの定義モジュール - 全フロー設定の単一真実源。

Why:
    JSONでのフロー定義では動的パターンマッチングや型安全性が得られない。
    Python化することでIDEサポート、テスト容易性、ロジックとの統合を実現。

What:
    - FlowDefinitionクラスによる動的パターンマッチング
    - 13フェーズ開発ワークフローのフェーズ定義
    - ワークフロー検証用の期待フック動作定義
    - TaskType列挙によるセッション成果ベース評価

Remarks:
    - flow-definitions.jsonを置き換え
    - get_flow_definition()、get_phase()、get_expected_hooks_for_phase()が主要API

Changelog:
    - silenvx/dekita#xxx: JSONからPython化
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# =============================================================================
# Task Type Definitions - for session outcome-based evaluation
# =============================================================================


class TaskType(Enum):
    """Task type for session outcome-based evaluation.

    Inferred from session outcomes (PRs, Issues, commits) at session end.

    Attributes:
        IMPLEMENTATION: PRs merged during session
        IMPLEMENTATION_WIP: PRs created but not merged
        REVIEW_RESPONSE: Pushed to existing PRs (review comment response)
        ISSUE_CREATION: Only created Issues
        RESEARCH: No commits or code changes (research/investigation)
        MAINTENANCE: Cleanup, refactoring without new features
        UNKNOWN: Could not determine task type
    """

    IMPLEMENTATION = "implementation"
    IMPLEMENTATION_WIP = "implementation_wip"
    REVIEW_RESPONSE = "review_response"
    ISSUE_CREATION = "issue_creation"
    RESEARCH = "research"
    MAINTENANCE = "maintenance"
    UNKNOWN = "unknown"


def estimate_task_type(outcomes: dict) -> TaskType:
    """Estimate task type from session outcomes.

    Args:
        outcomes: Dict containing session outcomes:
            - prs_merged: List of merged PR numbers
            - prs_created: List of created PR numbers
            - prs_pushed: List of PRs with new pushes
            - issues_created: List of created Issue numbers
            - commits_count: Number of commits made

    Returns:
        Estimated TaskType based on outcome patterns.
    """
    prs_merged = outcomes.get("prs_merged", [])
    prs_created = outcomes.get("prs_created", [])
    prs_pushed = outcomes.get("prs_pushed", [])
    issues_created = outcomes.get("issues_created", [])
    commits_count = outcomes.get("commits_count", 0)

    # PRs merged -> implementation complete
    if prs_merged:
        return TaskType.IMPLEMENTATION

    # PRs created but not merged -> work in progress
    if prs_created:
        return TaskType.IMPLEMENTATION_WIP

    # Pushed to existing PRs -> review response
    if prs_pushed:
        return TaskType.REVIEW_RESPONSE

    # Only Issues created -> issue creation task
    if issues_created and commits_count == 0:
        return TaskType.ISSUE_CREATION

    # No commits -> research/investigation
    if commits_count == 0:
        return TaskType.RESEARCH

    # Commits but no PR -> maintenance/local work
    if commits_count > 0 and not prs_created and not prs_merged:
        return TaskType.MAINTENANCE

    return TaskType.UNKNOWN


# =============================================================================
# Phase Definitions - 13-phase development workflow
# =============================================================================


@dataclass
class Phase:
    """A phase in the development workflow.

    Phases represent logical stages of the development process,
    each with expected hook activations.

    Attributes:
        id: Unique identifier for the phase
        name: Display name (Japanese)
        description: Detailed description
        order: Order index (0-based)
        expected_hooks: List of hook names expected to fire in this phase
        trigger_step: FlowStep ID that triggers this phase (None = manual/automatic)
        completion_step: FlowStep ID that completes this phase
    """

    id: str
    name: str
    description: str = ""
    order: int = 0
    expected_hooks: list[str] = field(default_factory=list)
    trigger_step: str | None = None
    completion_step: str | None = None


# 13 phases based on docs/development-flow.md
DEVELOPMENT_PHASES: list[Phase] = [
    Phase(
        id="session_start",
        name="セッション開始",
        description="セッション開始時の自動フック発動",
        order=0,
        expected_hooks=[
            "date_context_injector",
            "check-lefthook.sh",
            "session_handoff_reader",
            "open_pr_warning",
            "branch_check",
        ],
        trigger_step=None,
        completion_step=None,
    ),
    Phase(
        id="pre_check",
        name="事前確認",
        description="Issue/worktree/PRの確認",
        order=1,
        expected_hooks=[
            "open_issue_reminder",
            "task_start_checklist",
            "research_requirement_check",
            "planning_enforcement",
            "locked_worktree_guard",
        ],
        trigger_step=None,
        completion_step="worktree_created",
    ),
    Phase(
        id="worktree_create",
        name="Worktree作成",
        description="Issue用worktreeの作成",
        order=2,
        expected_hooks=[
            "worktree_path_guard",
            "orphan_worktree_check",
            "merged_worktree_check",
            "active_worktree_check",
            "issue_auto_assign",
            "development_workflow_tracker",
            "git_operations_tracker",
            "flow_progress_tracker",
            "worktree_main_freshness_check",  # Issue #931
        ],
        trigger_step="worktree_created",
        completion_step="worktree_created",
    ),
    Phase(
        id="implementation",
        name="実装",
        description="コード編集・変更",
        order=3,
        expected_hooks=[
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
        trigger_step="worktree_created",
        completion_step="committed",
    ),
    Phase(
        id="pre_commit_check",
        name="コミット前検証",
        description="lint/test/typecheckの実行",
        order=4,
        expected_hooks=[
            "python_lint_check",
            "existing_impl_check",
            "bash_failure_tracker",
            "e2e_test_check",
            "e2e_test_recorder",
        ],
        trigger_step="implementation",
        completion_step="committed",
    ),
    Phase(
        id="local_ai_review",
        name="ローカルAIレビュー",
        description="codex reviewの実行",
        order=5,
        expected_hooks=[
            "codex_review_logger",
            "codex_review_output_logger",
        ],
        trigger_step="committed",
        completion_step="pushed",
    ),
    Phase(
        id="pr_create",
        name="PR作成",
        description="PRの作成とレビュー依頼",
        order=6,
        expected_hooks=[
            "codex_review_check",
            "pr_scope_check",
            "closes_keyword_check",
            "closes_validation",
            "pr_issue_assign_check",
            "pr_overlap_check",
            "pr_issue_alignment_check",
            "pr_metrics_collector",
        ],
        trigger_step="pushed",
        completion_step="pr_created",
    ),
    Phase(
        id="issue_work",
        name="Issue作成",
        description="問題発見時のIssue作成",
        order=7,
        expected_hooks=[
            "issue_label_check",
            "issue_scope_check",
            "issue_creation_tracker",
            "issue_ai_review",
        ],
        trigger_step=None,  # Can happen at any time
        completion_step=None,
    ),
    Phase(
        id="ci_review",
        name="CI監視+レビュー対応",
        description="CI完了待ちとレビューコメント対応",
        order=8,
        expected_hooks=[
            "ci_wait_check",
            "ci_recovery_tracker",
            "copilot_review_retry_suggestion",
            "issue_comments_check",
            "issue_review_response_check",
            "recurring_problem_block",
            "reflection_reminder",
        ],
        trigger_step="pr_created",
        completion_step="ci_passed",
    ),
    Phase(
        id="merge",
        name="マージ",
        description="PRのマージ",
        order=9,
        expected_hooks=[
            "merge_check",
            "reviewer_removal_check",
            "force_push_guard",
            "issue_incomplete_close_check",
            "worktree_auto_cleanup",
            "pr_merge_pull_reminder",
            "resolve_thread_guard",  # Issue #931
        ],
        trigger_step="ci_passed",
        completion_step="merged",
    ),
    Phase(
        id="cleanup",
        name="クリーンアップ",
        description="worktree削除とmain pull",
        order=10,
        expected_hooks=[
            "worktree_removal_check",
        ],
        trigger_step="merged",
        completion_step="cleaned_up",
    ),
    Phase(
        id="production",
        name="本番確認",
        description="本番環境でのデプロイ確認",
        order=11,
        expected_hooks=[
            "production_url_warning",
            "secret_deploy_trigger",
        ],
        trigger_step="merged",
        completion_step=None,
    ),
    Phase(
        id="session_end",
        name="セッション終了",
        description="セッション終了時の評価・振り返り",
        order=12,
        expected_hooks=[
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
            "worktree_cleanup_suggester",  # Issue #931
            "session_end_worktree_cleanup",  # Issue #931
        ],
        trigger_step=None,
        completion_step=None,
    ),
]


def get_phase(phase_id: str) -> Phase | None:
    """Get a phase definition by ID."""
    for phase in DEVELOPMENT_PHASES:
        if phase.id == phase_id:
            return phase
    return None


def get_all_phases() -> list[Phase]:
    """Get all phase definitions in order."""
    return sorted(DEVELOPMENT_PHASES, key=lambda p: p.order)


def get_expected_hooks_for_phase(phase_id: str) -> list[str]:
    """Get list of expected hooks for a phase."""
    phase = get_phase(phase_id)
    return phase.expected_hooks if phase else []


# =============================================================================
# Expected Hook Behavior Definitions
# =============================================================================


@dataclass
class ExpectedHookBehavior:
    """Expected behavior definition for a hook.

    Attributes:
        hook_name: Hook identifier (file name without .py)
        phase_id: Primary phase where this hook fires
        trigger_type: SessionStart, PreToolUse, PostToolUse, Stop
        trigger_tool: Tool matcher (Bash, Edit, Write, etc.)
        expected_decision: Expected decision (approve, block, either)
        description: What this hook does
        can_block: Whether this hook can block operations
    """

    hook_name: str
    phase_id: str
    trigger_type: str
    trigger_tool: str | None = None
    expected_decision: str = "either"  # approve, block, either
    description: str = ""
    can_block: bool = True


# All hooks from settings.json with their expected behaviors
EXPECTED_HOOK_BEHAVIORS: dict[str, ExpectedHookBehavior] = {
    # SessionStart hooks
    "date_context_injector": ExpectedHookBehavior(
        hook_name="date_context_injector",
        phase_id="session_start",
        trigger_type="SessionStart",
        expected_decision="approve",
        description="現在日時をコンテキストに注入",
        can_block=False,
    ),
    "check-lefthook.sh": ExpectedHookBehavior(
        hook_name="check-lefthook.sh",
        phase_id="session_start",
        trigger_type="SessionStart",
        expected_decision="approve",
        description="lefthookインストール状態確認",
        can_block=False,
    ),
    "session_handoff_reader": ExpectedHookBehavior(
        hook_name="session_handoff_reader",
        phase_id="session_start",
        trigger_type="SessionStart",
        expected_decision="approve",
        description="前セッションの引き継ぎ情報読み取り",
        can_block=False,
    ),
    "open_pr_warning": ExpectedHookBehavior(
        hook_name="open_pr_warning",
        phase_id="session_start",
        trigger_type="SessionStart",
        expected_decision="approve",
        description="オープンPRの警告表示",
        can_block=False,
    ),
    "branch_check": ExpectedHookBehavior(
        hook_name="branch_check",
        phase_id="session_start",
        trigger_type="SessionStart",
        expected_decision="approve",
        description="セッション開始時のブランチ確認",
        can_block=False,
    ),
    # PreToolUse - Navigation
    "production_url_warning": ExpectedHookBehavior(
        hook_name="production_url_warning",
        phase_id="production",
        trigger_type="PreToolUse",
        trigger_tool="mcp__chrome-devtools__navigate_page|new_page",
        expected_decision="either",
        description="本番URL操作時に警告",
    ),
    # PreToolUse - Edit/Write
    "task_start_checklist": ExpectedHookBehavior(
        hook_name="task_start_checklist",
        phase_id="pre_check",
        trigger_type="PreToolUse",
        trigger_tool="Edit|Write|Bash",
        expected_decision="approve",
        description="タスク開始時の要件確認チェックリスト",
        can_block=False,
    ),
    "worktree_warning": ExpectedHookBehavior(
        hook_name="worktree_warning",
        phase_id="implementation",
        trigger_type="PreToolUse",
        trigger_tool="Edit|Write",
        expected_decision="either",
        description="mainブランチでの編集をブロック、worktree外を警告",
    ),
    "empty_return_check": ExpectedHookBehavior(
        hook_name="empty_return_check",
        phase_id="implementation",
        trigger_type="PreToolUse",
        trigger_tool="Edit|Write",
        expected_decision="either",
        description="空文字列返却をブロック",
    ),
    # PreToolUse - Bash (alphabetical order)
    "active_worktree_check": ExpectedHookBehavior(
        hook_name="active_worktree_check",
        phase_id="worktree_create",
        trigger_type="PreToolUse",
        trigger_tool="Bash",
        expected_decision="approve",
        description="アクティブworktreeの状態確認",
        can_block=False,
    ),
    "ci_wait_check": ExpectedHookBehavior(
        hook_name="ci_wait_check",
        phase_id="ci_review",
        trigger_type="PreToolUse",
        trigger_tool="Bash",
        expected_decision="either",
        description="CI監視をci-monitor.pyに一元化",
    ),
    "closes_keyword_check": ExpectedHookBehavior(
        hook_name="closes_keyword_check",
        phase_id="pr_create",
        trigger_type="PreToolUse",
        trigger_tool="Bash",
        expected_decision="either",
        description="Closes/Fixes記法の確認",
    ),
    "closes_validation": ExpectedHookBehavior(
        hook_name="closes_validation",
        phase_id="pr_create",
        trigger_type="PreToolUse",
        trigger_tool="Bash",
        expected_decision="either",
        description="Closes参照先の妥当性検証",
    ),
    "codex_review_check": ExpectedHookBehavior(
        hook_name="codex_review_check",
        phase_id="pr_create",
        trigger_type="PreToolUse",
        trigger_tool="Bash",
        expected_decision="either",
        description="PR作成時にレビュー済みか確認",
    ),
    "codex_review_logger": ExpectedHookBehavior(
        hook_name="codex_review_logger",
        phase_id="local_ai_review",
        trigger_type="PreToolUse",
        trigger_tool="Bash",
        expected_decision="approve",
        description="codex review実行をログ記録",
        can_block=False,
    ),
    "dependency_check_reminder": ExpectedHookBehavior(
        hook_name="dependency_check_reminder",
        phase_id="implementation",
        trigger_type="PreToolUse",
        trigger_tool="Bash",
        expected_decision="approve",
        description="依存関係追加時にContext7確認を促す",
        can_block=False,
    ),
    "e2e_test_check": ExpectedHookBehavior(
        hook_name="e2e_test_check",
        phase_id="pre_commit_check",
        trigger_type="PreToolUse",
        trigger_tool="Bash",
        expected_decision="approve",
        description="E2Eテスト実行必要性チェック",
        can_block=False,
    ),
    "existing_impl_check": ExpectedHookBehavior(
        hook_name="existing_impl_check",
        phase_id="pre_commit_check",
        trigger_type="PreToolUse",
        trigger_tool="Bash",
        expected_decision="either",
        description="既存実装との重複チェック",
    ),
    "force_push_guard": ExpectedHookBehavior(
        hook_name="force_push_guard",
        phase_id="merge",
        trigger_type="PreToolUse",
        trigger_tool="Bash",
        expected_decision="either",
        description="強制pushをブロック/警告",
    ),
    "hooks_design_check": ExpectedHookBehavior(
        hook_name="hooks_design_check",
        phase_id="implementation",
        trigger_type="PreToolUse",
        trigger_tool="Bash",
        expected_decision="either",
        description="新規フックに設計レビュー日付があるか確認",
    ),
    "issue_auto_assign": ExpectedHookBehavior(
        hook_name="issue_auto_assign",
        phase_id="worktree_create",
        trigger_type="PreToolUse",
        trigger_tool="Bash",
        expected_decision="either",
        description="worktree作成時にIssue自動assign（クローズ済み/重複/オープンPR時ブロック）",
    ),
    "issue_comments_check": ExpectedHookBehavior(
        hook_name="issue_comments_check",
        phase_id="ci_review",
        trigger_type="PreToolUse",
        trigger_tool="Bash",
        expected_decision="approve",
        description="Issueコメントの確認促進",
        can_block=False,
    ),
    "issue_label_check": ExpectedHookBehavior(
        hook_name="issue_label_check",
        phase_id="issue_work",
        trigger_type="PreToolUse",
        trigger_tool="Bash",
        expected_decision="either",
        description="Issue作成時のラベル付与確認",
    ),
    "issue_review_response_check": ExpectedHookBehavior(
        hook_name="issue_review_response_check",
        phase_id="ci_review",
        trigger_type="PreToolUse",
        trigger_tool="Bash",
        expected_decision="either",
        description="Issueレビューへの返答確認（未対応時ブロック）",
    ),
    "issue_incomplete_close_check": ExpectedHookBehavior(
        hook_name="issue_incomplete_close_check",
        phase_id="merge",
        trigger_type="PreToolUse",
        trigger_tool="Bash",
        expected_decision="either",
        description="Issue部分完了でのクローズ防止（未完了チェックボックス検出）",
    ),
    "issue_scope_check": ExpectedHookBehavior(
        hook_name="issue_scope_check",
        phase_id="issue_work",
        trigger_type="PreToolUse",
        trigger_tool="Bash",
        expected_decision="either",
        description="Issueスコープの適切性確認",
    ),
    "locked_worktree_guard": ExpectedHookBehavior(
        hook_name="locked_worktree_guard",
        phase_id="pre_check",
        trigger_type="PreToolUse",
        trigger_tool="Bash",
        expected_decision="either",
        description="ロック中worktreeへの操作ブロック",
    ),
    "merge_check": ExpectedHookBehavior(
        hook_name="merge_check",
        phase_id="merge",
        trigger_type="PreToolUse",
        trigger_tool="Bash",
        expected_decision="either",
        description="マージ安全性チェック",
    ),
    "merged_worktree_check": ExpectedHookBehavior(
        hook_name="merged_worktree_check",
        phase_id="worktree_create",
        trigger_type="PreToolUse",
        trigger_tool="Bash",
        expected_decision="approve",
        description="マージ済みworktree検出→削除促進",
        can_block=False,
    ),
    "open_issue_reminder": ExpectedHookBehavior(
        hook_name="open_issue_reminder",
        phase_id="pre_check",
        trigger_type="PreToolUse",
        trigger_tool="Bash",
        expected_decision="approve",
        description="未アサインIssue表示",
        can_block=False,
    ),
    "orphan_worktree_check": ExpectedHookBehavior(
        hook_name="orphan_worktree_check",
        phase_id="worktree_create",
        trigger_type="PreToolUse",
        trigger_tool="Bash",
        expected_decision="approve",
        description="ブランチ削除済みworktree検出",
        can_block=False,
    ),
    "planning_enforcement": ExpectedHookBehavior(
        hook_name="planning_enforcement",
        phase_id="pre_check",
        trigger_type="PreToolUse",
        trigger_tool="Bash",
        expected_decision="either",
        description="計画が必要なタスクのプランニング強制",
    ),
    "pr_issue_alignment_check": ExpectedHookBehavior(
        hook_name="pr_issue_alignment_check",
        phase_id="pr_create",
        trigger_type="PreToolUse",
        trigger_tool="Bash",
        expected_decision="either",
        description="PRとIssueの整合性確認",
    ),
    "pr_issue_assign_check": ExpectedHookBehavior(
        hook_name="pr_issue_assign_check",
        phase_id="pr_create",
        trigger_type="PreToolUse",
        trigger_tool="Bash",
        expected_decision="either",
        description="参照Issueがアサインされているか確認",
    ),
    "pr_overlap_check": ExpectedHookBehavior(
        hook_name="pr_overlap_check",
        phase_id="pr_create",
        trigger_type="PreToolUse",
        trigger_tool="Bash",
        expected_decision="either",
        description="同一Issueに対する複数PR検出",
    ),
    "pr_scope_check": ExpectedHookBehavior(
        hook_name="pr_scope_check",
        phase_id="pr_create",
        trigger_type="PreToolUse",
        trigger_tool="Bash",
        expected_decision="either",
        description="1 Issue = 1 PRルール強制",
    ),
    "python_lint_check": ExpectedHookBehavior(
        hook_name="python_lint_check",
        phase_id="pre_commit_check",
        trigger_type="PreToolUse",
        trigger_tool="Bash",
        expected_decision="either",
        description="git commitでPythonファイルをruffチェック",
    ),
    "recurring_problem_block": ExpectedHookBehavior(
        hook_name="recurring_problem_block",
        phase_id="ci_review",
        trigger_type="PreToolUse",
        trigger_tool="Bash",
        expected_decision="either",
        description="繰り返し発生する問題をブロック",
    ),
    "research_requirement_check": ExpectedHookBehavior(
        hook_name="research_requirement_check",
        phase_id="pre_check",
        trigger_type="PreToolUse",
        trigger_tool="Bash",
        expected_decision="either",
        description="調査が必要なタスクの事前調査確認",
    ),
    # Issue #931: Moved to alphabetical position
    "resolve_thread_guard": ExpectedHookBehavior(
        hook_name="resolve_thread_guard",
        phase_id="merge",
        trigger_type="PreToolUse",
        trigger_tool="Bash",
        expected_decision="either",
        description="マージ前に未解決レビュースレッドをチェック",
    ),
    "reviewer_removal_check": ExpectedHookBehavior(
        hook_name="reviewer_removal_check",
        phase_id="merge",
        trigger_type="PreToolUse",
        trigger_tool="Bash",
        expected_decision="either",
        description="レビュアー削除操作を検出・警告",
    ),
    "ui_check_reminder": ExpectedHookBehavior(
        hook_name="ui_check_reminder",
        phase_id="implementation",
        trigger_type="PreToolUse",
        trigger_tool="Bash",
        expected_decision="approve",
        description="UI変更後の目視確認リマインド",
        can_block=False,
    ),
    # Issue #931: Moved to alphabetical position
    "worktree_main_freshness_check": ExpectedHookBehavior(
        hook_name="worktree_main_freshness_check",
        phase_id="worktree_create",
        trigger_type="PreToolUse",
        trigger_tool="Bash",
        expected_decision="either",
        description="git worktree add時にmainブランチの新鮮さをチェック",
    ),
    "worktree_path_guard": ExpectedHookBehavior(
        hook_name="worktree_path_guard",
        phase_id="worktree_create",
        trigger_type="PreToolUse",
        trigger_tool="Bash",
        expected_decision="either",
        description="worktreeパスの妥当性確認",
    ),
    "worktree_removal_check": ExpectedHookBehavior(
        hook_name="worktree_removal_check",
        phase_id="cleanup",
        trigger_type="PreToolUse",
        trigger_tool="Bash",
        expected_decision="either",
        description="worktree削除前のアクティブ作業検出",
    ),
    # PostToolUse - Bash
    "bash_failure_tracker": ExpectedHookBehavior(
        hook_name="bash_failure_tracker",
        phase_id="pre_commit_check",
        trigger_type="PostToolUse",
        trigger_tool="Bash",
        expected_decision="approve",
        description="コマンド失敗を追跡",
        can_block=False,
    ),
    "ci_recovery_tracker": ExpectedHookBehavior(
        hook_name="ci_recovery_tracker",
        phase_id="ci_review",
        trigger_type="PostToolUse",
        trigger_tool="Bash",
        expected_decision="approve",
        description="CI失敗→復旧の追跡",
        can_block=False,
    ),
    "codex_review_output_logger": ExpectedHookBehavior(
        hook_name="codex_review_output_logger",
        phase_id="local_ai_review",
        trigger_type="PostToolUse",
        trigger_tool="Bash",
        expected_decision="approve",
        description="Codex CLIレビュー結果をログ記録",
        can_block=False,
    ),
    "copilot_review_retry_suggestion": ExpectedHookBehavior(
        hook_name="copilot_review_retry_suggestion",
        phase_id="ci_review",
        trigger_type="PostToolUse",
        trigger_tool="Bash",
        expected_decision="approve",
        description="Copilotレビュー失敗時リトライ提案",
        can_block=False,
    ),
    "development_workflow_tracker": ExpectedHookBehavior(
        hook_name="development_workflow_tracker",
        phase_id="worktree_create",
        trigger_type="PostToolUse",
        trigger_tool="Bash",
        expected_decision="approve",
        description="ワークフロー進捗記録",
        can_block=False,
    ),
    "e2e_test_recorder": ExpectedHookBehavior(
        hook_name="e2e_test_recorder",
        phase_id="pre_commit_check",
        trigger_type="PostToolUse",
        trigger_tool="Bash",
        expected_decision="approve",
        description="E2Eテスト実行を記録",
        can_block=False,
    ),
    "flow_progress_tracker": ExpectedHookBehavior(
        hook_name="flow_progress_tracker",
        phase_id="worktree_create",
        trigger_type="PostToolUse",
        trigger_tool="Bash",
        expected_decision="approve",
        description="フロー進捗追跡",
        can_block=False,
    ),
    "git_operations_tracker": ExpectedHookBehavior(
        hook_name="git_operations_tracker",
        phase_id="worktree_create",
        trigger_type="PostToolUse",
        trigger_tool="Bash",
        expected_decision="approve",
        description="Git操作を記録",
        can_block=False,
    ),
    "issue_ai_review": ExpectedHookBehavior(
        hook_name="issue_ai_review",
        phase_id="issue_work",
        trigger_type="PostToolUse",
        trigger_tool="Bash",
        expected_decision="approve",
        description="Issue作成後にAIレビュー自動実行",
        can_block=False,
    ),
    "issue_creation_tracker": ExpectedHookBehavior(
        hook_name="issue_creation_tracker",
        phase_id="issue_work",
        trigger_type="PostToolUse",
        trigger_tool="Bash",
        expected_decision="approve",
        description="Issue作成を記録",
        can_block=False,
    ),
    "pr_metrics_collector": ExpectedHookBehavior(
        hook_name="pr_metrics_collector",
        phase_id="pr_create",
        trigger_type="PostToolUse",
        trigger_tool="Bash",
        expected_decision="approve",
        description="PRメトリクス収集",
        can_block=False,
    ),
    "pr_merge_pull_reminder": ExpectedHookBehavior(
        hook_name="pr_merge_pull_reminder",
        phase_id="merge",
        trigger_type="PostToolUse",
        trigger_tool="Bash",
        expected_decision="approve",
        description="マージ後のpullリマインダー",
        can_block=False,
    ),
    "reflection_reminder": ExpectedHookBehavior(
        hook_name="reflection_reminder",
        phase_id="ci_review",
        trigger_type="PostToolUse",
        trigger_tool="Bash",
        expected_decision="approve",
        description="振り返りリマインダー",
        can_block=False,
    ),
    "secret_deploy_trigger": ExpectedHookBehavior(
        hook_name="secret_deploy_trigger",
        phase_id="production",
        trigger_type="PostToolUse",
        trigger_tool="Bash",
        expected_decision="approve",
        description="秘密情報デプロイのトリガー検出",
        can_block=False,
    ),
    "worktree_auto_cleanup": ExpectedHookBehavior(
        hook_name="worktree_auto_cleanup",
        phase_id="merge",
        trigger_type="PostToolUse",
        trigger_tool="Bash",
        expected_decision="approve",
        description="マージ後のworktree自動クリーンアップ提案",
        can_block=False,
    ),
    # PostToolUse - Edit
    "rework_tracker": ExpectedHookBehavior(
        hook_name="rework_tracker",
        phase_id="implementation",
        trigger_type="PostToolUse",
        trigger_tool="Edit",
        expected_decision="approve",
        description="同一ファイル再編集（手戻り）追跡",
        can_block=False,
    ),
    "tool_efficiency_tracker": ExpectedHookBehavior(
        hook_name="tool_efficiency_tracker",
        phase_id="implementation",
        trigger_type="PostToolUse",
        trigger_tool="Bash|Edit|Read|Glob|Grep",
        expected_decision="approve",
        description="ツール使用効率の追跡",
        can_block=False,
    ),
    # PostToolUse - Read/Glob/Grep
    "exploration_tracker": ExpectedHookBehavior(
        hook_name="exploration_tracker",
        phase_id="implementation",
        trigger_type="PostToolUse",
        trigger_tool="Read|Glob|Grep",
        expected_decision="approve",
        description="コード探索パターンを追跡",
        can_block=False,
    ),
    # PostToolUse - WebSearch/WebFetch
    "research_tracker": ExpectedHookBehavior(
        hook_name="research_tracker",
        phase_id="implementation",
        trigger_type="PostToolUse",
        trigger_tool="WebSearch|WebFetch",
        expected_decision="approve",
        description="Web調査を追跡",
        can_block=False,
    ),
    # Stop hooks
    "cwd_check": ExpectedHookBehavior(
        hook_name="cwd_check",
        phase_id="session_end",
        trigger_type="Stop",
        expected_decision="either",
        description="カレントディレクトリ消失検知",
    ),
    "flow_effect_verifier": ExpectedHookBehavior(
        hook_name="flow_effect_verifier",
        phase_id="session_end",
        trigger_type="Stop",
        expected_decision="either",
        description="フロー効果検証",
    ),
    "git_status_check": ExpectedHookBehavior(
        hook_name="git_status_check",
        phase_id="session_end",
        trigger_type="Stop",
        expected_decision="either",
        description="mainの未コミット変更検知",
    ),
    "hook_behavior_evaluator": ExpectedHookBehavior(
        hook_name="hook_behavior_evaluator",
        phase_id="session_end",
        trigger_type="Stop",
        expected_decision="approve",
        description="期待動作と実際の動作のギャップ検知",
        can_block=False,
    ),
    "hook_effectiveness_evaluator": ExpectedHookBehavior(
        hook_name="hook_effectiveness_evaluator",
        phase_id="session_end",
        trigger_type="Stop",
        expected_decision="approve",
        description="フック有効性の評価",
        can_block=False,
    ),
    "problem_report_check": ExpectedHookBehavior(
        hook_name="problem_report_check",
        phase_id="session_end",
        trigger_type="Stop",
        expected_decision="either",
        description="発見した問題のIssue化確認",
    ),
    "related_task_check": ExpectedHookBehavior(
        hook_name="related_task_check",
        phase_id="session_end",
        trigger_type="Stop",
        expected_decision="either",
        description="関連タスク検知",
    ),
    "reflection_prompt": ExpectedHookBehavior(
        hook_name="reflection_prompt",
        phase_id="session_end",
        trigger_type="Stop",
        expected_decision="either",
        description="五省ベースの自己評価",
    ),
    "secret_deploy_check": ExpectedHookBehavior(
        hook_name="secret_deploy_check",
        phase_id="session_end",
        trigger_type="Stop",
        expected_decision="either",
        description="秘密情報デプロイ漏れチェック",
    ),
    "session_metrics_collector": ExpectedHookBehavior(
        hook_name="session_metrics_collector",
        phase_id="session_end",
        trigger_type="Stop",
        expected_decision="approve",
        description="セッションメトリクス収集",
        can_block=False,
    ),
    "session_handoff_writer": ExpectedHookBehavior(
        hook_name="session_handoff_writer",
        phase_id="session_end",
        trigger_type="Stop",
        expected_decision="approve",
        description="次セッションへの引き継ぎ情報記録",
        can_block=False,
    ),
    "systematization_check": ExpectedHookBehavior(
        hook_name="systematization_check",
        phase_id="session_end",
        trigger_type="Stop",
        expected_decision="either",
        description="教訓の仕組み化確認",
    ),
    # Issue #931: Add missing Stop hooks
    "worktree_cleanup_suggester": ExpectedHookBehavior(
        hook_name="worktree_cleanup_suggester",
        phase_id="session_end",
        trigger_type="Stop",
        expected_decision="approve",
        description="マージ済みworktreeのクリーンアップ提案",
        can_block=False,
    ),
    "session_end_worktree_cleanup": ExpectedHookBehavior(
        hook_name="session_end_worktree_cleanup",
        phase_id="session_end",
        trigger_type="Stop",
        expected_decision="approve",
        description="セッション終了時のworktreeクリーンアップ確認",
        can_block=False,
    ),
}


def get_hook_behavior(hook_name: str) -> ExpectedHookBehavior | None:
    """Get expected behavior for a hook."""
    return EXPECTED_HOOK_BEHAVIORS.get(hook_name)


def get_all_hook_names() -> list[str]:
    """Get list of all defined hook names."""
    return list(EXPECTED_HOOK_BEHAVIORS.keys())


def get_hooks_by_phase(phase_id: str) -> list[ExpectedHookBehavior]:
    """Get all hooks for a specific phase."""
    return [h for h in EXPECTED_HOOK_BEHAVIORS.values() if h.phase_id == phase_id]


def get_hooks_by_trigger_type(trigger_type: str) -> list[ExpectedHookBehavior]:
    """Get all hooks by trigger type."""
    return [h for h in EXPECTED_HOOK_BEHAVIORS.values() if h.trigger_type == trigger_type]


# =============================================================================
# FlowStep and FlowDefinition (existing code)
# =============================================================================


@dataclass
class FlowStep:
    """A single step in a flow.

    Attributes:
        id: Unique identifier for the step
        name: Display name
        description: Detailed description
        order: Order index (0-based) for basic sequencing
        required: If True, step must be completed (cannot be skipped)
        blocking: If True, next steps cannot start until this completes
        repeatable: If True, step can be completed multiple times
        parallel_with: List of step IDs that can run in parallel with this step
        depends_on: List of step IDs that must be completed before this step
        condition: Optional condition key for conditional steps (e.g., "has_review_comments")
        phase: Phase name for hierarchical display (e.g., "setup", "implementation", "review")
    """

    id: str
    name: str
    description: str = ""
    order: int = 0
    # Step characteristics
    required: bool = True
    blocking: bool = True
    repeatable: bool = False
    parallel_with: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    condition: str | None = None
    phase: str | None = None


@dataclass
class FlowDefinition(ABC):
    """Base class for flow definitions.

    Each flow must implement matches_step() for context-aware pattern matching.

    Attributes:
        id: Unique identifier for the flow
        name: Display name
        description: Detailed description
        steps: List of FlowStep instances
        blocking_on_session_end: If True, session cannot end with this flow incomplete
        completion_step: Step ID that marks the flow as complete when finished
                        (remaining steps are optional cleanup, not tracked as pending)
    """

    id: str
    name: str
    description: str = ""
    steps: list[FlowStep] = field(default_factory=list)
    blocking_on_session_end: bool = False
    completion_step: str | None = None

    def get_step_ids(self) -> list[str]:
        """Return ordered list of step IDs."""
        return [s.id for s in sorted(self.steps, key=lambda s: s.order)]

    def get_step(self, step_id: str) -> FlowStep | None:
        """Get a step by its ID."""
        for step in self.steps:
            if step.id == step_id:
                return step
        return None

    def get_step_order(self, step_id: str) -> int:
        """Get the order of a step (-1 if not found)."""
        step = self.get_step(step_id)
        return step.order if step else -1

    @abstractmethod
    def matches_step(self, step_id: str, command: str, context: dict[str, Any]) -> bool:
        """Check if a command matches a step with the given context.

        Args:
            step_id: The step ID to check
            command: The Bash command that was executed
            context: Flow context (e.g., {"issue_number": 123})

        Returns:
            True if the command matches the step for this context.
        """
        ...

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary (for backward compatibility)."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "steps": [
                {"id": s.id, "name": s.name, "description": s.description}
                for s in sorted(self.steps, key=lambda s: s.order)
            ],
            "blocking": {"on_session_end": self.blocking_on_session_end},
        }


class IssueAIReviewFlow(FlowDefinition):
    """Flow for AI review of GitHub Issues.

    Triggered when an Issue is created. Tracks:
    1. review_posted - AI review comment posted
    2. review_viewed - Claude views the review comments
    3. issue_updated - Claude updates the Issue body with review feedback

    Note: This is a regular class (not a dataclass) to avoid the fragile pattern of
    redefining parent dataclass fields with default values.
    """

    def __init__(self) -> None:
        """Initialize the IssueAIReviewFlow with predefined configuration."""
        super().__init__(
            id="issue_ai_review",
            name="Issue AIレビューフロー",
            description="AIレビュー投稿後、Claudeがレビュー内容を確認してIssueに反映するフロー",
            steps=[
                FlowStep(
                    id="review_posted",
                    name="AIレビュー投稿",
                    description="Gemini/CodexがIssueにレビューコメントを投稿",
                    order=0,
                ),
                FlowStep(
                    id="review_viewed",
                    name="レビュー確認",
                    description="Claudeがレビューコメントを確認",
                    order=1,
                ),
                FlowStep(
                    id="issue_updated",
                    name="Issue更新",
                    description="レビュー内容をIssue本文に反映",
                    order=2,
                ),
            ],
            # Issue #2108: AIレビューは情報提供目的のため、セッション終了をブロックしない
            # review_viewed/issue_updated が未完了でもセッション終了を許可
            blocking_on_session_end=False,
        )

    def matches_step(self, step_id: str, command: str, context: dict[str, Any]) -> bool:
        """Match commands with Issue-specific context.

        Args:
            step_id: The step to check
            command: The Bash command
            context: Must contain "issue_number"

        Returns:
            True if command matches the step for the specific Issue.
        """
        issue_number = context.get("issue_number")
        if not issue_number:
            return False

        # Validate and normalize issue_number to prevent regex injection
        try:
            issue_number_int = int(issue_number)
        except (TypeError, ValueError):
            return False
        issue_number_str = str(issue_number_int)

        if step_id == "review_viewed":
            # Must view the specific Issue's comments
            # Pattern: gh issue view <number> --comments
            pattern = rf"^gh\s+issue\s+view\s+{issue_number_str}\b.*--comments"
            return bool(re.search(pattern, command))

        if step_id == "issue_updated":
            # Must edit the specific Issue
            # Pattern: gh issue edit <number> or REST API PATCH
            # Issue #1374: REST API fallback when GraphQL rate limited
            patterns = [
                rf"^gh\s+issue\s+edit\s+{issue_number_str}\b",
                rf"^gh\s+api\s+repos/[^/]+/[^/]+/issues/{issue_number_str}\b.*(?:-X\s+PATCH|-X=PATCH)\b",
            ]
            return any(bool(re.search(p, command)) for p in patterns)

        # review_posted is marked programmatically, not by command matching
        return False


class DevelopmentWorkflow(FlowDefinition):
    """Flow for tracking the entire development workflow.

    Tracks the complete Issue-to-merge lifecycle:
    1. worktree_created - Worktree created for the Issue
    2. implementation - Implementation started (free phase, repeatable)
    3. committed - Changes committed
    4. pushed - Changes pushed to remote
    5. pr_created - PR created
    6. ci_passed - CI checks passed (can run parallel with review)
    7. review_addressed - Review comments addressed (optional, conditional)
    8. merged - PR merged
    9. cleaned_up - Worktree cleaned up (optional)

    Note: This flow has mixed characteristics:
    - Some steps are strictly ordered (worktree → commit → push → PR → merge)
    - Some can run in parallel (ci_passed, review_addressed)
    - Some are optional (cleaned_up)
    - Some are repeatable (implementation, committed, pushed)
    """

    def __init__(self) -> None:
        """Initialize the DevelopmentWorkflow with predefined configuration."""
        super().__init__(
            id="development-workflow",
            name="開発ワークフロー",
            description="Issue対応の開発ワークフロー全体を追跡",
            steps=[
                # Phase: setup (strict order)
                FlowStep(
                    id="worktree_created",
                    name="Worktree作成",
                    description="Issue用のworktreeを作成",
                    order=0,
                    required=True,
                    blocking=True,
                    phase="setup",
                ),
                # Phase: implementation (free phase)
                FlowStep(
                    id="implementation",
                    name="実装",
                    description="機能の実装（自由区間、繰り返し可）",
                    order=1,
                    required=True,
                    blocking=False,  # Non-blocking to allow flexible work
                    repeatable=True,
                    phase="implementation",
                ),
                # Phase: implementation (Commit-Push loop, repeatable)
                FlowStep(
                    id="committed",
                    name="コミット",
                    description="変更をコミット",
                    order=2,
                    required=True,
                    blocking=True,
                    repeatable=True,
                    phase="implementation",
                ),
                FlowStep(
                    id="pushed",
                    name="プッシュ",
                    description="変更をリモートにプッシュ",
                    order=3,
                    required=True,
                    blocking=True,
                    repeatable=True,
                    depends_on=["committed"],
                    phase="implementation",
                ),
                # Phase: review (PR and CI, parallel possible)
                FlowStep(
                    id="pr_created",
                    name="PR作成",
                    description="プルリクエストを作成",
                    order=4,
                    required=True,
                    blocking=True,
                    phase="review",
                ),
                FlowStep(
                    id="ci_passed",
                    name="CI通過",
                    description="CIチェックが全てパス",
                    order=5,
                    required=True,
                    blocking=False,  # Can wait asynchronously
                    parallel_with=["review_addressed"],
                    phase="review",
                ),
                FlowStep(
                    id="review_addressed",
                    name="レビュー対応",
                    description="レビューコメントに対応",
                    order=5,  # Same order as ci_passed (parallel)
                    required=False,  # Optional if no review comments
                    blocking=False,
                    parallel_with=["ci_passed"],
                    condition="has_review_comments",
                    phase="review",
                ),
                # Phase: complete (Merge is the completion step)
                FlowStep(
                    id="merged",
                    name="マージ",
                    description="PRをマージ",
                    order=6,
                    required=True,
                    blocking=True,
                    depends_on=["ci_passed"],
                    phase="complete",
                ),
                # Phase: complete (Cleanup is optional, after completion)
                FlowStep(
                    id="cleaned_up",
                    name="クリーンアップ",
                    description="worktreeを削除",
                    order=7,
                    required=False,
                    blocking=False,
                    phase="complete",
                ),
            ],
            blocking_on_session_end=False,  # Don't block session end
            completion_step="merged",  # Flow is complete when merged
        )

    def matches_step(self, step_id: str, command: str, context: dict[str, Any]) -> bool:
        """Match commands for development workflow steps.

        Args:
            step_id: The step to check
            command: The Bash command
            context: May contain "issue_number", "branch_name", etc.

        Returns:
            True if command matches the step.
        """
        issue_number = context.get("issue_number")

        if step_id == "worktree_created":
            # Pattern: git worktree add ... issue-<number>
            # Support both `git worktree add` and `cd /path && git worktree add` patterns
            # Issue #2534: Use (?:^|&&\s*) to match start of line or after &&
            # This avoids false positives from echo/comments while supporting cd prefix
            if issue_number:
                pattern = rf"(?:^|&&\s*)\s*git\s+worktree\s+add\b.*\bissue-{re.escape(str(issue_number))}\b"
                return bool(re.search(pattern, command))
            return bool(re.search(r"(?:^|&&\s*)\s*git\s+worktree\s+add\b", command))

        if step_id == "committed":
            # Pattern: git commit
            return bool(re.search(r"^git\s+commit\b", command))

        if step_id == "pushed":
            # Pattern: git push
            return bool(re.search(r"^git\s+push\b", command))

        if step_id == "pr_created":
            # Pattern: gh pr create
            return bool(re.search(r"^gh\s+pr\s+create\b", command))

        if step_id == "ci_passed":
            # This is typically marked programmatically after CI completes
            # Could match: gh run watch, gh pr checks
            return bool(re.search(r"^gh\s+(run\s+watch|pr\s+checks)\b", command))

        if step_id == "merged":
            # Pattern: gh pr merge
            return bool(re.search(r"^gh\s+pr\s+merge\b", command))

        if step_id == "cleaned_up":
            # Pattern: git worktree remove
            return bool(re.search(r"^git\s+worktree\s+remove\b", command))

        # implementation and review_addressed are typically marked programmatically
        return False


# Registry of all flow definitions
FLOW_REGISTRY: dict[str, FlowDefinition] = {
    "issue_ai_review": IssueAIReviewFlow(),
    "development-workflow": DevelopmentWorkflow(),
}


def get_flow_definition(flow_id: str) -> FlowDefinition | None:
    """Get a flow definition by its ID.

    Args:
        flow_id: The flow ID to look up

    Returns:
        The FlowDefinition instance, or None if not found.
    """
    return FLOW_REGISTRY.get(flow_id)


def get_all_flow_definitions() -> dict[str, FlowDefinition]:
    """Get all registered flow definitions.

    Returns:
        Dictionary of flow_id -> FlowDefinition.
    """
    return FLOW_REGISTRY.copy()


def validate_step_order(
    flow_id: str, completed_steps: list[str], new_step_id: str
) -> tuple[bool, str]:
    """Validate that a step is being completed in the correct order.

    Considers:
    - Basic order (order field)
    - Explicit dependencies (depends_on field)
    - Parallel execution (parallel_with field)
    - Blocking steps (blocking field)

    Args:
        flow_id: The flow ID
        completed_steps: List of already completed step IDs
        new_step_id: The step being completed

    Returns:
        Tuple of (is_valid, error_message).
        If valid, error_message is empty.
    """
    flow = get_flow_definition(flow_id)
    if not flow:
        return False, f"Unknown flow: {flow_id}"

    new_step = flow.get_step(new_step_id)
    if not new_step:
        return False, f"Unknown step: {new_step_id}"

    new_step_order = new_step.order

    # Check explicit dependencies first (depends_on takes priority)
    for dep_id in new_step.depends_on:
        if dep_id not in completed_steps:
            return False, (f"Step '{new_step_id}' depends on '{dep_id}' which is not completed")

    # Check order-based dependencies (blocking steps must complete first)
    for step in flow.steps:
        if step.order < new_step_order:
            # Skip if this step can run in parallel with the new step
            if new_step_id in step.parallel_with or step.id in new_step.parallel_with:
                continue

            # Skip optional (non-required) steps
            if not step.required:
                continue

            # Blocking steps must be completed
            if step.blocking and step.id not in completed_steps:
                return False, (
                    f"Step '{new_step_id}' cannot be completed before "
                    f"blocking step '{step.id}' (order: {step.order} < {new_step_order})"
                )

    return True, ""


def can_skip_step(flow_id: str, step_id: str, context: dict[str, Any]) -> bool:
    """Check if a step can be skipped based on its characteristics.

    Args:
        flow_id: The flow ID
        step_id: The step to check
        context: Flow context for condition evaluation

    Returns:
        True if the step can be skipped.
    """
    flow = get_flow_definition(flow_id)
    if not flow:
        return False

    step = flow.get_step(step_id)
    if not step:
        return False

    # Required steps cannot be skipped
    if step.required:
        return False

    # Conditional steps can be skipped if condition is not met
    if step.condition:
        # Condition key should be in context as a boolean
        return not context.get(step.condition, False)

    # Optional (non-required) steps can be skipped
    return True


def get_pending_required_steps(flow_id: str, completed_steps: list[str]) -> list[str]:
    """Get list of required steps that are not yet completed.

    Args:
        flow_id: The flow ID
        completed_steps: List of already completed step IDs

    Returns:
        List of pending required step IDs in order.
    """
    flow = get_flow_definition(flow_id)
    if not flow:
        return []

    pending = []
    for step in sorted(flow.steps, key=lambda s: s.order):
        if step.required and step.id not in completed_steps:
            pending.append(step.id)

    return pending
