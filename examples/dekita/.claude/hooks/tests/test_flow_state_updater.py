#!/usr/bin/env python3
"""Tests for flow-state-updater.py."""

import json
import os
import re
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

# Add tests and hooks directory to path for imports
TESTS_DIR = Path(__file__).parent
HOOKS_DIR = TESTS_DIR.parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from conftest import load_hook_module

# Load module under test (handles hyphenated filename)
fsu = load_hook_module("flow-state-updater")


class TestDetectPhaseTransition:
    """Tests for detect_phase_transition function.

    Issue #769: detect_phase_transition now returns 3 values:
    (new_phase, loop_reason, transition_reason)
    """

    def test_session_start_on_session_start_hook(self):
        """SessionStart hook should trigger session_start phase when not already in it."""
        hook_input = {"hook_type": "SessionStart"}
        state = {}
        # When transitioning from a different phase (e.g., pre_check), should trigger session_start
        new_phase, loop_reason, transition_reason, _ = fsu.detect_phase_transition(
            "pre_check", hook_input, state
        )
        assert new_phase == "session_start"
        assert loop_reason is None
        assert transition_reason == "hook_type: SessionStart"

    def test_no_transition_when_already_in_session_start(self):
        """No transition should occur when already in session_start phase."""
        hook_input = {"hook_type": "SessionStart"}
        state = {}
        new_phase, loop_reason, transition_reason, _ = fsu.detect_phase_transition(
            "session_start", hook_input, state
        )
        assert new_phase is None
        assert loop_reason is None
        assert transition_reason is None

    def test_pre_check_on_read_tool(self):
        """Read tool should trigger pre_check phase."""
        hook_input = {"tool_name": "Read", "hook_type": "PreToolUse"}
        state = {}
        new_phase, loop_reason, transition_reason, _ = fsu.detect_phase_transition(
            "session_start", hook_input, state
        )
        assert new_phase == "pre_check"
        assert loop_reason is None
        assert transition_reason == "tool: Read"

    def test_implementation_on_edit_tool(self):
        """Edit tool should trigger implementation phase."""
        hook_input = {"tool_name": "Edit", "hook_type": "PreToolUse"}
        state = {}
        new_phase, loop_reason, transition_reason, _ = fsu.detect_phase_transition(
            "pre_check", hook_input, state
        )
        assert new_phase == "implementation"
        assert loop_reason is None
        assert transition_reason == "tool: Edit"

    def test_worktree_create_on_git_worktree_add(self):
        """git worktree add command should trigger worktree_create phase."""
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "git worktree add .worktrees/issue-123"},
            "hook_type": "PreToolUse",
        }
        state = {}
        new_phase, loop_reason, transition_reason, _ = fsu.detect_phase_transition(
            "pre_check", hook_input, state
        )
        assert new_phase == "worktree_create"
        assert loop_reason is None
        # pre_check has exit_pattern for "git worktree add", so it's exit_pattern
        assert "exit_pattern:" in transition_reason

    def test_pr_create_on_gh_pr_create(self):
        """gh pr create command should trigger pr_create phase."""
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr create --title 'test'"},
            "hook_type": "PreToolUse",
        }
        state = {}
        new_phase, loop_reason, transition_reason, _ = fsu.detect_phase_transition(
            "implementation", hook_input, state
        )
        assert new_phase == "pr_create"
        assert loop_reason is None
        assert "enter_pattern:" in transition_reason

    def test_session_end_on_stop_hook(self):
        """Stop hook should trigger session_end phase."""
        hook_input = {"hook_type": "Stop"}
        state = {}
        new_phase, loop_reason, transition_reason, _ = fsu.detect_phase_transition(
            "ci_review", hook_input, state
        )
        assert new_phase == "session_end"
        assert loop_reason is None
        assert transition_reason == "hook_type: Stop"

    def test_loop_on_ci_failure(self):
        """CI failure should trigger loop back to implementation from ci_review."""
        hook_input = {
            "tool_name": "Bash",
            "tool_output": "CI failed: build error",
            "hook_type": "PostToolUse",
        }
        state = {}
        # implementation has loop_from: ["ci_review"], so when in ci_review and CI fails,
        # should loop back to implementation
        new_phase, loop_reason, transition_reason, _ = fsu.detect_phase_transition(
            "ci_review", hook_input, state
        )
        assert new_phase == "implementation"
        assert loop_reason == "ci_failed"
        assert "loop_trigger:" in transition_reason

    def test_no_transition_when_same_phase(self):
        """No transition should occur when already in the target phase."""
        hook_input = {"tool_name": "Read", "hook_type": "PreToolUse"}
        state = {}
        new_phase, loop_reason, transition_reason, _ = fsu.detect_phase_transition(
            "pre_check", hook_input, state
        )
        # Already in pre_check, Read triggers pre_check but skipped as we're already there
        assert new_phase is None
        assert loop_reason is None
        assert transition_reason is None

    def test_read_during_implementation_does_not_trigger_pre_check(self):
        """Issue #1369: Read/Grep/Glob during active phases should not trigger pre_check.

        Reading code during implementation is normal behavior and should not
        reset the phase back to pre_check.
        """
        # Test all Read/Grep/Glob tools during implementation
        for tool_name in ["Read", "Grep", "Glob"]:
            hook_input = {"tool_name": tool_name, "hook_type": "PreToolUse"}
            state = {}
            new_phase, loop_reason, transition_reason, _ = fsu.detect_phase_transition(
                "implementation", hook_input, state
            )
            assert new_phase is None, (
                f"{tool_name} should not trigger pre_check during implementation"
            )
            assert loop_reason is None
            assert transition_reason is None

    def test_read_during_other_active_phases_does_not_trigger_pre_check(self):
        """Issue #1369: Read/Grep/Glob should not trigger pre_check in any active work phase."""
        # Issue #1380: Use constant instead of hardcoding
        for phase in fsu.ACTIVE_WORK_PHASES:
            hook_input = {"tool_name": "Read", "hook_type": "PreToolUse"}
            state = {}
            new_phase, loop_reason, transition_reason, _ = fsu.detect_phase_transition(
                phase, hook_input, state
            )
            assert new_phase is None, f"Read should not trigger pre_check during {phase}"

    def test_read_during_session_start_triggers_pre_check(self):
        """Read during session_start SHOULD trigger pre_check (not an active work phase)."""
        hook_input = {"tool_name": "Read", "hook_type": "PreToolUse"}
        state = {}
        new_phase, loop_reason, transition_reason, _ = fsu.detect_phase_transition(
            "session_start", hook_input, state
        )
        assert new_phase == "pre_check", "Read during session_start should trigger pre_check"
        assert transition_reason == "tool: Read"

    def test_git_push_during_merge_allows_ci_review_reentry(self):
        """git push during merge phase SHOULD trigger ci_review.

        A failed merge attempt (e.g., branch behind) leaves workflow in "merge" phase.
        Subsequent git push to update the PR should return to ci_review for new CI run.
        This is the correct behavior per Codex review feedback.
        """
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "git push origin main"},
            "hook_type": "PreToolUse",
        }
        state = {}
        new_phase, loop_reason, transition_reason, _ = fsu.detect_phase_transition(
            "merge", hook_input, state
        )
        # merge phase allows ci_review re-entry for failed merge recovery
        assert new_phase == "ci_review", "git push during merge SHOULD trigger ci_review"
        assert "enter_pattern" in transition_reason

    def test_git_push_during_cleanup_does_not_trigger_ci_review(self):
        """Issue #1363: git push during cleanup phase should NOT re-trigger ci_review."""
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "git push"},
            "hook_type": "PreToolUse",
        }
        state = {}
        new_phase, loop_reason, transition_reason, _ = fsu.detect_phase_transition(
            "cleanup", hook_input, state
        )
        assert new_phase is None, "git push during cleanup should NOT trigger ci_review"

    def test_git_push_during_session_end_does_not_trigger_ci_review(self):
        """Issue #1363: git push during session_end phase should NOT re-trigger ci_review.

        session_end is a truly post-merge phase where ci_review re-entry is blocked.
        """
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "git push"},
            "hook_type": "PreToolUse",
        }
        state = {}
        new_phase, loop_reason, transition_reason, _ = fsu.detect_phase_transition(
            "session_end", hook_input, state
        )
        assert new_phase is None, "git push during session_end should NOT trigger ci_review"

    def test_git_push_during_ci_review_still_works(self):
        """git push during ci_review should still work (not a post-merge phase)."""
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "git push origin feat/issue-123"},
            "hook_type": "PreToolUse",
        }
        state = {}
        # ci_review -> ci_review is same phase, so no transition
        new_phase, loop_reason, transition_reason, _ = fsu.detect_phase_transition(
            "ci_review", hook_input, state
        )
        # Already in ci_review, so no new transition
        assert new_phase is None

    def test_gh_pr_create_during_merge_triggers_pr_create_not_ci_review(self):
        """Issue #1363: gh pr create during merge should trigger pr_create, not ci_review.

        gh pr create matches both pr_create and ci_review patterns, but pr_create
        should take precedence and ci_review should be blocked from post-merge phases.
        """
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr create --title 'Hotfix'"},
            "hook_type": "PreToolUse",
        }
        state = {}
        new_phase, loop_reason, transition_reason, _ = fsu.detect_phase_transition(
            "merge", hook_input, state
        )
        # pr_create is allowed (new PR for hotfix)
        assert new_phase == "pr_create", "gh pr create should trigger pr_create"


