#!/usr/bin/env python3
"""
Unit tests for validate-hooks-settings.py

Tests cover:
- extract_hook_paths() with various command formats
- Missing file detection
- Invalid JSON handling
- Regex edge cases
"""

import importlib.util
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

# validate-hooks-settings.py has hyphens, so we need dynamic import
SCRIPT_PATH = Path(__file__).parent.parent / "validate_hooks_settings.py"
_spec = importlib.util.spec_from_file_location("validate_hooks_settings", SCRIPT_PATH)
validate_hooks_settings = importlib.util.module_from_spec(_spec)
sys.modules["validate_hooks_settings"] = validate_hooks_settings
_spec.loader.exec_module(validate_hooks_settings)

extract_hook_paths = validate_hooks_settings.extract_hook_paths
main = validate_hooks_settings.main


class TestExtractHookPaths:
    """Tests for extract_hook_paths function."""

    def test_empty_settings(self):
        """Should return empty list for empty settings."""
        result = extract_hook_paths({}, Path("/project"))
        assert result == []

    def test_no_hooks_key(self):
        """Should return empty list when hooks key is missing."""
        result = extract_hook_paths({"other": "value"}, Path("/project"))
        assert result == []

    def test_empty_hooks(self):
        """Should return empty list when hooks is empty."""
        result = extract_hook_paths({"hooks": {}}, Path("/project"))
        assert result == []

    def test_standard_command_format(self):
        """Should extract paths from standard command format."""
        settings = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "command": 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/test.py',
                            }
                        ],
                    }
                ]
            }
        }
        result = extract_hook_paths(settings, Path("/project"))

        assert len(result) == 1
        assert result[0][1] == Path("/project/.claude/hooks/test.py")

    def test_command_without_quotes(self):
        """Should extract paths from command without quotes around variable."""
        settings = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "python3 $CLAUDE_PROJECT_DIR/.claude/hooks/hook.py",
                            }
                        ],
                    }
                ]
            }
        }
        result = extract_hook_paths(settings, Path("/project"))

        assert len(result) == 1
        assert result[0][1] == Path("/project/.claude/hooks/hook.py")

    def test_command_with_arguments(self):
        """Should stop extracting at .py even with arguments after."""
        settings = {
            "hooks": {
                "PostToolUse": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/script.py --arg1 value',
                            }
                        ],
                    }
                ]
            }
        }
        result = extract_hook_paths(settings, Path("/project"))

        assert len(result) == 1
        assert result[0][1] == Path("/project/.claude/hooks/script.py")

    def test_direct_path_format(self):
        """Should extract paths from direct path format (fallback)."""
        settings = {
            "hooks": {
                "Stop": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "python3 .claude/hooks/stop-hook.py",
                            }
                        ],
                    }
                ]
            }
        }
        result = extract_hook_paths(settings, Path("/project"))

        assert len(result) == 1
        assert result[0][1] == Path("/project/.claude/hooks/stop-hook.py")

    def test_multiple_hooks(self):
        """Should extract paths from multiple hooks."""
        settings = {
            "hooks": {
                "PreToolUse": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/hook1.py',
                            },
                            {
                                "type": "command",
                                "command": 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/hook2.py',
                            },
                        ],
                    }
                ],
                "PostToolUse": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/hook3.py',
                            },
                        ],
                    }
                ],
            }
        }
        result = extract_hook_paths(settings, Path("/project"))

        assert len(result) == 3

    def test_non_command_type_ignored(self):
        """Should ignore hooks that are not command type."""
        settings = {
            "hooks": {
                "PreToolUse": [
                    {
                        "hooks": [
                            {"type": "other", "value": "something"},
                            {
                                "type": "command",
                                "command": 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/test.py',
                            },
                        ],
                    }
                ]
            }
        }
        result = extract_hook_paths(settings, Path("/project"))

        assert len(result) == 1

    def test_empty_command(self):
        """Should handle empty command string."""
        settings = {
            "hooks": {
                "PreToolUse": [
                    {
                        "hooks": [
                            {"type": "command", "command": ""},
                        ],
                    }
                ]
            }
        }
        result = extract_hook_paths(settings, Path("/project"))

        assert result == []

    def test_non_python_command(self):
        """Should ignore non-python commands."""
        settings = {
            "hooks": {
                "PreToolUse": [
                    {
                        "hooks": [
                            {"type": "command", "command": "bash script.sh"},
                        ],
                    }
                ]
            }
        }
        result = extract_hook_paths(settings, Path("/project"))

        assert result == []

    def test_nested_path(self):
        """Should handle deeply nested paths."""
        settings = {
            "hooks": {
                "PreToolUse": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/sub/dir/script.py',
                            },
                        ],
                    }
                ]
            }
        }
        result = extract_hook_paths(settings, Path("/project"))

        assert len(result) == 1
        assert result[0][1] == Path("/project/.claude/hooks/sub/dir/script.py")

    def test_path_with_hyphen(self):
        """Should handle paths containing hyphens."""
        settings = {
            "hooks": {
                "PreToolUse": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/my-hook-script.py',
                            },
                        ],
                    }
                ]
            }
        }
        result = extract_hook_paths(settings, Path("/project"))

        assert len(result) == 1
        assert result[0][1] == Path("/project/.claude/hooks/my-hook-script.py")

    def test_path_with_underscore(self):
        """Should handle paths containing underscores."""
        settings = {
            "hooks": {
                "PreToolUse": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/my_hook_script.py',
                            },
                        ],
                    }
                ]
            }
        }
        result = extract_hook_paths(settings, Path("/project"))

        assert len(result) == 1
        assert result[0][1] == Path("/project/.claude/hooks/my_hook_script.py")


