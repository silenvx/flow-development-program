#!/usr/bin/env python3
"""Tests for merge-confirmation-warning hook (Issue #2284)."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

# Add hooks directory to path
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

# Import module with hyphen in name using importlib
spec = importlib.util.spec_from_file_location(
    "merge_confirmation_warning",
    HOOKS_DIR / "merge-confirmation-warning.py",
)
merge_confirmation_warning = importlib.util.module_from_spec(spec)
spec.loader.exec_module(merge_confirmation_warning)


class TestCheckMergeConfirmation:
    """Tests for check_merge_confirmation function."""

    def test_detects_basic_confirmation(self):
        """Test detection of basic merge confirmation patterns."""
        content = json.dumps(
            [
                {"role": "user", "content": "マージして"},
                {"role": "assistant", "content": "マージしますか？"},
            ]
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(content)
            f.flush()
            temp_path = f.name

        try:
            result = merge_confirmation_warning.check_merge_confirmation(temp_path)
            assert result["confirmation_count"] == 1
            assert len(result["violations"]) == 1
            assert "マージしますか？" in result["violations"][0]["pattern"]
        finally:
            os.unlink(temp_path)

    def test_detects_polite_confirmation(self):
        """Test detection of polite merge confirmation patterns."""
        content = json.dumps(
            [
                {"role": "assistant", "content": "マージしてよいですか？"},
            ]
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(content)
            f.flush()
            temp_path = f.name

        try:
            result = merge_confirmation_warning.check_merge_confirmation(temp_path)
            assert result["confirmation_count"] == 1
        finally:
            os.unlink(temp_path)

    def test_detects_very_polite_confirmation(self):
        """Test detection of very polite merge confirmation patterns."""
        content = json.dumps(
            [
                {"role": "assistant", "content": "マージしてもよろしいですか？"},
            ]
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(content)
            f.flush()
            temp_path = f.name

        try:
            result = merge_confirmation_warning.check_merge_confirmation(temp_path)
            assert result["confirmation_count"] == 1
        finally:
            os.unlink(temp_path)

    def test_ignores_code_blocks(self):
        """Test that patterns in code blocks are ignored."""
        content = json.dumps(
            [
                {
                    "role": "assistant",
                    "content": "例:\n```\nマージしますか？\n```\n以上です。",
                },
            ]
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(content)
            f.flush()
            temp_path = f.name

        try:
            result = merge_confirmation_warning.check_merge_confirmation(temp_path)
            assert result["confirmation_count"] == 0
        finally:
            os.unlink(temp_path)

    def test_ignores_agents_md_references(self):
        """Test that AGENTS.md references are ignored."""
        content = json.dumps(
            [
                {
                    "role": "assistant",
                    "content": "AGENTS.mdによると「マージしますか？」は禁止です。",
                },
            ]
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(content)
            f.flush()
            temp_path = f.name

        try:
            result = merge_confirmation_warning.check_merge_confirmation(temp_path)
            assert result["confirmation_count"] == 0
        finally:
            os.unlink(temp_path)

    def test_ignores_user_messages(self):
        """Test that user messages are not checked."""
        content = json.dumps(
            [
                {"role": "user", "content": "マージしますか？"},
                {"role": "assistant", "content": "マージを実行します。"},
            ]
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(content)
            f.flush()
            temp_path = f.name

        try:
            result = merge_confirmation_warning.check_merge_confirmation(temp_path)
            assert result["confirmation_count"] == 0
        finally:
            os.unlink(temp_path)

    def test_returns_empty_for_missing_file(self):
        """Test that missing files return empty result."""
        result = merge_confirmation_warning.check_merge_confirmation("/nonexistent/file.json")
        assert result["confirmation_count"] == 0
        assert len(result["violations"]) == 0

    def test_detects_multiple_patterns(self):
        """Test detection of multiple confirmation patterns."""
        content = json.dumps(
            [
                {"role": "assistant", "content": "マージしますか？"},
                {"role": "assistant", "content": "マージしてよいですか？"},
            ]
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(content)
            f.flush()
            temp_path = f.name

        try:
            result = merge_confirmation_warning.check_merge_confirmation(temp_path)
            assert result["confirmation_count"] == 2
        finally:
            os.unlink(temp_path)


class TestMain:
    """Tests for main function."""

    def test_continues_without_transcript_path(self):
        """Test continues when no transcript path is provided."""
        with patch.object(merge_confirmation_warning, "parse_hook_input", return_value={}):
            with patch("builtins.print") as mock_print:
                merge_confirmation_warning.main()

                output = mock_print.call_args[0][0]
                assert json.loads(output)["continue"] is True

    def test_continues_with_invalid_path(self):
        """Test continues with invalid transcript path."""
        with patch.object(
            merge_confirmation_warning,
            "parse_hook_input",
            return_value={"transcript_path": "/etc/passwd"},
        ):
            with patch("builtins.print") as mock_print:
                merge_confirmation_warning.main()

                output = mock_print.call_args[0][0]
                assert json.loads(output)["continue"] is True

    def test_warns_on_violations(self):
        """Test warns when violations are detected."""
        content = json.dumps([{"role": "assistant", "content": "マージしますか？"}])

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(content)
            f.flush()
            transcript_path = f.name

        try:
            with patch.object(
                merge_confirmation_warning,
                "parse_hook_input",
                return_value={"transcript_path": transcript_path},
            ):
                with patch.object(
                    merge_confirmation_warning, "is_safe_transcript_path", return_value=True
                ):
                    with patch("builtins.print") as mock_print:
                        merge_confirmation_warning.main()

                        output = json.loads(mock_print.call_args[0][0])
                        assert output["continue"] is True
                        assert "message" in output
                        assert "マージ確認" in output["message"]
        finally:
            os.unlink(transcript_path)

    def test_no_warning_when_clean(self):
        """Test no warning when no violations."""
        content = json.dumps([{"role": "assistant", "content": "マージを実行しました。"}])

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(content)
            f.flush()
            transcript_path = f.name

        try:
            with patch.object(
                merge_confirmation_warning,
                "parse_hook_input",
                return_value={"transcript_path": transcript_path},
            ):
                with patch.object(
                    merge_confirmation_warning, "is_safe_transcript_path", return_value=True
                ):
                    with patch("builtins.print") as mock_print:
                        merge_confirmation_warning.main()

                        output = json.loads(mock_print.call_args[0][0])
                        assert output["continue"] is True
                        assert "message" not in output
        finally:
            os.unlink(transcript_path)
