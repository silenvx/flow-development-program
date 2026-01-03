#!/usr/bin/env python3
"""Tests for skill-usage-reminder.py hook."""

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

# Load module from hyphenated filename
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))


def load_module(name: str, filepath: Path):
    """Load a Python module from a hyphenated filename."""
    spec = importlib.util.spec_from_file_location(name, filepath)
    if spec is None or spec.loader is None:
        raise ImportError(f"Failed to load spec for {filepath}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


skill_usage_reminder = load_module("skill_usage_reminder", HOOKS_DIR / "skill-usage-reminder.py")


class TestCheckCommandForSkillRequirement:
    """Tests for check_command_for_skill_requirement function."""

    def test_detects_git_worktree_add(self):
        """Should detect git worktree add command."""
        result = skill_usage_reminder.check_command_for_skill_requirement(
            "git worktree add .worktrees/issue-123 -b feat/issue-123"
        )
        assert result is not None
        skill, desc = result
        assert skill == "development-workflow"
        assert "worktree" in desc

    def test_detects_gh_pr_create(self):
        """Should detect gh pr create command."""
        result = skill_usage_reminder.check_command_for_skill_requirement(
            "gh pr create --title 'Test' --body 'Test body'"
        )
        assert result is not None
        skill, desc = result
        assert skill == "development-workflow"
        assert "PR" in desc

    def test_detects_gh_api_pr_comments(self):
        """Should detect gh api for PR comments."""
        result = skill_usage_reminder.check_command_for_skill_requirement(
            "gh api repos/owner/repo/pulls/123/comments"
        )
        assert result is not None
        skill, desc = result
        assert skill == "code-review"

    def test_detects_batch_resolve_threads(self):
        """Should detect batch_resolve_threads.py script."""
        result = skill_usage_reminder.check_command_for_skill_requirement(
            "python3 .claude/scripts/batch_resolve_threads.py 123"
        )
        assert result is not None
        skill, desc = result
        assert skill == "code-review"

    def test_ignores_other_commands(self):
        """Should ignore commands that don't require Skill usage."""
        commands = [
            "git status",
            "git push origin main",
            "gh pr view 123",
            "gh pr list",
            "npm run test",
            "ls -la",
            "git worktree list",  # Not 'add'
            "gh pr merge 123",  # Not 'create'
        ]
        for cmd in commands:
            result = skill_usage_reminder.check_command_for_skill_requirement(cmd)
            assert result is None, f"Should not match: {cmd}"


class TestGetSkillUsageFromTranscript:
    """Tests for get_skill_usage_from_transcript function."""

    def test_extracts_skill_usage(self):
        """Should extract Skill names from transcript."""
        transcript_content = [
            {
                "sessionId": "test-session",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Skill",
                            "input": {"skill": "development-workflow"},
                        }
                    ]
                },
            },
            {
                "sessionId": "test-session",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Skill",
                            "input": {"skill": "code-review"},
                        }
                    ]
                },
            },
        ]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for entry in transcript_content:
                f.write(json.dumps(entry) + "\n")
            f.flush()

            # Patch is_safe_transcript_path to allow temp file
            with patch.object(skill_usage_reminder, "is_safe_transcript_path", return_value=True):
                skills = skill_usage_reminder.get_skill_usage_from_transcript(
                    f.name, "test-session"
                )

        assert "development-workflow" in skills
        assert "code-review" in skills

    def test_filters_by_session_id(self):
        """Should only count Skills from current session."""
        transcript_content = [
            {
                "sessionId": "other-session",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Skill",
                            "input": {"skill": "development-workflow"},
                        }
                    ]
                },
            },
            {
                "sessionId": "current-session",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Skill",
                            "input": {"skill": "code-review"},
                        }
                    ]
                },
            },
        ]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for entry in transcript_content:
                f.write(json.dumps(entry) + "\n")
            f.flush()

            with patch.object(skill_usage_reminder, "is_safe_transcript_path", return_value=True):
                skills = skill_usage_reminder.get_skill_usage_from_transcript(
                    f.name, "current-session"
                )

        assert "code-review" in skills
        assert "development-workflow" not in skills

    def test_handles_empty_transcript(self):
        """Should return empty set for empty transcript."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.flush()

            with patch.object(skill_usage_reminder, "is_safe_transcript_path", return_value=True):
                skills = skill_usage_reminder.get_skill_usage_from_transcript(
                    f.name, "test-session"
                )

        assert len(skills) == 0

    def test_handles_missing_file(self):
        """Should return empty set for missing file."""
        skills = skill_usage_reminder.get_skill_usage_from_transcript(
            "/nonexistent/path.jsonl", "test-session"
        )
        assert len(skills) == 0

    def test_handles_invalid_json(self):
        """Should handle invalid JSON gracefully."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write("invalid json\n")
            f.write('{"sessionId": "test", "message": {"content": []}}\n')
            f.flush()

            with patch.object(skill_usage_reminder, "is_safe_transcript_path", return_value=True):
                skills = skill_usage_reminder.get_skill_usage_from_transcript(
                    f.name, "test-session"
                )

        # Should not raise, just return empty set
        assert len(skills) == 0