class TestIsValidPhaseTransition:
    """Tests for is_valid_phase_transition function (Issue #1309)."""

    def test_same_phase_is_valid(self):
        """Looping back to the same phase should be valid."""
        is_valid, violation = fsu.is_valid_phase_transition("implementation", "implementation")
        assert is_valid is True
        assert violation is None

    def test_session_start_to_pre_check_is_valid(self):
        """session_start -> pre_check is the required transition."""
        is_valid, violation = fsu.is_valid_phase_transition("session_start", "pre_check")
        assert is_valid is True
        assert violation is None

    def test_session_start_to_implementation_is_invalid(self):
        """session_start -> implementation should be invalid (skips pre_check)."""
        is_valid, violation = fsu.is_valid_phase_transition("session_start", "implementation")
        assert is_valid is False
        assert "pre_check" in violation

    def test_implementation_to_pre_commit_check_is_valid(self):
        """implementation -> pre_commit_check is the required transition."""
        is_valid, violation = fsu.is_valid_phase_transition("implementation", "pre_commit_check")
        assert is_valid is True
        assert violation is None

    def test_implementation_to_pr_create_is_invalid(self):
        """implementation -> pr_create should be invalid (skips pre_commit_check)."""
        is_valid, violation = fsu.is_valid_phase_transition("implementation", "pr_create")
        assert is_valid is False
        assert "pre_commit_check" in violation

    def test_merge_to_cleanup_is_valid(self):
        """merge -> cleanup is the required transition."""
        is_valid, violation = fsu.is_valid_phase_transition("merge", "cleanup")
        assert is_valid is True
        assert violation is None

    def test_merge_to_session_end_is_invalid(self):
        """merge -> session_end should be invalid (skips cleanup)."""
        is_valid, violation = fsu.is_valid_phase_transition("merge", "session_end")
        assert is_valid is False
        assert "cleanup" in violation

    def test_optional_phase_is_valid_but_logs_violation(self):
        """Transitioning to an optional phase is valid but logs bypass violation (Issue #1345)."""
        # session_start -> issue_work (issue_work is optional)
        # This should be allowed but should log that pre_check was bypassed
        is_valid, violation = fsu.is_valid_phase_transition("session_start", "issue_work")
        assert is_valid is True
        # Issue #1345: Optional phase should report the bypass violation
        assert violation is not None
        assert "pre_check" in violation
        assert "bypassed" in violation
        assert "issue_work" in violation

    def test_session_start_to_worktree_create_reports_bypass(self):
        """session_start -> worktree_create should report pre_check bypass (Issue #1345)."""
        is_valid, violation = fsu.is_valid_phase_transition("session_start", "worktree_create")
        assert is_valid is True
        assert violation is not None
        assert "pre_check" in violation
        assert "worktree_create" in violation

    def test_implementation_to_worktree_create_reports_bypass(self):
        """implementation -> worktree_create should report pre_commit_check bypass (Issue #1345)."""
        is_valid, violation = fsu.is_valid_phase_transition("implementation", "worktree_create")
        assert is_valid is True
        assert violation is not None
        assert "pre_commit_check" in violation
        assert "worktree_create" in violation

    def test_merge_to_optional_phase_reports_bypass(self):
        """merge -> optional phase should report cleanup bypass (Issue #1345)."""
        is_valid, violation = fsu.is_valid_phase_transition("merge", "production_check")
        assert is_valid is True
        assert violation is not None
        assert "cleanup" in violation
        assert "production_check" in violation

    def test_violation_reason_contains_required_phase(self):
        """Violation reason should specify what phase was required."""
        is_valid, violation = fsu.is_valid_phase_transition("implementation", "merge")
        assert is_valid is False
        assert "pre_commit_check" in violation
        assert "implementation" in violation
        assert "merge" in violation


class TestUpdateWorkflowState:
    """Tests for update_workflow_state function."""

    def test_create_new_workflow(self):
        """Should create new workflow entry when it doesn't exist."""
        state = {"workflows": {}}
        fsu.update_workflow_state(state, "issue-123", "implementation", None)

        assert "issue-123" in state["workflows"]
        assert state["workflows"]["issue-123"]["current_phase"] == "implementation"
        assert state["active_workflow"] == "issue-123"

    def test_update_existing_workflow(self):
        """Should update existing workflow phase."""
        state = {
            "workflows": {
                "issue-123": {
                    "current_phase": "pre_check",
                    "phases": {"pre_check": {"status": "in_progress", "iterations": 1}},
                }
            }
        }
        fsu.update_workflow_state(state, "issue-123", "implementation", None)

        assert state["workflows"]["issue-123"]["current_phase"] == "implementation"
        assert state["workflows"]["issue-123"]["phases"]["pre_check"]["status"] == "completed"
        assert (
            state["workflows"]["issue-123"]["phases"]["implementation"]["status"] == "in_progress"
        )

    def test_increment_iteration_on_loop(self):
        """Should increment iteration count when looping."""
        state = {
            "workflows": {
                "issue-123": {
                    "current_phase": "ci_review",
                    "phases": {"implementation": {"status": "completed", "iterations": 1}},
                }
            }
        }
        fsu.update_workflow_state(state, "issue-123", "implementation", "ci_failed")

        assert state["workflows"]["issue-123"]["phases"]["implementation"]["iterations"] == 2
        assert (
            "ci_failed"
            in state["workflows"]["issue-123"]["phases"]["implementation"]["loop_reasons"]
        )

    def test_sets_phase_start_time_on_new_workflow(self):
        """Issue #1642: Should set phase_start_time when creating new workflow."""
        state = {"workflows": {}}
        fsu.update_workflow_state(state, "issue-123", "implementation", None)

        assert "phase_start_time" in state["workflows"]["issue-123"]
        # Verify it's a valid ISO timestamp
        phase_start = state["workflows"]["issue-123"]["phase_start_time"]
        datetime.fromisoformat(phase_start)  # Should not raise

    def test_updates_phase_start_time_on_transition(self):
        """Issue #1642: Should update phase_start_time when transitioning phases."""
        old_time = (datetime.now(UTC) - timedelta(seconds=30)).isoformat()
        state = {
            "workflows": {
                "issue-123": {
                    "current_phase": "pre_check",
                    "phases": {"pre_check": {"status": "in_progress", "iterations": 1}},
                    "phase_start_time": old_time,
                }
            }
        }
        fsu.update_workflow_state(state, "issue-123", "implementation", None)

        new_time = state["workflows"]["issue-123"]["phase_start_time"]
        assert new_time != old_time
        # New time should be more recent
        assert datetime.fromisoformat(new_time) > datetime.fromisoformat(old_time)


class TestGetCurrentWorkflow:
    """Tests for get_current_workflow function."""

    def test_detect_from_worktree_path(self):
        """Should detect workflow from worktree path."""
        with mock.patch("os.getcwd", return_value="/path/.worktrees/issue-123/some/dir"):
            workflow = fsu.get_current_workflow()
            assert workflow == "issue-123"

    def test_detect_main_branch(self):
        """Should detect main workflow from main branch."""
        with mock.patch("os.getcwd", return_value="/path/to/repo"):
            with mock.patch("subprocess.run") as mock_run:
                mock_run.return_value = mock.Mock(returncode=0, stdout="main\n")
                workflow = fsu.get_current_workflow()
                assert workflow == "main"

    def test_detect_issue_from_branch(self):
        """Should detect issue number from branch name."""
        with mock.patch("os.getcwd", return_value="/path/to/repo"):
            with mock.patch("subprocess.run") as mock_run:
                mock_run.return_value = mock.Mock(
                    returncode=0, stdout="feat/issue-456-new-feature\n"
                )
                workflow = fsu.get_current_workflow()
                assert workflow == "issue-456"

    def test_detect_from_cleanup_command_absolute_path(self):
        """Issue #1365: Should detect workflow from cleanup command with absolute path."""
        hook_input = {
            "tool_input": {"command": "git worktree remove /path/to/.worktrees/issue-1346"}
        }
        # Even when cwd is main repo, should detect from command target
        with mock.patch("os.getcwd", return_value="/path/to/main/repo"):
            workflow = fsu.get_current_workflow(hook_input)
            assert workflow == "issue-1346"

    def test_detect_from_cleanup_command_relative_path(self):
        """Issue #1365: Should detect workflow from cleanup command with relative path."""
        hook_input = {"tool_input": {"command": "git worktree remove .worktrees/issue-789"}}
        with mock.patch("os.getcwd", return_value="/path/to/main/repo"):
            workflow = fsu.get_current_workflow(hook_input)
            assert workflow == "issue-789"

    def test_cleanup_command_takes_priority(self):
        """Issue #1365: Cleanup command target should take priority over cwd."""
        hook_input = {"tool_input": {"command": "git worktree remove /path/.worktrees/issue-100"}}
        # Even when cwd is a different worktree
        with mock.patch("os.getcwd", return_value="/path/.worktrees/issue-999"):
            workflow = fsu.get_current_workflow(hook_input)
            assert workflow == "issue-100"

    def test_non_cleanup_command_uses_cwd(self):
        """Issue #1365: Non-cleanup commands should still use cwd detection."""
        hook_input = {"tool_input": {"command": "git status"}}
        with mock.patch("os.getcwd", return_value="/path/.worktrees/issue-123"):
            workflow = fsu.get_current_workflow(hook_input)
            assert workflow == "issue-123"

    def test_detect_from_cleanup_command_path_with_spaces(self):
        """Issue #1365: Should detect workflow from paths containing spaces."""
        hook_input = {
            "tool_input": {
                "command": "git worktree remove /Users/John Doe/dev/.worktrees/issue-1346"
            }
        }
        with mock.patch("os.getcwd", return_value="/Users/John Doe/dev"):
            workflow = fsu.get_current_workflow(hook_input)
            assert workflow == "issue-1346"

    def test_detect_from_cleanup_command_with_force_flag(self):
        """Issue #1365: Should detect workflow when -f or --force flag is used."""
        # Test with -f flag
        hook_input = {
            "tool_input": {"command": "git worktree remove -f /path/.worktrees/issue-456"}
        }
        with mock.patch("os.getcwd", return_value="/path"):
            workflow = fsu.get_current_workflow(hook_input)
            assert workflow == "issue-456"

        # Test with --force flag
        hook_input = {"tool_input": {"command": "git worktree remove --force .worktrees/issue-789"}}
        with mock.patch("os.getcwd", return_value="/path"):
            workflow = fsu.get_current_workflow(hook_input)
            assert workflow == "issue-789"

        # Test with multiple flags combination (e.g., -f with another valid flag)
        hook_input = {
            "tool_input": {"command": "git worktree remove -f --dry-run .worktrees/issue-multi"}
        }
        with mock.patch("os.getcwd", return_value="/path"):
            workflow = fsu.get_current_workflow(hook_input)
            assert workflow == "issue-multi"

    def test_detect_from_cleanup_command_windows_path(self):
        """Issue #1365: Should detect workflow from Windows-style paths."""
        hook_input = {
            "tool_input": {"command": r"git worktree remove C:\Users\Dev\.worktrees\issue-123"}
        }
        with mock.patch("os.getcwd", return_value=r"C:\Users\Dev"):
            workflow = fsu.get_current_workflow(hook_input)
            assert workflow == "issue-123"

    def test_detect_from_cleanup_command_quoted_path(self):
        """Issue #1365: Should correctly extract workflow name from quoted paths."""
        hook_input = {
            "tool_input": {"command": 'git worktree remove "/Users/John Doe/.worktrees/issue-999"'}
        }
        with mock.patch("os.getcwd", return_value="/Users/John Doe"):
            workflow = fsu.get_current_workflow(hook_input)
            assert workflow == "issue-999"


