#!/usr/bin/env python3
"""Tests for hook-change-detector.py hook."""

import json
import os
import subprocess
from pathlib import Path

HOOK_PATH = Path(__file__).parent.parent / "hook-change-detector.py"


def run_hook(input_data: dict, env: dict | None = None) -> dict:
    """Run the hook with given input and return the result.

    Args:
        input_data: The JSON input data to pass to the hook.
        env: Optional environment variables to set for the subprocess.
             If provided, these are merged with the current environment.
    """
    # Merge with current environment if custom env is provided
    subprocess_env = os.environ.copy()
    if env:
        subprocess_env.update(env)

    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
        env=subprocess_env,
    )
    return json.loads(result.stdout)


class TestHookChangeDetectorIntegration:
    """Integration tests for hook-change-detector hook."""

    def test_ignores_non_git_commands(self):
        """Should approve non-git commands."""
        result = run_hook(
            {"tool_input": {"command": "ls -la"}},
            env={"_TEST_STAGED_FILES": ""},
        )
        assert result["decision"] == "approve"

    def test_ignores_git_status(self):
        """Should approve git status commands."""
        result = run_hook(
            {"tool_input": {"command": "git status"}},
            env={"_TEST_STAGED_FILES": ""},
        )
        assert result["decision"] == "approve"

    def test_ignores_git_push(self):
        """Should approve git push commands."""
        result = run_hook(
            {"tool_input": {"command": "git push"}},
            env={"_TEST_STAGED_FILES": ""},
        )
        assert result["decision"] == "approve"

    def test_handles_empty_command(self):
        """Should handle empty command gracefully."""
        result = run_hook(
            {"tool_input": {"command": ""}},
            env={"_TEST_STAGED_FILES": ""},
        )
        assert result["decision"] == "approve"

    def test_handles_missing_tool_input(self):
        """Should handle missing tool_input gracefully."""
        result = run_hook({}, env={"_TEST_STAGED_FILES": ""})
        assert result["decision"] == "approve"