class TestMainFunction:
    """Tests for main function."""

    def test_missing_settings_file(self):
        """Should return 0 when settings.json doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            scripts_dir = tmpdir_path / ".claude" / "scripts"
            scripts_dir.mkdir(parents=True)

            with patch.object(Path, "parent", new_callable=lambda: property(lambda s: scripts_dir)):
                # Patch __file__ to use the temp directory
                with patch.object(
                    validate_hooks_settings,
                    "__file__",
                    str(scripts_dir / "validate_hooks_settings.py"),
                ):
                    captured_output = io.StringIO()
                    with patch("sys.stdout", captured_output):
                        result = main()

            assert result == 0
            assert "No settings.json found" in captured_output.getvalue()

    def test_invalid_json(self):
        """Should return 1 when settings.json contains invalid JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            claude_dir = tmpdir_path / ".claude"
            claude_dir.mkdir()
            scripts_dir = claude_dir / "scripts"
            scripts_dir.mkdir()

            settings_path = claude_dir / "settings.json"
            settings_path.write_text("{ invalid json }")

            with patch.object(
                validate_hooks_settings,
                "__file__",
                str(scripts_dir / "validate_hooks_settings.py"),
            ):
                captured_output = io.StringIO()
                with patch("sys.stdout", captured_output):
                    result = main()

            assert result == 1
            assert "Invalid JSON" in captured_output.getvalue()

    def test_no_hooks_in_settings(self):
        """Should return 0 when no hooks are defined."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            claude_dir = tmpdir_path / ".claude"
            claude_dir.mkdir()
            scripts_dir = claude_dir / "scripts"
            scripts_dir.mkdir()

            settings_path = claude_dir / "settings.json"
            settings_path.write_text(json.dumps({"other": "setting"}))

            with patch.object(
                validate_hooks_settings,
                "__file__",
                str(scripts_dir / "validate_hooks_settings.py"),
            ):
                captured_output = io.StringIO()
                with patch("sys.stdout", captured_output):
                    result = main()

            assert result == 0
            assert "No hook file references found" in captured_output.getvalue()

    def test_all_hooks_exist(self):
        """Should return 0 when all hook files exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            claude_dir = tmpdir_path / ".claude"
            claude_dir.mkdir()
            scripts_dir = claude_dir / "scripts"
            scripts_dir.mkdir()
            hooks_dir = claude_dir / "hooks"
            hooks_dir.mkdir()

            # Create hook file
            hook_file = hooks_dir / "test_hook.py"
            hook_file.write_text("# hook")

            settings_path = claude_dir / "settings.json"
            settings = {
                "hooks": {
                    "PreToolUse": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/test-hook.py',
                                },
                            ],
                        }
                    ]
                }
            }
            settings_path.write_text(json.dumps(settings))

            with patch.object(
                validate_hooks_settings,
                "__file__",
                str(scripts_dir / "validate_hooks_settings.py"),
            ):
                captured_output = io.StringIO()
                with patch("sys.stdout", captured_output):
                    result = main()

            assert result == 0
            assert "All 1 hook file references are valid" in captured_output.getvalue()

    def test_missing_hook_file(self):
        """Should return 1 when hook file is missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            claude_dir = tmpdir_path / ".claude"
            claude_dir.mkdir()
            scripts_dir = claude_dir / "scripts"
            scripts_dir.mkdir()
            hooks_dir = claude_dir / "hooks"
            hooks_dir.mkdir()

            settings_path = claude_dir / "settings.json"
            settings = {
                "hooks": {
                    "PreToolUse": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/missing-hook.py',
                                },
                            ],
                        }
                    ]
                }
            }
            settings_path.write_text(json.dumps(settings))

            with patch.object(
                validate_hooks_settings,
                "__file__",
                str(scripts_dir / "validate_hooks_settings.py"),
            ):
                captured_output = io.StringIO()
                with patch("sys.stdout", captured_output):
                    result = main()

            assert result == 1
            output = captured_output.getvalue()
            assert "Missing hook files detected" in output
            assert "missing_hook.py" in output

    def test_partial_missing_hooks(self):
        """Should detect missing files when some hooks exist and others don't."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            claude_dir = tmpdir_path / ".claude"
            claude_dir.mkdir()
            scripts_dir = claude_dir / "scripts"
            scripts_dir.mkdir()
            hooks_dir = claude_dir / "hooks"
            hooks_dir.mkdir()

            # Create one hook file
            existing_hook = hooks_dir / "existing.py"
            existing_hook.write_text("# hook")

            settings_path = claude_dir / "settings.json"
            settings = {
                "hooks": {
                    "PreToolUse": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/existing.py',
                                },
                                {
                                    "type": "command",
                                    "command": 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/missing.py',
                                },
                            ],
                        }
                    ]
                }
            }
            settings_path.write_text(json.dumps(settings))

            with patch.object(
                validate_hooks_settings,
                "__file__",
                str(scripts_dir / "validate_hooks_settings.py"),
            ):
                captured_output = io.StringIO()
                with patch("sys.stdout", captured_output):
                    result = main()

            assert result == 1
            output = captured_output.getvalue()
            assert "Missing hook files detected" in output
            assert "missing.py" in output
            assert "existing.py" not in output


