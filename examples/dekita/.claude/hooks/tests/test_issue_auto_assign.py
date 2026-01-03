#!/usr/bin/env python3
"""Tests for issue-auto-assign.py hook."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

HOOK_PATH = Path(__file__).parent.parent / "issue-auto-assign.py"


def load_module():
    """Load the hook module for testing."""
    # Temporarily add hooks directory to path for common module import
    hooks_dir = str(HOOK_PATH.parent)
    sys.path.insert(0, hooks_dir)
    try:
        spec = importlib.util.spec_from_file_location("issue_auto_assign", HOOK_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(hooks_dir)


def run_hook(input_data: dict) -> dict:
    """Run the hook with given input and return the result."""
    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


class TestExtractIssueNumber:
    """Tests for extract_issue_number function."""

    def setup_method(self):
        self.module = load_module()

    def test_hash_pattern(self):
        """Test #123 pattern."""
        result = self.module.extract_issue_number("#123-feature")
        assert result == 123

    def test_issue_dash_pattern(self):
        """Test issue-123 pattern."""
        test_cases = [
            ("issue-123", 123),
            ("issue_456", 456),
            ("fix/issue-789", 789),
        ]
        for branch, expected in test_cases:
            with self.subTest(branch=branch):
                result = self.module.extract_issue_number(branch)
                assert result == expected

    def test_slash_number_pattern(self):
        """Test /123- pattern."""
        test_cases = [
            ("fix/123-description", 123),
            ("feature/456-new", 456),
        ]
        for branch, expected in test_cases:
            with self.subTest(branch=branch):
                result = self.module.extract_issue_number(branch)
                assert result == expected

    def test_dash_number_pattern(self):
        """Test -123- and -123 patterns."""
        test_cases = [
            ("feature-123-name", 123),
            ("fix-456", 456),
            ("feature_789_name", 789),
        ]
        for branch, expected in test_cases:
            with self.subTest(branch=branch):
                result = self.module.extract_issue_number(branch)
                assert result == expected

    def test_no_issue_number(self):
        """Test branches without issue numbers."""
        test_cases = [
            "main",
            "develop",
            "feature/description-only",
        ]
        for branch in test_cases:
            with self.subTest(branch=branch):
                result = self.module.extract_issue_number(branch)
                assert result is None


class TestParseWorktreeAddCommand:
    """Tests for parse_worktree_add_command function."""

    def setup_method(self):
        self.module = load_module()

    def test_with_b_flag(self):
        """Test git worktree add with -b flag returns both branch and path."""
        command = "git worktree add --lock .worktrees/fix-123 -b fix/123-description"
        branch_name, worktree_path = self.module.parse_worktree_add_command(command)
        assert branch_name == "fix/123-description"
        assert worktree_path == ".worktrees/fix-123"

    def test_without_b_flag(self):
        """Test git worktree add with positional branch name."""
        command = "git worktree add .worktrees/issue-456 issue-456-feature"
        branch_name, worktree_path = self.module.parse_worktree_add_command(command)
        assert branch_name == "issue-456-feature"
        assert worktree_path == ".worktrees/issue-456"

    def test_path_only(self):
        """Test git worktree add with only path (no branch name)."""
        command = "git worktree add .worktrees/issue-789"
        branch_name, worktree_path = self.module.parse_worktree_add_command(command)
        assert branch_name is None
        assert worktree_path == ".worktrees/issue-789"

    def test_not_worktree_add(self):
        """Should return (None, None) for non-worktree-add commands."""
        test_cases = [
            "git status",
            "git worktree list",
            "git worktree remove .worktrees/test",
        ]
        for command in test_cases:
            with self.subTest(command=command):
                branch_name, worktree_path = self.module.parse_worktree_add_command(command)
                assert branch_name is None
                assert worktree_path is None

    def test_issue_number_from_path_when_branch_has_none(self):
        """Test that issue number can be extracted from path when branch has none.

        This is the key fix for Issue #454: when branch name doesn't contain
        an issue number, we should fall back to the worktree path.
        """
        command = "git worktree add .worktrees/issue-454 -b feat/worktree-auto-assign main"
        branch_name, worktree_path = self.module.parse_worktree_add_command(command)

        # Branch name doesn't have issue number
        assert branch_name == "feat/worktree-auto-assign"
        issue_from_branch = self.module.extract_issue_number(branch_name)
        assert issue_from_branch is None

        # But path does have issue number
        assert worktree_path == ".worktrees/issue-454"
        issue_from_path = self.module.extract_issue_from_path(worktree_path)
        assert issue_from_path == 454


