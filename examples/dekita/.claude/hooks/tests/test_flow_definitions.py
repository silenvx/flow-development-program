#!/usr/bin/env python3
"""Tests for flow_definitions.py - Single Source of Truth for flow configurations."""

import sys
from pathlib import Path

# Add parent directory to path for module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)


class TestIssueAIReviewFlow:
    """Tests for IssueAIReviewFlow class."""

    def setup_method(self):
        """Set up test fixtures."""
        from flow_definitions import IssueAIReviewFlow

        self.flow = IssueAIReviewFlow()

    def test_flow_has_correct_id(self):
        """Flow should have correct ID."""
        assert self.flow.id == "issue-ai-review"

    def test_flow_has_three_steps(self):
        """Flow should have three steps in correct order."""
        step_ids = self.flow.get_step_ids()
        assert step_ids == ["review_posted", "review_viewed", "issue_updated"]

    def test_flow_not_blocking_on_session_end(self):
        """Flow should NOT block on session end (Issue #2108: informational flow)."""
        assert not self.flow.blocking_on_session_end

    def test_matches_review_viewed_with_correct_issue(self):
        """matches_step should match review_viewed for correct Issue."""
        context = {"issue_number": 123}
        command = "gh issue view 123 --comments"
        assert self.flow.matches_step("review_viewed", command, context)

    def test_matches_review_viewed_with_extra_args(self):
        """matches_step should match review_viewed with extra arguments."""
        context = {"issue_number": 456}
        command = "gh issue view 456 --comments | head -50"
        assert self.flow.matches_step("review_viewed", command, context)

    def test_does_not_match_review_viewed_wrong_issue(self):
        """matches_step should NOT match review_viewed for wrong Issue."""
        context = {"issue_number": 123}
        command = "gh issue view 456 --comments"
        assert not self.flow.matches_step("review_viewed", command, context)

    def test_does_not_match_review_viewed_without_comments(self):
        """matches_step should NOT match review_viewed without --comments."""
        context = {"issue_number": 123}
        command = "gh issue view 123"
        assert not self.flow.matches_step("review_viewed", command, context)

    def test_matches_issue_updated_with_correct_issue(self):
        """matches_step should match issue_updated for correct Issue."""
        context = {"issue_number": 789}
        command = "gh issue edit 789 --body 'Updated content'"
        assert self.flow.matches_step("issue_updated", command, context)

    def test_does_not_match_issue_updated_wrong_issue(self):
        """matches_step should NOT match issue_updated for wrong Issue."""
        context = {"issue_number": 123}
        command = "gh issue edit 456 --body 'Updated content'"
        assert not self.flow.matches_step("issue_updated", command, context)

    def test_matches_issue_updated_with_rest_api(self):
        """matches_step should match issue_updated via REST API PATCH (Issue #1374)."""
        context = {"issue_number": 789}
        command = "gh api repos/owner/repo/issues/789 -X PATCH -f body='Updated'"
        assert self.flow.matches_step("issue_updated", command, context)

    def test_does_not_match_issue_updated_rest_api_wrong_issue(self):
        """matches_step should NOT match REST API PATCH for wrong Issue."""
        context = {"issue_number": 123}
        command = "gh api repos/owner/repo/issues/456 -X PATCH -f body='Updated'"
        assert not self.flow.matches_step("issue_updated", command, context)

    def test_matches_issue_updated_rest_api_args_before_method(self):
        """matches_step should match REST API with -X PATCH at end (Issue #1374)."""
        context = {"issue_number": 789}
        command = "gh api repos/owner/repo/issues/789 -f body='Updated' -X PATCH"
        assert self.flow.matches_step("issue_updated", command, context)

    def test_does_not_match_issue_updated_rest_api_get_method(self):
        """matches_step should NOT match REST API GET (only PATCH)."""
        context = {"issue_number": 789}
        command = "gh api repos/owner/repo/issues/789 -X GET"
        assert not self.flow.matches_step("issue_updated", command, context)

    def test_matches_issue_updated_rest_api_equals_syntax(self):
        """matches_step should match REST API with -X=PATCH syntax (Issue #1374)."""
        context = {"issue_number": 789}
        command = "gh api repos/owner/repo/issues/789 -X=PATCH -f body='Updated'"
        assert self.flow.matches_step("issue_updated", command, context)

    def test_does_not_match_issue_updated_rest_api_suffix(self):
        """matches_step should NOT match issue number with suffix (e.g., 789a)."""
        context = {"issue_number": 789}
        command = "gh api repos/owner/repo/issues/789a -X PATCH -f body='Updated'"
        assert not self.flow.matches_step("issue_updated", command, context)

    def test_review_posted_does_not_match_command(self):
        """review_posted step should not match any command (programmatic only)."""
        context = {"issue_number": 123}
        assert not self.flow.matches_step("review_posted", "any command", context)

    def test_matches_step_returns_false_without_context(self):
        """matches_step should return False without issue_number in context."""
        context = {}
        command = "gh issue view 123 --comments"
        assert not self.flow.matches_step("review_viewed", command, context)


