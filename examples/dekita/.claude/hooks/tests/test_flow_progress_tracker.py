#!/usr/bin/env python3
"""Tests for flow-progress-tracker.py hook."""

import json
import sys
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

# Add parent directory to path for module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)


class TestFlowProgressTracker:
    """Tests for flow progress tracker hook."""

    def setup_method(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.logs_dir = Path(self.temp_dir) / "logs" / "flows"
        self.logs_dir.mkdir(parents=True)

    def teardown_method(self):
        """Clean up temp directories."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_pattern_matching_moved_to_flow_definitions(self):
        """Pattern matching is now in flow_definitions.py, tested in test_flow_definitions.py."""
        # This test confirms the refactoring: check_command_matches_step was removed
        # and pattern matching is now done via flow_definitions.matches_step()
        # See test_flow_definitions.py for comprehensive pattern matching tests
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "flow_progress_tracker",
            Path(__file__).parent.parent / "flow-progress-tracker.py",
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules["flow_progress_tracker"] = module
        spec.loader.exec_module(module)

        # Verify the old function no longer exists
        assert not hasattr(module, "check_command_matches_step")

    @patch("sys.stdin", new_callable=StringIO)
    def test_ignores_non_bash_tools(self, mock_stdin):
        """Hook should silently exit for non-Bash tools."""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "flow_progress_tracker",
            Path(__file__).parent.parent / "flow-progress-tracker.py",
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules["flow_progress_tracker"] = module
        spec.loader.exec_module(module)

        mock_stdin.write(json.dumps({"tool_name": "Edit", "tool_input": {}}))
        mock_stdin.seek(0)

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            module.main()
            output = mock_stdout.getvalue()

        # Should produce no output
        assert output == ""

    @patch("sys.stdin", new_callable=StringIO)
    def test_ignores_failed_commands(self, mock_stdin):
        """Hook should not mark steps complete for failed commands (non-zero exit_code)."""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "flow_progress_tracker",
            Path(__file__).parent.parent / "flow-progress-tracker.py",
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules["flow_progress_tracker"] = module
        spec.loader.exec_module(module)

        # Simulate a failed Bash command (exit_code != 0)
        mock_stdin.write(
            json.dumps(
                {
                    "tool_name": "Bash",
                    "tool_input": {"command": "gh issue view 123 --comments"},
                    "tool_result": {"exit_code": 1, "stdout": "", "stderr": "error"},
                }
            )
        )
        mock_stdin.seek(0)

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            module.main()
            output = mock_stdout.getvalue()

        # Should produce no output (step not marked complete due to failed command)
        assert output == ""


class TestFlowProgressTrackerIntegration:
    """Integration tests for flow progress tracker (#636)."""

    def setup_method(self):
        """Set up test fixtures with temp log file."""
        self.temp_dir = tempfile.mkdtemp()
        self.log_file = Path(self.temp_dir) / "flow-progress.jsonl"
        self.session_id = "test-session-123"

    def teardown_method(self):
        """Clean up temp directories."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_flow_log_entry(self, event: str, **kwargs) -> str:
        """Create a flow log entry as JSON line."""
        entry = {
            "timestamp": "2025-01-01T00:00:00+00:00",
            "session_id": self.session_id,
            "event": event,
            **kwargs,
        }
        return json.dumps(entry)

    def _start_flow(self, flow_id: str, instance_id: str, expected_steps: list, context: dict):
        """Write a flow_started entry to the log file."""
        entry = self._create_flow_log_entry(
            "flow_started",
            flow_id=flow_id,
            flow_instance_id=instance_id,
            flow_name=f"Test {flow_id}",
            expected_steps=expected_steps,
            context=context,
        )
        with open(self.log_file, "a") as f:
            f.write(entry + "\n")

    @patch("sys.stdin", new_callable=StringIO)
    def test_full_flow_step_completion(self, mock_stdin):
        """Integration: Flow start → matching command → step completion."""
        from unittest.mock import MagicMock

        # Create mock flow definition
        mock_step = MagicMock()
        mock_step.name = "レビュー確認"

        mock_flow_def = MagicMock()
        mock_flow_def.matches_step = MagicMock(
            side_effect=lambda step_id, cmd, ctx: (
                step_id == "review_viewed" and "gh issue view" in cmd and "--comments" in cmd
            )
        )
        mock_flow_def.get_step = MagicMock(return_value=mock_step)

        mock_flow_definitions = MagicMock()
        mock_flow_definitions.get_flow_definition = MagicMock(return_value=mock_flow_def)
        mock_flow_definitions.validate_step_order = MagicMock(return_value=(True, ""))

        # Start a flow
        self._start_flow(
            "issue-ai-review",
            "test-instance-1",
            ["review_viewed", "issue_updated"],
            {"issue_number": 123},
        )

        # Mock common module to use our temp file
        mock_common = MagicMock()
        mock_common.parse_hook_input = lambda: json.load(sys.stdin)
        mock_common.complete_flow_step = MagicMock(return_value=True)
        mock_common.get_incomplete_flows = MagicMock(
            return_value=[
                {
                    "flow_id": "issue-ai-review",
                    "flow_instance_id": "test-instance-1",
                    "pending_steps": ["review_viewed", "issue_updated"],
                    "completed_steps": [],
                    "context": {"issue_number": 123},
                }
            ]
        )

        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "flow_progress_tracker",
            Path(__file__).parent.parent / "flow-progress-tracker.py",
        )
        module = importlib.util.module_from_spec(spec)

        with patch.dict(
            "sys.modules",
            {"common": mock_common, "flow_definitions": mock_flow_definitions},
        ):
            spec.loader.exec_module(module)

        # Simulate matching Bash command
        mock_stdin.write(
            json.dumps(
                {
                    "tool_name": "Bash",
                    "tool_input": {"command": "gh issue view 123 --comments"},
                    "tool_result": {"exit_code": 0, "stdout": "Review comments..."},
                }
            )
        )
        mock_stdin.seek(0)

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            module.main()
            output = mock_stdout.getvalue()

        # Should output step completion notification
        result = json.loads(output)
        assert result.get("continue")
        # Check decoded JSON content (not raw unicode-escaped string)
        assert "ステップ完了" in result["systemMessage"]
        assert "レビュー確認" in result["systemMessage"]

    @patch("sys.stdin", new_callable=StringIO)
    def test_non_matching_command_no_step_completion(self, mock_stdin):
        """Integration: Command that doesn't match pattern produces no output."""
        from unittest.mock import MagicMock

        # Create mock flow definition that doesn't match
        mock_flow_def = MagicMock()
        mock_flow_def.matches_step = MagicMock(return_value=False)

        mock_flow_definitions = MagicMock()
        mock_flow_definitions.get_flow_definition = MagicMock(return_value=mock_flow_def)
        mock_flow_definitions.validate_step_order = MagicMock(return_value=(True, ""))

        mock_common = MagicMock()
        mock_common.parse_hook_input = lambda: json.load(sys.stdin)
        mock_common.get_incomplete_flows = MagicMock(
            return_value=[
                {
                    "flow_id": "issue-ai-review",
                    "flow_instance_id": "test-instance-1",
                    "pending_steps": ["review_viewed"],
                    "completed_steps": [],
                    "context": {"issue_number": 123},
                }
            ]
        )

        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "flow_progress_tracker",
            Path(__file__).parent.parent / "flow-progress-tracker.py",
        )
        module = importlib.util.module_from_spec(spec)

        with patch.dict(
            "sys.modules",
            {"common": mock_common, "flow_definitions": mock_flow_definitions},
        ):
            spec.loader.exec_module(module)

        # Simulate non-matching command
        mock_stdin.write(
            json.dumps(
                {
                    "tool_name": "Bash",
                    "tool_input": {"command": "ls -la"},  # Doesn't match any pattern
                    "tool_result": {"exit_code": 0, "stdout": "files..."},
                }
            )
        )
        mock_stdin.seek(0)

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            module.main()
            output = mock_stdout.getvalue()

        # Should produce no output
        assert output == ""

    @patch("sys.stdin", new_callable=StringIO)
    def test_multiple_steps_tracked(self, mock_stdin):
        """Integration: Multiple steps in a flow are tracked correctly."""
        from unittest.mock import MagicMock

        # Track which step is being matched
        step_matched = {"current": None}

        def mock_matches(step_id, cmd, ctx):
            if step_id == "step1" and "first_cmd" in cmd:
                step_matched["current"] = "step1"
                return True
            if step_id == "step2" and "second_cmd" in cmd:
                step_matched["current"] = "step2"
                return True
            return False

        mock_step1 = MagicMock()
        mock_step1.name = "Step 1"
        mock_step2 = MagicMock()
        mock_step2.name = "Step 2"

        mock_flow_def = MagicMock()
        mock_flow_def.matches_step = MagicMock(side_effect=mock_matches)
        mock_flow_def.get_step = MagicMock(
            side_effect=lambda sid: mock_step1 if sid == "step1" else mock_step2
        )

        mock_flow_definitions = MagicMock()
        mock_flow_definitions.get_flow_definition = MagicMock(return_value=mock_flow_def)
        mock_flow_definitions.validate_step_order = MagicMock(return_value=(True, ""))

        mock_common = MagicMock()
        mock_common.parse_hook_input = lambda: json.load(sys.stdin)
        mock_common.complete_flow_step = MagicMock(return_value=True)
        mock_common.get_incomplete_flows = MagicMock(
            return_value=[
                {
                    "flow_id": "multi-step-flow",
                    "flow_instance_id": "test-multi",
                    "pending_steps": ["step1", "step2"],
                    "completed_steps": [],
                    "context": {},
                }
            ]
        )

        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "flow_progress_tracker",
            Path(__file__).parent.parent / "flow-progress-tracker.py",
        )
        module = importlib.util.module_from_spec(spec)

        with patch.dict(
            "sys.modules",
            {"common": mock_common, "flow_definitions": mock_flow_definitions},
        ):
            spec.loader.exec_module(module)

        # First command matches step1
        mock_stdin.write(
            json.dumps(
                {
                    "tool_name": "Bash",
                    "tool_input": {"command": "first_cmd"},
                    "tool_result": {"exit_code": 0},
                }
            )
        )
        mock_stdin.seek(0)

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            module.main()
            output = mock_stdout.getvalue()

        result = json.loads(output)
        assert "Step 1" in result["systemMessage"]
        mock_common.complete_flow_step.assert_called_with("test-multi", "step1", "multi-step-flow")
