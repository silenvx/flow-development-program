#!/usr/bin/env python3
"""environment-integrity-check.py のテスト。"""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

# Add parent directory to path for module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)


# Import module with hyphen in name using importlib
spec = importlib.util.spec_from_file_location(
    "environment_integrity_check",
    Path(__file__).parent.parent / "environment-integrity-check.py",
)
hook_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook_module)


class TestGetRegisteredScripts:
    """get_registered_scripts関数のテスト。"""

    def test_extracts_python_scripts(self, tmp_path: Path) -> None:
        """settings.jsonからPythonスクリプトを抽出する。"""
        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/git-config-check.py',
                            },
                            {
                                "type": "command",
                                "command": 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/flow-state-updater.py',
                            },
                        ]
                    }
                ],
            }
        }
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(settings))

        with patch.object(hook_module, "SETTINGS_FILE", settings_file):
            result = hook_module.get_registered_scripts()

        assert ".claude/hooks/git-config-check.py" in result
        assert ".claude/hooks/flow-state-updater.py" in result

    def test_extracts_shell_scripts(self, tmp_path: Path) -> None:
        """settings.jsonからシェルスクリプトを抽出する。"""
        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": '"$CLAUDE_PROJECT_DIR"/scripts/check-lefthook.sh',
                            },
                        ]
                    }
                ],
            }
        }
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(settings))

        with patch.object(hook_module, "SETTINGS_FILE", settings_file):
            result = hook_module.get_registered_scripts()

        assert "scripts/check-lefthook.sh" in result

    def test_extracts_mixed_scripts(self, tmp_path: Path) -> None:
        """PythonとシェルスクリプトがOに抽出される。"""
        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/hook.py',
                            },
                            {
                                "type": "command",
                                "command": '"$CLAUDE_PROJECT_DIR"/scripts/check.sh',
                            },
                        ]
                    }
                ],
            }
        }
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(settings))

        with patch.object(hook_module, "SETTINGS_FILE", settings_file):
            result = hook_module.get_registered_scripts()

        assert ".claude/hooks/hook.py" in result
        assert "scripts/check.sh" in result

    def test_handles_missing_settings(self, tmp_path: Path) -> None:
        """settings.jsonが存在しない場合は空リストを返す。"""
        missing_file = tmp_path / "nonexistent.json"

        with patch.object(hook_module, "SETTINGS_FILE", missing_file):
            result = hook_module.get_registered_scripts()

        assert result == []

    def test_handles_invalid_json(self, tmp_path: Path) -> None:
        """settings.jsonが無効なJSONの場合は空リストを返す。"""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text("{ invalid json }")

        with patch.object(hook_module, "SETTINGS_FILE", settings_file):
            result = hook_module.get_registered_scripts()

        assert result == []

    def test_handles_empty_hooks(self, tmp_path: Path) -> None:
        """hooks が空の場合は空リストを返す。"""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"hooks": {}}))

        with patch.object(hook_module, "SETTINGS_FILE", settings_file):
            result = hook_module.get_registered_scripts()

        assert result == []


class TestCheckScriptFiles:
    """check_script_files関数のテスト。"""

    def test_detects_missing_python_files(self, tmp_path: Path) -> None:
        """不足しているPythonファイルを検出する。"""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        hooks_dir = project_dir / ".claude" / "hooks"
        hooks_dir.mkdir(parents=True)
        (hooks_dir / "exists.py").touch()

        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/exists.py',
                            },
                            {
                                "type": "command",
                                "command": 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/missing.py',
                            },
                        ]
                    }
                ]
            }
        }
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(settings))

        with (
            patch.object(hook_module, "PROJECT_DIR", project_dir),
            patch.object(hook_module, "SETTINGS_FILE", settings_file),
        ):
            found, missing = hook_module.check_script_files()

        assert ".claude/hooks/exists.py" in found
        assert ".claude/hooks/missing.py" in missing

    def test_detects_missing_shell_scripts(self, tmp_path: Path) -> None:
        """不足しているシェルスクリプトを検出する。"""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        scripts_dir = project_dir / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "exists.sh").touch()

        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": '"$CLAUDE_PROJECT_DIR"/scripts/exists.sh',
                            },
                            {
                                "type": "command",
                                "command": '"$CLAUDE_PROJECT_DIR"/scripts/missing.sh',
                            },
                        ]
                    }
                ]
            }
        }
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(settings))

        with (
            patch.object(hook_module, "PROJECT_DIR", project_dir),
            patch.object(hook_module, "SETTINGS_FILE", settings_file),
        ):
            found, missing = hook_module.check_script_files()

        assert "scripts/exists.sh" in found
        assert "scripts/missing.sh" in missing

    def test_all_files_exist(self, tmp_path: Path) -> None:
        """全ファイル存在時はmissingが空。"""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        hooks_dir = project_dir / ".claude" / "hooks"
        hooks_dir.mkdir(parents=True)
        scripts_dir = project_dir / "scripts"
        scripts_dir.mkdir()
        (hooks_dir / "hook.py").touch()
        (scripts_dir / "script.sh").touch()

        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/hook.py',
                            },
                            {
                                "type": "command",
                                "command": '"$CLAUDE_PROJECT_DIR"/scripts/script.sh',
                            },
                        ]
                    }
                ]
            }
        }
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(settings))

        with (
            patch.object(hook_module, "PROJECT_DIR", project_dir),
            patch.object(hook_module, "SETTINGS_FILE", settings_file),
        ):
            found, missing = hook_module.check_script_files()

        assert len(found) == 2
        assert len(missing) == 0


class TestMain:
    """main関数のテスト。"""

    def test_outputs_warning_for_missing(self, capsys, tmp_path: Path) -> None:
        """不足時に警告を出力する。"""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/missing-hook.py',
                            }
                        ]
                    }
                ]
            }
        }
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(settings))

        with (
            patch.object(hook_module, "PROJECT_DIR", project_dir),
            patch.object(hook_module, "SETTINGS_FILE", settings_file),
            patch.object(hook_module, "parse_hook_input", return_value={}),
        ):
            hook_module.main()

        captured = capsys.readouterr()
        assert "environment-integrity-check" in captured.err
        assert "missing-hook.py" in captured.err
        assert "不足ファイル" in captured.err
        assert '{"continue": true}' in captured.out

    def test_always_continues(self, capsys, tmp_path: Path) -> None:
        """常にcontinue: trueを返す（不足時も含む）。"""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/missing.py',
                            }
                        ]
                    }
                ]
            }
        }
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(settings))

        with (
            patch.object(hook_module, "PROJECT_DIR", project_dir),
            patch.object(hook_module, "SETTINGS_FILE", settings_file),
            patch.object(hook_module, "parse_hook_input", return_value={}),
        ):
            hook_module.main()

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["continue"] is True

    def test_no_warning_when_all_exist(self, capsys, tmp_path: Path) -> None:
        """全ファイル存在時は警告なし。"""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        hooks_dir = project_dir / ".claude" / "hooks"
        hooks_dir.mkdir(parents=True)
        (hooks_dir / "hook.py").touch()

        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/hook.py',
                            }
                        ]
                    }
                ]
            }
        }
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(settings))

        with (
            patch.object(hook_module, "PROJECT_DIR", project_dir),
            patch.object(hook_module, "SETTINGS_FILE", settings_file),
            patch.object(hook_module, "parse_hook_input", return_value={}),
        ):
            hook_module.main()

        captured = capsys.readouterr()
        assert captured.err == ""
        assert '{"continue": true}' in captured.out
