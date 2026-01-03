#!/usr/bin/env python3
"""Unit tests for skill-failure-detector.py

Issue #2417: Automatic problem detection when Skill/reflect fails.
"""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

# Add parent directory to path for lib module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# Dynamic import for hyphenated module name
HOOK_PATH = Path(__file__).parent.parent / "skill-failure-detector.py"
_spec = importlib.util.spec_from_file_location("skill_failure_detector", HOOK_PATH)
skill_failure_detector = importlib.util.module_from_spec(_spec)
sys.modules["skill_failure_detector"] = skill_failure_detector
_spec.loader.exec_module(skill_failure_detector)

_is_skill_failure = skill_failure_detector._is_skill_failure


class TestIsSkillFailure:
    """Tests for _is_skill_failure function."""

    def test_detects_file_not_exist(self):
        """Should detect 'File does not exist' error."""
        tool_result = {"error": "File does not exist: /path/to/skill.md"}
        is_failure, reason = _is_skill_failure(tool_result)
        assert is_failure is True
        assert "ファイルが見つかりません" in reason

    def test_detects_directory_not_exist(self):
        """Should detect 'Directory does not exist' error."""
        tool_result = {"error": "Directory does not exist: /path/to/dir"}
        is_failure, reason = _is_skill_failure(tool_result)
        assert is_failure is True
        assert "ディレクトリが見つかりません" in reason

    def test_detects_tool_use_error(self):
        """Should detect 'tool_use_error' pattern."""
        tool_result = {"type": "tool_use_error", "message": "Something went wrong"}
        is_failure, reason = _is_skill_failure(tool_result)
        assert is_failure is True
        assert "ツール実行エラー" in reason

    def test_detects_error_reading_file(self):
        """Should detect 'error reading file' pattern."""
        tool_result = {"message": "error occurred while reading file"}
        is_failure, reason = _is_skill_failure(tool_result)
        assert is_failure is True
        assert "ファイル読み込みエラー" in reason

    def test_detects_no_such_file_or_directory(self):
        """Should detect 'No such file or directory' pattern."""
        tool_result = {"stderr": "No such file or directory"}
        is_failure, reason = _is_skill_failure(tool_result)
        assert is_failure is True
        assert "ファイル/ディレクトリが存在しません" in reason

    def test_returns_false_for_success(self):
        """Should return False for successful result."""
        tool_result = {"output": "Skill executed successfully"}
        is_failure, reason = _is_skill_failure(tool_result)
        assert is_failure is False
        assert reason == ""

    def test_returns_false_for_non_dict(self):
        """Should return False for non-dict input."""
        is_failure, reason = _is_skill_failure("string input")
        assert is_failure is False
        assert reason == ""

    def test_returns_false_for_none(self):
        """Should return False for None input."""
        is_failure, reason = _is_skill_failure(None)
        assert is_failure is False
        assert reason == ""

    def test_case_insensitive_detection(self):
        """Should detect patterns case-insensitively."""
        tool_result = {"error": "FILE DOES NOT EXIST: /path/to/file"}
        is_failure, reason = _is_skill_failure(tool_result)
        assert is_failure is True


class TestMainFunction:
    """Tests for main function."""

    def test_skips_non_skill_tool(self, capsys):
        """Should skip when tool is not Skill."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
        }
        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            skill_failure_detector.main()
            captured = capsys.readouterr()
            result = json.loads(captured.out)
            assert result.get("continue") is True
            assert "decision" not in result

    def test_approves_successful_skill(self, capsys):
        """Should approve when Skill succeeds."""
        input_data = {
            "tool_name": "Skill",
            "tool_input": {"skill": "reflect"},
            "tool_result": {"output": "Skill executed successfully"},
        }
        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            skill_failure_detector.main()
            captured = capsys.readouterr()
            result = json.loads(captured.out)
            assert result.get("continue") is True
            assert result.get("decision") != "block"

    def test_blocks_on_skill_failure(self, capsys):
        """Should block when Skill fails with file not found."""
        input_data = {
            "tool_name": "Skill",
            "tool_input": {"skill": "reflect"},
            "tool_result": {"error": "File does not exist: /path/to/execute.md"},
        }
        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            with patch.object(skill_failure_detector, "log_hook_execution"):
                skill_failure_detector.main()
                captured = capsys.readouterr()
                result = json.loads(captured.out)
                assert result.get("decision") == "block"
                assert result.get("continue") is True
                assert "reflect" in result.get("reason", "")
                assert "Issue" in result.get("reason", "")

    def test_handles_empty_input(self, capsys):
        """Should handle empty input gracefully."""
        with patch("sys.stdin.read", return_value="{}"):
            skill_failure_detector.main()
            captured = capsys.readouterr()
            result = json.loads(captured.out)
            assert result.get("continue") is True

    def test_handles_invalid_json(self, capsys):
        """Should handle invalid JSON gracefully."""
        with patch("sys.stdin.read", return_value="not valid json"):
            skill_failure_detector.main()
            captured = capsys.readouterr()
            result = json.loads(captured.out)
            assert result.get("continue") is True

    def test_handles_exception_gracefully(self, capsys):
        """Should handle exceptions and continue."""
        input_data = {
            "tool_name": "Skill",
            "tool_input": {"skill": "reflect"},
        }
        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            with patch.object(
                skill_failure_detector,
                "get_tool_result",
                side_effect=Exception("Unexpected error"),
            ):
                with patch.object(skill_failure_detector, "log_hook_execution"):
                    skill_failure_detector.main()
                    captured = capsys.readouterr()
                    result = json.loads(captured.out)
                    # Should still output valid JSON
                    assert isinstance(result, dict)
