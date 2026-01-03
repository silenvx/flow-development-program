#!/usr/bin/env python3
"""
Unit tests for worktree-main-freshness-check.py

Tests cover:
- is_worktree_add_from_main detection
- fetch_origin_main function
- get_commit_hash function
- get_behind_count function
- main hook logic
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from conftest import load_hook_module

# Import the hook using conftest helper
hook_module = load_hook_module("worktree-main-freshness-check")

# Import symbols
is_worktree_add_from_main = hook_module.is_worktree_add_from_main
is_cwd_inside_worktree = hook_module.is_cwd_inside_worktree
extract_git_c_directory = hook_module.extract_git_c_directory
fetch_origin_main = hook_module.fetch_origin_main
get_commit_hash = hook_module.get_commit_hash
get_behind_count = hook_module.get_behind_count
get_current_branch = hook_module.get_current_branch
try_auto_pull_main = hook_module.try_auto_pull_main


class TestIsWorktreeAddFromMain:
    """Tests for is_worktree_add_from_main function."""

    def test_basic_worktree_add_main(self):
        """Test basic git worktree add with main."""
        assert is_worktree_add_from_main("git worktree add .worktrees/issue-123 main")

    def test_worktree_add_with_branch(self):
        """Test git worktree add with -b flag and main as base."""
        assert is_worktree_add_from_main(
            "git worktree add .worktrees/issue-123 -b feat/issue-123-xxx main"
        )

    def test_worktree_add_with_skip_plan(self):
        """Test with SKIP_PLAN=1 prefix."""
        assert is_worktree_add_from_main(
            "SKIP_PLAN=1 git worktree add .worktrees/issue-123 -b feat main"
        )

    def test_worktree_add_not_from_main(self):
        """Test worktree add from a different branch."""
        assert not is_worktree_add_from_main("git worktree add .worktrees/test -b feat develop")

    def test_not_worktree_command(self):
        """Test non-worktree commands."""
        assert not is_worktree_add_from_main("git status")
        assert not is_worktree_add_from_main("git branch main")
        assert not is_worktree_add_from_main("git checkout main")

    def test_worktree_list_command(self):
        """Test git worktree list (not add)."""
        assert not is_worktree_add_from_main("git worktree list")

    def test_worktree_add_from_origin_main(self):
        """Test git worktree add with origin/main as base."""
        assert is_worktree_add_from_main("git worktree add .worktrees/test -b feat origin/main")

    def test_main_in_branch_name(self):
        """Test when 'main' appears in new branch name (after -b)."""
        # This should be False because 'main' after -b is the new branch name
        assert not is_worktree_add_from_main(
            "git worktree add .worktrees/test -b main-feature develop"
        )


class TestExtractGitCDirectory:
    """Tests for extract_git_c_directory function (Issue #1405)."""

    def test_basic_git_c_option(self):
        """Test basic git -C /path command."""
        assert extract_git_c_directory("git -C /path/to/main worktree add ...") == "/path/to/main"

    def test_git_c_with_space_separated(self):
        """Test git -C with space-separated path."""
        assert extract_git_c_directory("git -C /tmp worktree list") == "/tmp"

    def test_git_c_with_quoted_path(self):
        """Test git -C with quoted path containing spaces."""
        assert (
            extract_git_c_directory("git -C '/path with spaces' worktree add ...")
            == "/path with spaces"
        )

    def test_git_c_with_double_quotes(self):
        """Test git -C with double-quoted path."""
        assert extract_git_c_directory('git -C "/path/to/main" worktree add ...') == "/path/to/main"

    def test_no_git_c_option(self):
        """Test command without -C option returns None."""
        assert extract_git_c_directory("git worktree add .worktrees/issue-123 main") is None

    def test_git_c_no_space_format(self):
        """Test git -C/path format (no space between -C and path)."""
        assert extract_git_c_directory("git -C/path/to/main worktree add ...") == "/path/to/main"

    def test_with_env_var_prefix(self):
        """Test with SKIP_PLAN=1 prefix."""
        assert (
            extract_git_c_directory("SKIP_PLAN=1 git -C /path/to/main worktree add ...")
            == "/path/to/main"
        )

    def test_non_git_command(self):
        """Test non-git command returns None."""
        assert extract_git_c_directory("ls -la") is None

    def test_invalid_shell_syntax(self):
        """Test invalid shell syntax (unclosed quote) returns None."""
        assert extract_git_c_directory("git -C '/unclosed worktree add") is None

    def test_git_c_at_end_without_path(self):
        """Test git -C at end without path returns None."""
        assert extract_git_c_directory("git -C") is None

    def test_full_path_git(self):
        """Test git with full path like /usr/bin/git."""
        assert (
            extract_git_c_directory("/usr/bin/git -C /path/to/main worktree add ...")
            == "/path/to/main"
        )

    def test_git_c_option_before_C(self):
        """Test git -c option before -C is handled correctly."""
        assert (
            extract_git_c_directory("git -c foo=bar -C /path/to/main worktree add ...")
            == "/path/to/main"
        )

    def test_git_multiple_c_options(self):
        """Test git with multiple -c options before -C."""
        assert extract_git_c_directory("git -c a=b -c c=d -C /path worktree add ...") == "/path"

    def test_chained_commands_first_git(self):
        """Test that chained commands extract from first git (documented limitation)."""
        # Note: This is a documented limitation - only first git command is processed
        result = extract_git_c_directory("git -C /path1 status && git -C /path2 worktree add ...")
        assert result == "/path1"  # First git's -C is returned


class TestIsCwdInsideWorktree:
    """Tests for is_cwd_inside_worktree function (Issue #822)."""

    @patch("worktree_main_freshness_check.subprocess.run")
    def test_inside_worktree_returns_true(self, mock_run: MagicMock, tmp_path):
        """Test detection when inside a worktree."""
        from pathlib import Path

        # Setup: create a fake worktree structure
        worktree_root = tmp_path / "worktree"
        worktree_root.mkdir()
        git_file = worktree_root / ".git"
        git_file.write_text("gitdir: /path/to/main/.git/worktrees/issue-123")

        # Mock git rev-parse to return our worktree root
        mock_run.return_value = MagicMock(returncode=0, stdout=str(worktree_root) + "\n")

        with patch("worktree_main_freshness_check.Path.cwd", return_value=worktree_root):
            # Need to patch the Path operations on repo_root
            with patch.object(Path, "is_file", return_value=True):
                with patch.object(
                    Path, "read_text", return_value="gitdir: /path/to/main/.git/worktrees/issue-123"
                ):
                    result, main_repo = is_cwd_inside_worktree()
                    assert result is True
                    assert main_repo == Path("/path/to/main")

    @patch("worktree_main_freshness_check.subprocess.run")
    def test_main_repo_returns_false(self, mock_run: MagicMock, tmp_path):
        """Test that main repository (not worktree) returns False."""
        # In main repo, .git is a directory, not a file
        main_repo = tmp_path / "main"
        main_repo.mkdir()
        git_dir = main_repo / ".git"
        git_dir.mkdir()

        mock_run.return_value = MagicMock(returncode=0, stdout=str(main_repo) + "\n")

        result, repo_path = is_cwd_inside_worktree()
        assert result is False
        assert repo_path is None

    @patch("worktree_main_freshness_check.subprocess.run")
    def test_git_rev_parse_failure(self, mock_run: MagicMock):
        """Test that git rev-parse failure returns False (fail open)."""
        mock_run.return_value = MagicMock(returncode=1)

        result, repo_path = is_cwd_inside_worktree()
        assert result is False
        assert repo_path is None

    @patch("worktree_main_freshness_check.subprocess.run")
    def test_git_rev_parse_timeout(self, mock_run: MagicMock):
        """Test that git rev-parse timeout returns False (fail open)."""
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git rev-parse", timeout=5)

        result, repo_path = is_cwd_inside_worktree()
        assert result is False
        assert repo_path is None

    @patch("worktree_main_freshness_check.subprocess.run")
    def test_git_c_directory_uses_correct_command(self, mock_run: MagicMock, tmp_path):
        """Test that git_c_directory parameter uses git -C option (Issue #1405)."""
        # In main repo, .git is a directory, not a file
        main_repo = tmp_path / "main"
        main_repo.mkdir()
        git_dir = main_repo / ".git"
        git_dir.mkdir()

        mock_run.return_value = MagicMock(returncode=0, stdout=str(main_repo) + "\n")

        result, repo_path = is_cwd_inside_worktree("/path/to/main")

        # Verify that git -C was used
        call_args = mock_run.call_args[0][0]
        assert "-C" in call_args
        assert "/path/to/main" in call_args
        assert result is False
        assert repo_path is None

    @patch("worktree_main_freshness_check.subprocess.run")
    def test_git_c_directory_pointing_to_main_repo_returns_false(
        self, mock_run: MagicMock, tmp_path
    ):
        """Test that git -C pointing to main repo returns False (Issue #1405)."""
        # Main repo has .git as directory
        main_repo = tmp_path / "main"
        main_repo.mkdir()
        git_dir = main_repo / ".git"
        git_dir.mkdir()

        mock_run.return_value = MagicMock(returncode=0, stdout=str(main_repo) + "\n")

        result, repo_path = is_cwd_inside_worktree(str(main_repo))
        assert result is False
        assert repo_path is None


class TestFetchOriginMain:
    """Tests for fetch_origin_main function."""

    @patch("worktree_main_freshness_check.subprocess.run")
    def test_fetch_success(self, mock_run: MagicMock):
        """Test successful fetch."""
        mock_run.return_value = MagicMock(returncode=0)
        assert fetch_origin_main()

    @patch("worktree_main_freshness_check.subprocess.run")
    def test_fetch_failure(self, mock_run: MagicMock):
        """Test failed fetch."""
        mock_run.return_value = MagicMock(returncode=1)
        assert not fetch_origin_main()

    @patch("worktree_main_freshness_check.subprocess.run")
    def test_fetch_timeout(self, mock_run: MagicMock):
        """Test fetch timeout."""
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git fetch", timeout=10)
        assert not fetch_origin_main()

    @patch("worktree_main_freshness_check.subprocess.run")
    def test_fetch_with_git_c_directory(self, mock_run: MagicMock):
        """Test that git_c_directory parameter uses git -C option (Issue #1421)."""
        mock_run.return_value = MagicMock(returncode=0)
        fetch_origin_main("/path/to/repo")

        call_args = mock_run.call_args[0][0]
        assert "-C" in call_args
        assert "/path/to/repo" in call_args
        assert "fetch" in call_args
        assert "origin" in call_args
        assert "main" in call_args


class TestGetCommitHash:
    """Tests for get_commit_hash function."""

    @patch("worktree_main_freshness_check.subprocess.run")
    def test_get_hash_success(self, mock_run: MagicMock):
        """Test successful hash retrieval."""
        mock_run.return_value = MagicMock(returncode=0, stdout="abc123def456\n")
        assert get_commit_hash("main") == "abc123def456"

    @patch("worktree_main_freshness_check.subprocess.run")
    def test_get_hash_failure(self, mock_run: MagicMock):
        """Test failed hash retrieval."""
        mock_run.return_value = MagicMock(returncode=1)
        assert get_commit_hash("nonexistent") is None

    @patch("worktree_main_freshness_check.subprocess.run")
    def test_get_hash_with_git_c_directory(self, mock_run: MagicMock):
        """Test that git_c_directory parameter uses git -C option (Issue #1421)."""
        mock_run.return_value = MagicMock(returncode=0, stdout="abc123def456\n")
        get_commit_hash("main", "/path/to/repo")

        call_args = mock_run.call_args[0][0]
        assert "-C" in call_args
        assert "/path/to/repo" in call_args
        assert "rev-parse" in call_args
        assert "main" in call_args


class TestGetBehindCount:
    """Tests for get_behind_count function."""

    @patch("worktree_main_freshness_check.subprocess.run")
    def test_behind_count(self, mock_run: MagicMock):
        """Test getting behind count."""
        mock_run.return_value = MagicMock(returncode=0, stdout="5\n")
        assert get_behind_count() == 5

    @patch("worktree_main_freshness_check.subprocess.run")
    def test_behind_count_zero(self, mock_run: MagicMock):
        """Test getting zero behind count."""
        mock_run.return_value = MagicMock(returncode=0, stdout="0\n")
        assert get_behind_count() == 0

    @patch("worktree_main_freshness_check.subprocess.run")
    def test_behind_count_error(self, mock_run: MagicMock):
        """Test error returns zero."""
        mock_run.return_value = MagicMock(returncode=1)
        assert get_behind_count() == 0

    @patch("worktree_main_freshness_check.subprocess.run")
    def test_behind_count_with_git_c_directory(self, mock_run: MagicMock):
        """Test that git_c_directory parameter uses git -C option (Issue #1421)."""
        mock_run.return_value = MagicMock(returncode=0, stdout="5\n")
        get_behind_count("/path/to/repo")

        call_args = mock_run.call_args[0][0]
        assert "-C" in call_args
        assert "/path/to/repo" in call_args
        assert "rev-list" in call_args


class TestGetCurrentBranch:
    """Tests for get_current_branch function (Issue #845)."""

    @patch("worktree_main_freshness_check.subprocess.run")
    def test_get_current_branch_on_main(self, mock_run: MagicMock):
        """Test getting branch name when on main."""
        mock_run.return_value = MagicMock(returncode=0, stdout="main\n")
        assert get_current_branch() == "main"

    @patch("worktree_main_freshness_check.subprocess.run")
    def test_get_current_branch_on_feature(self, mock_run: MagicMock):
        """Test getting branch name when on feature branch."""
        mock_run.return_value = MagicMock(returncode=0, stdout="feat/issue-123\n")
        assert get_current_branch() == "feat/issue-123"

    @patch("worktree_main_freshness_check.subprocess.run")
    def test_get_current_branch_detached_head(self, mock_run: MagicMock):
        """Test that detached HEAD returns None."""
        mock_run.return_value = MagicMock(returncode=0, stdout="HEAD\n")
        assert get_current_branch() is None

    @patch("worktree_main_freshness_check.subprocess.run")
    def test_get_current_branch_error(self, mock_run: MagicMock):
        """Test that errors return None (fail open)."""
        mock_run.return_value = MagicMock(returncode=1)
        assert get_current_branch() is None

    @patch("worktree_main_freshness_check.subprocess.run")
    def test_get_current_branch_with_git_c_directory(self, mock_run: MagicMock):
        """Test that git_c_directory parameter uses git -C option (Issue #1421)."""
        mock_run.return_value = MagicMock(returncode=0, stdout="main\n")
        get_current_branch("/path/to/repo")

        call_args = mock_run.call_args[0][0]
        assert "-C" in call_args
        assert "/path/to/repo" in call_args
        assert "rev-parse" in call_args
        assert "--abbrev-ref" in call_args


class TestTryAutoPullMain:
    """Tests for try_auto_pull_main function (Issue #845)."""

    @patch("worktree_main_freshness_check.get_current_branch")
    @patch("worktree_main_freshness_check.subprocess.run")
    def test_auto_pull_success_on_main(self, mock_run: MagicMock, mock_branch: MagicMock):
        """Test successful auto-pull when on main branch."""
        mock_branch.return_value = "main"
        mock_run.return_value = MagicMock(returncode=0)
        success, message = try_auto_pull_main()
        assert success is True
        assert "自動更新しました" in message
        # Should use pull when on main
        call_args = mock_run.call_args[0][0]
        assert "pull" in call_args

    @patch("worktree_main_freshness_check.get_current_branch")
    @patch("worktree_main_freshness_check.subprocess.run")
    def test_auto_pull_success_on_feature(self, mock_run: MagicMock, mock_branch: MagicMock):
        """Test successful auto-pull when on feature branch."""
        mock_branch.return_value = "feat/issue-123"
        mock_run.return_value = MagicMock(returncode=0)
        success, message = try_auto_pull_main()
        assert success is True
        assert "自動更新しました" in message
        # Should use fetch when not on main
        call_args = mock_run.call_args[0][0]
        assert "fetch" in call_args
        assert "main:main" in call_args

    @patch("worktree_main_freshness_check.get_current_branch")
    @patch("worktree_main_freshness_check.subprocess.run")
    def test_auto_pull_fetch_failure_on_feature_branch(
        self, mock_run: MagicMock, mock_branch: MagicMock
    ):
        """Test failed fetch on feature branch (e.g., diverged history)."""
        mock_branch.return_value = "feat/issue-123"
        mock_run.return_value = MagicMock(
            returncode=1, stderr="fatal: Not possible to fast-forward"
        )
        success, message = try_auto_pull_main()
        assert success is False
        assert "自動更新に失敗" in message
        # Verify fetch was used (not pull)
        call_args = mock_run.call_args[0][0]
        assert "fetch" in call_args

    @patch("worktree_main_freshness_check.get_current_branch")
    @patch("worktree_main_freshness_check.subprocess.run")
    def test_auto_pull_failure_on_main_branch(self, mock_run: MagicMock, mock_branch: MagicMock):
        """Test failed pull on main branch (e.g., uncommitted changes)."""
        mock_branch.return_value = "main"
        mock_run.return_value = MagicMock(
            returncode=1,
            stderr="error: Your local changes would be overwritten by merge",
        )
        success, message = try_auto_pull_main()
        assert success is False
        assert "自動更新に失敗" in message
        # Verify pull was used (not fetch)
        call_args = mock_run.call_args[0][0]
        assert "pull" in call_args

    @patch("worktree_main_freshness_check.get_current_branch")
    @patch("worktree_main_freshness_check.subprocess.run")
    def test_auto_pull_timeout(self, mock_run: MagicMock, mock_branch: MagicMock):
        """Test auto-pull timeout."""
        import subprocess

        mock_branch.return_value = "main"
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git pull", timeout=10)
        success, message = try_auto_pull_main()
        assert success is False
        assert "タイムアウト" in message

    @patch("worktree_main_freshness_check.get_current_branch")
    @patch("worktree_main_freshness_check.subprocess.run")
    def test_auto_pull_git_not_found(self, mock_run: MagicMock, mock_branch: MagicMock):
        """Test auto-pull when git command is not found."""
        mock_branch.return_value = "main"
        mock_run.side_effect = FileNotFoundError("git not found")
        success, message = try_auto_pull_main()
        assert success is False
        assert "gitコマンドが見つかりません" in message

    @patch("worktree_main_freshness_check.get_current_branch")
    @patch("worktree_main_freshness_check.subprocess.run")
    def test_auto_pull_uses_pull_on_main(self, mock_run: MagicMock, mock_branch: MagicMock):
        """Test that auto-pull uses git pull --ff-only --no-rebase when on main branch."""
        mock_branch.return_value = "main"
        mock_run.return_value = MagicMock(returncode=0)
        try_auto_pull_main()
        call_args = mock_run.call_args[0][0]
        assert "pull" in call_args
        assert "--ff-only" in call_args
        assert "--no-rebase" in call_args  # Issue #1398: Override user's pull.rebase config

    @patch("worktree_main_freshness_check.get_current_branch")
    @patch("worktree_main_freshness_check.subprocess.run")
    def test_auto_pull_uses_fetch_refspec_not_on_main(
        self, mock_run: MagicMock, mock_branch: MagicMock
    ):
        """Test that auto-pull uses fetch with refspec when not on main."""
        mock_branch.return_value = "feat/other"
        mock_run.return_value = MagicMock(returncode=0)
        try_auto_pull_main()
        call_args = mock_run.call_args[0][0]
        # Should use 'git fetch origin main:main' to update main ref directly
        assert "fetch" in call_args
        assert "main:main" in call_args

    @patch("worktree_main_freshness_check.get_current_branch")
    @patch("worktree_main_freshness_check.subprocess.run")
    def test_auto_pull_uses_fetch_on_detached_head(
        self, mock_run: MagicMock, mock_branch: MagicMock
    ):
        """Test that auto-pull uses fetch when in detached HEAD state (None branch)."""
        # Detached HEAD or error returns None
        mock_branch.return_value = None
        mock_run.return_value = MagicMock(returncode=0)
        success, message = try_auto_pull_main()
        # Should succeed using fetch (safe for detached HEAD)
        assert success is True
        call_args = mock_run.call_args[0][0]
        # Should use 'git fetch origin main:main' when branch is None
        assert "fetch" in call_args
        assert "main:main" in call_args

    @patch("worktree_main_freshness_check.get_current_branch")
    @patch("worktree_main_freshness_check.subprocess.run")
    def test_auto_pull_with_git_c_directory(self, mock_run: MagicMock, mock_branch: MagicMock):
        """Test that git_c_directory parameter uses git -C option (Issue #1421)."""
        mock_branch.return_value = "main"
        mock_run.return_value = MagicMock(returncode=0)
        try_auto_pull_main("/path/to/repo")

        # Verify get_current_branch was called with git_c_directory
        mock_branch.assert_called_once_with("/path/to/repo")

        # Verify git -C was used in the pull command
        call_args = mock_run.call_args[0][0]
        assert "-C" in call_args
        assert "/path/to/repo" in call_args
        assert "pull" in call_args


class TestMainHook:
    """Tests for main hook function."""

    def test_blocks_when_behind_and_auto_pull_fails(self):
        """Test that hook blocks when main is behind and auto-pull fails (Issue #845)."""
        import io

        input_data = json.dumps(
            {"tool_input": {"command": "git worktree add .worktrees/issue-123 -b feat main"}}
        )

        # Patch sys.stdin directly in the hook module's sys reference
        # Use side_effect=SystemExit to actually stop execution
        with patch.object(hook_module, "is_cwd_inside_worktree", return_value=(False, None)):
            with patch.object(hook_module, "fetch_origin_main", return_value=True):
                with patch.object(hook_module, "get_commit_hash", side_effect=["abc123", "def456"]):
                    with patch.object(hook_module, "get_behind_count", return_value=3):
                        # Mock auto-pull to fail
                        with patch.object(
                            hook_module,
                            "try_auto_pull_main",
                            return_value=(False, "自動更新に失敗: conflict"),
                        ):
                            with patch.object(hook_module.sys, "stdin", io.StringIO(input_data)):
                                with patch.object(hook_module.sys, "exit", side_effect=SystemExit):
                                    with patch("builtins.print") as mock_print:
                                        with pytest.raises(SystemExit):
                                            hook_module.main()

                                        call_args = mock_print.call_args[0][0]
                                        result = json.loads(call_args)
                                        assert result["decision"] == "block"
                                        assert "3コミット遅れ" in result["reason"]
                                        assert "自動更新を試みましたが失敗" in result["reason"]

    def test_approves_when_behind_and_auto_pull_succeeds(self):
        """Test that hook approves when main is behind but auto-pull succeeds (Issue #845)."""
        import io

        input_data = json.dumps(
            {"tool_input": {"command": "git worktree add .worktrees/issue-123 -b feat main"}}
        )

        with patch.object(hook_module, "is_cwd_inside_worktree", return_value=(False, None)):
            with patch.object(hook_module, "fetch_origin_main", return_value=True):
                with patch.object(hook_module, "get_commit_hash", side_effect=["abc123", "def456"]):
                    with patch.object(hook_module, "get_behind_count", return_value=3):
                        # Mock auto-pull to succeed
                        with patch.object(
                            hook_module,
                            "try_auto_pull_main",
                            return_value=(True, "mainブランチを自動更新しました"),
                        ):
                            with patch.object(hook_module.sys, "stdin", io.StringIO(input_data)):
                                with patch.object(hook_module.sys, "exit", side_effect=SystemExit):
                                    with patch("builtins.print") as mock_print:
                                        with pytest.raises(SystemExit):
                                            hook_module.main()

                                        call_args = mock_print.call_args[0][0]
                                        result = json.loads(call_args)
                                        assert result["decision"] == "approve"
                                        assert "自動更新しました" in result.get("systemMessage", "")

    def test_approves_when_up_to_date(self):
        """Test that hook approves silently when main is up to date (behind_count=0)."""
        import io

        input_data = json.dumps(
            {"tool_input": {"command": "git worktree add .worktrees/issue-123 -b feat main"}}
        )

        with patch.object(hook_module, "is_cwd_inside_worktree", return_value=(False, None)):
            with patch.object(hook_module, "fetch_origin_main", return_value=True):
                with patch.object(hook_module, "get_commit_hash", side_effect=["abc123", "abc123"]):
                    with patch.object(hook_module, "get_behind_count", return_value=0):
                        with patch.object(hook_module.sys, "stdin", io.StringIO(input_data)):
                            with patch.object(hook_module.sys, "exit", side_effect=SystemExit):
                                with patch("builtins.print") as mock_print:
                                    with pytest.raises(SystemExit):
                                        hook_module.main()

                                    # Silent approval - print should not be called
                                    mock_print.assert_not_called()

    def test_approves_when_local_is_ahead(self):
        """Test that hook approves silently when local main is ahead of origin/main."""
        import io

        input_data = json.dumps(
            {"tool_input": {"command": "git worktree add .worktrees/issue-123 -b feat main"}}
        )

        # Hashes differ but behind_count=0 means local is ahead
        with patch.object(hook_module, "is_cwd_inside_worktree", return_value=(False, None)):
            with patch.object(hook_module, "fetch_origin_main", return_value=True):
                with patch.object(hook_module, "get_commit_hash", side_effect=["abc123", "def456"]):
                    with patch.object(hook_module, "get_behind_count", return_value=0):
                        with patch.object(hook_module.sys, "stdin", io.StringIO(input_data)):
                            with patch.object(hook_module.sys, "exit", side_effect=SystemExit):
                                with patch("builtins.print") as mock_print:
                                    with pytest.raises(SystemExit):
                                        hook_module.main()

                                    # Silent approval - print should not be called
                                    mock_print.assert_not_called()

    def test_approves_non_worktree_commands(self):
        """Test that non-worktree commands are approved silently."""
        import io

        input_data = json.dumps({"tool_input": {"command": "git status"}})

        with patch.object(hook_module.sys, "stdin", io.StringIO(input_data)):
            with patch.object(hook_module.sys, "exit", side_effect=SystemExit):
                with patch("builtins.print") as mock_print:
                    with pytest.raises(SystemExit):
                        hook_module.main()

                    # Silent approval - print should not be called
                    mock_print.assert_not_called()

    def test_blocks_when_inside_worktree(self):
        """Test that hook blocks worktree add when inside another worktree (Issue #822)."""
        import io
        from pathlib import Path

        input_data = json.dumps(
            {"tool_input": {"command": "git worktree add .worktrees/issue-123 -b feat main"}}
        )

        # Mock is_cwd_inside_worktree to return True (inside a worktree)
        with patch.object(
            hook_module, "is_cwd_inside_worktree", return_value=(True, Path("/path/to/main"))
        ):
            with patch.object(hook_module.sys, "stdin", io.StringIO(input_data)):
                with patch.object(hook_module.sys, "exit", side_effect=SystemExit):
                    with patch("builtins.print") as mock_print:
                        with pytest.raises(SystemExit):
                            hook_module.main()

                        call_args = mock_print.call_args[0][0]
                        result = json.loads(call_args)
                        assert result["decision"] == "block"
                        assert "worktree内から" in result["reason"]
                        assert "/path/to/main" in result["reason"]

    def test_allows_worktree_add_from_main_repo(self):
        """Test that worktree add is allowed silently from main repository (Issue #822)."""
        import io

        input_data = json.dumps(
            {"tool_input": {"command": "git worktree add .worktrees/issue-123 -b feat main"}}
        )

        # Mock is_cwd_inside_worktree to return False (in main repo)
        with patch.object(hook_module, "is_cwd_inside_worktree", return_value=(False, None)):
            with patch.object(hook_module, "fetch_origin_main", return_value=True):
                with patch.object(hook_module, "get_commit_hash", side_effect=["abc123", "abc123"]):
                    with patch.object(hook_module, "get_behind_count", return_value=0):
                        with patch.object(hook_module.sys, "stdin", io.StringIO(input_data)):
                            with patch.object(hook_module.sys, "exit", side_effect=SystemExit):
                                with patch("builtins.print") as mock_print:
                                    with pytest.raises(SystemExit):
                                        hook_module.main()

                                    # Silent approval - print should not be called
                                    mock_print.assert_not_called()

    def test_allows_git_c_to_main_repo_from_worktree(self):
        """Test that git -C /main/repo worktree add is allowed from worktree (Issue #1405)."""
        import io

        # Command uses git -C to specify main repo
        input_data = json.dumps(
            {
                "tool_input": {
                    "command": "git -C /path/to/main worktree add .worktrees/issue-123 -b feat main"
                }
            }
        )

        # extract_git_c_directory should return /path/to/main
        # is_cwd_inside_worktree(/path/to/main) should return False (main repo)
        with patch.object(hook_module, "is_cwd_inside_worktree", return_value=(False, None)):
            with patch.object(hook_module, "fetch_origin_main", return_value=True):
                with patch.object(hook_module, "get_commit_hash", side_effect=["abc123", "abc123"]):
                    with patch.object(hook_module, "get_behind_count", return_value=0):
                        with patch.object(hook_module.sys, "stdin", io.StringIO(input_data)):
                            with patch.object(hook_module.sys, "exit", side_effect=SystemExit):
                                with patch("builtins.print") as mock_print:
                                    with pytest.raises(SystemExit):
                                        hook_module.main()

                                    # Silent approval - print should not be called
                                    mock_print.assert_not_called()

    def test_blocks_git_c_to_worktree_from_worktree(self):
        """Test that git -C /worktree worktree add is blocked (Issue #1405)."""
        import io
        from pathlib import Path

        # Command uses git -C to specify another worktree
        input_data = json.dumps(
            {
                "tool_input": {
                    "command": "git -C /worktree worktree add .worktrees/issue-123 -b feat main"
                }
            }
        )

        # is_cwd_inside_worktree("/worktree") returns True (it's a worktree)
        with patch.object(
            hook_module, "is_cwd_inside_worktree", return_value=(True, Path("/path/to/main"))
        ):
            with patch.object(hook_module.sys, "stdin", io.StringIO(input_data)):
                with patch.object(hook_module.sys, "exit", side_effect=SystemExit):
                    with patch("builtins.print") as mock_print:
                        with pytest.raises(SystemExit):
                            hook_module.main()

                        call_args = mock_print.call_args[0][0]
                        result = json.loads(call_args)
                        assert result["decision"] == "block"
                        assert "worktree内から" in result["reason"]
                        # Should suggest using git -C to main repo
                        assert "git -C" in result["reason"]
                        assert "/path/to/main" in result["reason"]

    def test_extract_git_c_directory_called_in_main(self):
        """Test that extract_git_c_directory is called and passed to is_cwd_inside_worktree."""
        import io

        input_data = json.dumps(
            {
                "tool_input": {
                    "command": "git -C /main/repo worktree add .worktrees/issue-123 -b feat main"
                }
            }
        )

        with patch.object(
            hook_module, "extract_git_c_directory", return_value="/main/repo"
        ) as mock_extract:
            with patch.object(
                hook_module, "is_cwd_inside_worktree", return_value=(False, None)
            ) as mock_check:
                with patch.object(hook_module, "fetch_origin_main", return_value=True):
                    with patch.object(
                        hook_module, "get_commit_hash", side_effect=["abc123", "abc123"]
                    ):
                        with patch.object(hook_module, "get_behind_count", return_value=0):
                            with patch.object(hook_module.sys, "stdin", io.StringIO(input_data)):
                                with patch.object(hook_module.sys, "exit", side_effect=SystemExit):
                                    with pytest.raises(SystemExit):
                                        hook_module.main()

                                    # Verify extract_git_c_directory was called
                                    mock_extract.assert_called_once()
                                    # Verify is_cwd_inside_worktree was called with the result
                                    mock_check.assert_called_once_with("/main/repo")