class TestLoadAndSaveState:
    """Tests for load_state and save_state functions."""

    def test_load_state_creates_initial_state(self):
        """Should create initial state when file doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(fsu, "FLOW_LOG_DIR", Path(tmpdir)):
                # Issue #734: load_state now requires session_id
                session_id = "test-session-123"
                state = fsu.load_state(session_id)
                assert state["session_id"] == session_id
                assert state["active_workflow"] is None
                assert state["workflows"] == {}

    def test_save_and_load_state(self):
        """Should save and load state correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(fsu, "FLOW_LOG_DIR", Path(tmpdir)):
                # Issue #734: load_state/save_state now require session_id
                session_id = "test-session"
                test_state = {
                    "session_id": session_id,
                    "active_workflow": "issue-123",
                    "workflows": {"issue-123": {"current_phase": "implementation"}},
                    "global": {"hooks_fired_total": 5},
                }
                fsu.save_state(test_state, session_id)

                # Verify file was created with session-specific name
                state_file = Path(tmpdir) / f"state-{session_id}.json"
                loaded = json.loads(state_file.read_text())
                assert loaded["session_id"] == session_id
                assert loaded["active_workflow"] == "issue-123"


class TestLogEvent:
    """Tests for log_event function."""

    def test_log_event_appends_to_file(self):
        """Should append event to session-specific events file.

        Issue #1831: Events are now stored per-session.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            session_id = "test-session-123"
            events_file = Path(tmpdir) / f"events-{session_id}.jsonl"
            with mock.patch.object(fsu, "FLOW_LOG_DIR", Path(tmpdir)):
                fsu.log_event({"event": "test", "workflow": "issue-123", "session_id": session_id})
                fsu.log_event({"event": "test2", "workflow": "issue-123", "session_id": session_id})

                lines = events_file.read_text().strip().split("\n")
                assert len(lines) == 2
                assert json.loads(lines[0])["event"] == "test"
                assert json.loads(lines[1])["event"] == "test2"


class TestInferToolResult:
    """Tests for infer_tool_result function (Issue #769)."""

    def test_success_on_normal_output(self):
        """Should return success for normal output."""
        hook_input = {"tool_name": "Bash", "tool_output": "Command completed successfully"}
        result = fsu.infer_tool_result(hook_input)
        assert result == "success"

    def test_failure_on_error_output(self):
        """Should return failure for error output."""
        hook_input = {"tool_name": "Bash", "tool_output": "Error: command not found"}
        result = fsu.infer_tool_result(hook_input)
        assert result == "failure"

    def test_failure_on_exit_code_1(self):
        """Should return failure for exit code 1."""
        hook_input = {"tool_name": "Bash", "tool_output": "Exit code 1\ntest failed"}
        result = fsu.infer_tool_result(hook_input)
        assert result == "failure"

    def test_blocked_on_hook_denied(self):
        """Should return blocked when hook denied the operation."""
        hook_input = {
            "tool_name": "Bash",
            "tool_output": "Hook PreToolUse:Bash denied this tool",
        }
        result = fsu.infer_tool_result(hook_input)
        assert result == "blocked"

    def test_none_for_pre_tool_use(self):
        """Should return None for PreToolUse hooks (no tool_output)."""
        hook_input = {"tool_name": "Bash", "tool_input": {"command": "ls"}}
        result = fsu.infer_tool_result(hook_input)
        assert result is None

    def test_success_on_empty_output(self):
        """Should return success for empty output (tool completed)."""
        hook_input = {"tool_name": "Edit", "tool_output": ""}
        result = fsu.infer_tool_result(hook_input)
        assert result == "success"


class TestLoopTriggers:
    """Tests for loop trigger detection."""

    def test_ci_failed_patterns(self):
        """Should detect CI failure patterns."""
        patterns = fsu.LOOP_TRIGGERS["ci_failed"]
        test_outputs = [
            "CI failed: build error",
            "check failed",
            "workflow failed with exit code 1",
            "Build failed in 2m30s",
        ]

        for output in test_outputs:
            matched = any(re.search(p, output, re.IGNORECASE) for p in patterns)
            assert matched, f"Pattern not matched: {output}"

    def test_lint_error_patterns(self):
        """Should detect lint error patterns."""
        patterns = fsu.LOOP_TRIGGERS["lint_error"]
        test_outputs = [
            "lint error in file.py",
            "ruff check failed",
            "biome lint errors",
            "eslint: 3 errors",
            "Lint error: unused variable",
        ]

        for output in test_outputs:
            matched = any(re.search(p, output, re.IGNORECASE) for p in patterns)
            assert matched, f"Pattern not matched: {output}"

    def test_test_failed_patterns(self):
        """Should detect test failure patterns."""
        patterns = fsu.LOOP_TRIGGERS["test_failed"]
        test_outputs = [
            "test failed: expected 1, got 2",
            "FAILED tests/test_foo.py",
            "AssertionError: values differ",
        ]

        for output in test_outputs:
            matched = any(re.search(p, output, re.IGNORECASE) for p in patterns)
            assert matched, f"Pattern not matched: {output}"


class TestCleanupOldStateFiles:
    """Tests for cleanup_old_session_files function (Issue #1280)."""

    def test_deletes_old_files(self):
        """Should delete state files older than CLEANUP_MAX_AGE_HOURS."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(fsu, "FLOW_LOG_DIR", Path(tmpdir)):
                # Create an old state file (older than 24 hours)
                old_file = Path(tmpdir) / "state-old-session.json"
                old_file.write_text('{"session_id": "old"}')
                # Set mtime to 25 hours ago
                old_mtime = time.time() - (25 * 60 * 60)
                os.utime(old_file, (old_mtime, old_mtime))

                # Create a recent state file
                recent_file = Path(tmpdir) / "state-recent-session.json"
                recent_file.write_text('{"session_id": "recent"}')

                # Run cleanup
                deleted_count = fsu.cleanup_old_session_files()

                assert deleted_count == 1
                assert not old_file.exists()
                assert recent_file.exists()

    def test_no_deletion_when_all_recent(self):
        """Should not delete any files if all are recent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(fsu, "FLOW_LOG_DIR", Path(tmpdir)):
                # Create recent state files
                for i in range(3):
                    state_file = Path(tmpdir) / f"state-session-{i}.json"
                    state_file.write_text(f'{{"session_id": "session-{i}"}}')

                deleted_count = fsu.cleanup_old_session_files()

                assert deleted_count == 0
                assert len(list(Path(tmpdir).glob("state-*.json"))) == 3

    def test_handles_nonexistent_directory(self):
        """Should handle case when log directory doesn't exist."""
        with mock.patch.object(fsu, "FLOW_LOG_DIR", Path("/nonexistent/path")):
            deleted_count = fsu.cleanup_old_session_files()
            assert deleted_count == 0


class TestCleanupSessionState:
    """Tests for cleanup_session_state function (Issue #1280)."""

    def test_deletes_session_state_file(self):
        """Should delete state file for the specified session."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(fsu, "FLOW_LOG_DIR", Path(tmpdir)):
                session_id = "test-session-123"
                state_file = Path(tmpdir) / f"state-{session_id}.json"
                state_file.write_text('{"session_id": "test"}')

                result = fsu.cleanup_session_state(session_id)

                assert result is True
                assert not state_file.exists()

    def test_returns_false_when_file_not_exists(self):
        """Should return False when state file doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(fsu, "FLOW_LOG_DIR", Path(tmpdir)):
                result = fsu.cleanup_session_state("nonexistent-session")
                assert result is False

    def test_handles_permission_error(self):
        """Should handle permission error gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(fsu, "FLOW_LOG_DIR", Path(tmpdir)):
                session_id = "test-session"
                state_file = Path(tmpdir) / f"state-{session_id}.json"
                state_file.write_text('{"session_id": "test"}')

                # Mock unlink to raise OSError
                with mock.patch.object(Path, "unlink", side_effect=OSError("Permission denied")):
                    result = fsu.cleanup_session_state(session_id)
                    # Should handle error gracefully and return False
                    assert result is False


class TestCleanupIntegration:
    """Integration tests for cleanup behavior in main function (Issue #1280)."""

    def test_session_end_triggers_cleanup(self):
        """Should cleanup session state when session_end phase is reached."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(fsu, "FLOW_LOG_DIR", Path(tmpdir)):
                session_id = "test-session"
                state_file = Path(tmpdir) / f"state-{session_id}.json"

                # Create initial state
                initial_state = {
                    "session_id": session_id,
                    "active_workflow": "main",
                    "workflows": {"main": {"current_phase": "cleanup"}},
                    "global": {"hooks_fired_total": 5},
                }
                fsu.save_state(initial_state, session_id)
                assert state_file.exists()

                # Simulate session end - cleanup should delete the file
                fsu.cleanup_session_state(session_id)

                assert not state_file.exists()

    def test_periodic_cleanup_runs_at_frequency(self):
        """Should run cleanup_old_session_files every CLEANUP_FREQUENCY hook executions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(fsu, "FLOW_LOG_DIR", Path(tmpdir)):
                with mock.patch.object(fsu, "cleanup_old_session_files") as mock_cleanup:
                    mock_ctx = mock.MagicMock()
                    mock_ctx.get_session_id.return_value = "test-sess"
                    with mock.patch.object(fsu, "create_hook_context", return_value=mock_ctx):
                        with mock.patch.object(fsu, "get_current_workflow", return_value="main"):
                            # Simulate hook executions
                            # Cleanup should be called when hooks_fired_total % CLEANUP_FREQUENCY == 0

                            # First, create initial state with hooks_fired_total = 9
                            # (one less than CLEANUP_FREQUENCY=10)
                            session_id = "test-sess"
                            initial_state = {
                                "session_id": session_id,
                                "active_workflow": "main",
                                "workflows": {"main": {"current_phase": "implementation"}},
                                "global": {"hooks_fired_total": 9},
                            }
                            fsu.save_state(initial_state, session_id)

                            # Simulate a hook call with a simple Read tool
                            hook_input = {"tool_name": "Read", "hook_type": "PreToolUse"}
                            with mock.patch("sys.stdin.read", return_value=json.dumps(hook_input)):
                                with mock.patch("builtins.print"):
                                    fsu.main()

                            # After this call, hooks_fired_total becomes 10
                            # 10 % 10 == 0, so cleanup should be called
                            mock_cleanup.assert_called_once()

    def test_main_session_end_preserves_state_file(self):
        """main() should preserve state file when session_end phase is reached (Issue #1665).

        Previously, state file was deleted at session_end, but this caused flow-verifier.py
        to not be able to read the session_end phase. Now we keep the state file and rely
        on age-based cleanup (CLEANUP_MAX_AGE_HOURS) to remove old files.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(fsu, "FLOW_LOG_DIR", Path(tmpdir)):
                session_id = "test-session-end"
                mock_ctx = mock.MagicMock()
                mock_ctx.get_session_id.return_value = session_id
                with mock.patch.object(fsu, "create_hook_context", return_value=mock_ctx):
                    with mock.patch.object(fsu, "get_current_workflow", return_value="main"):
                        state_file = Path(tmpdir) / f"state-{session_id}.json"

                        # Create initial state in cleanup phase (one before session_end)
                        initial_state = {
                            "session_id": session_id,
                            "active_workflow": "main",
                            "workflows": {
                                "main": {
                                    "current_phase": "cleanup",
                                    "branch": "",
                                    "phases": {
                                        "cleanup": {"status": "in_progress", "iterations": 1}
                                    },
                                }
                            },
                            "global": {"hooks_fired_total": 5},
                        }
                        fsu.save_state(initial_state, session_id)
                        assert state_file.exists()

                        # Simulate a Stop hook (triggers session_end phase)
                        hook_input = {"hook_type": "Stop"}
                        with mock.patch("sys.stdin.read", return_value=json.dumps(hook_input)):
                            with mock.patch("builtins.print"):
                                # cleanup_old_session_files は I/O を伴うため、テストではモックして副作用を防ぐ
                                with mock.patch.object(fsu, "cleanup_old_session_files"):
                                    fsu.main()

                        # Issue #1665: State file should be preserved (not deleted)
                        # This allows flow-verifier.py to read session_end phase
                        assert state_file.exists()

                        # Verify session_end phase was recorded
                        saved_state = fsu.load_state(session_id)
                        assert saved_state["workflows"]["main"]["current_phase"] == "session_end"
                        assert "session_end" in saved_state["workflows"]["main"]["phases"]

    def test_main_stop_hook_with_stop_hook_active_records_session_end(self):
        """main() should record session_end even when stop_hook_active is True (Issue #1680).

        Previously, stop_hook_active caused early return, skipping session_end recording.
        Now Stop hooks proceed normally while recursive tool calls are skipped.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(fsu, "FLOW_LOG_DIR", Path(tmpdir)):
                session_id = "test-stop-hook-active"
                mock_ctx = mock.MagicMock()
                mock_ctx.get_session_id.return_value = session_id
                with mock.patch.object(fsu, "create_hook_context", return_value=mock_ctx):
                    with mock.patch.object(fsu, "get_current_workflow", return_value="main"):
                        # Create initial state in cleanup phase
                        initial_state = {
                            "session_id": session_id,
                            "active_workflow": "main",
                            "workflows": {
                                "main": {
                                    "current_phase": "cleanup",
                                    "branch": "",
                                    "phases": {
                                        "cleanup": {"status": "in_progress", "iterations": 1}
                                    },
                                }
                            },
                            "global": {"hooks_fired_total": 5},
                        }
                        fsu.save_state(initial_state, session_id)

                        # Issue #1680: Stop hook WITH stop_hook_active should still work
                        hook_input = {"hook_type": "Stop", "stop_hook_active": True}
                        with mock.patch("sys.stdin.read", return_value=json.dumps(hook_input)):
                            with mock.patch("builtins.print"):
                                with mock.patch.object(fsu, "cleanup_old_session_files"):
                                    fsu.main()

                        # Verify session_end phase was recorded
                        saved_state = fsu.load_state(session_id)
                        assert saved_state["workflows"]["main"]["current_phase"] == "session_end"
                        assert "session_end" in saved_state["workflows"]["main"]["phases"]

    def test_main_recursive_tool_call_during_stop_is_skipped(self):
        """main() should skip recursive tool calls during Stop to prevent infinite loop (Issue #1680).

        When a Stop hook runs a tool (e.g., Bash), PreToolUse/PostToolUse hooks are called
        with stop_hook_active=True. These should be skipped.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(fsu, "FLOW_LOG_DIR", Path(tmpdir)):
                session_id = "test-recursive-call"
                mock_ctx = mock.MagicMock()
                mock_ctx.get_session_id.return_value = session_id
                with mock.patch.object(fsu, "create_hook_context", return_value=mock_ctx):
                    with mock.patch.object(fsu, "get_current_workflow", return_value="main"):
                        # Create initial state
                        initial_state = {
                            "session_id": session_id,
                            "active_workflow": "main",
                            "workflows": {
                                "main": {
                                    "current_phase": "cleanup",
                                    "branch": "",
                                    "phases": {
                                        "cleanup": {"status": "in_progress", "iterations": 1}
                                    },
                                }
                            },
                            "global": {"hooks_fired_total": 5},
                        }
                        fsu.save_state(initial_state, session_id)

                        # Recursive PreToolUse call during Stop (not hook_type: Stop)
                        # This simulates a Stop hook calling a Bash command
                        hook_input = {
                            "tool_name": "Bash",
                            "tool_input": {"command": "echo hello"},
                            "stop_hook_active": True,
                        }
                        captured_output = []
                        with mock.patch("sys.stdin.read", return_value=json.dumps(hook_input)):
                            with mock.patch(
                                "builtins.print",
                                side_effect=lambda x: captured_output.append(x),
                            ):
                                fsu.main()

                        # Should approve without processing
                        assert len(captured_output) == 1
                        result = json.loads(captured_output[0])
                        assert result["decision"] == "approve"

                        # State should be unchanged (still in cleanup, not session_end)
                        saved_state = fsu.load_state(session_id)
                        assert saved_state["workflows"]["main"]["current_phase"] == "cleanup"


class TestExternalPRDetection:
    """Tests for external PR detection (Issue #1631)."""

    def test_check_external_pr_exists_returns_pr_info(self):
        """Should return PR info when PR exists for branch."""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(
                returncode=0,
                stdout='[{"number": 123, "url": "https://github.com/owner/repo/pull/123"}]',
            )
            result = fsu.check_external_pr_exists("feat/issue-123")
            assert result is not None
            assert result["number"] == 123
            assert "github.com" in result["url"]

    def test_check_external_pr_exists_returns_none_when_no_pr(self):
        """Should return None when no PR exists for branch."""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0, stdout="[]")
            result = fsu.check_external_pr_exists("feat/no-pr")
            assert result is None

    def test_check_external_pr_exists_handles_network_error(self):
        """Should return None when network error occurs."""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.side_effect = Exception("Network error")
            result = fsu.check_external_pr_exists("feat/issue-123")
            assert result is None

    def test_check_external_pr_exists_handles_timeout(self):
        """Should return None when command times out."""
        import subprocess

        with mock.patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=10)
            result = fsu.check_external_pr_exists("feat/issue-123")
            assert result is None

    def test_get_current_branch_returns_branch_name(self):
        """Should return current branch name."""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0, stdout="feat/issue-456\n")
            result = fsu.get_current_branch()
            assert result == "feat/issue-456"

    def test_get_current_branch_returns_none_on_error(self):
        """Should return None when git command fails."""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=1, stdout="")
            result = fsu.get_current_branch()
            assert result is None

    def test_update_workflow_state_detects_external_pr(self):
        """Should detect external PR and auto-complete pr_create phase."""
        state = {
            "workflows": {
                "issue-123": {
                    "current_phase": "implementation",
                    "phases": {"implementation": {"status": "in_progress", "iterations": 1}},
                }
            }
        }

        with mock.patch.object(fsu, "get_current_branch", return_value="feat/issue-123"):
            with mock.patch.object(
                fsu,
                "check_external_pr_exists",
                return_value={"number": 999, "url": "https://github.com/test/pr/999"},
            ):
                external_pr, auto_detected_merge = fsu.update_workflow_state(
                    state, "issue-123", "ci_review", None
                )

        assert external_pr is not None
        assert external_pr["number"] == 999
        assert auto_detected_merge is None  # Not entering cleanup
        # pr_create should be auto-completed
        assert "pr_create" in state["workflows"]["issue-123"]["phases"]
        pr_phase = state["workflows"]["issue-123"]["phases"]["pr_create"]
        assert pr_phase["status"] == "completed"
        assert pr_phase["source"] == "external"
        assert pr_phase["pr_number"] == 999

    def test_update_workflow_state_skips_check_when_pr_create_exists(self):
        """Should not check external PR when pr_create phase already exists."""
        state = {
            "workflows": {
                "issue-123": {
                    "current_phase": "pr_create",
                    "phases": {
                        "pr_create": {"status": "completed", "iterations": 1},
                    },
                }
            }
        }

        with mock.patch.object(fsu, "check_external_pr_exists") as mock_check:
            fsu.update_workflow_state(state, "issue-123", "ci_review", None)
            # Should not call check_external_pr_exists when pr_create already exists
            mock_check.assert_not_called()

    def test_update_workflow_state_skips_for_non_requiring_phases(self):
        """Should not check external PR for phases that don't require pr_create."""
        state = {
            "workflows": {
                "issue-123": {
                    "current_phase": "implementation",
                    "phases": {},
                }
            }
        }

        with mock.patch.object(fsu, "check_external_pr_exists") as mock_check:
            # Transitioning to pre_commit_check (not in PHASES_REQUIRING_PR)
            fsu.update_workflow_state(state, "issue-123", "pre_commit_check", None)
            mock_check.assert_not_called()

    def test_update_workflow_state_handles_no_branch(self):
        """Should handle case when branch cannot be determined."""
        state = {
            "workflows": {
                "issue-123": {
                    "current_phase": "implementation",
                    "phases": {},
                }
            }
        }

        with mock.patch.object(fsu, "get_current_branch", return_value=None):
            external_pr, auto_detected_merge = fsu.update_workflow_state(
                state, "issue-123", "ci_review", None
            )
            # Should not fail, just skip external PR detection
            assert external_pr is None
            assert auto_detected_merge is None

    def test_update_workflow_state_handles_no_external_pr(self):
        """Should handle case when no external PR exists."""
        state = {
            "workflows": {
                "issue-123": {
                    "current_phase": "implementation",
                    "phases": {},
                }
            }
        }

        with mock.patch.object(fsu, "get_current_branch", return_value="feat/issue-123"):
            with mock.patch.object(fsu, "check_external_pr_exists", return_value=None):
                external_pr, auto_detected_merge = fsu.update_workflow_state(
                    state, "issue-123", "ci_review", None
                )
                assert external_pr is None
                assert auto_detected_merge is None
                # pr_create should NOT be in phases
                assert "pr_create" not in state["workflows"]["issue-123"]["phases"]

    def test_phases_requiring_pr_contains_expected_phases(self):
        """Verify PHASES_REQUIRING_PR contains the expected phases."""
        assert "ci_review" in fsu.PHASES_REQUIRING_PR
        assert "merge" in fsu.PHASES_REQUIRING_PR
        # Should not contain other phases
        assert "implementation" not in fsu.PHASES_REQUIRING_PR
        assert "pr_create" not in fsu.PHASES_REQUIRING_PR


