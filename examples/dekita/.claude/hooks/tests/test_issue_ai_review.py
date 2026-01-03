#!/usr/bin/env python3
"""Unit tests for issue-ai-review.py"""

import importlib.util
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add parent directory to path for common module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# Import with dynamic loading due to hyphens in filename
HOOK_PATH = Path(__file__).parent.parent / "issue-ai-review.py"
_spec = importlib.util.spec_from_file_location("issue_ai_review", HOOK_PATH)
issue_ai_review = importlib.util.module_from_spec(_spec)
sys.modules["issue_ai_review"] = issue_ai_review
_spec.loader.exec_module(issue_ai_review)


class TestExtractIssueNumber:
    """Tests for extract_issue_number function."""

    def test_extracts_issue_number_from_github_url(self):
        """Should extract issue number from GitHub URL."""
        output = "https://github.com/owner/repo/issues/123\n"
        result = issue_ai_review.extract_issue_number(output)
        assert result == 123

    def test_extracts_from_multiline_output(self):
        """Should extract issue number from multiline output."""
        output = """Creating issue in owner/repo

https://github.com/owner/repo/issues/456
"""
        result = issue_ai_review.extract_issue_number(output)
        assert result == 456

    def test_returns_none_for_no_url(self):
        """Should return None when no GitHub URL found."""
        output = "Some random output without URL"
        result = issue_ai_review.extract_issue_number(output)
        assert result is None

    def test_returns_none_for_empty_string(self):
        """Should return None for empty string."""
        result = issue_ai_review.extract_issue_number("")
        assert result is None

    def test_handles_pr_url(self):
        """Should not match PR URLs."""
        output = "https://github.com/owner/repo/pull/789"
        result = issue_ai_review.extract_issue_number(output)
        assert result is None


class TestRunAiReview:
    """Tests for run_ai_review function."""

    def setup_method(self):
        """Create temporary directory for scripts."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.scripts_dir = self.temp_path / ".claude" / "scripts"
        self.scripts_dir.mkdir(parents=True, exist_ok=True)

    def teardown_method(self):
        """Clean up temporary directory."""
        self.temp_dir.cleanup()

    def test_logs_when_script_not_found(self):
        """Should log when review script is not found."""
        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(self.temp_path)}):
            with patch.object(issue_ai_review, "log_hook_execution") as mock_log:
                issue_ai_review.run_ai_review(123)
                mock_log.assert_called_once()
                args = mock_log.call_args[0]
                assert args[0] == "issue-ai-review"
                assert "not found" in args[2]

    def test_runs_review_script_synchronously(self):
        """Should run review script synchronously and return review content."""
        # Create dummy script
        script_path = self.scripts_dir / "issue-ai-review.sh"
        script_path.write_text("#!/bin/bash\necho test")
        script_path.chmod(0o755)

        mock_run_result = MagicMock()
        mock_run_result.returncode = 0
        mock_run_result.stdout = ""
        mock_run_result.stderr = ""

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(self.temp_path)}):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = mock_run_result
                with patch.object(
                    issue_ai_review, "fetch_ai_review_comment", return_value="Test review content"
                ) as mock_fetch:
                    with patch.object(issue_ai_review, "log_hook_execution") as mock_log:
                        result = issue_ai_review.run_ai_review(123)

                        # Verify subprocess.run was called (synchronous execution)
                        mock_run.assert_called_once()
                        call_args = mock_run.call_args
                        assert "123" in str(call_args[0][0])

                        # Verify fetch was called after script completed
                        mock_fetch.assert_called_once_with(123)

                        # Verify result contains review content
                        assert result == "Test review content"

                        # Verify success was logged
                        mock_log.assert_called()
                        log_args = mock_log.call_args[0]
                        assert "completed" in log_args[2]


class TestFetchAiReviewComment:
    """Tests for fetch_ai_review_comment function."""

    def test_returns_comment_body(self):
        """Should return the full comment body."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "# 🤖 AI Review\n\n- Suggestion 1\n- Suggestion 2"

        with patch("subprocess.run", return_value=mock_result):
            result = issue_ai_review.fetch_ai_review_comment(123)
            assert result == "# 🤖 AI Review\n\n- Suggestion 1\n- Suggestion 2"

    def test_returns_none_for_null_result(self):
        """Should return None when jq returns null."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "null"

        with patch("subprocess.run", return_value=mock_result):
            result = issue_ai_review.fetch_ai_review_comment(123)
            assert result is None

    def test_returns_none_for_empty_result(self):
        """Should return None when no comment found."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            result = issue_ai_review.fetch_ai_review_comment(123)
            assert result is None

    def test_returns_none_on_command_failure(self):
        """Should return None when gh command fails."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            result = issue_ai_review.fetch_ai_review_comment(123)
            assert result is None


class TestExtractEditSuggestions:
    """Tests for extract_edit_suggestions function."""

    def test_extracts_suggestions_after_keyword(self):
        """Should extract bullet points after 改善提案 keyword."""
        review_content = """レビュー結果