class TestMain:
    """Tests for main function."""

    def test_approves_non_bash_tool(self, capsys):
        """Should approve non-Bash tools."""
        mock_input = {
            "tool_name": "Read",
            "tool_input": {"file_path": "/some/file.txt"},
        }

        with (
            patch.object(skill_usage_reminder, "parse_hook_input", return_value=mock_input),
            patch.object(
                skill_usage_reminder,
                "create_hook_context",
                return_value=MagicMock(get_session_id=MagicMock(return_value="test-session")),
            ),
            patch.object(skill_usage_reminder, "log_hook_execution"),
        ):
            skill_usage_reminder.main()

        captured = capsys.readouterr()
        result = json.loads(captured.out.strip())
        assert result["decision"] == "approve"

    def test_approves_non_matching_command(self, capsys):
        """Should approve commands that don't require Skill."""
        mock_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "git status"},
        }

        with (
            patch.object(skill_usage_reminder, "parse_hook_input", return_value=mock_input),
            patch.object(
                skill_usage_reminder,
                "create_hook_context",
                return_value=MagicMock(get_session_id=MagicMock(return_value="test-session")),
            ),
            patch.object(skill_usage_reminder, "log_hook_execution"),
        ):
            skill_usage_reminder.main()

        captured = capsys.readouterr()
        result = json.loads(captured.out.strip())
        assert result["decision"] == "approve"

    def test_approves_when_skill_was_used(self, capsys):
        """Should approve when required Skill was already used."""
        mock_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "git worktree add .worktrees/issue-123"},
            "transcript_path": "/fake/path.jsonl",
        }

        with (
            patch.object(skill_usage_reminder, "parse_hook_input", return_value=mock_input),
            patch.object(
                skill_usage_reminder,
                "create_hook_context",
                return_value=MagicMock(get_session_id=MagicMock(return_value="test-session")),
            ),
            patch.object(
                skill_usage_reminder,
                "get_skill_usage_from_transcript",
                return_value={"development-workflow"},
            ),
            patch.object(skill_usage_reminder, "log_hook_execution"),
        ):
            skill_usage_reminder.main()

        captured = capsys.readouterr()
        result = json.loads(captured.out.strip())
        assert result["decision"] == "approve"

    def test_blocks_when_skill_not_used(self, capsys):
        """Should block when required Skill was not used."""
        mock_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "git worktree add .worktrees/issue-123"},
            "transcript_path": "/fake/path.jsonl",
        }

        with (
            patch.object(skill_usage_reminder, "parse_hook_input", return_value=mock_input),
            patch.object(
                skill_usage_reminder,
                "create_hook_context",
                return_value=MagicMock(get_session_id=MagicMock(return_value="test-session")),
            ),
            patch.object(
                skill_usage_reminder,
                "get_skill_usage_from_transcript",
                return_value=set(),  # No skills used
            ),
            patch.object(skill_usage_reminder, "log_hook_execution"),
        ):
            skill_usage_reminder.main()

        captured = capsys.readouterr()
        result = json.loads(captured.out.strip())
        assert result["decision"] == "block"
        assert "development-workflow" in result["reason"]

    def test_blocks_pr_create_without_skill(self, capsys):
        """Should block gh pr create when development-workflow not used."""
        mock_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr create --title 'Test'"},
            "transcript_path": "/fake/path.jsonl",
        }

        with (
            patch.object(skill_usage_reminder, "parse_hook_input", return_value=mock_input),
            patch.object(
                skill_usage_reminder,
                "create_hook_context",
                return_value=MagicMock(get_session_id=MagicMock(return_value="test-session")),
            ),
            patch.object(
                skill_usage_reminder,
                "get_skill_usage_from_transcript",
                return_value=set(),
            ),
            patch.object(skill_usage_reminder, "log_hook_execution"),
        ):
            skill_usage_reminder.main()

        captured = capsys.readouterr()
        result = json.loads(captured.out.strip())
        assert result["decision"] == "block"
        assert "development-workflow" in result["reason"]

    def test_blocks_code_review_without_skill(self, capsys):
        """Should block review operations when code-review not used."""
        mock_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "python3 .claude/scripts/batch_resolve_threads.py 123"},
            "transcript_path": "/fake/path.jsonl",
        }

        with (
            patch.object(skill_usage_reminder, "parse_hook_input", return_value=mock_input),
            patch.object(
                skill_usage_reminder,
                "create_hook_context",
                return_value=MagicMock(get_session_id=MagicMock(return_value="test-session")),
            ),
            patch.object(
                skill_usage_reminder,
                "get_skill_usage_from_transcript",
                return_value=set(),
            ),
            patch.object(skill_usage_reminder, "log_hook_execution"),
        ):
            skill_usage_reminder.main()

        captured = capsys.readouterr()
        result = json.loads(captured.out.strip())
        assert result["decision"] == "block"
        assert "code-review" in result["reason"]

    def test_approves_on_exception(self, capsys):
        """Should approve when exception occurs (fail-open)."""
        with (
            patch.object(
                skill_usage_reminder, "parse_hook_input", side_effect=Exception("Test error")
            ),
            patch.object(
                skill_usage_reminder,
                "create_hook_context",
                return_value=MagicMock(get_session_id=MagicMock(return_value="test-session")),
            ),
            patch.object(skill_usage_reminder, "log_hook_execution"),
        ):
            skill_usage_reminder.main()

        captured = capsys.readouterr()
        result = json.loads(captured.out.strip())
        # Should fail-open (approve) on exception
        assert result["decision"] == "approve"