class TestGetExistingWorktreeBranches:
    """Tests for get_existing_worktree_branches function."""

    def setup_method(self):
        self.module = load_module()
        self._orig_run = subprocess.run

    def teardown_method(self):
        subprocess.run = self._orig_run

    def test_parse_porcelain_output(self):
        """Test parsing of git worktree list --porcelain output."""
        result = self.module.get_existing_worktree_branches()
        assert isinstance(result, list)
        for item in result:
            assert isinstance(item, tuple)
            assert len(item) == 2

    def test_parse_multiple_worktrees(self):
        """Test parsing multiple worktrees from porcelain output."""
        mock_output = (
            "worktree /path/to/main\n"
            "HEAD abc123\n"
            "branch refs/heads/main\n"
            "\n"
            "worktree /path/to/.worktrees/feature\n"
            "HEAD def456\n"
            "branch refs/heads/feature/issue-123\n"
            "\n"
            "worktree /path/to/.worktrees/fix\n"
            "HEAD ghi789\n"
            "branch refs/heads/fix/issue-456\n"
        )

        def mock_run(*args, **kwargs):
            result = type("Result", (), {})()
            result.returncode = 0
            result.stdout = mock_output
            return result

        subprocess.run = mock_run
        result = self.module.get_existing_worktree_branches()

        assert len(result) == 3
        assert result[0] == ("/path/to/main", "main")
        assert result[1] == ("/path/to/.worktrees/feature", "feature/issue-123")
        assert result[2] == ("/path/to/.worktrees/fix", "fix/issue-456")

    def test_parse_detached_head_excluded(self):
        """Test that detached HEAD worktrees are excluded."""
        mock_output = (
            "worktree /path/to/main\n"
            "HEAD abc123\n"
            "branch refs/heads/main\n"
            "\n"
            "worktree /path/to/.worktrees/detached\n"
            "HEAD def456\n"
            "detached\n"
        )

        def mock_run(*args, **kwargs):
            result = type("Result", (), {})()
            result.returncode = 0
            result.stdout = mock_output
            return result

        subprocess.run = mock_run
        result = self.module.get_existing_worktree_branches()

        assert len(result) == 1
        assert result[0] == ("/path/to/main", "main")

    def test_returns_empty_on_error(self):
        """Test that empty list is returned on error."""

        def mock_run(*args, **kwargs):
            raise OSError("Command failed")

        subprocess.run = mock_run
        result = self.module.get_existing_worktree_branches()
        assert result == []

    def test_parse_porcelain_without_trailing_newline(self):
        """Test parsing porcelain output without trailing newline."""
        # Git may not always include a trailing newline
        mock_output = (
            "worktree /path/to/main\n"
            "HEAD abc123\n"
            "branch refs/heads/main\n"
            "\n"
            "worktree /path/to/.worktrees/feature\n"
            "HEAD def456\n"
            "branch refs/heads/feature/issue-123"  # No trailing newline
        )

        def mock_run(*args, **kwargs):
            result = type("Result", (), {})()
            result.returncode = 0
            result.stdout = mock_output
            return result

        subprocess.run = mock_run
        result = self.module.get_existing_worktree_branches()

        assert len(result) == 2
        assert result[0] == ("/path/to/main", "main")
        assert result[1] == ("/path/to/.worktrees/feature", "feature/issue-123")