改善提案:
- ファイル名を追記してください
- 受け入れ条件を追加してください

その他のコメント"""
        result = issue_ai_review.extract_edit_suggestions(review_content)
        assert len(result) == 2
        assert "ファイル名を追記してください" in result[0]
        assert "受け入れ条件を追加してください" in result[1]

    def test_extracts_inline_suggestion(self):
        """Should extract suggestion after colon on same line."""
        review_content = "提案: 可能であれば、エラー例を追記すると分かりやすくなります。"
        result = issue_ai_review.extract_edit_suggestions(review_content)
        assert len(result) == 1
        assert "エラー例を追記" in result[0]

    def test_extracts_numbered_suggestions(self):
        """Should extract numbered list items."""
        review_content = """改善点
1. 再現手順を追加してください
2. 期待動作を明確に記載してください
3. ラベルを見直してください"""
        result = issue_ai_review.extract_edit_suggestions(review_content)
        assert len(result) == 3

    def test_stops_at_empty_line(self):
        """Should stop extracting at empty line after suggestions."""
        review_content = """改善提案:
- 提案1の内容を記載しています
- 提案2の内容を記載しています

次のセクション
- これは別のセクションです"""
        result = issue_ai_review.extract_edit_suggestions(review_content)
        assert len(result) == 2

    def test_limits_to_eight_suggestions(self):
        """Should limit suggestions to 8 items."""
        # Each suggestion must be > MIN_SUGGESTION_LENGTH (10 chars)
        lines = ["改善提案:"] + [f"- 提案{i}の詳細な内容です" for i in range(15)]
        review_content = "\n".join(lines)
        result = issue_ai_review.extract_edit_suggestions(review_content)
        assert len(result) <= 8

    def test_skips_short_suggestions(self):
        """Should skip suggestions shorter than 10 characters."""
        review_content = """改善提案:
- 短い内容
- これは十分に長い提案内容です"""
        result = issue_ai_review.extract_edit_suggestions(review_content)
        assert len(result) == 1
        assert "十分に長い提案" in result[0]

    def test_returns_empty_for_no_suggestions(self):
        """Should return empty list when no suggestions found."""
        review_content = "問題ありません。良いIssueです。"
        result = issue_ai_review.extract_edit_suggestions(review_content)
        assert result == []


class TestBuildReviewNotification:
    """Tests for build_review_notification function."""

    def test_includes_issue_number(self):
        """Should include issue number in notification."""
        review_content = "- Suggestion 1\n- Suggestion 2"
        result = issue_ai_review.build_review_notification(123, review_content)
        assert "#123" in result

    def test_includes_edit_suggestions(self):
        """Should include extracted edit suggestions."""
        review_content = """レビュー結果

