#!/usr/bin/env python3
"""Tests for codex-review-check.py hook."""

import json
import subprocess
import tempfile
from pathlib import Path

HOOK_PATH = Path(__file__).parent.parent / "codex-review-check.py"


def run_hook(input_data: dict) -> dict:
    """Run the hook with given input and return the result."""
    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


class TestCodexReviewCheckBasic:
    """Basic tests for codex-review-check hook."""

    def test_approve_non_pr_create_commands(self):
        """Should approve commands that are not gh pr create."""
        test_cases = [
            "ls -la",
            "git status",
            "gh pr view 123",
            "gh pr merge 123 --squash",
            "echo 'gh pr create'",
        ]

        for command in test_cases:
            with self.subTest(command=command):
                result = run_hook({"tool_input": {"command": command}})
                assert result["decision"] == "approve", f"Should approve: {command}"

    def test_approve_empty_command(self):
        """Should approve when command is empty."""
        result = run_hook({"tool_input": {"command": ""}})
        assert result["decision"] == "approve"

    def test_approve_no_tool_input(self):
        """Should approve when tool_input is missing."""
        result = run_hook({})
        assert result["decision"] == "approve"


class TestCodexReviewCheckHelpers:
    """Tests for helper functions in codex-review-check."""

    def test_check_review_done_file_exists(self):
        """Test check_review_done returns True when log file exists with matching commit."""
        import importlib.util
        import sys

        # Add parent directory to path for common module import
        parent_dir = str(Path(__file__).parent.parent)
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)

        spec = importlib.util.spec_from_file_location("codex_review_check", HOOK_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # Create a temporary log file
        with tempfile.TemporaryDirectory() as tmpdir:
            # Patch LOG_DIR to use temp directory
            original_log_dir = module.MARKERS_LOG_DIR
            module.MARKERS_LOG_DIR = Path(tmpdir)

            try:
                # Create log file for branch "test-branch" with commit "abc1234"
                log_file = Path(tmpdir) / "codex-review-test-branch.done"
                log_file.write_text("test-branch:abc1234")

                # Matching commit should return True
                is_reviewed, reviewed_commit, diff_matched = module.check_review_done(
                    "test-branch", "abc1234", None
                )
                assert is_reviewed
                assert reviewed_commit == "abc1234"
                assert not diff_matched

                # Different commit should return False with reviewed_commit info
                is_reviewed, reviewed_commit, diff_matched = module.check_review_done(
                    "test-branch", "def5678", None
                )
                assert not is_reviewed
                assert reviewed_commit == "abc1234"
                assert not diff_matched

                # Non-existent branch should return False, None
                is_reviewed, reviewed_commit, diff_matched = module.check_review_done(
                    "other-branch", "abc1234", None
                )
                assert not is_reviewed
                assert reviewed_commit is None
                assert not diff_matched
            finally:
                module.MARKERS_LOG_DIR = original_log_dir

    def test_check_review_done_with_slash_branch(self):
        """Test check_review_done handles branch names with slashes."""
        import importlib.util
        import sys

        parent_dir = str(Path(__file__).parent.parent)
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)

        spec = importlib.util.spec_from_file_location("codex_review_check", HOOK_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = module.MARKERS_LOG_DIR
            module.MARKERS_LOG_DIR = Path(tmpdir)

            try:
                # Create log file for branch "feature/test" -> sanitized to "feature-test"
                log_file = Path(tmpdir) / "codex-review-feature-test.done"
                log_file.write_text("feature/test:abc1234")

                is_reviewed, _, _ = module.check_review_done("feature/test", "abc1234", None)
                assert is_reviewed
            finally:
                module.MARKERS_LOG_DIR = original_log_dir

    def test_check_review_done_invalid_format(self):
        """Test check_review_done handles invalid log format."""
        import importlib.util
        import sys

        parent_dir = str(Path(__file__).parent.parent)
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)

        spec = importlib.util.spec_from_file_location("codex_review_check", HOOK_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = module.MARKERS_LOG_DIR
            module.MARKERS_LOG_DIR = Path(tmpdir)

            try:
                # Create invalid log file (branch name only, missing commit and diff_hash)
                log_file = Path(tmpdir) / "codex-review-test-branch.done"
                log_file.write_text("test-branch")

                # Invalid format should be treated as not reviewed
                is_reviewed, reviewed_commit, diff_matched = module.check_review_done(
                    "test-branch", "abc1234", None
                )
                assert not is_reviewed
                assert reviewed_commit is None
                assert not diff_matched
            finally:
                module.MARKERS_LOG_DIR = original_log_dir

    def test_check_review_done_diff_hash_match(self):
        """Test check_review_done returns True when diff hash matches (Issue #841)."""
        import importlib.util
        import sys

        parent_dir = str(Path(__file__).parent.parent)
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)

        spec = importlib.util.spec_from_file_location("codex_review_check", HOOK_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = module.MARKERS_LOG_DIR
            module.MARKERS_LOG_DIR = Path(tmpdir)

            try:
                # Create log file with diff hash (new format: branch:commit:diff_hash)
                log_file = Path(tmpdir) / "codex-review-test-branch.done"
                log_file.write_text("test-branch:abc1234:diffhash123")

                # Same commit should return True, diff_matched=False
                is_reviewed, reviewed_commit, diff_matched = module.check_review_done(
                    "test-branch", "abc1234", "diffhash123"
                )
                assert is_reviewed
                assert reviewed_commit == "abc1234"
                assert not diff_matched  # commit matched, not diff

                # Different commit but same diff hash should return True, diff_matched=True
                is_reviewed, reviewed_commit, diff_matched = module.check_review_done(
                    "test-branch", "def5678", "diffhash123"
                )
                assert is_reviewed
                assert reviewed_commit == "abc1234"
                assert diff_matched  # diff matched after rebase

                # Different commit and different diff hash should return False
                is_reviewed, reviewed_commit, diff_matched = module.check_review_done(
                    "test-branch", "def5678", "otherhash456"
                )
                assert not is_reviewed
                assert reviewed_commit == "abc1234"
                assert not diff_matched
            finally:
                module.MARKERS_LOG_DIR = original_log_dir


class TestCodexReviewCheckRegex:
    """Tests for regex pattern strictness."""

    def test_is_gh_pr_create_command(self):
        """Test is_gh_pr_create_command function."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("codex_review_check", HOOK_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # Should detect
        assert module.is_gh_pr_create_command("gh pr create --title 'test'")
        assert module.is_gh_pr_create_command("gh  pr  create")
        assert module.is_gh_pr_create_command("some_cmd && gh pr create --title test")

        # Should NOT detect (inside quotes)
        assert not module.is_gh_pr_create_command("echo 'gh pr create'")
        assert not module.is_gh_pr_create_command('echo "gh pr create"')
        assert not module.is_gh_pr_create_command("printf 'run gh pr create to create PR'")

        # Should NOT detect (different commands)
        assert not module.is_gh_pr_create_command("gh pr view 123")
        assert not module.is_gh_pr_create_command("gh pr merge 123")
        assert not module.is_gh_pr_create_command("")

    def test_quoted_string_edge_cases(self):
        """Test edge cases with quoted strings."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("codex_review_check", HOOK_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # gh pr create outside quotes should still be detected
        assert module.is_gh_pr_create_command("echo 'test' && gh pr create --title test")

        # Nested quotes (simplified - our regex doesn't handle escaped quotes)
        assert not module.is_gh_pr_create_command('echo "gh pr create"')


class TestCodexReviewCheckEdgeCases:
    """Tests for edge cases."""

    def test_whitespace_handling(self):
        """Should handle extra whitespace in commands."""
        # The regex should match with multiple spaces
        result = run_hook({"tool_input": {"command": "gh   pr   create   --title 'test'"}})
        # This will either block (no review) or require actual branch detection
        # For now, just verify it doesn't crash
        assert result["decision"] in ["approve", "block"]

    def test_case_sensitivity(self):
        """Commands should be case-sensitive (gh is lowercase)."""
        result = run_hook({"tool_input": {"command": "GH pr create --title 'test'"}})
        # Uppercase GH should not match, so approve
        assert result["decision"] == "approve"


class TestIsGitPushCommand:
    """Tests for is_git_push_command function."""

    def setup_method(self):
        import importlib.util
        import sys

        parent_dir = str(Path(__file__).parent.parent)
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)

        spec = importlib.util.spec_from_file_location("codex_review_check", HOOK_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_basic_git_push(self):
        """Should detect basic git push commands."""
        assert self.module.is_git_push_command("git push")
        assert self.module.is_git_push_command("git push origin main")
        assert self.module.is_git_push_command("git push -u origin feature/test")
        assert self.module.is_git_push_command("git  push")

    def test_git_push_in_chain(self):
        """Should detect git push in command chains."""
        assert self.module.is_git_push_command("git add . && git commit -m 'test' && git push")

    def test_exclude_quoted_strings(self):
        """Should not detect git push inside quoted strings."""
        assert not self.module.is_git_push_command("echo 'git push'")
        assert not self.module.is_git_push_command('echo "git push"')
        assert not self.module.is_git_push_command("printf 'Run git push to upload changes'")

    def test_exclude_help_command(self):
        """Should not detect git push --help."""
        assert not self.module.is_git_push_command("git push --help")
        # Note: -h is NOT excluded (only --help is checked)
        assert self.module.is_git_push_command("git push -h")

    def test_empty_command(self):
        """Should return False for empty commands."""
        assert not self.module.is_git_push_command("")
        assert not self.module.is_git_push_command("   ")

    def test_non_push_git_commands(self):
        """Should not detect other git commands."""
        assert not self.module.is_git_push_command("git pull")
        assert not self.module.is_git_push_command("git status")
        assert not self.module.is_git_push_command("git commit -m 'test'")


class TestGetBlockReason:
    """Tests for get_block_reason function."""

    def setup_method(self):
        import importlib.util
        import sys

        parent_dir = str(Path(__file__).parent.parent)
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)

        spec = importlib.util.spec_from_file_location("codex_review_check", HOOK_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_no_review_pr_create(self):
        """Should generate appropriate message when no review record exists (PR create)."""
        reason = self.module.get_block_reason("test-branch", "abc123", None, "pr_create")
        assert "Codex CLIレビューが実行されていません" in reason
        assert "test-branch" in reason
        assert "PRを作成する" in reason
        assert "codex review --base main" in reason

    def test_no_review_git_push(self):
        """Should generate appropriate message when no review record exists (git push)."""
        reason = self.module.get_block_reason("test-branch", "abc123", None, "git_push")
        assert "Codex CLIレビューが実行されていません" in reason
        assert "test-branch" in reason
        assert "プッシュする" in reason

    def test_outdated_review(self):
        """Should generate message when review was done for different commit."""
        reason = self.module.get_block_reason("test-branch", "new123", "old456", "pr_create")
        assert "レビュー後に新しいコミットがあります" in reason
        assert "レビュー済みコミット: old456" in reason
        assert "現在のHEAD: new123" in reason


class TestHasSkipCodexReviewEnv:
    """Tests for has_skip_codex_review_env function (Issue #945)."""

    def setup_method(self):
        import importlib.util
        import sys

        parent_dir = str(Path(__file__).parent.parent)
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)

        spec = importlib.util.spec_from_file_location("codex_review_check", HOOK_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_inline_env_var(self):
        """Should detect inline SKIP_CODEX_REVIEW env var."""
        assert self.module.has_skip_codex_review_env("SKIP_CODEX_REVIEW=1 git push")
        assert self.module.has_skip_codex_review_env("SKIP_CODEX_REVIEW=true git push")

    def test_exported_env_var(self):
        """Should detect exported SKIP_CODEX_REVIEW env var."""
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"SKIP_CODEX_REVIEW": "1"}):
            assert self.module.has_skip_codex_review_env("git push")

    def test_no_env_var(self):
        """Should return False when SKIP_CODEX_REVIEW is not set."""
        import os
        from unittest.mock import patch

        # Ensure env var is not set
        env = {k: v for k, v in os.environ.items() if k != "SKIP_CODEX_REVIEW"}
        with patch.dict(os.environ, env, clear=True):
            assert not self.module.has_skip_codex_review_env("git push")

    def test_similar_env_var_not_matched(self):
        """Should not match similar but different env var names."""
        assert not self.module.has_skip_codex_review_env("SKIP_CODEX=1 git push")
        assert not self.module.has_skip_codex_review_env("CODEX_REVIEW=1 git push")

    def test_quoted_text_not_matched(self):
        """Should not match SKIP_CODEX_REVIEW inside quoted strings."""
        assert not self.module.has_skip_codex_review_env("echo 'SKIP_CODEX_REVIEW=1' && git push")
        assert not self.module.has_skip_codex_review_env('echo "SKIP_CODEX_REVIEW=1"')
        assert not self.module.has_skip_codex_review_env("sh -c 'SKIP_CODEX_REVIEW=1 git push'")

    def test_inline_env_var_falsy_values(self):
        """Should NOT skip when SKIP_CODEX_REVIEW has falsy value (Issue #956)."""
        assert not self.module.has_skip_codex_review_env("SKIP_CODEX_REVIEW=0 git push")
        assert not self.module.has_skip_codex_review_env("SKIP_CODEX_REVIEW=false git push")
        assert not self.module.has_skip_codex_review_env("SKIP_CODEX_REVIEW=False git push")

    def test_exported_env_var_falsy_values(self):
        """Should NOT skip when exported SKIP_CODEX_REVIEW has falsy value (Issue #956)."""
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"SKIP_CODEX_REVIEW": "0"}, clear=True):
            assert not self.module.has_skip_codex_review_env("git push")
        with patch.dict(os.environ, {"SKIP_CODEX_REVIEW": "false"}, clear=True):
            assert not self.module.has_skip_codex_review_env("git push")
        with patch.dict(os.environ, {"SKIP_CODEX_REVIEW": ""}, clear=True):
            assert not self.module.has_skip_codex_review_env("git push")

    def test_inline_env_var_quoted_value(self):
        """Should skip when SKIP_CODEX_REVIEW has quoted truthy value (Issue #956 fix)."""
        assert self.module.has_skip_codex_review_env('SKIP_CODEX_REVIEW="1" git push')
        assert self.module.has_skip_codex_review_env("SKIP_CODEX_REVIEW='true' git push")