class TestFlowRegistry:
    """Tests for flow registry functions."""

    def test_get_flow_definition_returns_flow(self):
        """get_flow_definition should return flow for known ID."""
        from flow_definitions import get_flow_definition

        flow = get_flow_definition("issue-ai-review")
        assert flow is not None
        assert flow.id == "issue-ai-review"

    def test_get_flow_definition_returns_none_for_unknown(self):
        """get_flow_definition should return None for unknown ID."""
        from flow_definitions import get_flow_definition

        flow = get_flow_definition("unknown-flow")
        assert flow is None

    def test_get_all_flow_definitions(self):
        """get_all_flow_definitions should return all registered flows."""
        from flow_definitions import get_all_flow_definitions

        flows = get_all_flow_definitions()
        assert "issue-ai-review" in flows


class TestValidateStepOrder:
    """Tests for step order validation."""

    def test_valid_order_first_step(self):
        """First step should be valid with no completed steps."""
        from flow_definitions import validate_step_order

        is_valid, error = validate_step_order("issue-ai-review", [], "review_posted")
        assert is_valid
        assert error == ""

    def test_valid_order_second_step(self):
        """Second step should be valid after first step."""
        from flow_definitions import validate_step_order

        is_valid, error = validate_step_order("issue-ai-review", ["review_posted"], "review_viewed")
        assert is_valid
        assert error == ""

    def test_valid_order_third_step(self):
        """Third step should be valid after first two steps."""
        from flow_definitions import validate_step_order

        is_valid, error = validate_step_order(
            "issue-ai-review", ["review_posted", "review_viewed"], "issue_updated"
        )
        assert is_valid
        assert error == ""

    def test_invalid_order_skip_step(self):
        """Skipping a step should be invalid."""
        from flow_definitions import validate_step_order

        is_valid, error = validate_step_order("issue-ai-review", ["review_posted"], "issue_updated")
        assert not is_valid
        assert "review_viewed" in error

    def test_invalid_order_first_step_not_done(self):
        """Second step without first step should be invalid."""
        from flow_definitions import validate_step_order

        is_valid, error = validate_step_order("issue-ai-review", [], "review_viewed")
        assert not is_valid
        assert "review_posted" in error

    def test_unknown_flow_returns_error(self):
        """Unknown flow should return error."""
        from flow_definitions import validate_step_order

        is_valid, error = validate_step_order("unknown-flow", [], "step1")
        assert not is_valid
        assert "Unknown flow" in error

    def test_unknown_step_returns_error(self):
        """Unknown step should return error."""
        from flow_definitions import validate_step_order

        is_valid, error = validate_step_order("issue-ai-review", [], "unknown_step")
        assert not is_valid
        assert "Unknown step" in error


class TestFlowToDict:
    """Tests for backward compatibility with dict format."""

    def test_to_dict_returns_valid_structure(self):
        """to_dict should return dict with expected keys."""
        from flow_definitions import IssueAIReviewFlow

        flow = IssueAIReviewFlow()
        d = flow.to_dict()

        assert d["id"] == "issue-ai-review"
        assert d["name"] == "Issue AIレビューフロー"
        assert "steps" in d
        assert len(d["steps"]) == 3
        assert "blocking" in d
        # Issue #2108: IssueAIReviewFlow is now non-blocking
        assert not d["blocking"]["on_session_end"]

    def test_to_dict_steps_have_required_fields(self):
        """to_dict steps should have id, name, description."""
        from flow_definitions import IssueAIReviewFlow

        flow = IssueAIReviewFlow()
        d = flow.to_dict()

        for step in d["steps"]:
            assert "id" in step
            assert "name" in step
            assert "description" in step