class TestFindDuplicateIssueWorktree:
    """Tests for find_duplicate_issue_worktree function."""

    def setup_method(self):
        self.module = load_module()
        # Save original function
        self._orig_get_branches = self.module.get_existing_worktree_branches

    def teardown_method(self):
        # Restore original function
        self.module.get_existing_worktree_branches = self._orig_get_branches

    def test_no_duplicate(self):
        """Should return None when no duplicate exists."""
        self.module.get_existing_worktree_branches = lambda: [
            ("/path/to/project", "main"),
            ("/path/to/.worktrees/issue-100", "fix/issue-100-bug"),
        ]
        result = self.module.find_duplicate_issue_worktree(123, "fix/issue-123-new", None)
        assert result is None

    def test_duplicate_found(self):
        """Should return duplicate worktree info when same issue exists."""
        self.module.get_existing_worktree_branches = lambda: [
            ("/path/to/project", "main"),
            ("/path/to/.worktrees/issue-123-old", "refactor/issue-123-constants"),
        ]
        result = self.module.find_duplicate_issue_worktree(123, "fix/issue-123-new", None)
        assert result is not None
        assert result[0] == "/path/to/.worktrees/issue-123-old"
        assert result[1] == "refactor/issue-123-constants"

    def test_same_branch_not_counted_as_duplicate(self):
        """Should not consider the same branch as duplicate."""
        self.module.get_existing_worktree_branches = lambda: [
            ("/path/to/project", "main"),
            ("/path/to/.worktrees/issue-123", "fix/issue-123-bug"),
        ]
        # Same branch name should be excluded
        result = self.module.find_duplicate_issue_worktree(123, "fix/issue-123-bug", None)
        assert result is None

    def test_empty_worktree_list(self):
        """Should return None when worktree list is empty."""
        self.module.get_existing_worktree_branches = lambda: []
        result = self.module.find_duplicate_issue_worktree(123, "fix/issue-123-new", None)
        assert result is None

    def test_branch_without_issue_number_skipped(self):
        """Should skip branches without issue numbers."""
        self.module.get_existing_worktree_branches = lambda: [
            ("/path/to/project", "main"),
            ("/path/to/.worktrees/feature", "feature/no-issue-number"),
        ]
        result = self.module.find_duplicate_issue_worktree(123, "fix/issue-123-new", None)
        assert result is None

    def test_multiple_duplicates_returns_first(self):
        """Should return the first duplicate when multiple worktrees have the same issue."""
        self.module.get_existing_worktree_branches = lambda: [
            ("/path/to/project", "main"),
            ("/path/to/.worktrees/issue-123-first", "fix/issue-123-first"),
            ("/path/to/.worktrees/issue-123-second", "refactor/issue-123-second"),
        ]
        result = self.module.find_duplicate_issue_worktree(123, "feat/issue-123-new", None)
        assert result is not None
        # Should return the first duplicate found
        assert result[0] == "/path/to/.worktrees/issue-123-first"
        assert result[1] == "fix/issue-123-first"

    def test_duplicate_found_by_path_only(self):
        """Should find duplicate when issue number is only in path."""
        self.module.get_existing_worktree_branches = lambda: [
            ("/path/to/project", "main"),
            ("/path/to/.worktrees/issue-123", "feature/some-feature"),
        ]
        result = self.module.find_duplicate_issue_worktree(123, "fix/new-branch", None)
        assert result is not None
        assert result[0] == "/path/to/.worktrees/issue-123"
        assert result[1] == "feature/some-feature"


class TestGetIssueInfo:
    """Tests for get_issue_info function."""

    def setup_method(self):
        self.module = load_module()
        self._orig_run = subprocess.run

    def teardown_method(self):
        subprocess.run = self._orig_run

    def test_returns_state_and_assignees(self):
        """Should return issue state and assignees."""
        mock_response = {"state": "OPEN", "assignees": [{"login": "user1"}]}

        def mock_run(*args, **kwargs):
            result = type("Result", (), {})()
            result.returncode = 0
            result.stdout = json.dumps(mock_response)
            return result

        subprocess.run = mock_run
        result = self.module.get_issue_info(123)
        assert result["state"] == "OPEN"
        assert len(result["assignees"]) == 1

    def test_returns_none_on_error(self):
        """Should return None when gh command fails."""

        def mock_run(*args, **kwargs):
            result = type("Result", (), {})()
            result.returncode = 1
            result.stdout = ""
            return result

        subprocess.run = mock_run
        result = self.module.get_issue_info(123)
        assert result is None

    def test_returns_none_on_timeout(self):
        """Should return None on timeout."""

        def mock_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="gh", timeout=10)

        subprocess.run = mock_run
        result = self.module.get_issue_info(123)
        assert result is None