class TestSkipCodexReviewIntegration:
    """Integration tests for SKIP_CODEX_REVIEW functionality."""

    def test_skip_with_inline_env_var(self):
        """Should approve when SKIP_CODEX_REVIEW=1 is in command."""
        result = run_hook({"tool_input": {"command": "SKIP_CODEX_REVIEW=1 git push origin main"}})
        assert result["decision"] == "approve"
        assert "SKIP_CODEX_REVIEW" in result.get("systemMessage", "")

    def test_skip_with_exported_env_var(self):
        """Should approve when SKIP_CODEX_REVIEW is exported in environment."""
        import os

        # Set environment variable
        os.environ["SKIP_CODEX_REVIEW"] = "1"
        try:
            result = run_hook({"tool_input": {"command": "git push origin main"}})
            assert result["decision"] == "approve"
            assert "SKIP_CODEX_REVIEW" in result.get("systemMessage", "")
        finally:
            # Clean up
            del os.environ["SKIP_CODEX_REVIEW"]

    def test_skip_with_inline_env_var_gh_pr_create(self):
        """Should approve gh pr create when SKIP_CODEX_REVIEW=1 is in command."""
        result = run_hook(
            {"tool_input": {"command": 'SKIP_CODEX_REVIEW=1 gh pr create --title "test"'}}
        )
        assert result["decision"] == "approve"
        assert "SKIP_CODEX_REVIEW" in result.get("systemMessage", "")