class TestHookChangeDetectorUnit:
    """Unit tests for hook-change-detector hook functions."""

    def setup_method(self):
        """Import module functions for testing."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("hook_change_detector", str(HOOK_PATH))
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_is_hook_file_positive(self):
        """Should identify hook files correctly."""
        assert self.module.is_hook_file(".claude/hooks/my-hook.py")
        assert self.module.is_hook_file(".claude/hooks/python-lint-check.py")
        assert self.module.is_hook_file(".claude/hooks/merge-check.py")

    def test_is_hook_file_excludes_tests(self):
        """Should exclude test files."""
        assert not self.module.is_hook_file(".claude/hooks/tests/test_my_hook.py")
        assert not self.module.is_hook_file(".claude/hooks/tests/conftest.py")

    def test_is_hook_file_excludes_lib(self):
        """Should exclude lib files."""
        assert not self.module.is_hook_file(".claude/hooks/lib/constants.py")
        assert not self.module.is_hook_file(".claude/hooks/lib/git.py")

    def test_is_hook_file_excludes_non_py(self):
        """Should exclude non-Python files."""
        assert not self.module.is_hook_file(".claude/hooks/README.md")
        assert not self.module.is_hook_file(".claude/hooks/config.json")

    def test_is_hook_file_excludes_other_dirs(self):
        """Should exclude files outside .claude/hooks/."""
        assert not self.module.is_hook_file("src/main.py")
        assert not self.module.is_hook_file(".claude/scripts/my-script.py")
        assert not self.module.is_hook_file("frontend/src/App.tsx")

    def test_classify_staged_files_mixed(self):
        """Should classify hook and non-hook files correctly."""
        files = [
            ".claude/hooks/my-hook.py",
            "src/main.py",
            ".claude/hooks/tests/test_my_hook.py",
            "frontend/App.tsx",
        ]
        hook_files, non_hook_files = self.module.classify_staged_files(files)
        assert hook_files == [".claude/hooks/my-hook.py"]
        assert non_hook_files == [
            "src/main.py",
            ".claude/hooks/tests/test_my_hook.py",
            "frontend/App.tsx",
        ]

    def test_classify_staged_files_only_hooks(self):
        """Should handle hook-only files."""
        files = [".claude/hooks/my-hook.py", ".claude/hooks/another-hook.py"]
        hook_files, non_hook_files = self.module.classify_staged_files(files)
        assert hook_files == [".claude/hooks/my-hook.py", ".claude/hooks/another-hook.py"]
        assert non_hook_files == []

    def test_classify_staged_files_only_non_hooks(self):
        """Should handle non-hook-only files."""
        files = ["src/main.py", "frontend/App.tsx"]
        hook_files, non_hook_files = self.module.classify_staged_files(files)
        assert hook_files == []
        assert non_hook_files == ["src/main.py", "frontend/App.tsx"]

    def test_classify_staged_files_empty(self):
        """Should handle empty file list."""
        hook_files, non_hook_files = self.module.classify_staged_files([])
        assert hook_files == []
        assert non_hook_files == []

    def test_is_git_add_or_commit_command(self):
        """Should detect git add/commit commands."""
        assert self.module.is_git_add_or_commit_command("git add .")
        assert self.module.is_git_add_or_commit_command("git commit -m 'test'")
        assert self.module.is_git_add_or_commit_command("git add . && git commit")

    def test_is_git_add_or_commit_command_negative(self):
        """Should NOT detect non-add/commit commands."""
        assert not self.module.is_git_add_or_commit_command("git status")
        assert not self.module.is_git_add_or_commit_command("git push")
        assert not self.module.is_git_add_or_commit_command("ls -la")
        assert not self.module.is_git_add_or_commit_command("")


class TestMixedStagingWarning:
    """Tests for mixed staging warning scenarios."""

    def test_warns_on_mixed_staging(self):
        """Should warn when hook and non-hook files are staged together."""
        result = run_hook(
            {"tool_input": {"command": "git commit -m 'test'"}},
            env={"_TEST_STAGED_FILES": ".claude/hooks/my-hook.py,src/main.py"},
        )
        assert result["decision"] == "approve"  # Warn, not block
        assert "systemMessage" in result
        assert "Chicken-and-egg" in result["systemMessage"]
        assert "hook-change-detector" in result["systemMessage"]

    def test_no_chicken_egg_warning_on_hook_only(self):
        """Should NOT show Chicken-and-egg warning when only hook files are staged.

        Note: hooks-reference Skill reminder is still shown for hook files.
        """
        result = run_hook(
            {"tool_input": {"command": "git commit -m 'test'"}},
            env={"_TEST_STAGED_FILES": ".claude/hooks/my-hook.py,.claude/hooks/another.py"},
        )
        assert result["decision"] == "approve"
        # Chicken-and-egg warning should NOT be shown (only for mixed staging)
        assert "Chicken-and-egg" not in result.get("systemMessage", "")
        # But hooks-reference Skill reminder SHOULD be shown (for all hook files)
        assert "hooks-reference Skill" in result.get("systemMessage", "")

    def test_no_warning_on_non_hook_only(self):
        """Should NOT warn when only non-hook files are staged."""
        result = run_hook(
            {"tool_input": {"command": "git commit -m 'test'"}},
            env={"_TEST_STAGED_FILES": "src/main.py,frontend/App.tsx"},
        )
        assert result["decision"] == "approve"
        assert "systemMessage" not in result or "Chicken-and-egg" not in result.get(
            "systemMessage", ""
        )

    def test_no_warning_on_empty_staging(self):
        """Should NOT warn when no files are staged."""
        result = run_hook(
            {"tool_input": {"command": "git commit -m 'test'"}},
            env={"_TEST_STAGED_FILES": ""},
        )
        assert result["decision"] == "approve"

    def test_hook_with_tests_shows_warning(self):
        """Hook file with test file triggers warning (safe pattern, but warning is shown)."""
        result = run_hook(
            {"tool_input": {"command": "git commit -m 'test'"}},
            env={
                "_TEST_STAGED_FILES": ".claude/hooks/my-hook.py,.claude/hooks/tests/test_my_hook.py"
            },
        )
        assert result["decision"] == "approve"
        # Test files count as non-hook, so warning is shown
        # However, the warning message explains this is a safe pattern
        assert "systemMessage" in result
        assert "Chicken-and-egg" in result["systemMessage"]
        assert "テストファイルとの混在" in result["systemMessage"]


class TestGitAddCommand:
    """Tests for git add command detection."""

    def test_detects_git_add_with_mixed_staging(self):
        """Should warn on git add when mixed files are already staged."""
        result = run_hook(
            {"tool_input": {"command": "git add ."}},
            env={"_TEST_STAGED_FILES": ".claude/hooks/my-hook.py,src/main.py"},
        )
        assert result["decision"] == "approve"
        # _TEST_STAGED_FILES simulates the current staging area
        # Since mixed files are staged, warning is shown
        assert "systemMessage" in result
        assert "Chicken-and-egg" in result["systemMessage"]

    def test_detects_git_add_chain_with_mixed_staging(self):
        """Should warn on git add && commit chain when mixed files are staged."""
        result = run_hook(
            {"tool_input": {"command": "git add . && git commit -m 'test'"}},
            env={"_TEST_STAGED_FILES": ".claude/hooks/my-hook.py,src/main.py"},
        )
        assert result["decision"] == "approve"
        # Hook checks staging area at the time of command invocation
        # Since _TEST_STAGED_FILES has mixed files, warning is shown
        assert "systemMessage" in result
        assert "Chicken-and-egg" in result["systemMessage"]


class TestPatternDetectionHook:
    """Tests for pattern detection hook functionality (Issue #1912)."""

    def setup_method(self):
        """Import module functions for testing."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("hook_change_detector", str(HOOK_PATH))
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_is_pattern_detection_hook_with_keywords(self):
        """Should detect hooks with *_KEYWORDS variable definitions."""
        content = """
DETECTION_KEYWORDS = [
    "後で",
    "将来",
]
"""
        assert self.module.is_pattern_detection_hook(content)

    def test_is_pattern_detection_hook_with_patterns(self):
        """Should detect hooks with *_PATTERNS variable definitions."""
        content = """
ERROR_PATTERNS = [
    r"Error:",
    r"Failed:",
]
"""
        assert self.module.is_pattern_detection_hook(content)

    def test_is_pattern_detection_hook_with_regex(self):
        """Should detect hooks with *_REGEX variable definitions."""
        content = """
URL_REGEX = [
    r"https?://",
]
"""
        assert self.module.is_pattern_detection_hook(content)

    def test_is_pattern_detection_hook_with_re_compile(self):
        """Should detect hooks using re.compile()."""
        content = """
pattern = re.compile(r"\\d+")
"""
        assert self.module.is_pattern_detection_hook(content)

    def test_is_pattern_detection_hook_with_re_search_pattern(self):
        """Should detect hooks using re.search with pattern variable."""
        content = """
if re.search(pattern, text):
    pass
"""
        assert self.module.is_pattern_detection_hook(content)

    def test_is_pattern_detection_hook_negative(self):
        """Should NOT detect hooks without pattern detection logic."""
        content = """
def main():
    data = parse_hook_input()
    command = data.get("command", "")
    print(json.dumps({"decision": "approve"}))
"""
        assert not self.module.is_pattern_detection_hook(content)

    def test_is_pattern_detection_hook_simple_regex(self):
        """Should detect hooks with raw string regex metacharacters."""
        content = """
patterns = [
    r"\\s+",
    r"\\d{4}",
]
"""
        assert self.module.is_pattern_detection_hook(content)

    def test_detect_pattern_hooks_with_content(self):
        """Should detect pattern hooks from staged files."""
        # Use environment variable to provide test content
        # Note: safe_name = file_path.replace("/", "_").replace(".", "_")
        # .claude/hooks/patternhook.py -> _claude_hooks_patternhook_py
        pattern_content = """
DETECTION_KEYWORDS = [
    "後で",
]
"""
        non_pattern_content = """
def main():
    pass
"""
        env = {
            "_TEST_FILE_CONTENT__claude_hooks_patternhook_py": pattern_content,
            "_TEST_FILE_CONTENT__claude_hooks_normalhook_py": non_pattern_content,
        }
        # Set environment variables temporarily
        for key, value in env.items():
            os.environ[key] = value

        try:
            hook_files = [".claude/hooks/patternhook.py", ".claude/hooks/normalhook.py"]
            pattern_hooks = self.module.detect_pattern_hooks(hook_files)
            assert ".claude/hooks/patternhook.py" in pattern_hooks
            assert ".claude/hooks/normalhook.py" not in pattern_hooks
        finally:
            # Clean up environment variables
            for key in env:
                os.environ.pop(key, None)

    def test_build_pattern_analysis_warning(self):
        """Should build proper warning message for pattern hooks."""
        pattern_hooks = [".claude/hooks/pattern-hook.py"]
        warning = self.module.build_pattern_analysis_warning(pattern_hooks)

        # Check key elements of the warning
        assert "パターン検出フック" in warning
        assert "実データ分析チェックリスト" in warning
        assert "実データソースを特定したか" in warning
        assert "GitHub PR comments" in warning
        assert "Issue comments" in warning
        assert "セッションログ" in warning
        assert "analyze-pattern-data.py" in warning
        assert "python3" in warning  # Should use python3, not python
        assert "pattern-hook.py" in warning

    def test_build_pattern_analysis_warning_truncates_long_list(self):
        """Should truncate hook list when more than 5 hooks."""
        pattern_hooks = [f".claude/hooks/hook-{i}.py" for i in range(8)]
        warning = self.module.build_pattern_analysis_warning(pattern_hooks)

        assert "hook-0.py" in warning
        assert "hook-4.py" in warning
        assert "hook-5.py" not in warning  # Should be truncated
        assert "and 3 more" in warning


class TestPatternDetectionWarningIntegration:
    """Integration tests for pattern detection warning."""

    def test_warns_on_pattern_hook_commit(self):
        """Should warn when pattern detection hook is committed.

        Both pattern analysis warning and hooks-reference Skill reminder are shown.
        """
        # Note: safe_name = file_path.replace("/", "_").replace(".", "_")
        # .claude/hooks/patternhook.py -> _claude_hooks_patternhook_py
        pattern_content = """
DETECTION_KEYWORDS = [
    "後で",
    "将来",
]
"""
        result = run_hook(
            {"tool_input": {"command": "git commit -m 'test'"}},
            env={
                "_TEST_STAGED_FILES": ".claude/hooks/patternhook.py",
                "_TEST_FILE_CONTENT__claude_hooks_patternhook_py": pattern_content,
            },
        )
        assert result["decision"] == "approve"
        assert "systemMessage" in result
        # Pattern analysis warning
        assert "パターン検出フック" in result["systemMessage"]
        assert "実データ分析チェックリスト" in result["systemMessage"]
        # hooks-reference Skill reminder (always shown for hook files)
        assert "hooks-reference Skill" in result["systemMessage"]

    def test_no_pattern_warning_for_normal_hook(self):
        """Should NOT show pattern warning for normal hooks without pattern detection.

        hooks-reference Skill reminder is still shown for all hook files.
        """
        # Note: safe_name = file_path.replace("/", "_").replace(".", "_")
        # .claude/hooks/normalhook.py -> _claude_hooks_normalhook_py
        normal_content = """
def main():
    print("hello")
"""
        result = run_hook(
            {"tool_input": {"command": "git commit -m 'test'"}},
            env={
                "_TEST_STAGED_FILES": ".claude/hooks/normalhook.py",
                "_TEST_FILE_CONTENT__claude_hooks_normalhook_py": normal_content,
            },
        )
        assert result["decision"] == "approve"
        assert "systemMessage" in result
        # Pattern detection warning should NOT be shown
        assert "パターン検出フック" not in result["systemMessage"]
        # hooks-reference Skill reminder SHOULD be shown
        assert "hooks-reference Skill" in result["systemMessage"]

    def test_all_three_warnings_on_mixed_pattern_hook(self):
        """Should show all three warnings when pattern hook is mixed with non-hook files.

        The three warnings are:
        1. Chicken-and-egg warning (mixed staging)
        2. Pattern detection warning
        3. hooks-reference Skill reminder
        """
        # Note: safe_name = file_path.replace("/", "_").replace(".", "_")
        # .claude/hooks/patternhook.py -> _claude_hooks_patternhook_py
        pattern_content = """
DETECTION_KEYWORDS = [
    "後で",
]
"""
        result = run_hook(
            {"tool_input": {"command": "git commit -m 'test'"}},
            env={
                "_TEST_STAGED_FILES": ".claude/hooks/patternhook.py,src/main.py",
                "_TEST_FILE_CONTENT__claude_hooks_patternhook_py": pattern_content,
            },
        )
        assert result["decision"] == "approve"
        assert "systemMessage" in result
        # 1. Chicken-and-egg warning (mixed staging)
        assert "Chicken-and-egg" in result["systemMessage"]
        # 2. Pattern detection warning
        assert "パターン検出フック" in result["systemMessage"]
        # 3. hooks-reference Skill reminder
        assert "hooks-reference Skill" in result["systemMessage"]


class TestHooksSkillReminder:
    """Tests for hooks-reference Skill reminder functionality (Issue #2379)."""

    def setup_method(self):
        """Import module functions for testing."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("hook_change_detector", str(HOOK_PATH))
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_build_hooks_skill_reminder(self):
        """Should build proper reminder message for hooks-reference Skill."""
        hook_files = [".claude/hooks/my-hook.py"]
        reminder = self.module.build_hooks_skill_reminder(hook_files)

        # Check key elements of the reminder
        assert "hooks-reference Skill" in reminder
        assert "参照リマインダー" in reminder
        assert "既存の実装パターン" in reminder
        assert "ZoneInfoNotFoundError" in reminder
        assert "make_block_result" in reminder
        assert "log_hook_execution" in reminder
        assert "SKIP環境変数" in reminder
        assert "/hooks-reference" in reminder
        assert "単純な修正だからSkill不要" in reminder
        assert "my-hook.py" in reminder

    def test_build_hooks_skill_reminder_truncates_long_list(self):
        """Should truncate hook list when more than 5 hooks."""
        hook_files = [f".claude/hooks/hook-{i}.py" for i in range(8)]
        reminder = self.module.build_hooks_skill_reminder(hook_files)

        assert "hook-0.py" in reminder
        assert "hook-4.py" in reminder
        assert "hook-5.py" not in reminder  # Should be truncated
        assert "and 3 more" in reminder


class TestHooksSkillReminderIntegration:
    """Integration tests for hooks-reference Skill reminder."""

    def test_shows_skill_reminder_on_hook_commit(self):
        """Should show hooks-reference Skill reminder when hook is committed."""
        normal_content = """
def main():
    print("hello")
"""
        result = run_hook(
            {"tool_input": {"command": "git commit -m 'test'"}},
            env={
                "_TEST_STAGED_FILES": ".claude/hooks/normalhook.py",
                "_TEST_FILE_CONTENT__claude_hooks_normalhook_py": normal_content,
            },
        )
        assert result["decision"] == "approve"
        assert "systemMessage" in result
        assert "hooks-reference Skill" in result["systemMessage"]
        assert "参照リマインダー" in result["systemMessage"]
        assert "単純な修正だからSkill不要" in result["systemMessage"]

    def test_no_skill_reminder_for_non_hook_files(self):
        """Should NOT show skill reminder when only non-hook files are staged."""
        result = run_hook(
            {"tool_input": {"command": "git commit -m 'test'"}},
            env={"_TEST_STAGED_FILES": "src/main.py,frontend/App.tsx"},
        )
        assert result["decision"] == "approve"
        # Should not have skill reminder
        if "systemMessage" in result:
            assert "hooks-reference Skill" not in result["systemMessage"]

    def test_shows_all_warnings_on_mixed_pattern_hook(self):
        """Should show all three warnings when pattern hook is mixed with non-hook files."""
        pattern_content = """
DETECTION_KEYWORDS = [
    "後で",
]
"""
        result = run_hook(
            {"tool_input": {"command": "git commit -m 'test'"}},
            env={
                "_TEST_STAGED_FILES": ".claude/hooks/patternhook.py,src/main.py",
                "_TEST_FILE_CONTENT__claude_hooks_patternhook_py": pattern_content,
            },
        )
        assert result["decision"] == "approve"
        assert "systemMessage" in result
        # Should have all three warnings
        assert "Chicken-and-egg" in result["systemMessage"]
        assert "パターン検出フック" in result["systemMessage"]
        assert "hooks-reference Skill" in result["systemMessage"]