class TestRegexEdgeCases:
    """Tests for regex edge cases in path extraction."""

    def test_quoted_path_with_space_before(self):
        """Should handle extra spaces before path."""
        settings = {
            "hooks": {
                "PreToolUse": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": 'python3   "$CLAUDE_PROJECT_DIR"/.claude/hooks/test.py',
                            },
                        ],
                    }
                ]
            }
        }
        result = extract_hook_paths(settings, Path("/project"))

        assert len(result) == 1

    def test_path_ending_with_py_but_not_extension(self):
        """Should only match .py extension, not 'py' in filename."""
        settings = {
            "hooks": {
                "PreToolUse": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/happy.py',
                            },
                        ],
                    }
                ]
            }
        }
        result = extract_hook_paths(settings, Path("/project"))

        assert len(result) == 1
        assert result[0][1] == Path("/project/.claude/hooks/happy.py")

    def test_scripts_directory_path(self):
        """Should handle scripts in scripts directory."""
        settings = {
            "hooks": {
                "PreToolUse": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": 'python3 "$CLAUDE_PROJECT_DIR"/.claude/scripts/check.py',
                            },
                        ],
                    }
                ]
            }
        }
        result = extract_hook_paths(settings, Path("/project"))

        assert len(result) == 1
        assert result[0][1] == Path("/project/.claude/scripts/check.py")