class TestMergePhaseAutoDetection:
    """Tests for merge phase auto-detection feature (Issue #2567)."""

    def test_check_merged_pr_for_workflow_finds_merged_pr(self):
        """Should find merged PR when branch pattern matches."""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(
                returncode=0,
                stdout='[{"number": 123, "url": "https://github.com/test/pr/123", "state": "MERGED", "mergedAt": "2026-01-02T10:00:00Z"}]',
            )
            result = fsu.check_merged_pr_for_workflow("issue-456")

        assert result is not None
        assert result["number"] == 123
        assert result["state"] == "MERGED"
        assert result["merged_at"] == "2026-01-02T10:00:00Z"
        # Issue #2577: Verify --search is used instead of --head for partial matching
        call_args = mock_run.call_args[0][0]
        assert "--search" in call_args
        # Should use head: prefix for partial branch name matching
        search_idx = call_args.index("--search")
        assert call_args[search_idx + 1].startswith("head:")

    def test_check_merged_pr_for_workflow_returns_none_for_non_issue_workflow(self):
        """Should return None for workflows not matching issue-XXX pattern."""
        result = fsu.check_merged_pr_for_workflow("main")
        assert result is None

        result = fsu.check_merged_pr_for_workflow("feature-branch")
        assert result is None

    def test_check_merged_pr_for_workflow_returns_none_when_no_merged_pr(self):
        """Should return None when no merged PR exists."""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0, stdout="[]")
            result = fsu.check_merged_pr_for_workflow("issue-789")

        assert result is None

    def test_check_merged_pr_for_workflow_handles_subprocess_error(self):
        """Should return None when subprocess fails."""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.side_effect = Exception("Network error")
            result = fsu.check_merged_pr_for_workflow("issue-999")

        assert result is None

    def test_update_workflow_state_auto_detects_merge_on_cleanup(self):
        """Should auto-complete merge phase when entering cleanup with merged PR."""
        state = {
            "workflows": {
                "issue-123": {
                    "current_phase": "session_start",
                    "phases": {"session_start": {"status": "in_progress", "iterations": 1}},
                }
            }
        }

        with mock.patch.object(
            fsu,
            "check_merged_pr_for_workflow",
            return_value={
                "number": 456,
                "url": "https://github.com/test/pr/456",
                "state": "MERGED",
                "merged_at": "2026-01-02T12:00:00Z",
            },
        ):
            external_pr, auto_detected_merge = fsu.update_workflow_state(
                state, "issue-123", "cleanup", None
            )

        assert auto_detected_merge is not None
        assert auto_detected_merge["number"] == 456
        # merge phase should be auto-completed
        assert "merge" in state["workflows"]["issue-123"]["phases"]
        merge_phase = state["workflows"]["issue-123"]["phases"]["merge"]
        assert merge_phase["status"] == "completed"
        assert merge_phase["source"] == "auto_detected"
        assert merge_phase["pr_number"] == 456
        assert merge_phase["merged_at"] == "2026-01-02T12:00:00Z"

    def test_update_workflow_state_skips_auto_detect_when_merge_exists(self):
        """Should not auto-detect merge when merge phase already exists."""
        state = {
            "workflows": {
                "issue-123": {
                    "current_phase": "merge",
                    "phases": {
                        "merge": {"status": "in_progress", "iterations": 1},
                    },
                }
            }
        }

        with mock.patch.object(fsu, "check_merged_pr_for_workflow") as mock_check:
            external_pr, auto_detected_merge = fsu.update_workflow_state(
                state, "issue-123", "cleanup", None
            )
            # Should not call check_merged_pr_for_workflow when merge already exists
            mock_check.assert_not_called()

    def test_update_workflow_state_skips_auto_detect_for_non_cleanup_phases(self):
        """Should not auto-detect merge for phases other than cleanup."""
        state = {
            "workflows": {
                "issue-123": {
                    "current_phase": "implementation",
                    "phases": {},
                }
            }
        }

        with mock.patch.object(fsu, "check_merged_pr_for_workflow") as mock_check:
            # Transitioning to ci_review (not cleanup)
            external_pr, auto_detected_merge = fsu.update_workflow_state(
                state, "issue-123", "ci_review", None
            )
            mock_check.assert_not_called()

    def test_update_workflow_state_handles_no_merged_pr_found(self):
        """Should handle case when no merged PR is found."""
        state = {
            "workflows": {
                "issue-123": {
                    "current_phase": "session_start",
                    "phases": {},
                }
            }
        }

        with mock.patch.object(fsu, "check_merged_pr_for_workflow", return_value=None):
            external_pr, auto_detected_merge = fsu.update_workflow_state(
                state, "issue-123", "cleanup", None
            )

        assert auto_detected_merge is None
        # merge phase should NOT be in phases
        assert "merge" not in state["workflows"]["issue-123"]["phases"]