class TestContextIsolation:
    """Tests for context isolation between different Issues."""

    def test_different_issues_do_not_interfere(self):
        """Commands for different Issues should not interfere."""
        from flow_definitions import IssueAIReviewFlow

        flow = IssueAIReviewFlow()

        # Issue #100 context
        context_100 = {"issue_number": 100}
        # Issue #200 context
        context_200 = {"issue_number": 200}

        # Command for Issue #200
        command = "gh issue edit 200 --body 'test'"

        # Should match Issue #200's flow
        assert flow.matches_step("issue_updated", command, context_200)

        # Should NOT match Issue #100's flow (this was the bug)
        assert not flow.matches_step("issue_updated", command, context_100)


class TestDevelopmentWorkflow:
    """Tests for DevelopmentWorkflow class."""

    def setup_method(self):
        """Set up test fixtures."""
        from flow_definitions import DevelopmentWorkflow

        self.flow = DevelopmentWorkflow()

    def test_flow_has_correct_id(self):
        """Flow should have correct ID."""
        assert self.flow.id == "development-workflow"

    def test_flow_has_nine_steps(self):
        """Flow should have nine steps."""
        assert len(self.flow.steps) == 9

    def test_flow_step_order(self):
        """Steps should be in correct order."""
        step_ids = self.flow.get_step_ids()
        expected = [
            "worktree_created",
            "implementation",
            "committed",
            "pushed",
            "pr_created",
            "ci_passed",
            "review_addressed",
            "merged",
            "cleaned_up",
        ]
        assert step_ids == expected

    def test_flow_not_blocking_on_session_end(self):
        """Flow should NOT block on session end."""
        assert not self.flow.blocking_on_session_end

    def test_matches_worktree_created(self):
        """matches_step should match worktree creation."""
        context = {"issue_number": 123}
        command = "git worktree add ../.worktrees/issue-123 -b issue-123"
        assert self.flow.matches_step("worktree_created", command, context)

    def test_matches_committed(self):
        """matches_step should match git commit."""
        command = "git commit -m 'fix: something'"
        assert self.flow.matches_step("committed", command, {})

    def test_matches_pushed(self):
        """matches_step should match git push."""
        command = "git push -u origin feature-branch"
        assert self.flow.matches_step("pushed", command, {})

    def test_matches_pr_created(self):
        """matches_step should match PR creation."""
        command = "gh pr create --title 'Fix bug' --body 'Description'"
        assert self.flow.matches_step("pr_created", command, {})

    def test_matches_merged(self):
        """matches_step should match PR merge."""
        command = "gh pr merge 123 --squash"
        assert self.flow.matches_step("merged", command, {})

    def test_matches_cleaned_up(self):
        """matches_step should match worktree removal."""
        command = "git worktree remove ../.worktrees/issue-123"
        assert self.flow.matches_step("cleaned_up", command, {})

    def test_matches_ci_passed(self):
        """matches_step should match CI check commands."""
        # gh run watch
        command = "gh run watch 12345 --exit-status"
        assert self.flow.matches_step("ci_passed", command, {})
        # gh pr checks
        command = "gh pr checks 123"
        assert self.flow.matches_step("ci_passed", command, {})

    def test_implementation_does_not_match_command(self):
        """implementation step should not match any command (programmatic only)."""
        assert not self.flow.matches_step("implementation", "any command", {})
        assert not self.flow.matches_step("implementation", "git status", {})

    def test_review_addressed_does_not_match_command(self):
        """review_addressed step should not match any command (programmatic only)."""
        assert not self.flow.matches_step("review_addressed", "any command", {})
        assert not self.flow.matches_step("review_addressed", "gh pr view 123", {})

    def test_worktree_created_does_not_match_false_positive(self):
        """worktree_created should not match commands containing the string in non-command context."""
        context = {"issue_number": 123}
        # Should not match echo command containing the string
        command = 'echo "git worktree add" && git status'
        assert not self.flow.matches_step("worktree_created", command, context)
        # Should not match commented command
        command = "# git worktree add issue-123"
        assert not self.flow.matches_step("worktree_created", command, context)

    def test_worktree_created_matches_cd_prefix_pattern(self):
        """worktree_created should match cd ... && git worktree add pattern.

        Issue #2534: Commands with `cd /path && git worktree add` prefix
        should be detected correctly.
        """
        context = {"issue_number": 2525}
        # Should match cd && git worktree add pattern
        command = "cd /Users/test/repo && git worktree add --lock .worktrees/issue-2525 -b feat/issue-2525-fix"
        assert self.flow.matches_step("worktree_created", command, context)

        # Should also match without issue context
        assert self.flow.matches_step("worktree_created", command, {})


