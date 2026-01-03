"""Tests for problem-report-check.py hook."""

import importlib.util
import json
import sys
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

# Add hooks directory to path for common module
hooks_dir = Path(__file__).parent.parent
sys.path.insert(0, str(hooks_dir))

# Load module from file path (handles hyphenated filenames)
script_path = hooks_dir / "problem-report-check.py"
spec = importlib.util.spec_from_file_location("problem_report_check", script_path)
problem_report_check = importlib.util.module_from_spec(spec)
sys.modules["problem_report_check"] = problem_report_check
spec.loader.exec_module(problem_report_check)


class TestExtractClaudeMessages:
    """Tests for extract_claude_messages function."""

    def test_empty_transcript(self):
        """Empty transcript returns empty list."""
        result = problem_report_check.extract_claude_messages([])
        assert result == []

    def test_single_assistant_message(self):
        """Single assistant message with text block is extracted."""
        transcript = [
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "問題がありました"}],
            }
        ]
        result = problem_report_check.extract_claude_messages(transcript)
        assert result == ["問題がありました"]

    def test_user_messages_ignored(self):
        """User messages are not extracted."""
        transcript = [
            {"role": "user", "content": [{"type": "text", "text": "問題があります"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "承知しました"}]},
        ]
        result = problem_report_check.extract_claude_messages(transcript)
        assert result == ["承知しました"]

    def test_multiple_text_blocks(self):
        """Multiple text blocks in one message are all extracted."""
        transcript = [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "First part"},
                    {"type": "tool_use", "name": "Bash"},
                    {"type": "text", "text": "Second part"},
                ],
            }
        ]
        result = problem_report_check.extract_claude_messages(transcript)
        assert result == ["First part", "Second part"]

    def test_string_content(self):
        """String content (not list) is handled."""
        transcript = [{"role": "assistant", "content": "Direct string message"}]
        result = problem_report_check.extract_claude_messages(transcript)
        assert result == ["Direct string message"]


class TestExtractBashCommands:
    """Tests for extract_bash_commands function."""

    def test_empty_transcript(self):
        """Empty transcript returns empty list."""
        result = problem_report_check.extract_bash_commands([])
        assert result == []

    def test_bash_tool_use(self):
        """Bash tool use command is extracted."""
        transcript = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "input": {"command": "gh issue create --title test"},
                    }
                ],
            }
        ]
        result = problem_report_check.extract_bash_commands(transcript)
        assert result == ["gh issue create --title test"]

    def test_non_bash_tool_use_ignored(self):
        """Non-Bash tool uses are ignored."""
        transcript = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Read",
                        "input": {"file_path": "/tmp/test.txt"},
                    },
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "input": {"command": "ls -la"},
                    },
                ],
            }
        ]
        result = problem_report_check.extract_bash_commands(transcript)
        assert result == ["ls -la"]

    def test_multiple_bash_commands(self):
        """Multiple Bash commands are extracted."""
        transcript = [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "Bash", "input": {"command": "git status"}},
                ],
            },
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "Bash", "input": {"command": "gh issue create"}},
                ],
            },
        ]
        result = problem_report_check.extract_bash_commands(transcript)
        assert result == ["git status", "gh issue create"]


class TestIsFalsePositiveMatch:
    """Tests for is_false_positive_match function."""

    def test_no_false_positive(self):
        """Match without false positive context returns False."""
        msg = "バグを発見しました。修正します。"
        result = problem_report_check.is_false_positive_match(msg, 0, 7)
        assert not result

    def test_false_positive_resolved(self):
        """Match within '問題は解決' context returns True."""
        msg = "先ほどの問題は解決しました。"
        # "問題" starts at index 4
        result = problem_report_check.is_false_positive_match(msg, 4, 6)
        assert result

    def test_false_positive_no_problem(self):
        """Match within '問題ありません' context returns True."""
        msg = "確認しましたが、問題ありません。"
        # "問題" starts at index 8
        result = problem_report_check.is_false_positive_match(msg, 8, 10)
        assert result


class TestFindProblemReports:
    """Tests for find_problem_reports function."""

    def test_no_problems(self):
        """No problem patterns returns empty list."""
        messages = ["すべて正常です", "タスクを完了しました"]
        result = problem_report_check.find_problem_reports(messages)
        assert result == []

    def test_japanese_problem_pattern(self):
        """Japanese problem patterns are detected."""
        messages = ["調査の結果、問題がありました。修正が必要です。"]
        result = problem_report_check.find_problem_reports(messages)
        assert len(result) == 1
        assert "問題" in result[0]

    def test_english_problem_pattern(self):
        """English problem patterns are detected."""
        messages = ["I found a bug in the authentication module."]
        result = problem_report_check.find_problem_reports(messages)
        assert len(result) == 1
        assert "bug" in result[0].lower()

    def test_false_positive_quoted(self):
        """Quoted problem mentions are false positives."""
        messages = ["「問題があります」というエラーメッセージが表示されます"]
        result = problem_report_check.find_problem_reports(messages)
        assert len(result) == 0

    def test_false_positive_no_problem(self):
        """'問題ありません' is false positive."""
        messages = ["確認しましたが、問題ありません。"]
        result = problem_report_check.find_problem_reports(messages)
        assert len(result) == 0

    def test_false_positive_resolved(self):
        """'問題は解決' is false positive."""
        messages = ["先ほどの問題は解決しました。"]
        result = problem_report_check.find_problem_reports(messages)
        assert len(result) == 0

    def test_multiple_problems(self):
        """Multiple problem reports are all detected."""
        messages = [
            "バグを発見しました",
            "正常です",
            "エラーが発生しています",
        ]
        result = problem_report_check.find_problem_reports(messages)
        assert len(result) == 2

    def test_problem_after_false_positive_in_same_message(self):
        """Problem after false positive in same message is still detected."""
        # This tests the improved logic - previously would skip entire message
        messages = ["#123の問題は解決しました。しかし、新しいバグを発見しました。"]
        result = problem_report_check.find_problem_reports(messages)
        assert len(result) == 1
        assert "バグを発見" in result[0]

    def test_excerpt_truncation(self):
        """Long messages are truncated in excerpts."""
        long_prefix = "A" * 100
        long_suffix = "B" * 100
        messages = [f"{long_prefix}バグを発見しました{long_suffix}"]
        result = problem_report_check.find_problem_reports(messages)
        assert len(result) == 1
        # Check that ellipsis is added
        assert "..." in result[0]