class TestCheckAndBlockIfNotReviewed:
    """Tests for check_and_block_if_not_reviewed function."""

    def setup_method(self):
        import importlib.util
        import sys

        parent_dir = str(Path(__file__).parent.parent)
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)

        spec = importlib.util.spec_from_file_location("codex_review_check", HOOK_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_skip_main_branch(self):
        """Should skip check for main/master branches."""
        # Mock get_current_branch to return "main"
        original_get_branch = self.module.get_current_branch
        self.module.get_current_branch = lambda: "main"
        try:
            result = self.module.check_and_block_if_not_reviewed("pr_create")
            assert result is None
        finally:
            self.module.get_current_branch = original_get_branch

    def test_skip_master_branch(self):
        """Should skip check for master branch."""
        original_get_branch = self.module.get_current_branch
        self.module.get_current_branch = lambda: "master"
        try:
            result = self.module.check_and_block_if_not_reviewed("git_push")
            assert result is None
        finally:
            self.module.get_current_branch = original_get_branch

    def test_block_on_git_error(self):
        """Should block if branch cannot be determined."""
        original_get_branch = self.module.get_current_branch
        self.module.get_current_branch = lambda: None
        try:
            result = self.module.check_and_block_if_not_reviewed("pr_create")
            assert result is not None
            assert result["decision"] == "block"
            assert "ブランチ名を取得できませんでした" in result["reason"]
        finally:
            self.module.get_current_branch = original_get_branch

    def test_block_when_not_reviewed(self):
        """Should block when review is not done."""
        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = self.module.MARKERS_LOG_DIR
            original_get_branch = self.module.get_current_branch
            original_get_commit = self.module.get_head_commit

            self.module.MARKERS_LOG_DIR = Path(tmpdir)
            self.module.get_current_branch = lambda: "feature/test"
            self.module.get_head_commit = lambda: "abc123"

            try:
                result = self.module.check_and_block_if_not_reviewed("pr_create")
                assert result is not None
                assert result["decision"] == "block"
            finally:
                self.module.MARKERS_LOG_DIR = original_log_dir
                self.module.get_current_branch = original_get_branch
                self.module.get_head_commit = original_get_commit

    def test_approve_when_reviewed(self):
        """Should return None (approve) when review is done."""
        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = self.module.MARKERS_LOG_DIR
            original_get_branch = self.module.get_current_branch
            original_get_commit = self.module.get_head_commit

            self.module.MARKERS_LOG_DIR = Path(tmpdir)
            self.module.get_current_branch = lambda: "feature/test"
            self.module.get_head_commit = lambda: "abc123"

            # Create review log file
            log_file = Path(tmpdir) / "codex-review-feature-test.done"
            log_file.write_text("feature/test:abc123")

            try:
                result = self.module.check_and_block_if_not_reviewed("pr_create")
                assert result is None
            finally:
                self.module.MARKERS_LOG_DIR = original_log_dir
                self.module.get_current_branch = original_get_branch
                self.module.get_head_commit = original_get_commit

    def test_skip_when_pr_already_merged(self):
        """Should return None (approve) when PR is already merged (Issue #890)."""
        original_get_branch = self.module.get_current_branch
        original_get_pr = self.module.get_pr_number_for_branch
        original_is_merged = self.module.is_pr_merged

        self.module.get_current_branch = lambda: "feature/test"
        self.module.get_pr_number_for_branch = lambda branch: "123"
        self.module.is_pr_merged = lambda pr: True

        try:
            result = self.module.check_and_block_if_not_reviewed("git_push")
            # Should return None (approve) because PR is already merged
            assert result is None
        finally:
            self.module.get_current_branch = original_get_branch
            self.module.get_pr_number_for_branch = original_get_pr
            self.module.is_pr_merged = original_is_merged

    def test_check_when_pr_not_merged(self):
        """Should proceed with check when PR exists but is not merged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = self.module.MARKERS_LOG_DIR
            original_get_branch = self.module.get_current_branch
            original_get_commit = self.module.get_head_commit
            original_get_pr = self.module.get_pr_number_for_branch
            original_is_merged = self.module.is_pr_merged

            self.module.MARKERS_LOG_DIR = Path(tmpdir)
            self.module.get_current_branch = lambda: "feature/test"
            self.module.get_head_commit = lambda: "abc123"
            self.module.get_pr_number_for_branch = lambda branch: "456"
            self.module.is_pr_merged = lambda pr: False  # PR exists but not merged

            try:
                # No review log, so should block
                result = self.module.check_and_block_if_not_reviewed("git_push")
                assert result is not None
                assert result["decision"] == "block"
            finally:
                self.module.MARKERS_LOG_DIR = original_log_dir
                self.module.get_current_branch = original_get_branch
                self.module.get_head_commit = original_get_commit
                self.module.get_pr_number_for_branch = original_get_pr
                self.module.is_pr_merged = original_is_merged