class TestDurationSeconds:
    """Tests for duration_seconds in phase transition events (Issue #1642)."""

    def test_duration_seconds_included_in_phase_transition_event(self):
        """Issue #1642: phase_transition events should include duration_seconds."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_id = "test-duration"
            # Issue #1831: Events are now stored per-session
            events_file = Path(tmpdir) / f"events-{session_id}.jsonl"
            with mock.patch.object(fsu, "FLOW_LOG_DIR", Path(tmpdir)):
                mock_ctx = mock.MagicMock()
                mock_ctx.get_session_id.return_value = session_id
                with mock.patch.object(fsu, "create_hook_context", return_value=mock_ctx):
                    with mock.patch.object(fsu, "get_current_workflow", return_value="main"):
                        # Create initial state with phase_start_time 5 seconds ago
                        old_time = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
                        initial_state = {
                            "session_id": session_id,
                            "active_workflow": "main",
                            "workflows": {
                                "main": {
                                    "current_phase": "session_start",
                                    "branch": "",
                                    "phases": {
                                        "session_start": {
                                            "status": "in_progress",
                                            "iterations": 1,
                                        }
                                    },
                                    "phase_start_time": old_time,
                                }
                            },
                            "global": {"hooks_fired_total": 1},
                        }
                        fsu.save_state(initial_state, session_id)

                        # Simulate Read tool (triggers pre_check phase)
                        hook_input = {"tool_name": "Read", "hook_type": "PreToolUse"}
                        with mock.patch("sys.stdin.read", return_value=json.dumps(hook_input)):
                            with mock.patch("builtins.print"):
                                fsu.main()

                        # Check events file for duration_seconds
                        events = events_file.read_text().strip().split("\n")
                        phase_transition_events = [
                            json.loads(e)
                            for e in events
                            if json.loads(e).get("event") == "phase_transition"
                        ]
                        assert len(phase_transition_events) >= 1
                        event = phase_transition_events[-1]
                        assert "duration_seconds" in event
                        # Duration should be approximately 5 seconds (with some tolerance)
                        assert 4 < event["duration_seconds"] < 10

    def test_duration_seconds_not_included_when_no_phase_start_time(self):
        """Issue #1642: If phase_start_time is missing, duration_seconds should not be included."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_id = "test-no-start-time"
            # Issue #1831: Events are now stored per-session
            events_file = Path(tmpdir) / f"events-{session_id}.jsonl"
            with mock.patch.object(fsu, "FLOW_LOG_DIR", Path(tmpdir)):
                mock_ctx = mock.MagicMock()
                mock_ctx.get_session_id.return_value = session_id
                with mock.patch.object(fsu, "create_hook_context", return_value=mock_ctx):
                    with mock.patch.object(fsu, "get_current_workflow", return_value="main"):
                        # Create initial state WITHOUT phase_start_time
                        initial_state = {
                            "session_id": session_id,
                            "active_workflow": "main",
                            "workflows": {
                                "main": {
                                    "current_phase": "session_start",
                                    "branch": "",
                                    "phases": {
                                        "session_start": {
                                            "status": "in_progress",
                                            "iterations": 1,
                                        }
                                    },
                                    # No phase_start_time!
                                }
                            },
                            "global": {"hooks_fired_total": 1},
                        }
                        fsu.save_state(initial_state, session_id)

                        # Simulate Read tool (triggers pre_check phase)
                        hook_input = {"tool_name": "Read", "hook_type": "PreToolUse"}
                        with mock.patch("sys.stdin.read", return_value=json.dumps(hook_input)):
                            with mock.patch("builtins.print"):
                                fsu.main()

                        # Check events file - duration_seconds should NOT be present
                        events = events_file.read_text().strip().split("\n")
                        phase_transition_events = [
                            json.loads(e)
                            for e in events
                            if json.loads(e).get("event") == "phase_transition"
                        ]
                        assert len(phase_transition_events) >= 1
                        event = phase_transition_events[-1]
                        assert "duration_seconds" not in event

    def test_new_workflow_initialization_includes_phase_start_time(self):
        """Issue #1642: New workflow initialization should include phase_start_time."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(fsu, "FLOW_LOG_DIR", Path(tmpdir)):
                session_id = "test-new-wf"
                mock_ctx = mock.MagicMock()
                mock_ctx.get_session_id.return_value = session_id
                with mock.patch.object(fsu, "create_hook_context", return_value=mock_ctx):
                    with mock.patch.object(fsu, "get_current_workflow", return_value="issue-123"):
                        # No initial state - simulate new workflow
                        hook_input = {"tool_name": "Read", "hook_type": "PreToolUse"}
                        with mock.patch("sys.stdin.read", return_value=json.dumps(hook_input)):
                            with mock.patch("builtins.print"):
                                fsu.main()

                        # Load state and check phase_start_time was set
                        state = fsu.load_state(session_id)
                        assert "issue-123" in state["workflows"]
                        assert "phase_start_time" in state["workflows"]["issue-123"]


class TestViolationActions:
    """Tests for is_valid_phase_transition violation detection (Issue #1690, #1714).

    Note: These tests verify is_valid_phase_transition() detects violations.
    For integration tests verifying main() blocks/warns, see TestViolationActionsIntegration.
    """

    def test_is_valid_detects_merge_to_session_end_violation(self):
        """Issue #1690, #1714: is_valid_phase_transition detects merge -> session_end."""
        from flow_constants import CRITICAL_VIOLATIONS

        # Verify the critical violation is defined
        assert ("merge", "session_end") in CRITICAL_VIOLATIONS

        # Test is_valid_phase_transition returns violation
        is_valid, violation = fsu.is_valid_phase_transition("merge", "session_end")
        assert not is_valid
        assert violation is not None
        assert "cleanup" in violation

    def test_merge_to_ci_review_is_allowed_loopback(self):
        """Issue #1739: merge -> ci_review is allowed for rebase workflows."""
        from flow_constants import ALLOWED_LOOPBACKS, CRITICAL_VIOLATIONS

        # merge -> ci_review should be in ALLOWED_LOOPBACKS
        assert ("merge", "ci_review") in ALLOWED_LOOPBACKS
        # And therefore NOT in CRITICAL_VIOLATIONS
        assert ("merge", "ci_review") not in CRITICAL_VIOLATIONS

        # is_valid_phase_transition should allow this transition
        is_valid, violation = fsu.is_valid_phase_transition("merge", "ci_review")
        assert is_valid
        # Violation message is still returned for logging purposes
        assert violation is not None
        assert "cleanup" in violation

    def test_is_valid_detects_merge_to_pr_create_violation(self):
        """Issue #1690, #1714: is_valid_phase_transition detects merge -> pr_create."""
        from flow_constants import CRITICAL_VIOLATIONS

        assert ("merge", "pr_create") in CRITICAL_VIOLATIONS

        is_valid, violation = fsu.is_valid_phase_transition("merge", "pr_create")
        assert not is_valid
        assert violation is not None

    def test_is_valid_detects_merge_to_pre_check_violation(self):
        """Issue #1690, #1714: is_valid_phase_transition detects merge -> pre_check."""
        from flow_constants import CRITICAL_VIOLATIONS

        assert ("merge", "pre_check") in CRITICAL_VIOLATIONS

        is_valid, violation = fsu.is_valid_phase_transition("merge", "pre_check")
        assert not is_valid
        assert violation is not None

    def test_is_valid_detects_non_critical_violation(self):
        """Issue #1690, #1714: is_valid_phase_transition detects non-critical violations."""
        # implementation -> pre_check is a non-critical violation
        # (Required: implementation -> pre_commit_check)
        is_valid, violation = fsu.is_valid_phase_transition("implementation", "pre_check")
        assert not is_valid
        assert violation is not None
        assert "pre_commit_check" in violation

    def test_is_valid_allows_valid_transition(self):
        """Issue #1690, #1714: is_valid_phase_transition allows valid transitions."""
        # merge -> cleanup is valid
        is_valid, violation = fsu.is_valid_phase_transition("merge", "cleanup")
        assert is_valid
        assert violation is None

    def test_is_valid_allows_session_start_from_any_phase(self):
        """Issue #1874: session_start transition is always valid from any phase.

        This prevents noise from session continuations where the previous session
        ended in a different phase (e.g., implementation -> session_start).
        """
        # Test from various phases that would normally require different next phases
        phases_to_test = [
            "implementation",  # Would normally require pre_commit_check
            "merge",  # Would normally require cleanup
            "session_start",  # Same phase (should also work)
            "pre_check",
            "ci_review",
            "cleanup",
        ]

        for phase in phases_to_test:
            is_valid, violation = fsu.is_valid_phase_transition(phase, "session_start")
            assert is_valid, f"Transition from {phase} to session_start should be valid"
            assert violation is None, f"No violation expected for {phase} to session_start"

    def test_skip_env_variable_bypasses_check(self):
        """Issue #1690: SKIP_FLOW_VIOLATION_CHECK=1 should bypass violation check."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(fsu, "FLOW_LOG_DIR", Path(tmpdir)):
                session_id = "test-skip-env"
                mock_ctx = mock.MagicMock()
                mock_ctx.get_session_id.return_value = session_id
                with mock.patch.object(fsu, "create_hook_context", return_value=mock_ctx):
                    with mock.patch.object(fsu, "get_current_workflow", return_value="main"):
                        # Set up state in merge phase
                        initial_state = {
                            "session_id": session_id,
                            "active_workflow": "main",
                            "workflows": {
                                "main": {
                                    "current_phase": "merge",
                                    "branch": "",
                                    "phases": {"merge": {"status": "in_progress", "iterations": 1}},
                                    "phase_start_time": datetime.now(UTC).isoformat(),
                                }
                            },
                            "global": {"hooks_fired_total": 1},
                        }
                        fsu.save_state(initial_state, session_id)

                        # Simulate Stop hook (triggers session_end)
                        hook_input = {"hook_type": "Stop"}
                        captured_output = []

                        def capture_print(msg):
                            captured_output.append(msg)

                        # With SKIP_FLOW_VIOLATION_CHECK=1
                        with mock.patch.dict(os.environ, {"SKIP_FLOW_VIOLATION_CHECK": "1"}):
                            with mock.patch("sys.stdin.read", return_value=json.dumps(hook_input)):
                                with mock.patch("builtins.print", side_effect=capture_print):
                                    fsu.main()

                        # Should approve without blocking
                        assert len(captured_output) == 1
                        result = json.loads(captured_output[0])
                        assert result.get("decision") == "approve"
                        # No error message
                        assert "error" not in result

                        # Issue #1690: Verify bypass mode warning message
                        system_message = result.get("systemMessage", "")
                        assert "BYPASS MODE" in system_message
                        assert "Critical violation bypassed" in system_message
                        assert "Normally would be BLOCKED" in system_message

                        # Issue #1690: Verify state was updated to session_end
                        updated_state = fsu.load_state(session_id)
                        assert updated_state["workflows"]["main"]["current_phase"] == "session_end"

    def test_critical_violations_programmatically_generated(self):
        """Issue #1716, #1728, #1739: CRITICAL_VIOLATIONS should be programmatically generated."""
        from flow_constants import (
            ALL_PHASES,
            ALLOWED_LOOPBACKS,
            BLOCKING_PHASE_TRANSITIONS,
            CRITICAL_VIOLATIONS,
        )

        # Issue #1728: Verify merge is in BLOCKING_PHASE_TRANSITIONS
        assert "merge" in BLOCKING_PHASE_TRANSITIONS
        assert BLOCKING_PHASE_TRANSITIONS["merge"] == "cleanup"

        # Verify all merge -> X (X != cleanup, X != merge, X not in ALLOWED_LOOPBACKS)
        # are in CRITICAL_VIOLATIONS
        # Issue #1739: ALLOWED_LOOPBACKS are excluded from violations
        allowed_from_merge = len([lb for lb in ALLOWED_LOOPBACKS if lb[0] == "merge"])
        expected_violations = (
            len(ALL_PHASES) - 2 - allowed_from_merge
        )  # Exclude cleanup, merge, and loopbacks
        merge_violations = [k for k in CRITICAL_VIOLATIONS if k[0] == "merge"]
        assert len(merge_violations) == expected_violations

        # Verify specific new patterns are blocked
        # Issue #2153: ("merge", "implementation") is now in ALLOWED_LOOPBACKS
        new_patterns = [
            ("merge", "worktree_create"),
            ("merge", "local_ai_review"),
            ("merge", "issue_work"),
        ]
        for pattern in new_patterns:
            assert pattern in CRITICAL_VIOLATIONS, f"{pattern} should be in CRITICAL_VIOLATIONS"

        # Verify allowed loopbacks are NOT in CRITICAL_VIOLATIONS
        for loopback in ALLOWED_LOOPBACKS:
            assert loopback not in CRITICAL_VIOLATIONS, (
                f"{loopback} should NOT be in CRITICAL_VIOLATIONS"
            )

    def test_merge_to_implementation_allowed_as_loopback(self):
        """Issue #2153: merge -> implementation is now allowed as a loopback."""
        from flow_constants import ALLOWED_LOOPBACKS, CRITICAL_VIOLATIONS

        # merge -> implementation is now in ALLOWED_LOOPBACKS
        assert ("merge", "implementation") in ALLOWED_LOOPBACKS
        # Therefore it should NOT be in CRITICAL_VIOLATIONS
        assert ("merge", "implementation") not in CRITICAL_VIOLATIONS

        # Issue #1739: ALLOWED_LOOPBACKS are valid but still return a violation
        # message for logging purposes
        is_valid, violation = fsu.is_valid_phase_transition("merge", "implementation")
        assert is_valid  # Valid because it's in ALLOWED_LOOPBACKS
        # Violation message is returned for logging purposes only
        assert violation is not None
        assert "cleanup" in violation

    def test_is_valid_handles_optional_phase_with_violation(self):
        """Issue #1714, #1716: is_valid_phase_transition handles optional phases correctly.

        Note: worktree_create is in OPTIONAL_PHASES, so is_valid_phase_transition
        returns (True, violation_reason). The actual blocking happens in main()
        when checking CRITICAL_VIOLATIONS. See TestViolationActionsIntegration for
        integration tests that verify main() blocking behavior.
        """
        from flow_constants import CRITICAL_VIOLATIONS, OPTIONAL_PHASES

        # Verify it's in CRITICAL_VIOLATIONS
        assert ("merge", "worktree_create") in CRITICAL_VIOLATIONS

        # worktree_create is optional, so is_valid returns True with violation reason
        assert "worktree_create" in OPTIONAL_PHASES
        is_valid, violation = fsu.is_valid_phase_transition("merge", "worktree_create")
        # Optional phases return True but with a violation reason
        assert is_valid  # True because it's optional
        assert violation is not None  # But still has violation reason
        assert "cleanup" in violation  # Must mention required phase


class TestViolationActionsIntegration:
    """Integration tests for violation blocking in main() function (Issue #1714).

    These tests verify that main() actually returns "block" for critical violations
    and that the state is NOT updated to the invalid phase.
    """

    def test_main_blocks_critical_violation_merge_to_session_end(self):
        """Issue #1714: main() should block merge -> session_end transition."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(fsu, "FLOW_LOG_DIR", Path(tmpdir)):
                session_id = "test-block-merge-session-end"
                mock_ctx = mock.MagicMock()
                mock_ctx.get_session_id.return_value = session_id
                with mock.patch.object(fsu, "create_hook_context", return_value=mock_ctx):
                    with mock.patch.object(fsu, "get_current_workflow", return_value="main"):
                        # Set up state in merge phase
                        initial_state = {
                            "session_id": session_id,
                            "active_workflow": "main",
                            "workflows": {
                                "main": {
                                    "current_phase": "merge",
                                    "branch": "",
                                    "phases": {"merge": {"status": "in_progress", "iterations": 1}},
                                    "phase_start_time": datetime.now(UTC).isoformat(),
                                }
                            },
                            "global": {"hooks_fired_total": 1},
                        }
                        fsu.save_state(initial_state, session_id)

                        # Simulate Stop hook (triggers session_end)
                        hook_input = {"hook_type": "Stop"}
                        captured_output = []

                        def capture_print(*args, **kwargs):
                            # Only capture stdout (not stderr)
                            if kwargs.get("file") is None:
                                captured_output.append(args[0] if args else "")

                        with mock.patch("sys.stdin.read", return_value=json.dumps(hook_input)):
                            with mock.patch("builtins.print", side_effect=capture_print):
                                fsu.main()

                        # Should block the transition
                        assert len(captured_output) == 1
                        result = json.loads(captured_output[0])
                        assert result.get("decision") == "block"
                        # Block reason is in "reason" key, not "error"
                        assert "cleanup" in result.get("reason", "").lower()

                        # State should NOT be updated to session_end
                        saved_state = fsu.load_state(session_id)
                        assert saved_state["workflows"]["main"]["current_phase"] == "merge"
                        assert "session_end" not in saved_state["workflows"]["main"]["phases"]

    def test_main_allows_merge_to_implementation_loopback(self):
        """Issue #2153: main() should allow merge -> implementation as loopback."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(fsu, "FLOW_LOG_DIR", Path(tmpdir)):
                session_id = "test-allow-merge-impl"
                mock_ctx = mock.MagicMock()
                mock_ctx.get_session_id.return_value = session_id
                with mock.patch.object(fsu, "create_hook_context", return_value=mock_ctx):
                    with mock.patch.object(fsu, "get_current_workflow", return_value="main"):
                        # Set up state in merge phase
                        initial_state = {
                            "session_id": session_id,
                            "active_workflow": "main",
                            "workflows": {
                                "main": {
                                    "current_phase": "merge",
                                    "branch": "",
                                    "phases": {"merge": {"status": "in_progress", "iterations": 1}},
                                    "phase_start_time": datetime.now(UTC).isoformat(),
                                }
                            },
                            "global": {"hooks_fired_total": 1},
                        }
                        fsu.save_state(initial_state, session_id)

                        # Simulate Edit tool (triggers implementation)
                        hook_input = {"tool_name": "Edit", "hook_type": "PreToolUse"}
                        captured_output = []

                        def capture_print(*args, **kwargs):
                            # Only capture stdout (not stderr)
                            if kwargs.get("file") is None:
                                captured_output.append(args[0] if args else "")

                        with mock.patch("sys.stdin.read", return_value=json.dumps(hook_input)):
                            with mock.patch("builtins.print", side_effect=capture_print):
                                fsu.main()

                        # Should allow the transition (loopback)
                        assert len(captured_output) == 1
                        result = json.loads(captured_output[0])
                        assert result.get("decision") == "approve"

                        # State should be updated to implementation
                        saved_state = fsu.load_state(session_id)
                        assert saved_state["workflows"]["main"]["current_phase"] == "implementation"

    def test_main_allows_valid_transition_merge_to_cleanup(self):
        """Issue #1714: main() should allow merge -> cleanup transition."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(fsu, "FLOW_LOG_DIR", Path(tmpdir)):
                session_id = "test-allow-merge-cleanup"
                mock_ctx = mock.MagicMock()
                mock_ctx.get_session_id.return_value = session_id
                with mock.patch.object(fsu, "create_hook_context", return_value=mock_ctx):
                    with mock.patch.object(fsu, "get_current_workflow", return_value="issue-123"):
                        # Set up state in merge phase
                        initial_state = {
                            "session_id": session_id,
                            "active_workflow": "issue-123",
                            "workflows": {
                                "issue-123": {
                                    "current_phase": "merge",
                                    "branch": "",
                                    "phases": {"merge": {"status": "in_progress", "iterations": 1}},
                                    "phase_start_time": datetime.now(UTC).isoformat(),
                                }
                            },
                            "global": {"hooks_fired_total": 1},
                        }
                        fsu.save_state(initial_state, session_id)

                        # Simulate git worktree remove (triggers cleanup)
                        hook_input = {
                            "tool_name": "Bash",
                            "tool_input": {"command": "git worktree remove .worktrees/issue-123"},
                            "hook_type": "PreToolUse",
                        }
                        captured_output = []

                        def capture_print(*args, **kwargs):
                            # Only capture stdout (not stderr)
                            if kwargs.get("file") is None:
                                captured_output.append(args[0] if args else "")

                        with mock.patch("sys.stdin.read", return_value=json.dumps(hook_input)):
                            with mock.patch("builtins.print", side_effect=capture_print):
                                fsu.main()

                        # Should approve the transition
                        assert len(captured_output) == 1
                        result = json.loads(captured_output[0])
                        assert result.get("decision") == "approve"

                        # State should be updated to cleanup
                        saved_state = fsu.load_state(session_id)
                        assert saved_state["workflows"]["issue-123"]["current_phase"] == "cleanup"

    def test_main_warns_for_non_critical_violation(self):
        """Issue #1714: main() should warn (not block) for non-critical violations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(fsu, "FLOW_LOG_DIR", Path(tmpdir)):
                session_id = "test-warn-non-critical"
                mock_ctx = mock.MagicMock()
                mock_ctx.get_session_id.return_value = session_id
                with mock.patch.object(fsu, "create_hook_context", return_value=mock_ctx):
                    with mock.patch.object(fsu, "get_current_workflow", return_value="main"):
                        # Set up state in implementation phase
                        initial_state = {
                            "session_id": session_id,
                            "active_workflow": "main",
                            "workflows": {
                                "main": {
                                    "current_phase": "implementation",
                                    "branch": "",
                                    "phases": {
                                        "implementation": {
                                            "status": "in_progress",
                                            "iterations": 1,
                                        }
                                    },
                                    "phase_start_time": datetime.now(UTC).isoformat(),
                                }
                            },
                            "global": {"hooks_fired_total": 1},
                        }
                        fsu.save_state(initial_state, session_id)

                        # Simulate gh pr create (triggers pr_create, skipping pre_commit_check)
                        hook_input = {
                            "tool_name": "Bash",
                            "tool_input": {"command": "gh pr create --title 'Test'"},
                            "hook_type": "PreToolUse",
                        }
                        captured_output = []

                        def capture_print(*args, **kwargs):
                            # Only capture stdout (not stderr)
                            if kwargs.get("file") is None:
                                captured_output.append(args[0] if args else "")

                        with mock.patch("sys.stdin.read", return_value=json.dumps(hook_input)):
                            with mock.patch("builtins.print", side_effect=capture_print):
                                fsu.main()

                        # Should approve with warning, not block
                        assert len(captured_output) == 1
                        result = json.loads(captured_output[0])
                        assert result.get("decision") == "approve"
                        # Should have warning in systemMessage (Issue #1967: enhanced format)
                        system_message = result.get("systemMessage", "")
                        assert "フロー逸脱を検出" in system_message
                        assert "pre_commit_check" in system_message

                        # State SHOULD be updated (non-critical violation is allowed)
                        saved_state = fsu.load_state(session_id)
                        assert saved_state["workflows"]["main"]["current_phase"] == "pr_create"


class TestMergeFailureHandling:
    """Tests for merge failure handling (Issue #1784).

    Issue #1784: When merge command is blocked/fails, the workflow should
    stay in ci_review phase instead of transitioning to merge phase.
    """

    def test_gh_pr_merge_command_does_not_trigger_merge_phase(self):
        """Issue #1784: gh pr merge command alone should NOT trigger merge phase.

        Previously, the enter_pattern "gh pr merge" would immediately transition
        to merge phase when the command was executed. Now we only transition
        on success (when "Merged" appears in output).
        """
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge --merge"},
            "hook_type": "PreToolUse",
        }
        state = {}
        new_phase, loop_reason, transition_reason, _ = fsu.detect_phase_transition(
            "ci_review", hook_input, state
        )
        # Should NOT transition to merge phase on command alone
        assert new_phase is None, "gh pr merge command should NOT trigger merge phase"
        assert loop_reason is None
        assert transition_reason is None

    def test_merge_success_output_triggers_merge_phase(self):
        """Issue #1784: Merge success output should trigger merge phase transition."""
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge --merge"},
            "tool_output": "✔ Merged pull request #123 into main",
            "hook_type": "PostToolUse",
        }
        state = {}
        new_phase, loop_reason, transition_reason, _ = fsu.detect_phase_transition(
            "ci_review", hook_input, state
        )
        # Should transition to merge phase on success
        assert new_phase == "merge", "Merge success output should trigger merge phase"
        assert "exit_pattern" in transition_reason

    def test_merge_blocked_output_stays_in_ci_review(self):
        """Issue #1784: When merge is blocked, should stay in ci_review phase."""
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge --merge"},
            "tool_output": "Pull request merge was blocked: issue acceptance criteria incomplete",
            "hook_type": "PostToolUse",
        }
        state = {}
        new_phase, loop_reason, transition_reason, _ = fsu.detect_phase_transition(
            "ci_review", hook_input, state
        )
        # Should NOT transition - stay in ci_review
        assert new_phase is None, "Blocked merge should NOT trigger phase transition"

    def test_merge_failure_stays_in_ci_review(self):
        """Issue #1784: When merge fails, should stay in ci_review phase."""
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge --merge"},
            "tool_output": "Error: Pull request #123 is not mergeable: CI checks are pending",
            "hook_type": "PostToolUse",
        }
        state = {}
        new_phase, loop_reason, transition_reason, _ = fsu.detect_phase_transition(
            "ci_review", hook_input, state
        )
        # Should NOT transition - stay in ci_review
        assert new_phase is None, "Failed merge should NOT trigger phase transition"

    def test_unmerged_paths_does_not_trigger_merge_phase(self):
        """Issue #1784: 'unmerged' in failure output should NOT trigger merge phase.

        Codex review feedback: The regex should be specific enough to avoid
        matching 'unmerged' in failure outputs.
        Note: This test avoids using 'conflict' keyword which triggers loop_trigger.
        """
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "git status"},
            "tool_output": "You have unmerged paths. Please resolve and retry.",
            "hook_type": "PostToolUse",
        }
        state = {}
        new_phase, loop_reason, transition_reason, _ = fsu.detect_phase_transition(
            "ci_review", hook_input, state
        )
        # Should NOT transition to merge - 'unmerged' should not match
        # Note: May transition to other phase (e.g., implementation) due to loop_trigger
        assert new_phase != "merge", "'unmerged' should NOT trigger merge phase transition"

    def test_unsuccessfully_merged_does_not_trigger_merge_phase(self):
        """Issue #1784: 'unsuccessfully merged' should NOT trigger merge phase.

        Copilot review feedback: 'successfully merged' pattern could match
        'unsuccessfully merged' without word boundary.
        """
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge"},
            "tool_output": "The PR was unsuccessfully merged due to policy violation",
            "hook_type": "PostToolUse",
        }
        state = {}
        new_phase, loop_reason, transition_reason, _ = fsu.detect_phase_transition(
            "ci_review", hook_input, state
        )
        # Should NOT transition to merge
        assert new_phase != "merge", "'unsuccessfully merged' should NOT trigger merge phase"

    def test_cannot_be_merged_into_does_not_trigger_merge_phase(self):
        """Issue #1784: 'cannot be merged into' should NOT trigger merge phase.

        Copilot review feedback: The pattern 'merged into' could match failure
        messages like 'cannot be merged into' or 'failed to be merged into'.
        """
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge"},
            "tool_output": "Error: Pull request cannot be merged into main due to status checks",
            "hook_type": "PostToolUse",
        }
        state = {}
        new_phase, loop_reason, transition_reason, _ = fsu.detect_phase_transition(
            "ci_review", hook_input, state
        )
        # Should NOT transition to merge - failure message should not match
        assert new_phase is None, "'cannot be merged into' should NOT trigger merge phase"

    def test_not_merging_does_not_trigger_merge_phase(self):
        """Issue #1784: 'not merging' failure message should NOT trigger merge phase."""
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge"},
            "tool_output": "Not merging - PR has pending status checks",
            "hook_type": "PostToolUse",
        }
        state = {}
        new_phase, loop_reason, transition_reason, _ = fsu.detect_phase_transition(
            "ci_review", hook_input, state
        )
        # Should NOT transition to merge - failure message should not match
        assert new_phase != "merge", "'not merging' should NOT trigger merge phase"

    def test_successfully_merged_triggers_transition(self):
        """Issue #1784: 'successfully merged' pattern should trigger transition."""
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge"},
            "tool_output": "Pull request successfully merged into main branch",
            "hook_type": "PostToolUse",
        }
        state = {}
        new_phase, loop_reason, transition_reason, _ = fsu.detect_phase_transition(
            "ci_review", hook_input, state
        )
        assert new_phase == "merge"

    def test_has_been_merged_pattern(self):
        """Issue #1784: 'has been merged' pattern should trigger transition."""
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge"},
            "tool_output": "Pull request #789 has been merged",
            "hook_type": "PostToolUse",
        }
        state = {}
        new_phase, loop_reason, transition_reason, _ = fsu.detect_phase_transition(
            "ci_review", hook_input, state
        )
        assert new_phase == "merge"

    def test_merge_phase_no_longer_has_enter_pattern(self):
        """Issue #1784: Verify merge phase no longer has enter_pattern."""
        merge_config = fsu.PHASE_TRIGGERS.get("merge", {})
        # enter_pattern should NOT be present
        assert "enter_pattern" not in merge_config, (
            "merge phase should NOT have enter_pattern (removed in Issue #1784)"
        )
        # exit_pattern should still be present
        assert "exit_pattern" in merge_config
        assert merge_config["exit_next"] == "cleanup"

    def test_ci_review_exit_pattern_updated(self):
        """Issue #1784: Verify ci_review exit_pattern matches on success, not command."""
        ci_review_config = fsu.PHASE_TRIGGERS.get("ci_review", {})
        exit_pattern = ci_review_config.get("exit_pattern", "")

        # Should NOT match bare "gh pr merge" command
        assert not re.search(exit_pattern, "gh pr merge --merge", re.IGNORECASE), (
            "exit_pattern should NOT match bare merge command"
        )

        # Should NOT match failure outputs (Codex/Copilot review feedback)
        assert not re.search(exit_pattern, "You have unmerged paths", re.IGNORECASE), (
            "exit_pattern should NOT match 'unmerged' in failure output"
        )
        assert not re.search(exit_pattern, "cannot be merged into main", re.IGNORECASE), (
            "exit_pattern should NOT match 'cannot be merged into'"
        )
        assert not re.search(exit_pattern, "not merged yet", re.IGNORECASE), (
            "exit_pattern should NOT match 'not merged yet'"
        )
        assert not re.search(exit_pattern, "not merging due to status checks", re.IGNORECASE), (
            "exit_pattern should NOT match 'not merging'"
        )
        assert not re.search(exit_pattern, "unsuccessfully merged", re.IGNORECASE), (
            "exit_pattern should NOT match 'unsuccessfully merged'"
        )
        assert not re.search(exit_pattern, "has not been merged", re.IGNORECASE), (
            "exit_pattern should NOT match 'has not been merged'"
        )

        # Should match success output patterns
        assert re.search(exit_pattern, "✔ Merged pull request #123", re.IGNORECASE), (
            "exit_pattern should match '✔ Merged pull request' success output"
        )
        assert re.search(exit_pattern, "Merged pull request #456 into main", re.IGNORECASE), (
            "exit_pattern should match 'Merged pull request' success output"
        )
        assert re.search(exit_pattern, "has been merged", re.IGNORECASE), (
            "exit_pattern should match 'has been merged' success output"
        )
        assert re.search(exit_pattern, "successfully merged into main", re.IGNORECASE), (
            "exit_pattern should match 'successfully merged' success output"
        )
