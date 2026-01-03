"""Tests for flow-verifier.py functions.

Issue #1694: Add test coverage for infer_skip_reason() and format_report_text().

Note: This file covers infer_skip_reason() and format_report_text() only.
Other functions in flow-verifier.py (e.g., calculate_completion_metrics,
load_all_sessions_workflows) have their own test coverage elsewhere or are
integration-tested via hooks.
"""

import importlib.util
import sys
from pathlib import Path

# Load flow-verifier.py (hyphenated filename requires importlib)
_hook_path = Path(__file__).parent.parent / "flow-verifier.py"
_spec = importlib.util.spec_from_file_location("flow_verifier", _hook_path)
_module = importlib.util.module_from_spec(_spec)
sys.modules["flow_verifier"] = _module
_spec.loader.exec_module(_module)

infer_skip_reason = _module.infer_skip_reason
format_report_text = _module.format_report_text
count_session_violations = _module.count_session_violations  # Issue #1690


class TestInferSkipReason:
    """Tests for infer_skip_reason function."""

    def test_pre_check_with_worktree_create(self):
        """pre_check skipped when worktree_create was done."""
        result = infer_skip_reason("pre_check", "issue-123", {"worktree_create", "implementation"})
        assert result["phase"] == "pre_check"
        assert result["status"] == "skipped"
        assert "directly without pre-check" in result["reason"]

    def test_pre_check_on_main(self):
        """pre_check skipped on main branch."""
        result = infer_skip_reason("pre_check", "main", set())
        assert "main branch" in result["reason"]
        assert result["context"]["workflow_id"] == "main"

    def test_pre_check_unknown_reason(self):
        """pre_check skipped for no clear reason."""
        result = infer_skip_reason("pre_check", "issue-456", set())
        assert "Skipped initial codebase exploration" in result["reason"]

    def test_worktree_create_on_main(self):
        """worktree_create skipped on main branch."""
        result = infer_skip_reason("worktree_create", "main", set())
        assert "no worktree needed" in result["reason"]

    def test_worktree_create_with_implementation(self):
        """worktree_create skipped when implementation was done."""
        result = infer_skip_reason("worktree_create", "issue-123", {"implementation"})
        assert "previous session" in result["reason"]

    def test_worktree_create_not_detected(self):
        """worktree_create not detected."""
        result = infer_skip_reason("worktree_create", "issue-123", set())
        assert "not detected" in result["reason"]

    def test_implementation_with_pre_commit_check(self):
        """implementation skipped when pre_commit_check was seen."""
        result = infer_skip_reason("implementation", "issue-123", {"pre_commit_check"})
        assert "Documentation or config" in result["reason"]

    def test_implementation_with_pr_create(self):
        """implementation skipped when pr_create was seen."""
        result = infer_skip_reason("implementation", "issue-123", {"pr_create"})
        assert "previous session" in result["reason"]

    def test_implementation_no_detection(self):
        """implementation not detected."""
        result = infer_skip_reason("implementation", "issue-123", set())
        assert "No implementation detected" in result["reason"]

    def test_pre_commit_check_with_pr_create(self):
        """pre_commit_check skipped when pr_create was done."""
        result = infer_skip_reason("pre_commit_check", "issue-123", {"pr_create"})
        assert "previous session" in result["reason"]

    def test_pre_commit_check_with_ci_review(self):
        """pre_commit_check skipped when ci_review was done (alternative trigger)."""
        result = infer_skip_reason("pre_commit_check", "issue-123", {"ci_review"})
        assert "previous session" in result["reason"]

    def test_pre_commit_check_no_commit(self):
        """pre_commit_check skipped when no commit detected."""
        result = infer_skip_reason("pre_commit_check", "issue-123", set())
        assert "No commit detected" in result["reason"]

    def test_local_ai_review_with_pr_create(self):
        """local_ai_review skipped when pr_create was seen."""
        result = infer_skip_reason("local_ai_review", "issue-123", {"pr_create"})
        assert "optional" in result["reason"]
        assert "suggestion" in result["context"]

    def test_local_ai_review_not_run(self):
        """local_ai_review not run."""
        result = infer_skip_reason("local_ai_review", "issue-123", set())
        assert "was not run" in result["reason"]

    def test_pr_create_on_main(self):
        """pr_create skipped on main branch."""
        result = infer_skip_reason("pr_create", "main", set())
        assert "no PR needed" in result["reason"]

    def test_pr_create_with_ci_review(self):
        """pr_create skipped when ci_review was done by another session."""
        result = infer_skip_reason("pr_create", "issue-123", {"ci_review"})
        assert "another session" in result["reason"]
        assert result["context"].get("external_session") is True

    def test_pr_create_with_merge(self):
        """pr_create skipped when merge was done by another session."""
        result = infer_skip_reason("pr_create", "issue-123", {"merge"})
        assert "another session" in result["reason"]
        assert result["context"].get("external_session") is True

    def test_pr_create_not_detected(self):
        """pr_create not detected."""
        result = infer_skip_reason("pr_create", "issue-123", set())
        assert "No PR creation detected" in result["reason"]

    def test_ci_review_with_merge(self):
        """ci_review skipped when merge was done by another session."""
        result = infer_skip_reason("ci_review", "issue-123", {"merge"})
        assert "another session" in result["reason"]
        assert result["context"].get("external_session") is True

    def test_ci_review_without_pr_create(self):
        """ci_review skipped when no pr_create."""
        result = infer_skip_reason("ci_review", "issue-123", set())
        assert "No PR was created" in result["reason"]

    def test_ci_review_not_detected(self):
        """ci_review not detected after pr_create."""
        result = infer_skip_reason("ci_review", "issue-123", {"pr_create"})
        assert "not detected" in result["reason"]

    def test_merge_with_cleanup(self):
        """merge skipped when cleanup was done by another session."""
        result = infer_skip_reason("merge", "issue-123", {"cleanup"})
        assert "another session" in result["reason"]
        assert result["context"].get("external_session") is True

    def test_merge_no_pr_workflow(self):
        """merge skipped when no PR workflow."""
        result = infer_skip_reason("merge", "issue-123", set())
        assert "No PR workflow" in result["reason"]

    def test_merge_with_pr_create_only(self):
        """merge with only pr_create does not trigger 'No PR workflow'."""
        result = infer_skip_reason("merge", "issue-123", {"pr_create"})
        # Should NOT say "No PR workflow" since pr_create exists
        assert "No PR workflow" not in result["reason"]

    def test_merge_with_ci_review_only(self):
        """merge with only ci_review does not trigger 'No PR workflow'."""
        result = infer_skip_reason("merge", "issue-123", {"ci_review"})
        # Should NOT say "No PR workflow" since ci_review exists
        assert "No PR workflow" not in result["reason"]

    def test_merge_not_merged(self):
        """merge not done after pr_create."""
        result = infer_skip_reason("merge", "issue-123", {"pr_create"})
        assert "not merged" in result["reason"]

    def test_cleanup_after_merge_critical(self):
        """cleanup skipped after merge is critical."""
        result = infer_skip_reason("cleanup", "issue-123", {"merge"})
        assert "CRITICAL" in result["reason"]
        assert result["context"].get("critical") is True
        assert "suggestion" in result["context"]

    def test_cleanup_with_session_end(self):
        """cleanup skipped when session_end occurs."""
        result = infer_skip_reason("cleanup", "issue-123", {"session_end"})
        assert "Session ended" in result["reason"]

    def test_cleanup_not_detected(self):
        """cleanup not detected."""
        result = infer_skip_reason("cleanup", "issue-123", set())
        assert "No cleanup detected" in result["reason"]

    def test_production_check_optional(self):
        """production_check is optional."""
        result = infer_skip_reason("production_check", "issue-123", set())
        assert "optional" in result["reason"]

    def test_session_end_not_recorded(self):
        """session_end not recorded."""
        result = infer_skip_reason("session_end", "issue-123", set())
        assert "not properly recorded" in result["reason"]

    def test_session_start_unknown(self):
        """session_start falls through to unknown reason.

        Note: infer_skip_reason has no session_start-specific skip logic;
        this phase intentionally falls through to the default "Unknown reason".
        """
        result = infer_skip_reason("session_start", "issue-123", set())
        assert result["reason"] == "Unknown reason"

    def test_issue_work_unknown(self):
        """issue_work falls through to unknown reason.

        Note: issue_work is in EXPECTED_PHASE_ORDER but has no specific skip logic;
        it intentionally falls through to the default "Unknown reason".
        """
        result = infer_skip_reason("issue_work", "issue-123", set())
        assert result["reason"] == "Unknown reason"