class TestFindIssueCreations:
    """Tests for find_issue_creations function."""

    def test_no_issue_commands(self):
        """No issue creation commands returns 0."""
        commands = ["git status", "ls -la"]
        result = problem_report_check.find_issue_creations(commands)
        assert result == 0

    def test_gh_issue_create(self):
        """gh issue create command is detected."""
        commands = ["gh issue create --title 'Bug fix'"]
        result = problem_report_check.find_issue_creations(commands)
        assert result == 1

    def test_multiple_issue_creates(self):
        """Multiple issue creations are counted."""
        commands = [
            "gh issue create --title 'Issue 1'",
            "git commit -m 'fix'",
            "gh issue create --title 'Issue 2'",
        ]
        result = problem_report_check.find_issue_creations(commands)
        assert result == 2

    def test_case_insensitive(self):
        """Detection is case insensitive."""
        commands = ["GH ISSUE CREATE --title test"]
        result = problem_report_check.find_issue_creations(commands)
        assert result == 1


class TestMain:
    """Tests for main function."""

    @patch.object(problem_report_check, "log_hook_execution")
    def test_stop_hook_active_approves(self, mock_log):
        """stop_hook_active=true approves immediately."""
        input_data = json.dumps({"stop_hook_active": True})

        captured_output = StringIO()
        sys.stdout = captured_output
        try:
            with patch("sys.stdin", StringIO(input_data)):
                problem_report_check.main()
        finally:
            sys.stdout = sys.__stdout__

        result = json.loads(captured_output.getvalue())
        assert result["decision"] == "approve"

    @patch.object(problem_report_check, "log_hook_execution")
    def test_no_transcript_path_approves(self, mock_log):
        """Missing transcript_path approves."""
        input_data = json.dumps({"stop_hook_active": False})

        captured_output = StringIO()
        sys.stdout = captured_output
        try:
            with patch("sys.stdin", StringIO(input_data)):
                problem_report_check.main()
        finally:
            sys.stdout = sys.__stdout__

        result = json.loads(captured_output.getvalue())
        assert result["decision"] == "approve"

    @patch.object(problem_report_check, "log_hook_execution")
    def test_problem_without_issue_warns(self, mock_log):
        """Problems without issue creation shows warning."""
        # Create a transcript with a problem but no issue creation
        transcript = [
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "バグを発見しました。修正します。"}],
            }
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(transcript, f)
            f.flush()
            temp_path = f.name

        try:
            input_data = json.dumps({"stop_hook_active": False, "transcript_path": temp_path})

            captured_output = StringIO()
            sys.stdout = captured_output
            try:
                with patch("sys.stdin", StringIO(input_data)):
                    problem_report_check.main()
            finally:
                sys.stdout = sys.__stdout__

            result = json.loads(captured_output.getvalue())
            assert result["decision"] == "approve"
            assert "systemMessage" in result
            assert "問題報告が検出されました" in result["systemMessage"]
        finally:
            Path(temp_path).unlink(missing_ok=True)

    @patch.object(problem_report_check, "log_hook_execution")
    def test_problem_with_issue_no_warning(self, mock_log):
        """Problems with issue creation shows no warning."""
        # Create a transcript with both problem and issue creation
        transcript = [
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "バグを発見しました。"}],
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "input": {"command": "gh issue create --title 'Bug fix'"},
                    }
                ],
            },
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(transcript, f)
            f.flush()
            temp_path = f.name

        try:
            input_data = json.dumps({"stop_hook_active": False, "transcript_path": temp_path})

            captured_output = StringIO()
            sys.stdout = captured_output
            try:
                with patch("sys.stdin", StringIO(input_data)):
                    problem_report_check.main()
            finally:
                sys.stdout = sys.__stdout__

            result = json.loads(captured_output.getvalue())
            assert result["decision"] == "approve"
            assert "systemMessage" not in result
        finally:
            Path(temp_path).unlink(missing_ok=True)

    @patch.object(problem_report_check, "log_hook_execution")
    def test_no_problems_no_warning(self, mock_log):
        """No problems shows no warning."""
        transcript = [
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "タスクを完了しました。"}],
            }
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(transcript, f)
            f.flush()
            temp_path = f.name

        try:
            input_data = json.dumps({"stop_hook_active": False, "transcript_path": temp_path})

            captured_output = StringIO()
            sys.stdout = captured_output
            try:
                with patch("sys.stdin", StringIO(input_data)):
                    problem_report_check.main()
            finally:
                sys.stdout = sys.__stdout__

            result = json.loads(captured_output.getvalue())
            assert result["decision"] == "approve"
            assert "systemMessage" not in result
        finally:
            Path(temp_path).unlink(missing_ok=True)