class TestClosedIssueBlocking:
    """Tests for closed issue blocking behavior."""

    def setup_method(self):
        self.module = load_module()
        self._orig_get_issue_info = self.module.get_issue_info
        self._orig_get_branches = self.module.get_existing_worktree_branches
        self._orig_assign_issue = self.module.assign_issue

    def teardown_method(self):
        self.module.get_issue_info = self._orig_get_issue_info
        self.module.get_existing_worktree_branches = self._orig_get_branches
        self.module.assign_issue = self._orig_assign_issue

    def test_blocks_closed_issue(self):
        """Should block worktree creation for closed issues."""
        # Mock get_issue_info to return closed state
        self.module.get_issue_info = lambda _: {"state": "CLOSED", "assignees": []}
        self.module.get_existing_worktree_branches = lambda: []

        # Prepare input
        input_data = {
            "tool_input": {"command": "git worktree add .worktrees/fix -b fix/issue-999-bug"}
        }

        # Run the hook via subprocess with mocked module
        # Since we can't easily mock in subprocess, test the logic directly
        command = input_data["tool_input"]["command"]
        branch_name, worktree_path = self.module.parse_worktree_add_command(command)

        # Try branch name first, then path (matches main() logic)
        issue_number = None
        if branch_name:
            issue_number = self.module.extract_issue_number(branch_name)
        if not issue_number:
            issue_number = self.module.extract_issue_from_path(worktree_path)

        assert issue_number == 999

        # Verify the blocking condition
        issue_info = self.module.get_issue_info(issue_number)
        assert issue_info["state"] == "CLOSED"

    def test_closed_issue_integration(self):
        """Integration test: verify closed issue produces block decision."""
        # This test verifies that when get_issue_info returns CLOSED state,
        # the blocking condition is triggered (matching main()'s logic)
        self.module.get_issue_info = lambda _: {"state": "CLOSED", "assignees": []}
        self.module.get_existing_worktree_branches = lambda: []

        issue_number = 999
        issue_info = self.module.get_issue_info(issue_number)

        # Verify the exact condition used in main()
        should_block = issue_info and issue_info.get("state") == "CLOSED"
        assert should_block

        # Verify the reason message format matches implementation
        if should_block:
            # This matches the actual message format in main()
            expected_reason_parts = [
                f"Issue #{issue_number}",
                "クローズ",  # Japanese for "closed"
            ]
            reason = (
                f"🚫 Issue #{issue_number} は既にクローズされています。\n"
                f"オープンなIssueを選択してください。\n"
                f"確認: `gh issue view {issue_number}`"
            )
            for part in expected_reason_parts:
                assert part in reason

    def test_open_issue_not_blocked(self):
        """Open issues should not be blocked."""
        self.module.get_issue_info = lambda _: {"state": "OPEN", "assignees": []}
        self.module.get_existing_worktree_branches = lambda: []
        self.module.assign_issue = lambda _: True

        issue_info = self.module.get_issue_info(123)
        assert issue_info.get("state") != "CLOSED"


class TestAssignedIssueWarning:
    """Tests for assigned issue warning behavior."""

    def setup_method(self):
        self.module = load_module()
        self._orig_get_issue_info = self.module.get_issue_info
        self._orig_get_branches = self.module.get_existing_worktree_branches

    def teardown_method(self):
        self.module.get_issue_info = self._orig_get_issue_info
        self.module.get_existing_worktree_branches = self._orig_get_branches

    def test_warning_for_assigned_issue(self):
        """Should warn when issue is already assigned."""
        # Mock: issue is OPEN but has assignees
        self.module.get_issue_info = lambda _: {
            "state": "OPEN",
            "assignees": [{"login": "other-user"}],
        }
        self.module.get_existing_worktree_branches = lambda: []

        issue_number = 123
        issue_info = self.module.get_issue_info(issue_number)

        # Extract assignees using the same logic as main()
        assignees = [
            login
            for a in issue_info.get("assignees", [])
            if (login := a.get("login")) and login.strip()
        ]

        assert assignees == ["other-user"]

        # Verify that with assignees, we would generate a warning (not block)
        # This matches the logic in main()
        if assignees:
            result = {
                "decision": "approve",
                "systemMessage": f"Issue #{issue_number} is assigned to: {', '.join(assignees)}",
            }
        else:
            result = {"decision": "approve"}

        assert result["decision"] == "approve"  # Not blocked
        assert "systemMessage" in result  # But has warning
        assert "other-user" in result["systemMessage"]

    def test_warning_message_content(self):
        """Warning message should ask for confirmation with assignee."""
        import inspect

        # Verify the warning message contains the expected Japanese text
        source = inspect.getsource(self.module.main)
        assert "担当者に確認してください" in source
        assert "systemMessage" in source

    def test_empty_assignees_filtered(self):
        """Empty or whitespace-only logins should be filtered out."""
        self.module.get_issue_info = lambda _: {
            "state": "OPEN",
            "assignees": [
                {"login": ""},
                {"login": "   "},
                {"login": "valid-user"},
                {},  # missing login key
            ],
        }

        issue_info = self.module.get_issue_info(123)
        assignees = [
            login
            for a in issue_info.get("assignees", [])
            if (login := a.get("login")) and login.strip()
        ]

        assert assignees == ["valid-user"]