class TestFormatReportText:
    """Tests for format_report_text function."""

    def test_basic_format(self):
        """Basic report formatting."""
        report = {
            "summary": {
                "total_phases": 10,
                "passed_phases": 8,
                "skipped_phases": 2,
                "total_loops": 1,
                "hooks_fired": 5,
                "completion_rate": 0.8,
                "total_critical_issues": 0,
            },
            "level1_accuracy": {"phase_transitions": {"correct": 7, "total": 8}},
            "level2_design": {},
        }
        text = format_report_text(report)
        assert "フロー検証レポート" in text
        assert "総フェーズ: 10" in text
        assert "完了: 8" in text
        assert "スキップ: 2" in text
        assert "完了率: 80%" in text

    def test_critical_issues_displayed(self):
        """Critical issues are displayed."""
        report = {
            "summary": {
                "total_phases": 5,
                "passed_phases": 3,
                "skipped_phases": 2,
                "total_loops": 0,
                "hooks_fired": 2,
                "completion_rate": 0.6,
                "total_critical_issues": 2,
            },
            "level1_accuracy": {"phase_transitions": {"correct": 3, "total": 4}},
            "level2_design": {},
        }
        text = format_report_text(report)
        assert "Critical問題: 2件" in text

    def test_skipped_phases_displayed(self):
        """Skipped phases with reasons are displayed."""
        report = {
            "summary": {
                "total_phases": 5,
                "passed_phases": 3,
                "skipped_phases": 2,
                "total_loops": 0,
                "hooks_fired": 1,
                "completion_rate": 0.6,
                "total_critical_issues": 0,
            },
            "level1_accuracy": {
                "phase_transitions": {"correct": 2, "total": 3},
                "skipped_phases": [
                    {
                        "phase": "pre_check",
                        "reason": "Started work directly",
                        "context": {"workflow_id": "issue-123"},
                    },
                    {
                        "phase": "cleanup",
                        "reason": "CRITICAL: Cleanup not done",
                        "context": {"workflow_id": "issue-123", "critical": True},
                    },
                ],
            },
            "level2_design": {},
        }
        text = format_report_text(report)
        assert "スキップされたフェーズ" in text
        assert "pre_check" in text
        assert "CRITICAL:" in text

    def test_suggestion_displayed(self):
        """Suggestions are displayed for skipped phases."""
        report = {
            "summary": {
                "total_phases": 5,
                "passed_phases": 4,
                "skipped_phases": 1,
                "total_loops": 0,
                "hooks_fired": 1,
                "completion_rate": 0.8,
                "total_critical_issues": 0,
            },
            "level1_accuracy": {
                "phase_transitions": {"correct": 3, "total": 3},
                "skipped_phases": [
                    {
                        "phase": "local_ai_review",
                        "reason": "PR created without local AI review",
                        "context": {
                            "workflow_id": "issue-123",
                            "suggestion": "Consider running codex review",
                        },
                    },
                ],
            },
            "level2_design": {},
        }
        text = format_report_text(report)
        assert "Consider running codex review" in text

    def test_level2_design_issues(self):
        """Level 2 design issues are displayed."""
        report = {
            "summary": {
                "total_phases": 5,
                "passed_phases": 5,
                "skipped_phases": 0,
                "total_loops": 0,
                "hooks_fired": 0,
                "completion_rate": 1.0,
                "total_critical_issues": 0,
            },
            "level1_accuracy": {"phase_transitions": {"correct": 4, "total": 4}},
            "level2_design": {
                "efficiency": {
                    "issues": ["Too many iterations"],
                    "suggestions": ["Consider batch processing"],
                }
            },
        }
        text = format_report_text(report)
        assert "フロー設計レビュー" in text
        assert "Too many iterations" in text
        assert "batch processing" in text

    def test_aggregated_stats(self):
        """Aggregated stats from all sessions are displayed."""
        report = {
            "summary": {
                "total_phases": 5,
                "passed_phases": 5,
                "skipped_phases": 0,
                "total_loops": 0,
                "hooks_fired": 0,
                "completion_rate": 1.0,
                "total_critical_issues": 0,
            },
            "level1_accuracy": {"phase_transitions": {"correct": 4, "total": 4}},
            "level2_design": {},
            "aggregated": {
                "total_sessions": 10,
                "total_workflows": 15,
                "completion_rate": 0.75,
                "total_critical_issues": 3,
            },
        }
        text = format_report_text(report)
        assert "全セッション統計" in text
        assert "総セッション数: 10" in text
        assert "全体完了率: 75%" in text
        assert "Critical問題（全体）: 3件" in text

    def test_empty_level2_design(self):
        """Empty level2_design does not add section."""
        report = {
            "summary": {
                "total_phases": 5,
                "passed_phases": 5,
                "skipped_phases": 0,
                "total_loops": 0,
                "hooks_fired": 0,
                "completion_rate": 1.0,
                "total_critical_issues": 0,
            },
            "level1_accuracy": {"phase_transitions": {"correct": 4, "total": 4}},
            "level2_design": {"efficiency": {}, "patterns": {}},
        }
        text = format_report_text(report)
        assert "フロー設計レビュー" not in text

    def test_skipped_phases_limited_to_five(self):
        """Skipped phases display is limited to 5 items."""
        skipped_phases = [
            {"phase": f"phase_{i}", "reason": f"Reason {i}", "context": {}} for i in range(7)
        ]
        report = {
            "summary": {
                "total_phases": 10,
                "passed_phases": 3,
                "skipped_phases": 7,
                "total_loops": 0,
                "hooks_fired": 0,
                "completion_rate": 0.3,
                "total_critical_issues": 0,
            },
            "level1_accuracy": {
                "phase_transitions": {"correct": 2, "total": 3},
                "skipped_phases": skipped_phases,
            },
            "level2_design": {},
        }
        text = format_report_text(report)
        # First 5 phases should be present
        for i in range(5):
            assert f"phase_{i}" in text
        # 6th and 7th should NOT be present (limited to 5)
        assert "phase_5" not in text
        assert "phase_6" not in text

    def test_multiple_level2_design_categories(self):
        """Multiple level2_design categories are displayed."""
        report = {
            "summary": {
                "total_phases": 5,
                "passed_phases": 5,
                "skipped_phases": 0,
                "total_loops": 0,
                "hooks_fired": 0,
                "completion_rate": 1.0,
                "total_critical_issues": 0,
            },
            "level1_accuracy": {"phase_transitions": {"correct": 4, "total": 4}},
            "level2_design": {
                "efficiency": {
                    "issues": ["Efficiency issue 1"],
                    "suggestions": ["Efficiency suggestion"],
                },
                "patterns": {
                    "issues": ["Pattern issue 1"],
                    "suggestions": ["Pattern suggestion"],
                },
            },
        }
        text = format_report_text(report)
        assert "フロー設計レビュー" in text
        assert "Efficiency issue 1" in text
        assert "Pattern issue 1" in text


