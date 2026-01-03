#!/usr/bin/env python3
"""Tests for recurring-problem-block.py hook.

Issue #1994: Updated to test new implementation that reads directly from
hook-execution.log instead of the redundant reflections/ directory.
"""

import json
import os
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from conftest import HOOKS_DIR, load_hook_module

HOOK_PATH = HOOKS_DIR / "recurring-problem-block.py"


def run_hook(input_data: dict, env: dict | None = None) -> dict:
    """Run the hook with given input and return the result."""
    test_env = os.environ.copy()
    if env:
        test_env.update(env)
    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
        env=test_env,
    )
    if result.stdout:
        return json.loads(result.stdout)
    return {"decision": "approve", "error": result.stderr}


class TestCheckIsMergeCommand:
    """Tests for check_is_merge_command function."""

    def setup_method(self):
        self.module = load_hook_module("recurring-problem-block")

    def test_basic_merge_command(self):
        """Should detect basic gh pr merge commands."""
        assert self.module.check_is_merge_command("gh pr merge 123")
        assert self.module.check_is_merge_command("gh pr merge 123 --squash")

    def test_chained_commands(self):
        """Should detect gh pr merge in chained commands."""
        assert self.module.check_is_merge_command("cd repo && gh pr merge 123")
        assert self.module.check_is_merge_command("git pull && gh pr merge 123 --squash")

    def test_env_var_prefixed_commands(self):
        """Should detect gh pr merge with environment variable prefixes."""
        assert self.module.check_is_merge_command('DISMISS_RECURRING="reason" gh pr merge 123')
        assert self.module.check_is_merge_command(
            "DISMISS_RECURRING=reason gh pr merge 123 --squash"
        )
        assert self.module.check_is_merge_command("VAR1=a VAR2=b gh pr merge 123")

    def test_non_merge_commands(self):
        """Should not detect non-merge commands."""
        assert not self.module.check_is_merge_command("gh pr view 123")
        assert not self.module.check_is_merge_command("gh pr create")
        assert not self.module.check_is_merge_command("git merge main")

    def test_empty_command(self):
        """Should return False for empty commands."""
        assert not self.module.check_is_merge_command("")