改善提案:
- Add error handling for edge cases
- Improve documentation with examples"""
        result = issue_ai_review.build_review_notification(456, review_content)
        assert "error handling" in result
        assert "documentation" in result

    def test_limits_suggestions_to_five(self):
        """Should limit suggestions to 5 items in notification."""
        lines = ["改善提案:"] + [f"- Suggestion {i} with enough text here" for i in range(10)]
        review_content = "\n".join(lines)
        result = issue_ai_review.build_review_notification(789, review_content)
        # Count bullet points in result (suggestions are formatted as "- ...")
        suggestion_count = result.count("- Suggestion")
        assert suggestion_count <= 5

    def test_truncates_long_suggestions_with_ellipsis(self):
        """Should truncate suggestions over 150 characters with ellipsis."""
        # Create a review with a long suggestion after keyword
        long_content = "x" * 200
        review_content = f"改善提案:\n- {long_content}"
        result = issue_ai_review.build_review_notification(123, review_content)
        # Should be truncated with "..."
        assert "..." in result
        # Should not contain the full 200 x's
        assert "x" * 200 not in result

    def test_shows_default_message_when_no_suggestions(self):
        """Should show default message when no suggestions extracted."""
        review_content = "問題ありません。良いIssueです。"
        result = issue_ai_review.build_review_notification(111, review_content)
        assert "具体的な編集提案なし" in result


class TestMain:
    """Integration tests for main() function."""

    def _run_main_with_input(self, input_data: dict, review_content: str | None = None) -> dict:
        """Helper to run main() with given input and capture output."""
        captured_output = io.StringIO()
        input_json = json.dumps(input_data)

        with (
            patch("sys.stdin", io.StringIO(input_json)),
            patch("sys.stdout", captured_output),
            patch.object(issue_ai_review, "log_hook_execution"),
            patch.object(
                issue_ai_review, "run_ai_review", return_value=review_content
            ) as mock_review,
        ):
            issue_ai_review.main()
            return json.loads(captured_output.getvalue()), mock_review

    def test_ignores_non_bash_tool(self):
        """Should ignore non-Bash tools."""
        input_data = {
            "tool_name": "Edit",
            "tool_input": {"file_path": "/some/file.py"},
            "tool_result": {"stdout": "File edited", "exit_code": 0},
        }
        result, mock_review = self._run_main_with_input(input_data)
        assert result["continue"]
        mock_review.assert_not_called()

    def test_ignores_non_issue_create_command(self):
        """Should ignore non-issue-create commands."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr list"},
            "tool_result": {"stdout": "PR list output", "exit_code": 0},
        }
        result, mock_review = self._run_main_with_input(input_data)
        assert result["continue"]
        mock_review.assert_not_called()

    def test_triggers_review_on_successful_issue_create(self):
        """Should trigger AI review on successful gh issue create."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh issue create --title 'Test'"},
            "tool_result": {
                "stdout": "https://github.com/owner/repo/issues/999",
                "exit_code": 0,
            },
        }
        result, mock_review = self._run_main_with_input(input_data)
        assert result["continue"]
        mock_review.assert_called_once_with(999)

    def test_does_not_trigger_review_on_failed_command(self):
        """Should not trigger review when command fails."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh issue create --title 'Test'"},
            "tool_result": {
                "stdout": "https://github.com/owner/repo/issues/888",
                "exit_code": 1,
            },
        }
        result, mock_review = self._run_main_with_input(input_data)
        assert result["continue"]
        mock_review.assert_not_called()

    def test_handles_empty_output(self):
        """Should handle empty tool output gracefully."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh issue create --title 'Test'"},
            "tool_result": {"stdout": "", "exit_code": 0},
        }
        result, mock_review = self._run_main_with_input(input_data)
        assert result["continue"]
        mock_review.assert_not_called()

    def test_always_continues(self):
        """Should always return continue: true (non-blocking hook)."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh issue create --title 'Test'"},
            "tool_result": {
                "stdout": "https://github.com/owner/repo/issues/123",
                "exit_code": 0,
            },
        }
        result, _ = self._run_main_with_input(input_data)
        assert result["continue"]

    def test_includes_system_message_when_review_content_returned(self):
        """Should include systemMessage when review content is returned."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh issue create --title 'Test'"},
            "tool_result": {
                "stdout": "https://github.com/owner/repo/issues/100",
                "exit_code": 0,
            },
        }
        review_content = (
            "# 🤖 AI Review\n- Suggestion 1 with enough text\n- Suggestion 2 with enough text"
        )
        result, mock_review = self._run_main_with_input(input_data, review_content)
        assert result["continue"]
        assert "systemMessage" in result
        assert "#100" in result["systemMessage"]
        mock_review.assert_called_once_with(100)

    def test_no_system_message_when_no_review_content(self):
        """Should not include systemMessage when review returns None."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh issue create --title 'Test'"},
            "tool_result": {
                "stdout": "https://github.com/owner/repo/issues/200",
                "exit_code": 0,
            },
        }
        result, mock_review = self._run_main_with_input(input_data, None)
        assert result["continue"]
        assert "systemMessage" not in result
        mock_review.assert_called_once_with(200)
