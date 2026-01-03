#!/usr/bin/env python3
"""Tests for flow management functions in common.py."""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

# Add parent directory to path for common module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)


class TestFlowManagement:
    """Tests for flow management functions."""

    def setup_method(self):
        """Set up test fixtures."""
        # Create temp directories for flow definitions and logs
        self.temp_dir = tempfile.mkdtemp()
        self.logs_dir = Path(self.temp_dir) / ".claude" / "logs" / "flows"
        self.logs_dir.mkdir(parents=True)

        # Mock flow definition for test-flow
        from flow_definitions import FlowDefinition, FlowStep

        class TestFlow(FlowDefinition):
            def __init__(self):
                super().__init__(
                    id="test-flow",
                    name="Test Flow",
                    steps=[
                        FlowStep(id="step1", name="Step 1", order=0),
                        FlowStep(id="step2", name="Step 2", order=1),
                    ],
                    blocking_on_session_end=True,
                )

            def matches_step(self, step_id, command, context):
                return False

        self.test_flow = TestFlow()
        self.mock_registry = {"test-flow": self.test_flow}

    def teardown_method(self):
        """Clean up temp directories."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("lib.flow.get_current_branch")
    def test_start_flow_creates_entry(self, mock_branch):
        """start_flow should create a flow entry in the log."""
        from common import start_flow

        mock_branch.return_value = "main"

        with (
            patch("flow_definitions.FLOW_REGISTRY", self.mock_registry),
            patch("common.FLOW_LOG_DIR", self.logs_dir),
        ):
            instance_id = start_flow("test-flow", {"issue": 123}, session_id="test-session")

        assert instance_id is not None

        # Check log was created (Issue #1840: session-specific file)
        log_file = self.logs_dir / "flow-progress-test-session.jsonl"
        assert log_file.exists()

        with open(log_file) as f:
            entry = json.loads(f.readline())

        assert entry["event"] == "flow_started"
        assert entry["flow_id"] == "test-flow"
        assert entry["expected_steps"] == ["step1", "step2"]
        assert entry["context"] == {"issue": 123}

    def test_start_flow_unknown_flow_returns_none(self):
        """start_flow should return None for unknown flow."""
        from common import start_flow

        with patch("flow_definitions.FLOW_REGISTRY", self.mock_registry):
            result = start_flow("unknown-flow", {})

        assert result is None

    def test_complete_flow_step_logs_completion(self):
        """complete_flow_step should log step completion."""
        from common import complete_flow_step

        with patch("common.FLOW_LOG_DIR", self.logs_dir):
            result = complete_flow_step("instance-123", "step1", session_id="test-session")

        assert result

        log_file = self.logs_dir / "flow-progress-test-session.jsonl"
        with open(log_file) as f:
            entry = json.loads(f.readline())

        assert entry["event"] == "step_completed"
        assert entry["flow_instance_id"] == "instance-123"
        assert entry["step_id"] == "step1"
        assert entry["flow_id"] is None  # flow_id not provided

    def test_complete_flow_step_with_flow_id(self):
        """complete_flow_step should log flow_id when provided."""
        from common import complete_flow_step

        with patch("common.FLOW_LOG_DIR", self.logs_dir):
            result = complete_flow_step(
                "instance-456", "step2", "development-workflow", session_id="test-session"
            )

        assert result

        log_file = self.logs_dir / "flow-progress-test-session.jsonl"
        with open(log_file) as f:
            entry = json.loads(f.readline())

        assert entry["event"] == "step_completed"
        assert entry["flow_instance_id"] == "instance-456"
        assert entry["step_id"] == "step2"
        assert entry["flow_id"] == "development-workflow"

    def test_get_flow_status_returns_correct_status(self):
        """get_flow_status should return correct flow status."""
        from common import get_flow_status

        # Create test log with flow start and step completion
        log_file = self.logs_dir / "flow-progress-test-session.jsonl"
        with open(log_file, "w") as f:
            f.write(
                json.dumps(
                    {
                        "event": "flow_started",
                        "flow_instance_id": "test-instance",
                        "flow_id": "test-flow",
                        "flow_name": "Test Flow",
                        "expected_steps": ["step1", "step2"],
                        "context": {},
                        "timestamp": "2025-12-20T10:00:00+00:00",
                    }
                )
                + "\n"
            )
            f.write(
                json.dumps(
                    {
                        "event": "step_completed",
                        "flow_instance_id": "test-instance",
                        "step_id": "step1",
                    }
                )
                + "\n"
            )

        with patch("common.FLOW_LOG_DIR", self.logs_dir):
            status = get_flow_status("test-instance", session_id="test-session")

        assert status is not None
        assert status["flow_id"] == "test-flow"
        assert status["completed_steps"] == ["step1"]
        assert status["pending_steps"] == ["step2"]
        assert not status["is_complete"]

    def test_get_flow_status_complete_flow(self):
        """get_flow_status should return is_complete=True when all steps done."""
        from common import get_flow_status

        log_file = self.logs_dir / "flow-progress-test-session.jsonl"
        with open(log_file, "w") as f:
            f.write(
                json.dumps(
                    {
                        "event": "flow_started",
                        "flow_instance_id": "test-instance",
                        "flow_id": "test-flow",
                        "flow_name": "Test Flow",
                        "expected_steps": ["step1"],
                        "context": {},
                        "timestamp": "2025-12-20T10:00:00+00:00",
                    }
                )
                + "\n"
            )
            f.write(
                json.dumps(
                    {
                        "event": "step_completed",
                        "flow_instance_id": "test-instance",
                        "step_id": "step1",
                    }
                )
                + "\n"
            )

        with patch("common.FLOW_LOG_DIR", self.logs_dir):
            status = get_flow_status("test-instance", session_id="test-session")

        assert status["is_complete"]

    def test_get_incomplete_flows_returns_only_current_session(self):
        """get_incomplete_flows should only return flows from current session.

        Issue #1840: Each session has its own file, so session isolation is now
        file-based. This test verifies that flows from other session files
        are not included.
        """
        from common import get_incomplete_flows

        # Create file for a different session (should be ignored)
        other_log_file = self.logs_dir / "flow-progress-other-session.jsonl"
        with open(other_log_file, "w") as f:
            f.write(
                json.dumps(
                    {
                        "event": "flow_started",
                        "session_id": "other-session",
                        "flow_instance_id": "other-instance",
                        "flow_id": "test-flow",
                        "flow_name": "Other Flow",
                        "expected_steps": ["step1"],
                        "context": {},
                    }
                )
                + "\n"
            )

        # Create file for current session
        log_file = self.logs_dir / "flow-progress-current-session.jsonl"
        with open(log_file, "w") as f:
            f.write(
                json.dumps(
                    {
                        "event": "flow_started",
                        "session_id": "current-session",
                        "flow_instance_id": "current-instance",
                        "flow_id": "test-flow",
                        "flow_name": "Current Flow",
                        "expected_steps": ["step1", "step2"],
                        "context": {},
                    }
                )
                + "\n"
            )

        with patch("common.FLOW_LOG_DIR", self.logs_dir):
            incomplete = get_incomplete_flows(session_id="current-session")

        assert len(incomplete) == 1
        assert incomplete[0]["flow_instance_id"] == "current-instance"

    def test_get_incomplete_flows_excludes_complete_flows(self):
        """get_incomplete_flows should exclude flows with all steps completed."""
        from common import get_incomplete_flows

        log_file = self.logs_dir / "flow-progress-test-session.jsonl"
        with open(log_file, "w") as f:
            # Complete flow
            f.write(
                json.dumps(
                    {
                        "event": "flow_started",
                        "session_id": "test-session",
                        "flow_instance_id": "complete-instance",
                        "flow_id": "test-flow",
                        "flow_name": "Complete Flow",
                        "expected_steps": ["step1"],
                        "context": {},
                    }
                )
                + "\n"
            )
            f.write(
                json.dumps(
                    {
                        "event": "step_completed",
                        "session_id": "test-session",
                        "flow_instance_id": "complete-instance",
                        "step_id": "step1",
                    }
                )
                + "\n"
            )
            # Incomplete flow
            f.write(
                json.dumps(
                    {
                        "event": "flow_started",
                        "session_id": "test-session",
                        "flow_instance_id": "incomplete-instance",
                        "flow_id": "test-flow",
                        "flow_name": "Incomplete Flow",
                        "expected_steps": ["step1", "step2"],
                        "context": {},
                    }
                )
                + "\n"
            )

        with patch("common.FLOW_LOG_DIR", self.logs_dir):
            incomplete = get_incomplete_flows(session_id="test-session")

        assert len(incomplete) == 1
        assert incomplete[0]["flow_instance_id"] == "incomplete-instance"


class TestCheckFlowCompletion:
    """Tests for check_flow_completion function."""

    def setup_method(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.logs_dir = Path(self.temp_dir) / "logs"
        self.logs_dir.mkdir(parents=True)

    def teardown_method(self):
        """Clean up temp directories."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_check_flow_completion_true(self):
        """check_flow_completion should return True when all steps complete."""
        from common import check_flow_completion

        log_file = self.logs_dir / "flow-progress-test-session.jsonl"
        with open(log_file, "w") as f:
            f.write(
                json.dumps(
                    {
                        "event": "flow_started",
                        "flow_instance_id": "test-instance",
                        "expected_steps": ["step1"],
                    }
                )
                + "\n"
            )
            f.write(
                json.dumps(
                    {
                        "event": "step_completed",
                        "flow_instance_id": "test-instance",
                        "step_id": "step1",
                    }
                )
                + "\n"
            )

        with patch("common.FLOW_LOG_DIR", self.logs_dir):
            result = check_flow_completion("test-instance", session_id="test-session")

        assert result

    def test_check_flow_completion_false(self):
        """check_flow_completion should return False when steps pending."""
        from common import check_flow_completion

        log_file = self.logs_dir / "flow-progress-test-session.jsonl"
        with open(log_file, "w") as f:
            f.write(
                json.dumps(
                    {
                        "event": "flow_started",
                        "flow_instance_id": "test-instance",
                        "expected_steps": ["step1", "step2"],
                    }
                )
                + "\n"
            )

        with patch("common.FLOW_LOG_DIR", self.logs_dir):
            result = check_flow_completion("test-instance", session_id="test-session")

        assert not result


