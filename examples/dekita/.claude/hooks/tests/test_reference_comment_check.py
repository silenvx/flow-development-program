#!/usr/bin/env python3
"""Tests for reference-comment-check hook."""

import json
import subprocess

from conftest import HOOKS_DIR, load_hook_module

HOOK_PATH = HOOKS_DIR / "reference_comment_check.py"


def run_hook(input_data: dict) -> dict:
    """Run the hook with given input and return the result."""
    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


class TestShouldCheckFile:
    """Tests for should_check_file function."""

    def setup_method(self):
        self.module = load_hook_module("reference_comment_check")

    def test_python_files(self):
        """Should check Python files."""
        assert self.module.should_check_file("test.py")
        assert self.module.should_check_file("/path/to/module.py")

    def test_typescript_files(self):
        """Should check TypeScript files."""
        assert self.module.should_check_file("component.ts")
        assert self.module.should_check_file("component.tsx")

    def test_javascript_files(self):
        """Should check JavaScript files."""
        assert self.module.should_check_file("script.js")
        assert self.module.should_check_file("component.jsx")

    def test_other_files(self):
        """Should not check other file types."""
        assert not self.module.should_check_file("README.md")
        assert not self.module.should_check_file("config.json")
        assert not self.module.should_check_file("style.css")
        assert not self.module.should_check_file("Makefile")


class TestFindReferenceComments:
    """Tests for find_reference_comments function."""

    def setup_method(self):
        self.module = load_hook_module("reference_comment_check")

    def test_japanese_same_as_pattern(self):
        """Should detect '〜と同じ' pattern."""
        text = "# pr_related_issue_check.pyと同じ"
        matches = self.module.find_reference_comments(text)
        assert len(matches) == 1
        assert "と同じ" in matches[0]

    def test_japanese_shared_pattern(self):
        """Should detect '〜と共通' pattern."""
        text = "# common.pyと共通の設定"
        matches = self.module.find_reference_comments(text)
        assert len(matches) == 1
        assert "と共通" in matches[0]

    def test_japanese_refer_pattern(self):
        """Should detect '〜を参照' pattern."""
        text = "# 詳細はconfig.pyを参照"
        matches = self.module.find_reference_comments(text)
        assert len(matches) == 1
        assert "を参照" in matches[0]

    def test_japanese_copy_pattern(self):
        """Should detect '〜からコピー' pattern."""
        text = "# utils.pyからコピー"
        matches = self.module.find_reference_comments(text)
        assert len(matches) == 1
        assert "からコピー" in matches[0]

    def test_english_same_as_pattern(self):
        """Should detect 'same as file.py' pattern."""
        text = "# Same as common.py"
        matches = self.module.find_reference_comments(text)
        assert len(matches) == 1
        assert "Same as" in matches[0]

    def test_english_copied_from_pattern(self):
        """Should detect 'copied from file.py' pattern."""
        text = "# Copied from utils.ts"
        matches = self.module.find_reference_comments(text)
        assert len(matches) == 1
        assert "Copied from" in matches[0]

    def test_english_see_pattern(self):
        """Should detect 'see file.py' pattern."""
        text = "# See constants.py for details"
        matches = self.module.find_reference_comments(text)
        assert len(matches) == 1
        assert "See" in matches[0]

    def test_no_match_for_normal_comments(self):
        """Should not match normal comments."""
        text = """
# This is a normal comment
# タイムアウトは10秒（ネットワーク遅延を考慮）
# Maximum number of retries
"""
        matches = self.module.find_reference_comments(text)
        assert len(matches) == 0

    def test_no_match_for_inline_code(self):
        """Should not match references in actual code."""
        text = """
def same_as_other():
    pass
"""
        matches = self.module.find_reference_comments(text)
        assert len(matches) == 0

    def test_multiple_matches(self):
        """Should find multiple reference comments."""
        text = """
# common.pyと同じ
TIMEOUT = 10

# Same as utils.py
MAX_RETRIES = 3
"""
        matches = self.module.find_reference_comments(text)
        assert len(matches) == 2

    def test_deduplicates_matches(self):
        """Should not include duplicate matches."""
        text = """
# common.pyと同じ
# common.pyと同じ
"""
        matches = self.module.find_reference_comments(text)
        assert len(matches) == 1

    def test_js_style_comments(self):
        """Should detect patterns in JS-style comments."""
        text = "// Same as utils.ts"
        matches = self.module.find_reference_comments(text)
        assert len(matches) == 1


class TestHookIntegration:
    """Integration tests for the hook."""

    def test_approve_non_edit_tool(self):
        """Should approve non-Edit tool calls."""
        result = run_hook(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "ls -la"},
            }
        )
        assert result["decision"] == "approve"
        assert "systemMessage" not in result

    def test_approve_non_checkable_file(self):
        """Should approve edits to non-checkable files."""
        result = run_hook(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": "/path/to/README.md",
                    "old_string": "old",
                    "new_string": "# Same as other.md",
                },
            }
        )
        assert result["decision"] == "approve"
        assert "systemMessage" not in result

    def test_approve_normal_edit(self):
        """Should approve edits without reference comments."""
        result = run_hook(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": "/path/to/module.py",
                    "old_string": "old",
                    "new_string": "# タイムアウトは10秒\nTIMEOUT = 10",
                },
            }
        )
        assert result["decision"] == "approve"
        assert "systemMessage" not in result

    def test_warn_on_reference_comment(self):
        """Should warn when reference comment is detected."""
        result = run_hook(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": "/path/to/module.py",
                    "old_string": "old",
                    "new_string": "# common.pyと同じ\nTIMEOUT = 10",
                },
            }
        )
        assert result["decision"] == "approve"
        assert "systemMessage" in result
        assert "参照スタイルのコメント" in result["systemMessage"]

    def test_approve_empty_new_string(self):
        """Should approve when new_string is empty."""
        result = run_hook(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": "/path/to/module.py",
                    "old_string": "# common.pyと同じ",
                    "new_string": "",
                },
            }
        )
        assert result["decision"] == "approve"
        assert "systemMessage" not in result