class TestAggregateRecurringProblems:
    """Tests for aggregate_recurring_problems function.

    Issue #1994: Updated to test session-specific file format.
    The function now reads from hook-execution-{session_id}.jsonl files
    and counts sessions where a WORKFLOW_PROBLEM_HOOK blocked 3+ times.
    """

    def setup_method(self):
        self.module = load_hook_module("recurring-problem-block")
        # Create a temporary directory for test data
        self.temp_dir = Path(tempfile.mkdtemp())

    def teardown_method(self):
        # Clean up temp directory
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_log_entry(self, hook: str, decision: str, session_id: str, timestamp=None):
        """Write a single log entry to the session-specific file."""
        if timestamp is None:
            timestamp = datetime.now(UTC)
        entry = {
            "timestamp": timestamp.isoformat(),
            "hook": hook,
            "decision": decision,
            "session_id": session_id,
        }
        # Issue #1994: Write to session-specific file
        log_file = self.temp_dir / f"hook-execution-{session_id}.jsonl"
        with open(log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def test_empty_directory(self):
        """Should return empty dict for empty log directory."""
        with patch.object(self.module, "EXECUTION_LOG_DIR", Path("/nonexistent/dir")):
            result = self.module.aggregate_recurring_problems()
            assert result == {}

    def test_count_sessions_with_repeated_blocks(self):
        """Should count sessions where hook blocked 3+ times.

        Issue #1994: Now reads hook-execution.log directly and counts sessions
        where a WORKFLOW_PROBLEM_HOOK blocked at least RECURRING_THRESHOLD times.
        Issue #2084: Changed from ci-wait-check to worktree-warning (ci-wait-check
        is now a protective hook).
        Issue #2217: Use test-workflow-hook since worktree-warning is now a
        protective hook. Patch WORKFLOW_PROBLEM_HOOKS to include test hook.
        """
        now = datetime.now(UTC)

        # Session 1: test-workflow-hook blocks 4 times (should count)
        for i in range(4):
            self._write_log_entry(
                "test-workflow-hook",
                "block",
                "session-1",
                now - timedelta(minutes=i),
            )

        # Session 2: test-workflow-hook blocks 3 times (should count)
        for i in range(3):
            self._write_log_entry(
                "test-workflow-hook",
                "block",
                "session-2",
                now - timedelta(hours=1, minutes=i),
            )

        with (
            patch.object(self.module, "EXECUTION_LOG_DIR", self.temp_dir),
            patch.object(self.module, "WORKFLOW_PROBLEM_HOOKS", frozenset({"test-workflow-hook"})),
        ):
            result = self.module.aggregate_recurring_problems()
            # Should be 2 sessions where test-workflow-hook blocked 3+ times
            assert result.get("test-workflow-hook") == 2

    def test_below_threshold_not_counted(self):
        """Should not count sessions with fewer than 3 blocks.

        Issue #2084: Changed from ci-wait-check to worktree-warning.
        Issue #2217: Use test-workflow-hook since worktree-warning is now a
        protective hook.
        """
        now = datetime.now(UTC)

        # Session 1: only 2 blocks (below threshold)
        for i in range(2):
            self._write_log_entry(
                "test-workflow-hook",
                "block",
                "session-1",
                now - timedelta(minutes=i),
            )

        with (
            patch.object(self.module, "EXECUTION_LOG_DIR", self.temp_dir),
            patch.object(self.module, "WORKFLOW_PROBLEM_HOOKS", frozenset({"test-workflow-hook"})),
        ):
            result = self.module.aggregate_recurring_problems()
            assert result.get("test-workflow-hook") is None

    def test_approve_not_counted(self):
        """Should not count approve decisions, only blocks.

        Issue #2084: Changed from ci-wait-check to worktree-warning.
        Issue #2217: Use test-workflow-hook since worktree-warning is now a
        protective hook.
        """
        now = datetime.now(UTC)

        # Session 1: 4 approves (should not count)
        for i in range(4):
            self._write_log_entry(
                "test-workflow-hook",
                "approve",
                "session-1",
                now - timedelta(minutes=i),
            )

        # Session 1: 1 block (below threshold)
        self._write_log_entry("test-workflow-hook", "block", "session-1", now)

        with (
            patch.object(self.module, "EXECUTION_LOG_DIR", self.temp_dir),
            patch.object(self.module, "WORKFLOW_PROBLEM_HOOKS", frozenset({"test-workflow-hook"})),
        ):
            result = self.module.aggregate_recurring_problems()
            assert result.get("test-workflow-hook") is None

    def test_protective_hooks_ignored(self):
        """Should ignore blocks from PROTECTIVE_HOOKS."""
        now = datetime.now(UTC)

        # Session 1: worktree-session-guard blocks 5 times (protective hook)
        for i in range(5):
            self._write_log_entry(
                "worktree-session-guard",
                "block",
                "session-1",
                now - timedelta(minutes=i),
            )

        with patch.object(self.module, "EXECUTION_LOG_DIR", self.temp_dir):
            result = self.module.aggregate_recurring_problems()
            # Should not be counted because it's a protective hook
            assert result.get("worktree-session-guard") is None

    def test_codex_review_check_is_protective_hook(self):
        """Should ignore codex-review-check as it's a protective hook (Issue #2042).

        codex-review-check is a quality gate that blocks until Codex review is run.
        It's expected to block once per session before the first push, so it should
        not be treated as a workflow problem indicator.
        """
        now = datetime.now(UTC)

        # Session 1: codex-review-check blocks 5 times
        for i in range(5):
            self._write_log_entry(
                "codex-review-check",
                "block",
                "session-1",
                now - timedelta(minutes=i),
            )

        with patch.object(self.module, "EXECUTION_LOG_DIR", self.temp_dir):
            result = self.module.aggregate_recurring_problems()
            # Should not be counted because codex-review-check is now a protective hook
            assert result.get("codex-review-check") is None

    def test_ci_wait_check_is_protective_hook(self):
        """Should ignore ci-wait-check as it's a protective hook (Issue #2084).

        ci-wait-check blocks merge before CI completion, which is expected behavior
        and not a workflow problem indicator.
        """
        now = datetime.now(UTC)

        # Session 1: ci-wait-check blocks 5 times
        for i in range(5):
            self._write_log_entry(
                "ci-wait-check",
                "block",
                "session-1",
                now - timedelta(minutes=i),
            )

        with patch.object(self.module, "EXECUTION_LOG_DIR", self.temp_dir):
            result = self.module.aggregate_recurring_problems()
            # Should not be counted because ci-wait-check is now a protective hook
            assert result.get("ci-wait-check") is None

    def test_resolve_thread_guard_is_protective_hook(self):
        """Should ignore resolve-thread-guard as it's a protective hook (Issue #2084).

        resolve-thread-guard blocks unsigned comment resolution, which is expected
        behavior and not a workflow problem indicator.
        """
        now = datetime.now(UTC)

        # Session 1: resolve-thread-guard blocks 5 times
        for i in range(5):
            self._write_log_entry(
                "resolve-thread-guard",
                "block",
                "session-1",
                now - timedelta(minutes=i),
            )

        with patch.object(self.module, "EXECUTION_LOG_DIR", self.temp_dir):
            result = self.module.aggregate_recurring_problems()
            # Should not be counted because resolve-thread-guard is now a protective hook
            assert result.get("resolve-thread-guard") is None

    def test_related_task_check_is_protective_hook(self):
        """Should ignore related-task-check as it's a protective hook (Issue #2084).

        related-task-check blocks until related tasks are confirmed, which is
        expected behavior and not a workflow problem indicator.
        """
        now = datetime.now(UTC)

        # Session 1: related-task-check blocks 5 times
        for i in range(5):
            self._write_log_entry(
                "related-task-check",
                "block",
                "session-1",
                now - timedelta(minutes=i),
            )

        with patch.object(self.module, "EXECUTION_LOG_DIR", self.temp_dir):
            result = self.module.aggregate_recurring_problems()
            # Should not be counted because related-task-check is now a protective hook
            assert result.get("related-task-check") is None

    def test_flow_effect_verifier_is_protective_hook(self):
        """Should ignore flow-effect-verifier as it's a protective hook (Issue #2115).

        flow-effect-verifier blocks when flows are incomplete, which is expected
        behavior and not a workflow problem indicator.
        """
        now = datetime.now(UTC)

        # Session 1: flow-effect-verifier blocks 5 times
        for i in range(5):
            self._write_log_entry(
                "flow-effect-verifier",
                "block",
                "session-1",
                now - timedelta(minutes=i),
            )

        with patch.object(self.module, "EXECUTION_LOG_DIR", self.temp_dir):
            result = self.module.aggregate_recurring_problems()
            # Should not be counted because flow-effect-verifier is now a protective hook
            assert result.get("flow-effect-verifier") is None

    def test_worktree_warning_is_protective_hook(self):
        """Should ignore worktree-warning as it's a protective hook (Issue #2217).

        worktree-warning blocks editing on main branch, which is expected behavior.
        The workflow of "edit blocked → create worktree → edit" is valid.
        """
        now = datetime.now(UTC)

        # Session 1: worktree-warning blocks 5 times
        for i in range(5):
            self._write_log_entry(
                "worktree-warning",
                "block",
                "session-1",
                now - timedelta(minutes=i),
            )

        with patch.object(self.module, "EXECUTION_LOG_DIR", self.temp_dir):
            result = self.module.aggregate_recurring_problems()
            # Should not be counted because worktree-warning is now a protective hook
            assert result.get("worktree-warning") is None

    def test_non_workflow_hooks_ignored(self):
        """Should only count WORKFLOW_PROBLEM_HOOKS, ignore others."""
        now = datetime.now(UTC)

        # Session 1: random-hook blocks 5 times (not in WORKFLOW_PROBLEM_HOOKS)
        for i in range(5):
            self._write_log_entry(
                "some-other-hook",
                "block",
                "session-1",
                now - timedelta(minutes=i),
            )

        with patch.object(self.module, "EXECUTION_LOG_DIR", self.temp_dir):
            result = self.module.aggregate_recurring_problems()
            assert result.get("some-other-hook") is None

    def test_filter_old_entries(self):
        """Should filter out entries older than 7 days.

        Issue #2084: Changed from resolve-thread-guard/ci-wait-check to
        worktree-warning/planning-enforcement.
        Issue #2217: Use test-workflow-hook since worktree-warning is now a
        protective hook.
        """
        now = datetime.now(UTC)

        # Recent entry - session with 3 blocks
        for i in range(3):
            self._write_log_entry(
                "test-workflow-hook",
                "block",
                "recent-session",
                now - timedelta(minutes=i),
            )

        # Old entry (8 days ago) - session with 4 blocks
        for i in range(4):
            self._write_log_entry(
                "test-workflow-hook",
                "block",
                "old-session",
                now - timedelta(days=8, minutes=i),
            )

        with (
            patch.object(self.module, "EXECUTION_LOG_DIR", self.temp_dir),
            patch.object(self.module, "WORKFLOW_PROBLEM_HOOKS", frozenset({"test-workflow-hook"})),
        ):
            result = self.module.aggregate_recurring_problems()
            # Only the recent session should be counted
            assert result.get("test-workflow-hook") == 1

    def test_multiple_hooks_same_session(self):
        """Should track each hook separately within a session.

        Issue #2084: Changed from resolve-thread-guard/ci-wait-check to
        worktree-warning (the only workflow problem hook after Issue #2182).

        Issue #2182: Removed planning-enforcement from WORKFLOW_PROBLEM_HOOKS.
        Issue #2217: Removed worktree-warning from WORKFLOW_PROBLEM_HOOKS.
        Now uses test-workflow-hook and test-workflow-hook-2 for testing.
        """
        now = datetime.now(UTC)

        # Session 1: test-workflow-hook blocks 3 times (should count)
        for i in range(3):
            self._write_log_entry(
                "test-workflow-hook",
                "block",
                "session-1",
                now - timedelta(minutes=i),
            )

        # Session 1: test-workflow-hook-2 blocks 4 times (should count)
        for i in range(4):
            self._write_log_entry(
                "test-workflow-hook-2",
                "block",
                "session-1",
                now - timedelta(minutes=10 + i),
            )

        with (
            patch.object(self.module, "EXECUTION_LOG_DIR", self.temp_dir),
            patch.object(
                self.module,
                "WORKFLOW_PROBLEM_HOOKS",
                frozenset({"test-workflow-hook", "test-workflow-hook-2"}),
            ),
        ):
            result = self.module.aggregate_recurring_problems()
            assert result.get("test-workflow-hook") == 1
            assert result.get("test-workflow-hook-2") == 1


class TestHookIntegration:
    """Integration tests for the hook."""

    def test_approve_non_merge_commands(self):
        """Should approve commands that are not gh pr merge."""
        test_cases = [
            "ls -la",
            "git status",
            "gh pr view 123",
            "gh pr create",
        ]
        for command in test_cases:
            with self.subTest(command=command):
                result = run_hook({"tool_input": {"command": command}})
                assert result["decision"] == "approve"

    def test_approve_empty_command(self):
        """Should approve when command is empty."""
        result = run_hook({"tool_input": {"command": ""}})
        assert result["decision"] == "approve"

    def test_approve_merge_when_no_log(self):
        """Should approve merge when no hook-execution.log exists."""
        result = run_hook({"tool_input": {"command": "gh pr merge 123"}})
        # Should approve because there's no recurring problems data
        # (fail open behavior)
        assert result["decision"] == "approve"


class TestHasIssue:
    """Tests for has_issue function."""

    def setup_method(self):
        self.module = load_hook_module("recurring-problem-block")

    def test_returns_true_on_cli_failure(self):
        """Should return True (fail open) when gh CLI fails."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = "error"
            result = self.module.has_issue("test-source")
            # Fail open: CLI failure should not block
            assert result

    def test_returns_true_on_timeout(self):
        """Should return True (fail open) on timeout."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("gh", 10)
            result = self.module.has_issue("test-source")
            assert result

    def test_returns_false_when_no_matching_issue(self):
        """Should return False when no Issue matches the pattern."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = json.dumps([{"number": 100, "title": "Unrelated Issue"}])
            result = self.module.has_issue("test-source")
            assert not result

    def test_returns_true_when_issue_exists(self):
        """Should return True when matching Issue exists."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = json.dumps(
                [{"number": 100, "title": "[改善] test-sourceの対策を検討"}]
            )
            result = self.module.has_issue("test-source")
            assert result

    def test_returns_true_when_closed_issue_exists(self):
        """Should return True when matching closed Issue exists.

        Issue #2226: The function uses --state all, so closed Issues
        (including NOT_PLANNED) are also detected and should not block.
        """
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            # Closed Issue is returned by gh issue list --state all
            mock_run.return_value.stdout = json.dumps(
                [{"number": 200, "title": "[改善] test-sourceの対策を検討"}]
            )
            result = self.module.has_issue("test-source")
            assert result

    def test_returns_false_when_empty_result(self):
        """Should return False when gh returns empty result."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "[]"
            result = self.module.has_issue("test-source")
            assert not result


class TestHookBlockScenarios:
    """Integration tests for blocking scenarios.

    Issue #1994: Updated to test session-specific file implementation.
    These tests simulate the hook's main logic by testing the core functions
    together, rather than running as a subprocess.
    """

    def setup_method(self):
        self.module = load_hook_module("recurring-problem-block")
        # Create temporary directory for test data
        self.temp_dir = Path(tempfile.mkdtemp())

    def teardown_method(self):
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_recurring_problem(
        self, hook_name: str, session_count: int = 3, blocks_per_session: int = 3
    ):
        """Create test data for a recurring problem.

        Issue #1994: Now creates entries in session-specific files.
        """
        now = datetime.now(UTC)
        for session_idx in range(session_count):
            session_id = f"session-{session_idx}"
            log_file = self.temp_dir / f"hook-execution-{session_id}.jsonl"
            for block_idx in range(blocks_per_session):
                entry = {
                    "timestamp": (
                        now - timedelta(hours=session_idx, minutes=block_idx)
                    ).isoformat(),
                    "hook": hook_name,
                    "decision": "block",
                    "session_id": session_id,
                }
                with open(log_file, "a") as f:
                    f.write(json.dumps(entry) + "\n")

    def _simulate_hook_logic(self, command: str):
        """Simulate the hook's main logic for testing.

        This replicates the key decision logic from the main() function
        without running as a subprocess, allowing patches to work.
        """
        # Check if it's a merge command
        if not self.module.check_is_merge_command(command):
            return {"decision": "approve"}

        # Aggregate recurring problems
        session_counts = self.module.aggregate_recurring_problems()

        # Find problems exceeding threshold
        blocking_problems = []
        for source, count in session_counts.items():
            if count >= self.module.RECURRING_THRESHOLD:
                # Check if Issue already exists
                if self.module.has_issue(source):
                    continue

                blocking_problems.append({"source": source, "count": count})

        if not blocking_problems:
            return {"decision": "approve"}

        # Build block message
        problem_list = "\n".join(
            f"  - {p['source']}: {p['count']}セッションで検出" for p in blocking_problems[:5]
        )
        reason = f"検出された問題:\n{problem_list}"

        return {"decision": "block", "reason": reason}

    def test_block_when_threshold_exceeded(self):
        """Should block merge when recurring problem exceeds threshold.

        Issue #2084: Changed from ci-wait-check to worktree-warning.
        Issue #2217: Use test-workflow-hook since worktree-warning is now a
        protective hook.
        """
        # Create a recurring problem: 4 sessions, each with 3+ blocks
        self._create_recurring_problem("test-workflow-hook", session_count=4, blocks_per_session=3)

        with (
            patch.object(self.module, "EXECUTION_LOG_DIR", self.temp_dir),
            patch.object(self.module, "has_issue", return_value=False),
            patch.object(self.module, "WORKFLOW_PROBLEM_HOOKS", frozenset({"test-workflow-hook"})),
        ):
            result = self._simulate_hook_logic("gh pr merge 123")

        assert result["decision"] == "block"
        assert "test-workflow-hook" in result.get("reason", "")

    def test_approve_when_below_threshold(self):
        """Should approve merge when problem session count is below threshold.

        Issue #2084: Changed from ci-wait-check to worktree-warning.
        Issue #2217: Use test-workflow-hook since worktree-warning is now a
        protective hook.
        """
        # Create a problem detected in only 2 sessions (below threshold of 3)
        self._create_recurring_problem("test-workflow-hook", session_count=2, blocks_per_session=3)

        with (
            patch.object(self.module, "EXECUTION_LOG_DIR", self.temp_dir),
            patch.object(self.module, "WORKFLOW_PROBLEM_HOOKS", frozenset({"test-workflow-hook"})),
        ):
            result = self._simulate_hook_logic("gh pr merge 123")

        assert result["decision"] == "approve"

    def test_approve_when_issue_exists(self):
        """Should approve merge when an Issue exists for the problem.

        Issue #2084: Changed from ci-wait-check to worktree-warning.
        Issue #2217: Use test-workflow-hook since worktree-warning is now a
        protective hook.
        """
        # Create a recurring problem
        self._create_recurring_problem("test-workflow-hook", session_count=4, blocks_per_session=3)

        # Mock has_issue to return True (Issue exists)
        with (
            patch.object(self.module, "EXECUTION_LOG_DIR", self.temp_dir),
            patch.object(self.module, "has_issue", return_value=True),
            patch.object(self.module, "WORKFLOW_PROBLEM_HOOKS", frozenset({"test-workflow-hook"})),
        ):
            result = self._simulate_hook_logic("gh pr merge 123")

        assert result["decision"] == "approve"

    def test_workflow_problem_blocks_merge(self):
        """Should block merge when workflow problem hook blocks repeatedly.

        Issue #2084: Changed from resolve-thread-guard/ci-wait-check to
        worktree-warning/planning-enforcement.
        Issue #2182: Removed planning-enforcement from WORKFLOW_PROBLEM_HOOKS.
        Issue #2217: Removed worktree-warning from WORKFLOW_PROBLEM_HOOKS.
        Now uses test-workflow-hook for testing.
        """
        now = datetime.now(UTC)

        # Create recurring test-workflow-hook problem with 4 sessions
        for session_idx in range(4):
            session_id = f"session-{session_idx}"
            log_file = self.temp_dir / f"hook-execution-{session_id}.jsonl"
            for block_idx in range(3):
                entry = {
                    "timestamp": (
                        now - timedelta(hours=session_idx, minutes=block_idx)
                    ).isoformat(),
                    "hook": "test-workflow-hook",
                    "decision": "block",
                    "session_id": session_id,
                }
                with open(log_file, "a") as f:
                    f.write(json.dumps(entry) + "\n")

        with (
            patch.object(self.module, "EXECUTION_LOG_DIR", self.temp_dir),
            patch.object(self.module, "has_issue", return_value=False),
            patch.object(self.module, "WORKFLOW_PROBLEM_HOOKS", frozenset({"test-workflow-hook"})),
        ):
            result = self._simulate_hook_logic("gh pr merge 123")

        assert result["decision"] == "block"
        reason = result.get("reason", "")
        assert "test-workflow-hook" in reason

    def test_non_merge_command_approves(self):
        """Should approve non-merge commands without checking problems.

        Issue #2084: Changed from ci-wait-check to worktree-warning.
        Issue #2217: Use test-workflow-hook since worktree-warning is now a
        protective hook.
        """
        # Create a problem that would normally block
        self._create_recurring_problem("test-workflow-hook", session_count=4, blocks_per_session=3)

        with (
            patch.object(self.module, "EXECUTION_LOG_DIR", self.temp_dir),
            patch.object(self.module, "WORKFLOW_PROBLEM_HOOKS", frozenset({"test-workflow-hook"})),
        ):
            result = self._simulate_hook_logic("git status")

        assert result["decision"] == "approve"


class TestEscapeGithubSearchTerm:
    """Tests for escape_github_search_term function (Issue #607)."""

    def setup_method(self):
        self.module = load_hook_module("recurring-problem-block")

    def test_simple_term(self):
        """Should return simple terms unchanged."""
        result = self.module.escape_github_search_term("codex-review-check")
        assert result == "codex-review-check"

    def test_escapes_double_quotes(self):
        """Should escape double quotes."""
        result = self.module.escape_github_search_term('source "with" quotes')
        assert result == 'source \\"with\\" quotes'

    def test_escapes_backslashes(self):
        """Should escape backslashes."""
        result = self.module.escape_github_search_term("source\\name")
        assert result == "source\\\\name"

    def test_escapes_backslash_before_quote(self):
        """Should properly escape backslash followed by quote."""
        result = self.module.escape_github_search_term('source\\"name')
        assert result == 'source\\\\\\"name'

    def test_hyphenated_source(self):
        """Should handle hyphenated source names."""
        result = self.module.escape_github_search_term("my-hook-name")
        assert result == "my-hook-name"

    def test_underscore_source(self):
        """Should handle underscored source names."""
        result = self.module.escape_github_search_term("my_hook_name")
        assert result == "my_hook_name"

    def test_empty_string(self):
        """Should handle empty string."""
        result = self.module.escape_github_search_term("")
        assert result == ""