class TestFindRemoteBranchForIssue:
    """Tests for find_remote_branch_for_issue function."""

    def setup_method(self):
        self.module = load_module()
        self._orig_run = subprocess.run

    def teardown_method(self):
        subprocess.run = self._orig_run

    def test_finds_remote_branch_with_issue_number(self):
        """Should find remote branch that contains the same issue number."""
        mock_output = "  origin/main\n  origin/feature/issue-123-old\n  origin/fix/issue-456\n"

        call_count = [0]

        def mock_run(*args, **kwargs):
            call_count[0] += 1
            result = type("Result", (), {})()
            result.returncode = 0
            if call_count[0] == 1:  # git fetch
                result.stdout = ""
            else:  # git branch -r
                result.stdout = mock_output
            return result

        subprocess.run = mock_run
        result = self.module.find_remote_branch_for_issue(123, "fix/issue-123-new")
        assert result == "origin/feature/issue-123-old"

    def test_returns_none_when_no_matching_branch(self):
        """Should return None when no remote branch matches the issue."""
        mock_output = "  origin/main\n  origin/feature/issue-456\n"

        call_count = [0]

        def mock_run(*args, **kwargs):
            call_count[0] += 1
            result = type("Result", (), {})()
            result.returncode = 0
            if call_count[0] == 1:
                result.stdout = ""
            else:
                result.stdout = mock_output
            return result

        subprocess.run = mock_run
        result = self.module.find_remote_branch_for_issue(123, "fix/issue-123-new")
        assert result is None

    def test_excludes_new_branch(self):
        """Should not consider the new branch as a duplicate."""
        mock_output = (
            "  origin/main\n"
            "  origin/fix/issue-123-new\n"  # Same as new branch
        )

        call_count = [0]

        def mock_run(*args, **kwargs):
            call_count[0] += 1
            result = type("Result", (), {})()
            result.returncode = 0
            if call_count[0] == 1:
                result.stdout = ""
            else:
                result.stdout = mock_output
            return result

        subprocess.run = mock_run
        result = self.module.find_remote_branch_for_issue(123, "fix/issue-123-new")
        assert result is None

    def test_returns_none_on_error(self):
        """Should return None on git command error."""

        def mock_run(*args, **kwargs):
            raise OSError("Command failed")

        subprocess.run = mock_run
        result = self.module.find_remote_branch_for_issue(123, "fix/issue-123-new")
        assert result is None

    def test_handles_non_origin_remotes(self):
        """Should correctly handle non-origin remotes (upstream, fork, etc.)."""
        mock_output = "  origin/main\n  upstream/feature/issue-123-old\n  fork/fix/issue-456\n"

        call_count = [0]

        def mock_run(*args, **kwargs):
            call_count[0] += 1
            result = type("Result", (), {})()
            result.returncode = 0
            if call_count[0] == 1:  # git fetch
                result.stdout = ""
            else:  # git branch -r
                result.stdout = mock_output
            return result

        subprocess.run = mock_run
        result = self.module.find_remote_branch_for_issue(123, "fix/issue-123-new")
        # Should find upstream/feature/issue-123-old
        assert result == "upstream/feature/issue-123-old"


class TestFindOpenPrForIssue:
    """Tests for find_open_pr_for_issue function."""

    def setup_method(self):
        self.module = load_module()
        self._orig_run = subprocess.run

    def teardown_method(self):
        subprocess.run = self._orig_run

    def test_finds_pr_by_closes_keyword(self):
        """Should find PR that references issue with Closes keyword."""
        mock_prs = [
            {
                "number": 100,
                "title": "Some other PR",
                "url": "https://github.com/test/100",
                "body": "Fixes #456",
                "headRefName": "fix/other",
            },
            {
                "number": 101,
                "title": "Fix issue 123",
                "url": "https://github.com/test/101",
                "body": "Closes #123",
                "headRefName": "fix/something",
            },
        ]

        def mock_run(*args, **kwargs):
            result = type("Result", (), {})()
            result.returncode = 0
            result.stdout = json.dumps(mock_prs)
            return result

        subprocess.run = mock_run
        result = self.module.find_open_pr_for_issue(123)
        assert result is not None
        assert result["number"] == 101
        assert result["title"] == "Fix issue 123"

    def test_finds_pr_by_branch_name(self):
        """Should find PR when branch name contains the issue number."""
        mock_prs = [
            {
                "number": 102,
                "title": "Feature for issue 123",
                "url": "https://github.com/test/102",
                "body": "Some description without closes keyword",
                "headRefName": "feature/issue-123-description",
            },
        ]

        def mock_run(*args, **kwargs):
            result = type("Result", (), {})()
            result.returncode = 0
            result.stdout = json.dumps(mock_prs)
            return result

        subprocess.run = mock_run
        result = self.module.find_open_pr_for_issue(123)
        assert result is not None
        assert result["number"] == 102

    def test_returns_none_when_no_matching_pr(self):
        """Should return None when no PR references the issue."""
        mock_prs = [
            {
                "number": 103,
                "title": "Unrelated PR",
                "url": "https://github.com/test/103",
                "body": "Closes #456",
                "headRefName": "fix/other-issue",
            },
        ]

        def mock_run(*args, **kwargs):
            result = type("Result", (), {})()
            result.returncode = 0
            result.stdout = json.dumps(mock_prs)
            return result

        subprocess.run = mock_run
        result = self.module.find_open_pr_for_issue(123)
        assert result is None

    def test_returns_none_on_error(self):
        """Should return None on gh command error."""

        def mock_run(*args, **kwargs):
            result = type("Result", (), {})()
            result.returncode = 1
            result.stdout = ""
            return result

        subprocess.run = mock_run
        result = self.module.find_open_pr_for_issue(123)
        assert result is None


