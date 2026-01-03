#!/usr/bin/env python3
"""Unit tests for task-start-checklist.py"""

import importlib.util
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Add parent directory to path for common module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

import common

# Import with dynamic loading due to hyphens in filename
HOOK_PATH = Path(__file__).parent.parent / "task-start-checklist.py"
_spec = importlib.util.spec_from_file_location("task_start_checklist", HOOK_PATH)
task_start_checklist = importlib.util.module_from_spec(_spec)
sys.modules["task_start_checklist"] = task_start_checklist
_spec.loader.exec_module(task_start_checklist)


class TestGetChecklistMessage:
    """Tests for get_checklist_message function."""

    def test_returns_non_empty_string(self):
        """Should return a non-empty string."""
        result = task_start_checklist.get_checklist_message()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_contains_checklist_header(self):
        """Should contain checklist header."""
        result = task_start_checklist.get_checklist_message()
        assert "タスク開始前の確認チェックリスト" in result

    def test_contains_session_file_section(self):
        """Should contain session file confirmation section (most important)."""
        result = task_start_checklist.get_checklist_message()
        assert "セッション開始時ファイル確認" in result
        assert "最重要" in result
        assert "読み込んだファイルの内容は" in result
        assert "タスクなら" in result

    def test_contains_requirements_section(self):
        """Should contain requirements confirmation section."""
        result = task_start_checklist.get_checklist_message()
        assert "要件確認" in result
        assert "要件は明確か" in result

    def test_contains_design_section(self):
        """Should contain design decision section."""
        result = task_start_checklist.get_checklist_message()
        assert "設計判断" in result
        assert "設計上の選択肢" in result

    def test_contains_impact_section(self):
        """Should contain impact scope section."""
        result = task_start_checklist.get_checklist_message()
        assert "影響範囲" in result
        assert "破壊的変更" in result

    def test_contains_prerequisites_section(self):
        """Should contain prerequisites section."""
        result = task_start_checklist.get_checklist_message()
        assert "前提条件" in result
        assert "環境" in result

    def test_contains_checkboxes(self):
        """Should contain checkbox markers."""
        result = task_start_checklist.get_checklist_message()
        # Should have multiple checkboxes (using [ ] format for consistency with docs)
        assert result.count("[ ]") >= 10

    def test_contains_emoji_icon(self):
        """Should contain emoji icons."""
        result = task_start_checklist.get_checklist_message()
        assert "📋" in result
        assert "💡" in result

    def test_contains_question_reminder(self):
        """Should contain reminder to ask questions."""
        result = task_start_checklist.get_checklist_message()
        assert "質問してください" in result


# Note: TestSessionMarkerPaths and TestCheckAndUpdateSessionMarker classes
# have been removed as they tested hook-specific session marker functions
# that are now consolidated in common.py.
# See test_common.py TestCheckAndUpdateSessionMarker for session marker tests.


class TestMain:
    """Integration tests for main() function."""

    def setup_method(self):
        """Create temporary directory for session markers."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        # Patch SESSION_DIR in common module (where check_and_update_session_marker uses it)
        self.session_dir_patcher = patch.object(common, "SESSION_DIR", self.temp_path)
        self.session_dir_patcher.start()

    def teardown_method(self):
        """Clean up temporary directory."""
        self.session_dir_patcher.stop()
        self.temp_dir.cleanup()

    def _run_main(self) -> dict:
        """Helper to run main() and capture output."""
        captured_output = io.StringIO()

        with (
            patch("sys.stdout", captured_output),
            patch("task_start_checklist.log_hook_execution"),
            pytest.raises(SystemExit) as ctx,
        ):
            task_start_checklist.main()

        assert ctx.value.code == 0
        return json.loads(captured_output.getvalue())

    def test_returns_approve_decision(self):
        """Should return approve decision."""
        result = self._run_main()
        assert result["decision"] == "approve"

    def test_outputs_valid_json(self):
        """Should output valid JSON."""
        captured_output = io.StringIO()

        with (
            patch("sys.stdout", captured_output),
            patch("task_start_checklist.log_hook_execution"),
            pytest.raises(SystemExit),
        ):
            task_start_checklist.main()

        output = captured_output.getvalue()
        # Should be valid JSON
        parsed = json.loads(output)
        assert isinstance(parsed, dict)

    def test_includes_system_message_on_new_session(self):
        """Should include systemMessage on new session."""
        result = self._run_main()
        assert "systemMessage" in result
        assert "タスク開始前の確認チェックリスト" in result["systemMessage"]

    def test_no_system_message_on_same_session(self):
        """Should not include systemMessage within same session."""
        # First call - new session
        result1 = self._run_main()
        assert "systemMessage" in result1

        # Second call - same session
        result2 = self._run_main()
        assert "systemMessage" not in result2

    def test_handles_exceptions_gracefully(self):
        """Should not block on errors."""
        with patch.object(
            task_start_checklist,
            "check_and_update_session_marker",
            side_effect=Exception("Test error"),
        ):
            result = self._run_main()

        # Should still return approve
        assert result["decision"] == "approve"