class TestStepCharacteristics:
    """Tests for step characteristics (required, blocking, parallel, etc.)."""

    def setup_method(self):
        """Set up test fixtures."""
        from flow_definitions import DevelopmentWorkflow

        self.flow = DevelopmentWorkflow()

    def test_worktree_step_is_required_and_blocking(self):
        """worktree_created should be required and blocking."""
        step = self.flow.get_step("worktree_created")
        assert step.required
        assert step.blocking
        assert not step.repeatable

    def test_implementation_step_is_repeatable(self):
        """implementation should be repeatable."""
        step = self.flow.get_step("implementation")
        assert step.required
        assert not step.blocking
        assert step.repeatable

    def test_commit_step_is_repeatable(self):
        """committed should be repeatable."""
        step = self.flow.get_step("committed")
        assert step.repeatable

    def test_pushed_depends_on_committed(self):
        """pushed should depend on committed."""
        step = self.flow.get_step("pushed")
        assert "committed" in step.depends_on

    def test_ci_and_review_are_parallel(self):
        """ci_passed and review_addressed should be parallel."""
        ci_step = self.flow.get_step("ci_passed")
        review_step = self.flow.get_step("review_addressed")

        assert "review_addressed" in ci_step.parallel_with
        assert "ci_passed" in review_step.parallel_with

    def test_review_addressed_is_optional(self):
        """review_addressed should be optional with condition."""
        step = self.flow.get_step("review_addressed")
        assert not step.required
        assert step.condition == "has_review_comments"

    def test_merged_depends_on_ci_passed(self):
        """merged should depend on ci_passed."""
        step = self.flow.get_step("merged")
        assert "ci_passed" in step.depends_on

    def test_cleaned_up_is_optional(self):
        """cleaned_up should be optional."""
        step = self.flow.get_step("cleaned_up")
        assert not step.required


class TestValidateStepOrderWithCharacteristics:
    """Tests for step order validation with new characteristics."""

    def test_parallel_steps_can_be_completed_in_any_order(self):
        """Parallel steps should be valid in any order."""
        from flow_definitions import validate_step_order

        # Complete ci_passed before review_addressed (both have same order)
        completed = [
            "worktree_created",
            "implementation",
            "committed",
            "pushed",
            "pr_created",
        ]

        # ci_passed should be valid
        is_valid, _ = validate_step_order("development-workflow", completed, "ci_passed")
        assert is_valid

        # review_addressed should also be valid (parallel)
        is_valid, _ = validate_step_order("development-workflow", completed, "review_addressed")
        assert is_valid

    def test_depends_on_validation(self):
        """Steps with depends_on should require those steps."""
        from flow_definitions import validate_step_order

        # pushed depends on committed
        completed = ["worktree_created", "implementation"]

        is_valid, error = validate_step_order("development-workflow", completed, "pushed")
        assert not is_valid
        assert "committed" in error

    def test_merged_requires_ci_passed(self):
        """merged should require ci_passed due to depends_on."""
        from flow_definitions import validate_step_order

        completed = [
            "worktree_created",
            "implementation",
            "committed",
            "pushed",
            "pr_created",
        ]

        is_valid, error = validate_step_order("development-workflow", completed, "merged")
        assert not is_valid
        assert "ci_passed" in error

    def test_optional_steps_can_be_skipped(self):
        """Optional steps should not block subsequent steps."""
        from flow_definitions import validate_step_order

        # Skip review_addressed (optional), go straight to merged after ci_passed
        completed = [
            "worktree_created",
            "implementation",
            "committed",
            "pushed",
            "pr_created",
            "ci_passed",
        ]

        is_valid, _ = validate_step_order("development-workflow", completed, "merged")
        assert is_valid


class TestCanSkipStep:
    """Tests for can_skip_step function."""

    def test_required_step_cannot_be_skipped(self):
        """Required steps cannot be skipped."""
        from flow_definitions import can_skip_step

        assert not can_skip_step("development-workflow", "committed", {})

    def test_optional_step_can_be_skipped(self):
        """Optional steps can be skipped."""
        from flow_definitions import can_skip_step

        assert can_skip_step("development-workflow", "cleaned_up", {})

    def test_conditional_step_can_be_skipped_when_condition_false(self):
        """Conditional steps can be skipped when condition is false."""
        from flow_definitions import can_skip_step

        # No review comments
        context = {"has_review_comments": False}
        assert can_skip_step("development-workflow", "review_addressed", context)

    def test_conditional_step_cannot_be_skipped_when_condition_true(self):
        """Conditional steps cannot be skipped when condition is true."""
        from flow_definitions import can_skip_step

        # Has review comments
        context = {"has_review_comments": True}
        assert not can_skip_step("development-workflow", "review_addressed", context)