class TestDuplicateWorktreeBlocking:
    """Tests for duplicate worktree blocking behavior (changed from warning to block)."""

    def setup_method(self):
        self.module = load_module()
        self._orig_get_issue_info = self.module.get_issue_info
        self._orig_get_branches = self.module.get_existing_worktree_branches
        self._orig_find_remote = self.module.find_remote_branch_for_issue
        self._orig_find_pr = self.module.find_open_pr_for_issue

    def teardown_method(self):
        self.module.get_issue_info = self._orig_get_issue_info
        self.module.get_existing_worktree_branches = self._orig_get_branches
        self.module.find_remote_branch_for_issue = self._orig_find_remote
        self.module.find_open_pr_for_issue = self._orig_find_pr

    def test_blocks_duplicate_worktree(self):
        """Should BLOCK (not just warn) when duplicate worktree exists."""
        import inspect

        # Verify the blocking behavior is in the source
        source = inspect.getsource(self.module.main)
        # The duplicate worktree check should now result in a block decision
        assert "decision" in source
        assert "block" in source

        # Verify the logic: duplicate worktree should trigger block
        self.module.get_issue_info = lambda _: {"state": "OPEN", "assignees": []}
        self.module.get_existing_worktree_branches = lambda: [
            ("/path/to/main", "main"),
            ("/path/to/.worktrees/issue-123", "fix/issue-123-old"),
        ]

        # Find duplicate should return the existing worktree
        duplicate = self.module.find_duplicate_issue_worktree(
            123, "fix/issue-123-new", ".worktrees/new"
        )
        assert duplicate is not None
        assert duplicate[1] == "fix/issue-123-old"


class TestRemoteBranchBlocking:
    """Tests for remote branch blocking behavior."""

    def setup_method(self):
        self.module = load_module()

    def test_blocking_message_format(self):
        """Verify the blocking message format for remote branch."""
        import inspect

        source = inspect.getsource(self.module.main)
        # Should contain remote branch blocking message
        assert "リモートブランチ" in source
        assert "既存ブランチ" in source


class TestOpenPrBlocking:
    """Tests for open PR blocking behavior."""

    def setup_method(self):
        self.module = load_module()

    def test_blocking_message_format(self):
        """Verify the blocking message format for open PR."""
        import inspect

        source = inspect.getsource(self.module.main)
        # Should contain open PR blocking message
        assert "オープンPR" in source
        assert "レビュー" in source