class TestCountSessionViolations:
    """Tests for count_session_violations function (Issue #1690)."""

    def test_counts_critical_violations(self):
        """Should count critical violations correctly.

        Issue #1739: merge->ci_review is now in ALLOWED_LOOPBACKS,
        so it's counted as warning instead of critical.
        Issue #2153: merge->implementation is now also in ALLOWED_LOOPBACKS.
        Changed second event to merge->worktree_create for consistent testing.
        """
        events = [
            {
                "event": "phase_transition",
                "current_phase": "merge",
                "new_phase": "session_end",
                "violation_reason": "Phase 'merge' must transition to 'cleanup' before 'session_end'",
            },
            {
                "event": "phase_transition",
                "current_phase": "merge",
                "new_phase": "worktree_create",
                "violation_reason": "Phase 'merge' must transition to 'cleanup' before 'worktree_create'",
            },
        ]
        result = count_session_violations(events)
        assert result["critical"] == 2
        assert result["warning"] == 0
        assert "merge->session_end" in result["patterns"]
        assert "merge->worktree_create" in result["patterns"]

    def test_counts_warning_violations(self):
        """Should count warning violations correctly."""
        events = [
            {
                "event": "phase_transition",
                "current_phase": "implementation",
                "new_phase": "pre_check",
                "violation_reason": "Phase 'implementation' must transition to 'pre_commit_check' before 'pre_check'",
            },
        ]
        result = count_session_violations(events)
        assert result["critical"] == 0
        assert result["warning"] == 1
        assert "implementation->pre_check" in result["patterns"]

    def test_ignores_events_without_violation(self):
        """Should ignore events without violation_reason."""
        events = [
            {
                "event": "phase_transition",
                "current_phase": "pre_check",
                "new_phase": "implementation",
            },
            {"event": "hook_fired", "current_phase": "implementation"},
        ]
        result = count_session_violations(events)
        assert result["critical"] == 0
        assert result["warning"] == 0
        assert result["patterns"] == {}

    def test_counts_patterns_correctly(self):
        """Should count same pattern violations."""
        events = [
            {
                "event": "phase_transition",
                "current_phase": "merge",
                "new_phase": "session_end",
                "violation_reason": "violation 1",
            },
            {
                "event": "phase_transition",
                "current_phase": "merge",
                "new_phase": "session_end",
                "violation_reason": "violation 2",
            },
        ]
        result = count_session_violations(events)
        assert result["patterns"]["merge->session_end"] == 2

    def test_empty_events(self):
        """Should handle empty events list."""
        result = count_session_violations([])
        assert result["critical"] == 0
        assert result["warning"] == 0
        assert result["patterns"] == {}
        assert result["details"] == []