class TestGetPendingRequiredSteps:
    """Tests for get_pending_required_steps function."""

    def test_all_steps_pending_initially(self):
        """All required steps should be pending initially."""
        from flow_definitions import get_pending_required_steps

        pending = get_pending_required_steps("development-workflow", [])
        # Should include all required steps (not cleaned_up, not review_addressed)
        assert "worktree_created" in pending
        assert "merged" in pending
        assert "cleaned_up" not in pending
        assert "review_addressed" not in pending

    def test_completed_steps_not_in_pending(self):
        """Completed steps should not be in pending."""
        from flow_definitions import get_pending_required_steps

        completed = ["worktree_created", "implementation"]
        pending = get_pending_required_steps("development-workflow", completed)

        assert "worktree_created" not in pending
        assert "implementation" not in pending
        assert "committed" in pending


class TestFlowRegistryIncludesDevelopmentWorkflow:
    """Tests for flow registry with development workflow."""

    def test_development_workflow_in_registry(self):
        """development-workflow should be in registry."""
        from flow_definitions import get_flow_definition

        flow = get_flow_definition("development-workflow")
        assert flow is not None
        assert flow.id == "development-workflow"

    def test_get_all_includes_both_flows(self):
        """get_all_flow_definitions should include both flows."""
        from flow_definitions import get_all_flow_definitions

        flows = get_all_flow_definitions()
        assert "issue-ai-review" in flows
        assert "development-workflow" in flows


class TestFlowStepPhaseAttribute:
    """Tests for FlowStep phase attribute (#681)."""

    def setup_method(self):
        """Set up test fixtures."""
        from flow_definitions import DevelopmentWorkflow

        self.flow = DevelopmentWorkflow()

    def test_worktree_step_has_setup_phase(self):
        """worktree_created should have 'setup' phase."""
        step = self.flow.get_step("worktree_created")
        assert step.phase == "setup"

    def test_implementation_steps_have_implementation_phase(self):
        """implementation, committed, pushed should have 'implementation' phase."""
        for step_id in ["implementation", "committed", "pushed"]:
            step = self.flow.get_step(step_id)
            assert step.phase == "implementation", f"{step_id} should have implementation phase"

    def test_review_steps_have_review_phase(self):
        """pr_created, ci_passed, review_addressed should have 'review' phase."""
        for step_id in ["pr_created", "ci_passed", "review_addressed"]:
            step = self.flow.get_step(step_id)
            assert step.phase == "review", f"{step_id} should have review phase"

    def test_final_steps_have_complete_phase(self):
        """merged, cleaned_up should have 'complete' phase."""
        for step_id in ["merged", "cleaned_up"]:
            step = self.flow.get_step(step_id)
            assert step.phase == "complete", f"{step_id} should have complete phase"

    def test_all_steps_have_phase_defined(self):
        """All steps in DevelopmentWorkflow should have a phase defined."""
        for step in self.flow.steps:
            assert step.phase is not None, f"Step {step.id} should have a phase"


class TestFlowDefinitionCompletionStep:
    """Tests for FlowDefinition completion_step attribute (#681)."""

    def setup_method(self):
        """Set up test fixtures."""
        from flow_definitions import DevelopmentWorkflow, IssueAIReviewFlow

        self.dev_workflow = DevelopmentWorkflow()
        self.review_flow = IssueAIReviewFlow()

    def test_development_workflow_has_merged_as_completion_step(self):
        """DevelopmentWorkflow should have 'merged' as completion_step."""
        assert self.dev_workflow.completion_step == "merged"

    def test_issue_ai_review_has_no_completion_step(self):
        """IssueAIReviewFlow should not have a completion_step (all steps required)."""
        assert self.review_flow.completion_step is None

    def test_completion_step_is_valid_step_id(self):
        """completion_step should be a valid step ID in the flow."""
        step_ids = self.dev_workflow.get_step_ids()
        assert self.dev_workflow.completion_step in step_ids


