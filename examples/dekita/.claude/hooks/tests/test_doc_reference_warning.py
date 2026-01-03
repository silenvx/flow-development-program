#!/usr/bin/env python3
from __future__ import annotations

"""Unit tests for doc-reference-warning.py"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Add parent directory to path for module imports
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# doc-reference-warning.py has hyphens, so we need dynamic import
HOOK_PATH = Path(__file__).parent.parent / "doc-reference-warning.py"
_spec = importlib.util.spec_from_file_location("doc_reference_warning", HOOK_PATH)
doc_reference_warning = importlib.util.module_from_spec(_spec)
sys.modules["doc_reference_warning"] = doc_reference_warning
_spec.loader.exec_module(doc_reference_warning)

extract_command_pattern = doc_reference_warning.extract_command_pattern
extract_read_md_files = doc_reference_warning.extract_read_md_files
search_pattern_in_file = doc_reference_warning.search_pattern_in_file
read_transcript = doc_reference_warning.read_transcript


class TestExtractCommandPattern:
    """Tests for extract_command_pattern function."""

    def test_extracts_claude_scripts_py(self):
        """Should extract .claude/scripts/*.py patterns."""
        cmd = "python3 .claude/scripts/ci-monitor.py 123"
        assert extract_command_pattern(cmd) == ".claude/scripts/ci-monitor.py"

    def test_extracts_claude_scripts_sh(self):
        """Should extract .claude/scripts/*.sh patterns."""
        cmd = ".claude/scripts/setup-worktree.sh .worktrees/issue-123"
        assert extract_command_pattern(cmd) == ".claude/scripts/setup-worktree.sh"

    def test_extracts_claude_hooks_py(self):
        """Should extract .claude/hooks/*.py patterns."""
        cmd = "python3 .claude/hooks/merge-check.py"
        assert extract_command_pattern(cmd) == ".claude/hooks/merge-check.py"

    def test_extracts_root_scripts_sh(self):
        """Should extract scripts/*.sh patterns."""
        cmd = "scripts/check-lefthook.sh"
        assert extract_command_pattern(cmd) == "scripts/check-lefthook.sh"

    def test_extracts_nested_claude_path(self):
        """Should extract nested .claude/ paths."""
        cmd = "python3 .claude/prompts/reflection/execute.py"
        assert extract_command_pattern(cmd) == ".claude/prompts/reflection/execute.py"

    def test_returns_none_for_unrelated_command(self):
        """Should return None for commands without script paths."""
        assert extract_command_pattern("git status") is None
        assert extract_command_pattern("npm run build") is None
        assert extract_command_pattern("ls -la") is None

    def test_handles_hyphenated_script_names(self):
        """Should handle script names with hyphens."""
        cmd = "python3 .claude/scripts/session-reflection.py"
        assert extract_command_pattern(cmd) == ".claude/scripts/session-reflection.py"


class TestExtractReadMdFiles:
    """Tests for extract_read_md_files function."""

    def test_extracts_md_files_from_read_tool(self):
        """Should extract .md file paths from Read tool entries."""
        transcript = [
            {"type": "tool_use", "name": "Read", "input": {"file_path": "/path/to/AGENTS.md"}},
            {"type": "tool_use", "name": "Read", "input": {"file_path": "/path/to/file.py"}},
            {"type": "tool_use", "name": "Read", "input": {"file_path": "/path/to/README.md"}},
        ]
        result = extract_read_md_files(transcript)
        assert result == ["/path/to/AGENTS.md", "/path/to/README.md"]

    def test_ignores_non_read_tools(self):
        """Should ignore non-Read tool entries."""
        transcript = [
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
            {"type": "tool_use", "name": "Write", "input": {"file_path": "/path/to/file.md"}},
        ]
        result = extract_read_md_files(transcript)
        assert result == []

    def test_handles_empty_transcript(self):
        """Should handle empty transcript."""
        assert extract_read_md_files([]) == []

    def test_handles_missing_file_path(self):
        """Should handle entries with missing file_path."""
        transcript = [
            {"type": "tool_use", "name": "Read", "input": {}},
        ]
        result = extract_read_md_files(transcript)
        assert result == []


class TestSearchPatternInFile:
    """Tests for search_pattern_in_file function."""

    def test_finds_pattern_in_file(self):
        """Should find pattern and return line numbers."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("Line 1\n")
            f.write("python3 .claude/scripts/test.py\n")
            f.write("Line 3\n")
            f.write(".claude/scripts/test.py again\n")
        try:
            result = search_pattern_in_file(f.name, ".claude/scripts/test.py")
            assert result == [2, 4]
        finally:
            os.unlink(f.name)

    def test_returns_empty_for_no_match(self):
        """Should return empty list when pattern not found."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("No matching content here\n")
        try:
            result = search_pattern_in_file(f.name, ".claude/scripts/missing.py")
            assert result == []
        finally:
            os.unlink(f.name)

    def test_returns_empty_for_missing_file(self):
        """Should return empty list for non-existent file."""
        result = search_pattern_in_file("/nonexistent/file.md", "pattern")
        assert result == []


class TestReadTranscript:
    """Tests for read_transcript function."""

    def test_reads_jsonl_transcript(self):
        """Should read and parse JSONL transcript."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write('{"type": "tool_use", "name": "Read"}\n')
            f.write('{"type": "tool_use", "name": "Bash"}\n')
        try:
            result = read_transcript(f.name)
            assert len(result) == 2
            assert result[0]["name"] == "Read"
            assert result[1]["name"] == "Bash"
        finally:
            os.unlink(f.name)

    def test_handles_missing_transcript(self):
        """Should return empty list for missing transcript."""
        result = read_transcript("/nonexistent/transcript.jsonl")
        assert result == []

    def test_handles_malformed_json_lines(self):
        """Should skip malformed JSON lines."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write('{"valid": true}\n')
            f.write("not json\n")
            f.write('{"also_valid": true}\n')
        try:
            result = read_transcript(f.name)
            assert len(result) == 2
        finally:
            os.unlink(f.name)


class TestHookIntegration:
    """Integration tests for the full hook behavior."""

    def setup_method(self):
        """Set up temporary directory for each test."""
        self.temp_dir = tempfile.TemporaryDirectory()

    def teardown_method(self):
        """Clean up temp directory."""
        self.temp_dir.cleanup()

    def run_hook(self, input_data: dict) -> dict:
        """Run the hook with given input and return parsed output."""
        env = os.environ.copy()
        env["TMPDIR"] = self.temp_dir.name
        result = subprocess.run(
            [sys.executable, str(HOOK_PATH)],
            input=json.dumps(input_data),
            capture_output=True,
            text=True,
            env=env,
        )
        if result.stdout:
            return json.loads(result.stdout)
        return {}

    def test_successful_command_returns_continue(self):
        """Successful Bash command should return continue: true."""
        input_data = {"tool_result": {"exit_code": 0, "stdout": "success", "stderr": ""}}
        output = self.run_hook(input_data)
        assert output.get("continue", False)

    def test_failed_command_without_file_error_returns_continue(self):
        """Failed command without file error should return continue without message."""
        input_data = {
            "tool_input": {"command": "python3 .claude/scripts/test.py"},
            "tool_result": {"exit_code": 1, "stdout": "", "stderr": "Some other error"},
        }
        output = self.run_hook(input_data)
        assert output.get("continue", False)
        assert "systemMessage" not in output

    def test_failed_command_with_file_error_but_no_transcript(self):
        """Failed command with file error but no transcript should return continue."""
        input_data = {
            "tool_input": {"command": "python3 .claude/scripts/missing.py"},
            "tool_result": {
                "exit_code": 1,
                "stdout": "",
                "stderr": "No such file or directory",
            },
        }
        output = self.run_hook(input_data)
        assert output.get("continue", False)

    def test_detects_outdated_doc_reference(self):
        """Should detect when failed command is referenced in read documentation."""
        # Create a mock transcript
        transcript_path = Path(self.temp_dir.name) / "transcript.jsonl"
        md_path = Path(self.temp_dir.name) / "test.md"

        # Create the markdown file with the script reference
        md_path.write_text(
            "# Documentation\n\nRun the script:\n```\npython3 .claude/scripts/missing-script.py\n```\n"
        )

        # Create transcript showing we read the markdown file
        transcript_entries = [
            {"type": "tool_use", "name": "Read", "input": {"file_path": str(md_path)}},
        ]
        with open(transcript_path, "w") as f:
            for entry in transcript_entries:
                f.write(json.dumps(entry) + "\n")

        input_data = {
            "transcript_path": str(transcript_path),
            "tool_input": {"command": "python3 .claude/scripts/missing-script.py"},
            "tool_result": {
                "exit_code": 1,
                "stdout": "",
                "stderr": "No such file or directory",
            },
        }
        output = self.run_hook(input_data)

        assert output.get("continue", False)
        assert "systemMessage" in output
        assert "doc-reference-warning" in output["systemMessage"]
        assert ".claude/scripts/missing-script.py" in output["systemMessage"]

    def test_no_warning_when_script_not_in_docs(self):
        """Should not warn when failed script is not in read documentation."""
        # Create a mock transcript
        transcript_path = Path(self.temp_dir.name) / "transcript.jsonl"
        md_path = Path(self.temp_dir.name) / "test.md"

        # Create the markdown file without the script reference
        md_path.write_text("# Documentation\n\nSome other content.\n")

        # Create transcript
        transcript_entries = [
            {"type": "tool_use", "name": "Read", "input": {"file_path": str(md_path)}},
        ]
        with open(transcript_path, "w") as f:
            for entry in transcript_entries:
                f.write(json.dumps(entry) + "\n")

        input_data = {
            "transcript_path": str(transcript_path),
            "tool_input": {"command": "python3 .claude/scripts/some-other-script.py"},
            "tool_result": {
                "exit_code": 1,
                "stdout": "",
                "stderr": "No such file or directory",
            },
        }
        output = self.run_hook(input_data)

        assert output.get("continue", False)
        assert "systemMessage" not in output