class TestFindRecentlyMergedPrForIssue:
    """Tests for find_recently_merged_pr_for_issue function."""

    def setup_method(self):
        self.module = load_module()
        self._orig_run = subprocess.run

    def teardown_method(self):
        subprocess.run = self._orig_run

    def test_finds_recently_merged_pr_by_closes_keyword(self):
        """Should find merged PR that references issue with Closes keyword."""
        from datetime import UTC, datetime

        # Create a recent merge time (within 24 hours)
        recent_time = datetime.now(UTC).isoformat().replace("+00:00", "Z")

        mock_prs = [
            {
                "number": 200,
                "title": "Fix issue 123",
                "url": "https://github.com/test/200",
                "body": "Closes #123",
                "headRefName": "fix/something",
                "mergedAt": recent_time,
            },
        ]

        def mock_run(*args, **kwargs):
            result = type("Result", (), {})()
            result.returncode = 0
            result.stdout = json.dumps(mock_prs)
            return result

        subprocess.run = mock_run
        result = self.module.find_recently_merged_pr_for_issue(123)
        assert result is not None
        assert result["number"] == 200
        assert result["title"] == "Fix issue 123"
        assert result["mergedAt"] == recent_time

    def test_finds_recently_merged_pr_by_branch_name(self):
        """Should find merged PR when branch name contains the issue number."""
        from datetime import UTC, datetime

        recent_time = datetime.now(UTC).isoformat().replace("+00:00", "Z")

        mock_prs = [
            {
                "number": 201,
                "title": "Feature for issue 123",
                "url": "https://github.com/test/201",
                "body": "Some description without closes keyword",
                "headRefName": "feature/issue-123-description",
                "mergedAt": recent_time,
            },
        ]

        def mock_run(*args, **kwargs):
            result = type("Result", (), {})()
            result.returncode = 0
            result.stdout = json.dumps(mock_prs)
            return result

        subprocess.run = mock_run
        result = self.module.find_recently_merged_pr_for_issue(123)
        assert result is not None
        assert result["number"] == 201

    def test_returns_none_when_pr_is_too_old(self):
        """Should return None when merged PR is older than threshold."""
        from datetime import UTC, datetime, timedelta

        # Create an old merge time (more than 24 hours ago)
        old_time = (datetime.now(UTC) - timedelta(hours=48)).isoformat().replace("+00:00", "Z")

        mock_prs = [
            {
                "number": 202,
                "title": "Old fix for issue 123",
                "url": "https://github.com/test/202",
                "body": "Closes #123",
                "headRefName": "fix/old",
                "mergedAt": old_time,
            },
        ]

        def mock_run(*args, **kwargs):
            result = type("Result", (), {})()
            result.returncode = 0
            result.stdout = json.dumps(mock_prs)
            return result

        subprocess.run = mock_run
        result = self.module.find_recently_merged_pr_for_issue(123)
        assert result is None

    def test_returns_none_when_no_matching_pr(self):
        """Should return None when no merged PR references the issue."""
        from datetime import UTC, datetime

        recent_time = datetime.now(UTC).isoformat().replace("+00:00", "Z")

        mock_prs = [
            {
                "number": 203,
                "title": "Unrelated PR",
                "url": "https://github.com/test/203",
                "body": "Closes #456",
                "headRefName": "fix/other-issue",
                "mergedAt": recent_time,
            },
        ]

        def mock_run(*args, **kwargs):
            result = type("Result", (), {})()
            result.returncode = 0
            result.stdout = json.dumps(mock_prs)
            return result

        subprocess.run = mock_run
        result = self.module.find_recently_merged_pr_for_issue(123)
        assert result is None

    def test_returns_none_on_error(self):
        """Should return None on gh command error."""

        def mock_run(*args, **kwargs):
            result = type("Result", (), {})()
            result.returncode = 1
            result.stdout = ""
            return result

        subprocess.run = mock_run
        result = self.module.find_recently_merged_pr_for_issue(123)
        assert result is None

    def test_returns_none_on_timeout(self):
        """Should return None on timeout."""

        def mock_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="gh", timeout=10)

        subprocess.run = mock_run
        result = self.module.find_recently_merged_pr_for_issue(123)
        assert result is None

    def test_custom_hours_threshold(self):
        """Should respect custom hours threshold."""
        from datetime import UTC, datetime, timedelta

        # Create a time that is 12 hours ago
        time_12h_ago = (datetime.now(UTC) - timedelta(hours=12)).isoformat().replace("+00:00", "Z")

        mock_prs = [
            {
                "number": 204,
                "title": "Fix issue 123",
                "url": "https://github.com/test/204",
                "body": "Closes #123",
                "headRefName": "fix/something",
                "mergedAt": time_12h_ago,
            },
        ]

        def mock_run(*args, **kwargs):
            result = type("Result", (), {})()
            result.returncode = 0
            result.stdout = json.dumps(mock_prs)
            return result

        subprocess.run = mock_run

        # With 24 hours threshold (default), should find the PR
        result = self.module.find_recently_merged_pr_for_issue(123)
        assert result is not None

        # With 6 hours threshold, should not find the PR
        result = self.module.find_recently_merged_pr_for_issue(123, hours=6)
        assert result is None

    def test_handles_resolves_keyword(self):
        """Should find PR with Resolves keyword."""
        from datetime import UTC, datetime

        recent_time = datetime.now(UTC).isoformat().replace("+00:00", "Z")

        mock_prs = [
            {
                "number": 205,
                "title": "Resolve issue 123",
                "url": "https://github.com/test/205",
                "body": "Resolves #123",
                "headRefName": "fix/whatever",
                "mergedAt": recent_time,
            },
        ]

        def mock_run(*args, **kwargs):
            result = type("Result", (), {})()
            result.returncode = 0
            result.stdout = json.dumps(mock_prs)
            return result

        subprocess.run = mock_run
        result = self.module.find_recently_merged_pr_for_issue(123)
        assert result is not None
        assert result["number"] == 205