class TestFormatFlowSummaryWithPhases:
    """Tests for format_flow_summary with phase-based grouping (#681)."""

    def setup_method(self):
        """Set up test fixtures."""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "flow_effect_verifier",
            Path(__file__).parent.parent / "flow-effect-verifier.py",
        )
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_phase_based_display_for_development_workflow(self):
        """format_flow_summary groups steps by phase for development-workflow."""
        flows = [
            {
                "flow_id": "development-workflow",
                "flow_name": "開発ワークフロー",
                "completed_steps": ["worktree_created", "implementation", "committed"],
                "pending_steps": ["pushed", "pr_created", "ci_passed", "merged", "cleaned_up"],
                "step_counts": {"committed": 2},
                "context": {"issue_number": 123},
            }
        ]

        result = self.module.format_flow_summary(flows)

        # Should show phase-based grouping
        assert "[準備] ✅ 完了" in result  # setup phase complete
        assert "[実装]" in result  # implementation phase expanded
        assert "  ✅ 実装" in result  # completed step in current phase
        assert "  ✅ コミット (2回)" in result  # repeated step count
        assert "  ⏳ プッシュ ← 次のステップ" in result  # next step
        assert "[レビュー] ⬜" in result  # pending phase collapsed
        assert "[完了] ⬜" in result  # complete phase collapsed

    def test_all_phases_collapsed_when_all_complete(self):
        """format_flow_summary shows collapsed phases when all complete in phase."""
        flows = [
            {
                "flow_id": "development-workflow",
                "flow_name": "開発ワークフロー",
                "completed_steps": [
                    "worktree_created",
                    "implementation",
                    "committed",
                    "pushed",
                    "pr_created",
                    "ci_passed",
                    "review_addressed",  # Include optional step for phase completion
                ],
                "pending_steps": ["merged", "cleaned_up"],
                "step_counts": {},
                "context": {},
            }
        ]

        result = self.module.format_flow_summary(flows)

        # Setup, implementation, and review phases should be complete
        assert "[準備] ✅ 完了" in result
        assert "[実装] ✅ 完了" in result
        assert "[レビュー] ✅ 完了" in result
        # Complete phase should be expanded (current phase)
        assert "[完了]" in result
        assert "  ⏳ マージ ← 次のステップ" in result


