"""Tests for reflection-log-collector.py hook."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add hooks directory to path
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))
spec = importlib.util.spec_from_file_location(
    "reflection_log_collector", HOOKS_DIR / "reflection-log-collector.py"
)
reflection_log_collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reflection_log_collector)


class TestGetBlockSummary:
    """Tests for get_block_summary function."""

    def test_no_log_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Should return zero counts if log file doesn't exist."""
        monkeypatch.setattr(reflection_log_collector, "EXECUTION_LOG_DIR", tmp_path)
        result = reflection_log_collector.get_block_summary("test-session")
        assert result == {"block_count": 0, "blocks_by_hook": {}}

    def test_empty_log_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Should return zero counts for empty log file."""
        monkeypatch.setattr(reflection_log_collector, "EXECUTION_LOG_DIR", tmp_path)
        (tmp_path / "hook-errors.log").write_text("")
        result = reflection_log_collector.get_block_summary("test-session")
        assert result == {"block_count": 0, "blocks_by_hook": {}}

    def test_counts_blocks_for_session(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Should count blocks only for the specified session."""
        monkeypatch.setattr(reflection_log_collector, "EXECUTION_LOG_DIR", tmp_path)
        log_entries = [
            {"session_id": "session-1", "hook": "hook-a"},
            {"session_id": "session-1", "hook": "hook-a"},
            {"session_id": "session-1", "hook": "hook-b"},
            {"session_id": "session-2", "hook": "hook-a"},  # Different session
        ]
        (tmp_path / "hook-errors.log").write_text("\n".join(json.dumps(e) for e in log_entries))

        result = reflection_log_collector.get_block_summary("session-1")
        assert result["block_count"] == 3
        assert result["blocks_by_hook"] == {"hook-a": 2, "hook-b": 1}

    def test_handles_malformed_json(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Should skip malformed JSON lines."""
        monkeypatch.setattr(reflection_log_collector, "EXECUTION_LOG_DIR", tmp_path)
        log_content = (
            '{"session_id": "session-1", "hook": "hook-a"}\n'
            "not valid json\n"
            '{"session_id": "session-1", "hook": "hook-b"}\n'
        )
        (tmp_path / "hook-errors.log").write_text(log_content)

        result = reflection_log_collector.get_block_summary("session-1")
        assert result["block_count"] == 2


class TestGetFlowStatus:
    """Tests for get_flow_status function."""

    def test_no_state_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Should return no_state_file status if file doesn't exist."""
        monkeypatch.setattr(reflection_log_collector, "FLOW_LOG_DIR", tmp_path)
        result = reflection_log_collector.get_flow_status("test-session")
        assert result == {"status": "no_state_file"}

    def test_reads_flow_state(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Should read and return flow state."""
        monkeypatch.setattr(reflection_log_collector, "FLOW_LOG_DIR", tmp_path)
        state = {
            "workflows": {
                "main": {
                    "current_phase": "implementation",
                    "phase_history": [
                        {"phase": "planning"},
                        {"phase": "implementation"},
                    ],
                }
            }
        }
        (tmp_path / "state-test-session.json").write_text(json.dumps(state))

        result = reflection_log_collector.get_flow_status("test-session")
        assert result["status"] == "found"
        assert result["current_phase"] == "implementation"
        assert result["phase_history_count"] == 2

    def test_handles_invalid_json(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Should return error status for invalid JSON."""
        monkeypatch.setattr(reflection_log_collector, "FLOW_LOG_DIR", tmp_path)
        (tmp_path / "state-test-session.json").write_text("not valid json")

        result = reflection_log_collector.get_flow_status("test-session")
        assert result == {"status": "error"}


class TestCheckRecurringProblems:
    """Tests for check_recurring_problems function."""

    def test_no_log_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Should return empty list if log file doesn't exist."""
        monkeypatch.setattr(reflection_log_collector, "EXECUTION_LOG_DIR", tmp_path)
        result = reflection_log_collector.check_recurring_problems("test-session")
        assert result == []

    def test_finds_recurring_problems(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Should find recurring problems for the session."""
        monkeypatch.setattr(reflection_log_collector, "EXECUTION_LOG_DIR", tmp_path)
        log_entries = [
            {
                "session_id": "session-1",
                "hook": "recurring-problem-block",
                "details": {
                    "blocking_problems": [
                        {"source": "Issue #1234"},
                        {"source": "Issue #5678"},
                    ]
                },
            },
            {
                "session_id": "session-1",
                "hook": "recurring-problem-block",
                "details": {
                    "blocking_problems": [
                        {"source": "Issue #1234"},  # Duplicate
                    ]
                },
            },
            {
                "session_id": "session-2",  # Different session
                "hook": "recurring-problem-block",
                "details": {"blocking_problems": [{"source": "Issue #9999"}]},
            },
        ]
        (tmp_path / "hook-errors.log").write_text("\n".join(json.dumps(e) for e in log_entries))

        result = reflection_log_collector.check_recurring_problems("session-1")
        assert set(result) == {"Issue #1234", "Issue #5678"}


class TestFormatLogSummary:
    """Tests for format_log_summary function."""

    def test_formats_zero_blocks(self):
        """Should format message for zero blocks."""
        block_summary = {"block_count": 0, "blocks_by_hook": {}}
        flow_status = {"status": "no_state_file"}
        recurring = []

        result = reflection_log_collector.format_log_summary(block_summary, flow_status, recurring)
        assert "ブロック**: 0件" in result
        assert "セッションログ自動集計" in result

    def test_formats_blocks_with_breakdown(self):
        """Should format message with block breakdown."""
        block_summary = {
            "block_count": 10,
            "blocks_by_hook": {
                "hook-a": 5,
                "hook-b": 3,
                "hook-c": 2,
            },
        }
        flow_status = {"status": "no_state_file"}
        recurring = []

        result = reflection_log_collector.format_log_summary(block_summary, flow_status, recurring)
        assert "ブロック**: 10件" in result
        assert "hook-a: 5" in result

    def test_includes_recurring_problems(self):
        """Should include recurring problems."""
        block_summary = {"block_count": 0, "blocks_by_hook": {}}
        flow_status = {"status": "no_state_file"}
        recurring = ["Issue #1234", "Issue #5678"]

        result = reflection_log_collector.format_log_summary(block_summary, flow_status, recurring)
        assert "recurring-problem-block検出" in result
        assert "Issue #1234" in result
        assert "Issue #5678" in result

    def test_includes_flow_status(self):
        """Should include flow status when found."""
        block_summary = {"block_count": 0, "blocks_by_hook": {}}
        flow_status = {
            "status": "found",
            "current_phase": "implementation",
        }
        recurring = []

        result = reflection_log_collector.format_log_summary(block_summary, flow_status, recurring)
        assert "現在フェーズ**: implementation" in result


class TestMainFunction:
    """Tests for main function."""

    def test_skips_non_skill_tools(self, capsys: pytest.CaptureFixture):
        """Should return continue for non-Skill tools."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
        }
        with patch.object(reflection_log_collector, "parse_hook_input", return_value=input_data):
            reflection_log_collector.main()

        output = json.loads(capsys.readouterr().out)
        assert output == {"continue": True}

    def test_skips_non_reflect_skills(self, capsys: pytest.CaptureFixture):
        """Should return continue for non-reflect skills."""
        input_data = {
            "tool_name": "Skill",
            "tool_input": {"skill": "commit"},
        }
        with patch.object(reflection_log_collector, "parse_hook_input", return_value=input_data):
            reflection_log_collector.main()

        output = json.loads(capsys.readouterr().out)
        assert output == {"continue": True}

    def test_collects_logs_for_reflect_skill(
        self, capsys: pytest.CaptureFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Should collect logs for reflect skill."""
        monkeypatch.setattr(reflection_log_collector, "EXECUTION_LOG_DIR", tmp_path)
        monkeypatch.setattr(reflection_log_collector, "FLOW_LOG_DIR", tmp_path)

        # Create test log data
        log_entries = [
            {"session_id": "test-session", "hook": "hook-a"},
        ]
        (tmp_path / "hook-errors.log").write_text("\n".join(json.dumps(e) for e in log_entries))

        input_data = {
            "tool_name": "Skill",
            "tool_input": {"skill": "reflect"},
        }
        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = "test-session"
        with patch.object(reflection_log_collector, "parse_hook_input", return_value=input_data):
            with patch.object(
                reflection_log_collector,
                "create_hook_context",
                return_value=mock_ctx,
            ):
                with patch.object(reflection_log_collector, "log_hook_execution"):
                    reflection_log_collector.main()

        output = json.loads(capsys.readouterr().out)
        assert output["continue"] is True
        assert "systemMessage" in output
        assert "セッションログ自動集計" in output["systemMessage"]

    def test_handles_reflection_skill_variant(
        self, capsys: pytest.CaptureFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Should also handle 'reflection' skill name."""
        monkeypatch.setattr(reflection_log_collector, "EXECUTION_LOG_DIR", tmp_path)
        monkeypatch.setattr(reflection_log_collector, "FLOW_LOG_DIR", tmp_path)

        input_data = {
            "tool_name": "Skill",
            "tool_input": {"skill": "reflection"},
        }
        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = "test-session"
        with patch.object(reflection_log_collector, "parse_hook_input", return_value=input_data):
            with patch.object(
                reflection_log_collector,
                "create_hook_context",
                return_value=mock_ctx,
            ):
                with patch.object(reflection_log_collector, "log_hook_execution"):
                    reflection_log_collector.main()

        output = json.loads(capsys.readouterr().out)
        assert output["continue"] is True
        assert "systemMessage" in output