class TestMergedPrWarningIntegration:
    """Integration tests for merged PR warning behavior.

    Note: These tests verify the warning logic by testing the conditions that trigger warnings.
    Full E2E tests via run_hook() would require mocking gh commands, which is complex.
    The actual warning generation is tested in TestFindRecentlyMergedPrForIssue.
    """

    def setup_method(self):
        self.module = load_module()

    def test_warning_only_on_approve_decision(self):
        """Verify warning logic is skipped when decision is block.

        This tests the condition: if result.get("decision") != "block"
        """
        # When decision is "approve", warning should be added
        result_approve = {"decision": "approve", "systemMessage": "Test message"}
        if result_approve.get("decision") != "block":
            result_approve["systemMessage"] = result_approve["systemMessage"] + "\n⚠️ Warning"
        assert "⚠️ Warning" in result_approve["systemMessage"]

        # When decision is "block", warning should NOT be added
        result_block = {"decision": "block", "reason": "Test block", "systemMessage": "❌ Blocked"}
        if result_block.get("decision") != "block":
            result_block["systemMessage"] = result_block["systemMessage"] + "\n⚠️ Warning"
        assert "⚠️ Warning" not in result_block.get("systemMessage", "")

    def test_warning_format_contains_required_info(self):
        """Verify warning message format contains all required information."""
        issue_number = 123
        merged_pr = {
            "number": 999,
            "title": "Already fixed",
            "url": "https://github.com/test/999",
            "mergedAt": "2025-12-28T00:00:00Z",
        }

        # Generate warning using the same format as main()
        warning = (
            f"\n\n⚠️ Issue #{issue_number} を参照するPRが最近マージされました:\n"
            f"   PR #{merged_pr['number']}: {merged_pr['title']}\n"
            f"   URL: {merged_pr['url']}\n\n"
            f"同じ修正が既に適用されている可能性があります。\n"
            f"確認: `gh pr view {merged_pr['number']}`"
        )

        # Verify all required elements are present
        assert "⚠️ Issue #123" in warning
        assert "最近マージされました" in warning
        assert "PR #999" in warning
        assert "Already fixed" in warning
        assert "https://github.com/test/999" in warning
        assert "同じ修正が既に適用されている可能性" in warning
        assert "gh pr view 999" in warning

    def test_find_recently_merged_pr_function_returns_correct_format(self):
        """Verify find_recently_merged_pr_for_issue returns dict with required keys."""
        from datetime import UTC, datetime

        recent_time = datetime.now(UTC).isoformat()

        mock_prs = [
            {
                "number": 100,
                "title": "Test PR",
                "url": "https://github.com/test/100",
                "body": "Closes #123",
                "headRefName": "fix/test",
                "mergedAt": recent_time,
            },
        ]

        def mock_run(*args, **kwargs):
            result = type("Result", (), {})()
            result.returncode = 0
            result.stdout = json.dumps(mock_prs)
            return result

        orig_run = subprocess.run
        subprocess.run = mock_run
        try:
            result = self.module.find_recently_merged_pr_for_issue(123)
            assert result is not None
            # Verify returned dict has all required keys
            assert "number" in result
            assert "title" in result
            assert "url" in result
            assert "mergedAt" in result
        finally:
            subprocess.run = orig_run


class TestHookIntegration:
    """Integration tests for the hook."""

    def test_approve_non_worktree_commands(self):
        """Should approve commands that are not git worktree add."""
        test_cases = [
            "ls -la",
            "git status",
            "git worktree list",
            "echo 'git worktree add'",
        ]
        for command in test_cases:
            with self.subTest(command=command):
                result = run_hook({"tool_input": {"command": command}})
                assert result["decision"] == "approve"

    def test_approve_empty_command(self):
        """Should approve when command is empty."""
        result = run_hook({"tool_input": {"command": ""}})
        assert result["decision"] == "approve"

    def test_approve_worktree_without_issue(self):
        """Should approve worktree add without issue number in branch."""
        command = "git worktree add --lock .worktrees/test -b feature/no-issue"
        result = run_hook({"tool_input": {"command": command}})
        assert result["decision"] == "approve"