class TestGetActiveFlowForContext:
    """Tests for get_active_flow_for_context function."""

    def setup_method(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.logs_dir = Path(self.temp_dir) / "logs" / "flows"
        self.logs_dir.mkdir(parents=True)

    def teardown_method(self):
        """Clean up temp directories."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_returns_existing_active_flow(self):
        """Should return existing flow ID when active flow exists for context."""
        from common import get_active_flow_for_context

        log_file = self.logs_dir / "flow-progress-test-session.jsonl"

        with open(log_file, "w") as f:
            f.write(
                json.dumps(
                    {
                        "event": "flow_started",
                        "session_id": "test-session",
                        "flow_id": "issue-ai-review",
                        "flow_instance_id": "existing-instance",
                        "expected_steps": ["step1", "step2"],
                        "context": {"issue_number": 123},
                    }
                )
                + "\n"
            )

        with patch("common.FLOW_LOG_DIR", self.logs_dir):
            result = get_active_flow_for_context(
                "issue-ai-review", {"issue_number": 123}, session_id="test-session"
            )

        assert result == "existing-instance"

    def test_returns_none_when_flow_complete(self):
        """Should return None when flow is already complete."""
        from common import get_active_flow_for_context

        log_file = self.logs_dir / "flow-progress-test-session.jsonl"

        with open(log_file, "w") as f:
            # Started flow
            f.write(
                json.dumps(
                    {
                        "event": "flow_started",
                        "session_id": "test-session",
                        "flow_id": "issue-ai-review",
                        "flow_instance_id": "complete-instance",
                        "expected_steps": ["step1"],
                        "context": {"issue_number": 123},
                    }
                )
                + "\n"
            )
            # Completed step
            f.write(
                json.dumps(
                    {
                        "event": "step_completed",
                        "session_id": "test-session",
                        "flow_instance_id": "complete-instance",
                        "step_id": "step1",
                    }
                )
                + "\n"
            )

        with patch("common.FLOW_LOG_DIR", self.logs_dir):
            result = get_active_flow_for_context(
                "issue-ai-review", {"issue_number": 123}, session_id="test-session"
            )

        assert result is None

    def test_returns_none_when_flow_explicitly_completed(self):
        """Should return None when flow has flow_completed event, even with pending steps."""
        from common import get_active_flow_for_context

        log_file = self.logs_dir / "flow-progress-test-session.jsonl"

        with open(log_file, "w") as f:
            # Flow with pending steps
            f.write(
                json.dumps(
                    {
                        "event": "flow_started",
                        "session_id": "test-session",
                        "flow_id": "issue-ai-review",
                        "flow_instance_id": "explicit-complete",
                        "expected_steps": ["step1", "step2", "step3"],
                        "context": {"issue_number": 123},
                    }
                )
                + "\n"
            )
            # Only step1 completed
            f.write(
                json.dumps(
                    {
                        "event": "step_completed",
                        "session_id": "test-session",
                        "flow_instance_id": "explicit-complete",
                        "step_id": "step1",
                    }
                )
                + "\n"
            )
            # But flow is explicitly completed (via completion_step or manual complete)
            f.write(
                json.dumps(
                    {
                        "event": "flow_completed",
                        "session_id": "test-session",
                        "flow_instance_id": "explicit-complete",
                        "flow_id": "issue-ai-review",
                    }
                )
                + "\n"
            )

        with patch("common.FLOW_LOG_DIR", self.logs_dir):
            result = get_active_flow_for_context(
                "issue-ai-review", {"issue_number": 123}, session_id="test-session"
            )

        # Should return None because flow is explicitly completed
        assert result is None

    def test_returns_none_for_different_context(self):
        """Should return None when context doesn't match."""
        from common import get_active_flow_for_context

        log_file = self.logs_dir / "flow-progress-test-session.jsonl"

        with open(log_file, "w") as f:
            f.write(
                json.dumps(
                    {
                        "event": "flow_started",
                        "session_id": "test-session",
                        "flow_id": "issue-ai-review",
                        "flow_instance_id": "other-instance",
                        "expected_steps": ["step1"],
                        "context": {"issue_number": 456},  # Different issue
                    }
                )
                + "\n"
            )

        with patch("common.FLOW_LOG_DIR", self.logs_dir):
            result = get_active_flow_for_context(
                "issue-ai-review", {"issue_number": 123}, session_id="test-session"
            )

        assert result is None


class TestStartFlowDuplicatePrevention:
    """Tests for start_flow duplicate prevention."""

    def setup_method(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.logs_dir = Path(self.temp_dir) / "logs" / "flows"
        self.logs_dir.mkdir(parents=True)

        # Mock flow definition for test-flow
        from flow_definitions import FlowDefinition, FlowStep

        class TestFlow(FlowDefinition):
            def __init__(self):
                super().__init__(
                    id="test-flow",
                    name="Test Flow",
                    steps=[FlowStep(id="step1", name="Step 1", order=0)],
                    blocking_on_session_end=True,
                )

            def matches_step(self, step_id, command, context):
                return False

        self.test_flow = TestFlow()
        self.mock_registry = {"test-flow": self.test_flow}

    def teardown_method(self):
        """Clean up temp directories."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_returns_existing_id_for_duplicate(self):
        """start_flow should return existing ID when duplicate context."""
        from common import start_flow

        log_file = self.logs_dir / "flow-progress-test-session.jsonl"

        # Create existing active flow
        with open(log_file, "w") as f:
            f.write(
                json.dumps(
                    {
                        "event": "flow_started",
                        "session_id": "test-session",
                        "flow_id": "test-flow",
                        "flow_instance_id": "existing-123",
                        "expected_steps": ["step1"],
                        "context": {"issue_number": 123},
                    }
                )
                + "\n"
            )

        with (
            patch("flow_definitions.FLOW_REGISTRY", self.mock_registry),
            patch("common.FLOW_LOG_DIR", self.logs_dir),
        ):
            result = start_flow("test-flow", {"issue_number": 123}, session_id="test-session")

        # Should return existing ID, not create new
        assert result == "existing-123"

        # Verify no new log entry was written (still only 1 entry)
        with open(log_file) as f:
            entries = [json.loads(line) for line in f if line.strip()]
        assert len(entries) == 1
        assert entries[0]["flow_instance_id"] == "existing-123"

    @patch("lib.flow.get_current_branch")
    def test_creates_new_flow_for_different_context(self, mock_branch):
        """start_flow should create new flow for different context."""
        from common import start_flow

        mock_branch.return_value = "main"
        log_file = self.logs_dir / "flow-progress-test-session.jsonl"

        # Create existing flow for different issue
        with open(log_file, "w") as f:
            f.write(
                json.dumps(
                    {
                        "event": "flow_started",
                        "session_id": "test-session",
                        "flow_id": "test-flow",
                        "flow_instance_id": "existing-456",
                        "expected_steps": ["step1"],
                        "context": {"issue_number": 456},  # Different issue
                    }
                )
                + "\n"
            )

        with (
            patch("flow_definitions.FLOW_REGISTRY", self.mock_registry),
            patch("common.FLOW_LOG_DIR", self.logs_dir),
        ):
            result = start_flow("test-flow", {"issue_number": 123}, session_id="test-session")

        # Should create new flow (different ID)
        assert result is not None
        assert result != "existing-456"

    @patch("lib.flow.get_current_branch")
    def test_allows_duplicates_for_none_context(self, mock_branch):
        """start_flow should allow duplicates when context is None."""
        from common import start_flow

        mock_branch.return_value = "main"
        log_file = self.logs_dir / "flow-progress-test-session.jsonl"

        # Create existing flow with empty context
        with open(log_file, "w") as f:
            f.write(
                json.dumps(
                    {
                        "event": "flow_started",
                        "session_id": "test-session",
                        "flow_id": "test-flow",
                        "flow_instance_id": "existing-none",
                        "expected_steps": ["step1"],
                        "context": {},
                    }
                )
                + "\n"
            )

        with (
            patch("flow_definitions.FLOW_REGISTRY", self.mock_registry),
            patch("common.FLOW_LOG_DIR", self.logs_dir),
        ):
            # Call with None context - should create new flow, not return existing
            result = start_flow("test-flow", None, session_id="test-session")

        # Should create new flow (different ID) because None context allows duplicates
        assert result is not None
        assert result != "existing-none"

        # Verify a new entry was added (2 entries total)
        with open(log_file) as f:
            entries = [json.loads(line) for line in f if line.strip()]
        assert len(entries) == 2


# Issue #777: TestGetConversationId and TestConversationIdFiltering removed.
# Claude Code's session_id from hook JSON input is unique per conversation,
# so separate conversation_id handling was unnecessary.


class TestCompleteFlow:
    """Tests for complete_flow function (Issue #1159)."""

    def setup_method(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.logs_dir = Path(self.temp_dir) / "logs"
        self.logs_dir.mkdir(parents=True)

    def teardown_method(self):
        """Clean up temp directories."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_complete_flow_logs_completion(self):
        """complete_flow should log flow_completed event."""
        from common import complete_flow

        log_file = self.logs_dir / "flow-progress-test-session.jsonl"

        with patch("common.FLOW_LOG_DIR", self.logs_dir):
            result = complete_flow("instance-123", "test-flow", session_id="test-session")

        assert result is True

        with open(log_file) as f:
            entry = json.loads(f.readline())

        assert entry["event"] == "flow_completed"
        assert entry["flow_instance_id"] == "instance-123"
        assert entry["flow_id"] == "test-flow"
        assert entry["session_id"] == "test-session"

    def test_complete_flow_without_flow_id(self):
        """complete_flow should work without flow_id."""
        from common import complete_flow

        log_file = self.logs_dir / "flow-progress-test-session.jsonl"

        with patch("common.FLOW_LOG_DIR", self.logs_dir):
            result = complete_flow("instance-456", session_id="test-session")

        assert result is True

        with open(log_file) as f:
            entry = json.loads(f.readline())

        assert entry["event"] == "flow_completed"
        assert entry["flow_instance_id"] == "instance-456"
        assert "flow_id" not in entry


class TestAutoFlowCompletion:
    """Tests for automatic flow completion when all steps are done (Issue #1159)."""

    def setup_method(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.logs_dir = Path(self.temp_dir) / "logs"
        self.logs_dir.mkdir(parents=True)

    def teardown_method(self):
        """Clean up temp directories."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_complete_flow_step_triggers_flow_completion(self):
        """complete_flow_step should trigger flow_completed when all steps done."""
        from common import complete_flow_step

        log_file = self.logs_dir / "flow-progress-test-session.jsonl"

        # Create flow with one step remaining
        with open(log_file, "w") as f:
            f.write(
                json.dumps(
                    {
                        "event": "flow_started",
                        "session_id": "test-session",
                        "flow_instance_id": "auto-complete-test",
                        "flow_id": "simple-flow",
                        "flow_name": "Simple Flow",
                        "expected_steps": ["step1"],
                        "context": {},
                        "timestamp": "2025-12-27T10:00:00+00:00",
                    }
                )
                + "\n"
            )

        with patch("common.FLOW_LOG_DIR", self.logs_dir):
            # Complete the only step - should trigger flow completion
            result = complete_flow_step("auto-complete-test", "step1", session_id="test-session")

        assert result is True

        # Verify both step_completed and flow_completed events were logged
        with open(log_file) as f:
            entries = [json.loads(line) for line in f if line.strip()]

        events = [e["event"] for e in entries]
        assert "flow_started" in events
        assert "step_completed" in events
        assert "flow_completed" in events

        # Verify flow_completed entry
        flow_completed = next(e for e in entries if e["event"] == "flow_completed")
        assert flow_completed["flow_instance_id"] == "auto-complete-test"

    def test_complete_flow_step_no_completion_when_steps_remaining(self):
        """complete_flow_step should not trigger flow_completed when steps remain."""
        from common import complete_flow_step

        log_file = self.logs_dir / "flow-progress-test-session.jsonl"

        # Create flow with two steps
        with open(log_file, "w") as f:
            f.write(
                json.dumps(
                    {
                        "event": "flow_started",
                        "session_id": "test-session",
                        "flow_instance_id": "partial-test",
                        "flow_id": "multi-step-flow",
                        "flow_name": "Multi Step Flow",
                        "expected_steps": ["step1", "step2"],
                        "context": {},
                        "timestamp": "2025-12-27T10:00:00+00:00",
                    }
                )
                + "\n"
            )

        with patch("common.FLOW_LOG_DIR", self.logs_dir):
            # Complete only the first step
            result = complete_flow_step("partial-test", "step1", session_id="test-session")

        assert result is True

        # Verify only step_completed was logged, not flow_completed
        with open(log_file) as f:
            entries = [json.loads(line) for line in f if line.strip()]

        events = [e["event"] for e in entries]
        assert "step_completed" in events
        assert "flow_completed" not in events


class TestFlowCompletedEventParsing:
    """Tests for parsing flow_completed events in status functions (Issue #1159)."""

    def setup_method(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.logs_dir = Path(self.temp_dir) / "logs"
        self.logs_dir.mkdir(parents=True)

    def teardown_method(self):
        """Clean up temp directories."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_get_flow_status_respects_flow_completed(self):
        """get_flow_status should return is_complete=True when flow_completed exists."""
        from common import get_flow_status

        log_file = self.logs_dir / "flow-progress-test-session.jsonl"
        # Create flow with pending steps but has flow_completed event
        with open(log_file, "w") as f:
            f.write(
                json.dumps(
                    {
                        "event": "flow_started",
                        "flow_instance_id": "test-instance",
                        "flow_id": "test-flow",
                        "flow_name": "Test Flow",
                        "expected_steps": ["step1", "step2"],
                        "context": {},
                        "timestamp": "2025-12-27T10:00:00+00:00",
                    }
                )
                + "\n"
            )
            # Only step1 completed, step2 still pending
            f.write(
                json.dumps(
                    {
                        "event": "step_completed",
                        "flow_instance_id": "test-instance",
                        "step_id": "step1",
                    }
                )
                + "\n"
            )
            # But flow was explicitly marked complete
            f.write(
                json.dumps(
                    {
                        "event": "flow_completed",
                        "flow_instance_id": "test-instance",
                        "flow_id": "test-flow",
                    }
                )
                + "\n"
            )

        with patch("common.FLOW_LOG_DIR", self.logs_dir):
            status = get_flow_status("test-instance", session_id="test-session")

        assert status is not None
        # Even though step2 is pending, flow should be complete due to flow_completed event
        assert status["is_complete"] is True
        assert status["pending_steps"] == ["step2"]

    def test_get_incomplete_flows_excludes_flow_completed(self):
        """get_incomplete_flows should exclude flows with flow_completed events."""
        from common import get_incomplete_flows

        log_file = self.logs_dir / "flow-progress-test-session.jsonl"

        with open(log_file, "w") as f:
            # Flow 1: Has flow_completed, should be excluded
            f.write(
                json.dumps(
                    {
                        "event": "flow_started",
                        "session_id": "test-session",
                        "flow_instance_id": "completed-flow",
                        "flow_id": "test-flow",
                        "flow_name": "Completed Flow",
                        "expected_steps": ["step1", "step2"],
                        "context": {},
                    }
                )
                + "\n"
            )
            f.write(
                json.dumps(
                    {
                        "event": "flow_completed",
                        "session_id": "test-session",
                        "flow_instance_id": "completed-flow",
                    }
                )
                + "\n"
            )
            # Flow 2: No flow_completed, should be included
            f.write(
                json.dumps(
                    {
                        "event": "flow_started",
                        "session_id": "test-session",
                        "flow_instance_id": "incomplete-flow",
                        "flow_id": "test-flow",
                        "flow_name": "Incomplete Flow",
                        "expected_steps": ["step1"],
                        "context": {},
                    }
                )
                + "\n"
            )

        with patch("common.FLOW_LOG_DIR", self.logs_dir):
            incomplete = get_incomplete_flows(session_id="test-session")

        # Only incomplete-flow should be returned
        assert len(incomplete) == 1
        assert incomplete[0]["flow_instance_id"] == "incomplete-flow"


class TestDuplicateFlowCompletedPrevention:
    """Tests for preventing duplicate flow_completed events (Issue #1159)."""

    def setup_method(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.logs_dir = Path(self.temp_dir) / "logs"
        self.logs_dir.mkdir(parents=True)

    def teardown_method(self):
        """Clean up temp directories."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_complete_flow_step_on_already_completed_flow_no_duplicate(self):
        """complete_flow_step should not create duplicate flow_completed when flow already completed."""
        from common import complete_flow_step

        log_file = self.logs_dir / "flow-progress-test-session.jsonl"

        # Create a flow that's already completed
        with open(log_file, "w") as f:
            f.write(
                json.dumps(
                    {
                        "event": "flow_started",
                        "session_id": "test-session",
                        "flow_instance_id": "already-completed",
                        "flow_id": "test-flow",
                        "flow_name": "Test Flow",
                        "expected_steps": ["step1", "step2"],
                        "context": {},
                        "timestamp": "2025-12-27T10:00:00+00:00",
                    }
                )
                + "\n"
            )
            f.write(
                json.dumps(
                    {
                        "event": "step_completed",
                        "session_id": "test-session",
                        "flow_instance_id": "already-completed",
                        "step_id": "step1",
                    }
                )
                + "\n"
            )
            # Flow already has flow_completed event
            f.write(
                json.dumps(
                    {
                        "event": "flow_completed",
                        "session_id": "test-session",
                        "flow_instance_id": "already-completed",
                        "flow_id": "test-flow",
                    }
                )
                + "\n"
            )

        with patch("common.FLOW_LOG_DIR", self.logs_dir):
            # Complete another step on already completed flow
            result = complete_flow_step("already-completed", "step2", session_id="test-session")

        assert result is True  # Step completion succeeded

        # Verify only ONE flow_completed event exists (no duplicate)
        with open(log_file) as f:
            entries = [json.loads(line) for line in f if line.strip()]

        flow_completed_events = [e for e in entries if e["event"] == "flow_completed"]
        assert len(flow_completed_events) == 1

    def test_has_flow_completed_in_status(self):
        """get_flow_status should return has_flow_completed flag."""
        from common import get_flow_status

        log_file = self.logs_dir / "flow-progress-test-session.jsonl"

        # Create a flow with flow_completed event
        with open(log_file, "w") as f:
            f.write(
                json.dumps(
                    {
                        "event": "flow_started",
                        "flow_instance_id": "test-instance",
                        "flow_id": "test-flow",
                        "flow_name": "Test Flow",
                        "expected_steps": ["step1"],
                        "context": {},
                    }
                )
                + "\n"
            )
            f.write(
                json.dumps(
                    {
                        "event": "flow_completed",
                        "flow_instance_id": "test-instance",
                    }
                )
                + "\n"
            )

        with patch("common.FLOW_LOG_DIR", self.logs_dir):
            status = get_flow_status("test-instance", session_id="test-session")

        assert status is not None
        assert status["has_flow_completed"] is True

    def test_has_flow_completed_false_when_not_completed(self):
        """get_flow_status should return has_flow_completed=False when no flow_completed event."""
        from common import get_flow_status

        log_file = self.logs_dir / "flow-progress-test-session.jsonl"

        # Create a flow without flow_completed event
        with open(log_file, "w") as f:
            f.write(
                json.dumps(
                    {
                        "event": "flow_started",
                        "flow_instance_id": "test-instance",
                        "flow_id": "test-flow",
                        "flow_name": "Test Flow",
                        "expected_steps": ["step1"],
                        "context": {},
                    }
                )
                + "\n"
            )

        with patch("common.FLOW_LOG_DIR", self.logs_dir):
            status = get_flow_status("test-instance", session_id="test-session")

        assert status is not None
        assert status["has_flow_completed"] is False


class TestCompletionStepLogic:
    """Tests for completion_step logic in _check_and_complete_flow (Issue #1159)."""

    def setup_method(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.logs_dir = Path(self.temp_dir) / "logs"
        self.logs_dir.mkdir(parents=True)

    def teardown_method(self):
        """Clean up temp directories."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_completion_step_triggers_flow_complete(self):
        """Completing completion_step should trigger flow_completed even with pending steps."""
        from common import complete_flow_step

        log_file = self.logs_dir / "flow-progress-test-session.jsonl"

        # Create a development-workflow flow (completion_step="merged")
        # with pending steps after merged
        with open(log_file, "w") as f:
            f.write(
                json.dumps(
                    {
                        "event": "flow_started",
                        "session_id": "test-session",
                        "flow_instance_id": "completion-step-test",
                        "flow_id": "development-workflow",
                        "flow_name": "開発ワークフロー",
                        "expected_steps": [
                            "worktree_created",
                            "implementation",
                            "committed",
                            "pushed",
                            "pr_created",
                            "ci_passed",
                            "merged",
                            "cleaned_up",  # Optional step after completion
                        ],
                        "context": {},
                        "timestamp": "2025-12-27T10:00:00+00:00",
                    }
                )
                + "\n"
            )

        with patch("common.FLOW_LOG_DIR", self.logs_dir):
            # Complete the completion_step (merged)
            result = complete_flow_step("completion-step-test", "merged", session_id="test-session")

        assert result is True

        # Verify flow_completed was recorded
        with open(log_file) as f:
            entries = [json.loads(line) for line in f if line.strip()]

        events = [e["event"] for e in entries]
        assert "flow_completed" in events

    def test_non_completion_step_does_not_trigger_flow_complete(self):
        """Completing a non-completion_step should not trigger flow_completed."""
        from common import complete_flow_step

        log_file = self.logs_dir / "flow-progress-test-session.jsonl"

        # Create a development-workflow flow (completion_step="merged")
        with open(log_file, "w") as f:
            f.write(
                json.dumps(
                    {
                        "event": "flow_started",
                        "session_id": "test-session",
                        "flow_instance_id": "non-completion-test",
                        "flow_id": "development-workflow",
                        "flow_name": "開発ワークフロー",
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
                        "context": {},
                        "timestamp": "2025-12-27T10:00:00+00:00",
                    }
                )
                + "\n"
            )

        with patch("common.FLOW_LOG_DIR", self.logs_dir):
            # Complete a non-completion step (pushed)
            result = complete_flow_step("non-completion-test", "pushed", session_id="test-session")

        assert result is True

        # Verify flow_completed was NOT recorded
        with open(log_file) as f:
            entries = [json.loads(line) for line in f if line.strip()]

        events = [e["event"] for e in entries]
        assert "flow_completed" not in events