class TestGetIncompleteFlowsWithCompletionStep:
    """Tests for get_incomplete_flows() respecting completion_step (#681).

    Issue #1840: Updated to use session-specific log files.
    """

    def setup_method(self):
        """Set up test fixtures with temp log file."""
        import tempfile
        from pathlib import Path

        self.temp_dir = tempfile.mkdtemp()
        self.session_id = "test-session-completion"
        # Issue #1840: Use session-specific log file
        self.log_file = Path(self.temp_dir) / f"flow-progress-{self.session_id}.jsonl"

    def teardown_method(self):
        """Clean up temp directories."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_log_entry(self, entry: dict) -> None:
        """Write a log entry to the temp log file."""
        import json

        with open(self.log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def test_flow_with_merged_completed_is_not_incomplete(self):
        """Flow should be considered complete when merged step is completed."""
        from unittest.mock import patch

        import common

        # Start a development workflow
        self._write_log_entry(
            {
                "session_id": self.session_id,
                "event": "flow_started",
                "flow_id": "development-workflow",
                "flow_instance_id": "test-instance-1",
                "expected_steps": [
                    "worktree_created",
                    "implementation",
                    "committed",
                    "pushed",
                    "pr_created",
                    "ci_passed",
                    "merged",
                    "cleaned_up",
                ],
            }
        )

        # Complete all steps up to and including merged (but not cleaned_up)
        for step in [
            "worktree_created",
            "implementation",
            "committed",
            "pushed",
            "pr_created",
            "ci_passed",
            "merged",
        ]:
            self._write_log_entry(
                {
                    "session_id": self.session_id,
                    "event": "step_completed",
                    "flow_instance_id": "test-instance-1",
                    "step_id": step,
                }
            )

        # Get incomplete flows - should be empty since merged is complete
        # Issue #2545: HookContextパターンに移行、session_idを直接渡す
        test_session_id = "test-session-12345"
        # Update session_id to match test value
        self.session_id = test_session_id
        # Rewrite log entries with correct session_id
        self.log_file = Path(self.temp_dir) / f"flow-progress-{self.session_id}.jsonl"
        # Start a development workflow
        self._write_log_entry(
            {
                "session_id": self.session_id,
                "event": "flow_started",
                "flow_id": "development-workflow",
                "flow_instance_id": "test-instance-1",
                "expected_steps": [
                    "worktree_created",
                    "implementation",
                    "committed",
                    "pushed",
                    "pr_created",
                    "ci_passed",
                    "merged",
                    "cleaned_up",
                ],
            }
        )
        # Complete all steps up to and including merged
        for step in [
            "worktree_created",
            "implementation",
            "committed",
            "pushed",
            "pr_created",
            "ci_passed",
            "merged",
        ]:
            self._write_log_entry(
                {
                    "session_id": self.session_id,
                    "event": "step_completed",
                    "flow_instance_id": "test-instance-1",
                    "step_id": step,
                }
            )
        with patch.object(common, "FLOW_LOG_DIR", Path(self.temp_dir)):
            incomplete = common.get_incomplete_flows(session_id=test_session_id)

        assert len(incomplete) == 0, "Flow should be complete after merged step"

    def test_flow_without_merged_is_still_incomplete(self):
        """Flow should still be incomplete if merged step is not completed."""
        # Get incomplete flows - should have one entry
        # Issue #2545: HookContextパターンに移行、session_idを直接渡す
        from unittest.mock import patch

        import common

        test_session_id = "test-session-12346"
        # Update session_id to match test value
        self.session_id = test_session_id
        # Rewrite log file path with correct session_id
        self.log_file = Path(self.temp_dir) / f"flow-progress-{self.session_id}.jsonl"
        # Start a development workflow
        self._write_log_entry(
            {
                "session_id": self.session_id,
                "event": "flow_started",
                "flow_id": "development-workflow",
                "flow_instance_id": "test-instance-2",
                "expected_steps": [
                    "worktree_created",
                    "implementation",
                    "committed",
                    "pushed",
                    "pr_created",
                    "ci_passed",
                    "merged",
                    "cleaned_up",
                ],
            }
        )

        # Complete steps up to ci_passed (not merged)
        for step in [
            "worktree_created",
            "implementation",
            "committed",
            "pushed",
            "pr_created",
            "ci_passed",
        ]:
            self._write_log_entry(
                {
                    "session_id": self.session_id,
                    "event": "step_completed",
                    "flow_instance_id": "test-instance-2",
                    "step_id": step,
                }
            )
        with patch.object(common, "FLOW_LOG_DIR", Path(self.temp_dir)):
            incomplete = common.get_incomplete_flows(session_id=test_session_id)

        assert len(incomplete) == 1, "Flow should be incomplete without merged step"
        assert "merged" in incomplete[0]["pending_steps"]


class TestPhaseDefinitions:
    """Tests for Phase definitions (#workflowverifier)."""

    def test_all_phases_defined(self):
        """DEVELOPMENT_PHASES should have 13 phases."""
        from flow_definitions import DEVELOPMENT_PHASES

        assert len(DEVELOPMENT_PHASES) == 13

    def test_phases_have_unique_ids(self):
        """All phases should have unique IDs."""
        from flow_definitions import DEVELOPMENT_PHASES

        ids = [p.id for p in DEVELOPMENT_PHASES]
        assert len(ids) == len(set(ids))

    def test_phases_have_correct_order(self):
        """Phases should have correct sequential order."""
        from flow_definitions import DEVELOPMENT_PHASES

        orders = [p.order for p in DEVELOPMENT_PHASES]
        assert orders == list(range(13))

    def test_get_phase_returns_phase(self):
        """get_phase should return phase for known ID."""
        from flow_definitions import get_phase

        phase = get_phase("session_start")
        assert phase is not None
        assert phase.id == "session_start"
        assert phase.name == "セッション開始"

    def test_get_phase_returns_none_for_unknown(self):
        """get_phase should return None for unknown ID."""
        from flow_definitions import get_phase

        phase = get_phase("unknown-phase")
        assert phase is None

    def test_get_all_phases_returns_sorted_list(self):
        """get_all_phases should return phases in order."""
        from flow_definitions import get_all_phases

        phases = get_all_phases()
        assert len(phases) == 13
        assert phases[0].id == "session_start"
        assert phases[-1].id == "session_end"

    def test_get_expected_hooks_for_phase(self):
        """get_expected_hooks_for_phase should return hook list."""
        from flow_definitions import get_expected_hooks_for_phase

        hooks = get_expected_hooks_for_phase("session_start")
        assert "date-context-injector" in hooks
        assert "session-handoff-reader" in hooks

    def test_session_start_phase_has_expected_hooks(self):
        """session_start phase should have SessionStart hooks."""
        from flow_definitions import get_phase

        phase = get_phase("session_start")
        expected = [
            "date-context-injector",
            "check-lefthook.sh",
            "session-handoff-reader",
            "open-pr-warning",
            "branch_check",
        ]
        for hook in expected:
            assert hook in phase.expected_hooks


class TestExpectedHookBehaviorDefinitions:
    """Tests for ExpectedHookBehavior definitions (#workflowverifier)."""

    def test_expected_hook_behaviors_not_empty(self):
        """EXPECTED_HOOK_BEHAVIORS should have hook definitions."""
        from flow_definitions import EXPECTED_HOOK_BEHAVIORS

        assert len(EXPECTED_HOOK_BEHAVIORS) > 0

    def test_hook_behavior_has_required_fields(self):
        """Each hook behavior should have required fields."""
        from flow_definitions import EXPECTED_HOOK_BEHAVIORS

        for hook_name, behavior in EXPECTED_HOOK_BEHAVIORS.items():
            assert behavior.hook_name == hook_name
            assert behavior.phase_id is not None
            assert behavior.trigger_type in ["SessionStart", "PreToolUse", "PostToolUse", "Stop"]

    def test_get_hook_behavior_returns_behavior(self):
        """get_hook_behavior should return behavior for known hook."""
        from flow_definitions import get_hook_behavior

        behavior = get_hook_behavior("merge-check")
        assert behavior is not None
        assert behavior.phase_id == "merge"
        assert behavior.trigger_type == "PreToolUse"

    def test_get_hook_behavior_returns_none_for_unknown(self):
        """get_hook_behavior should return None for unknown hook."""
        from flow_definitions import get_hook_behavior

        behavior = get_hook_behavior("unknown-hook")
        assert behavior is None

    def test_get_all_hook_names(self):
        """get_all_hook_names should return list of hook names."""
        from flow_definitions import get_all_hook_names

        names = get_all_hook_names()
        assert "merge-check" in names
        assert "worktree-warning" in names

    def test_get_hooks_by_phase(self):
        """get_hooks_by_phase should return hooks for specific phase."""
        from flow_definitions import get_hooks_by_phase

        hooks = get_hooks_by_phase("session_start")
        hook_names = [h.hook_name for h in hooks]
        assert "date-context-injector" in hook_names

    def test_get_hooks_by_trigger_type(self):
        """get_hooks_by_trigger_type should return hooks by type."""
        from flow_definitions import get_hooks_by_trigger_type

        stop_hooks = get_hooks_by_trigger_type("Stop")
        assert len(stop_hooks) > 0
        for hook in stop_hooks:
            assert hook.trigger_type == "Stop"

    def test_all_phases_have_at_least_one_hook(self):
        """Each phase should have at least one hook defined."""
        from flow_definitions import DEVELOPMENT_PHASES, get_hooks_by_phase

        for phase in DEVELOPMENT_PHASES:
            hooks = get_hooks_by_phase(phase.id)
            assert len(hooks) > 0, f"Phase {phase.id} should have at least one hook"


class TestHookBehaviorConsistency:
    """Tests for consistency between DEVELOPMENT_PHASES and EXPECTED_HOOK_BEHAVIORS."""

    def test_phase_expected_hooks_are_defined(self):
        """All hooks in phase.expected_hooks should be in EXPECTED_HOOK_BEHAVIORS."""
        from flow_definitions import DEVELOPMENT_PHASES, EXPECTED_HOOK_BEHAVIORS

        for phase in DEVELOPMENT_PHASES:
            for hook_name in phase.expected_hooks:
                assert hook_name in EXPECTED_HOOK_BEHAVIORS, (
                    f"Hook {hook_name} in phase {phase.id} should be defined"
                )

    def test_hook_phase_id_matches_phase(self):
        """Hook's phase_id should match the phase that includes it."""
        from flow_definitions import DEVELOPMENT_PHASES, EXPECTED_HOOK_BEHAVIORS

        for phase in DEVELOPMENT_PHASES:
            for hook_name in phase.expected_hooks:
                behavior = EXPECTED_HOOK_BEHAVIORS.get(hook_name)
                if behavior:
                    assert behavior.phase_id == phase.id, (
                        f"Hook {hook_name} should have phase_id={phase.id}"
                    )