class TestViolationSummaryInReport:
    """Tests for violation summary in format_report_text (Issue #1690)."""

    def test_shows_violation_summary_when_violations_exist(self):
        """Should show violation summary when violations exist."""
        report = {
            "summary": {
                "total_phases": 5,
                "passed_phases": 5,
                "skipped_phases": 0,
                "total_loops": 0,
                "hooks_fired": 10,
                "completion_rate": 1.0,
                "total_critical_issues": 0,
                "violations_critical": 1,
                "violations_warning": 2,
            },
            "level1_accuracy": {"phase_transitions": {"correct": 5, "total": 5}},
            "level2_design": {},
            "violations": {
                "patterns": {"merge->session_end": 1, "implementation->pre_check": 2},
            },
        }
        text = format_report_text(report)
        assert "フェーズ遷移違反" in text
        assert "Critical: 1件" in text
        assert "Warning: 2件" in text
        assert "merge->session_end" in text

    def test_no_violation_summary_when_no_violations(self):
        """Should not show violation summary when no violations."""
        report = {
            "summary": {
                "total_phases": 5,
                "passed_phases": 5,
                "skipped_phases": 0,
                "total_loops": 0,
                "hooks_fired": 10,
                "completion_rate": 1.0,
                "total_critical_issues": 0,
                "violations_critical": 0,
                "violations_warning": 0,
            },
            "level1_accuracy": {"phase_transitions": {"correct": 5, "total": 5}},
            "level2_design": {},
        }
        text = format_report_text(report)
        assert "フェーズ遷移違反" not in text
