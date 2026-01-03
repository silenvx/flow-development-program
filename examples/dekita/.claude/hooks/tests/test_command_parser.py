#!/usr/bin/env python3
"""Tests for command_parser module.

Issue #1175: Comprehensive tests for API operation logging.
"""

from lib.command_parser import (
    extract_result_from_output,
    extract_worktree_add_path,
    is_target_command,
    parse_command,
    parse_gh_command,
    parse_git_command,
    parse_npm_command,
)


class TestParseCommand:
    """Tests for parse_command() - main entry point."""

    def test_empty_command(self):
        """Empty command returns None."""
        assert parse_command("") is None
        assert parse_command("   ") is None
        assert parse_command(None) is None  # type: ignore

    def test_routes_to_gh_parser(self):
        """gh commands are routed to gh parser."""
        result = parse_command("gh pr view 123")
        assert result is not None
        assert result["type"] == "gh"

    def test_routes_to_git_parser(self):
        """git commands are routed to git parser."""
        result = parse_command("git push origin main")
        assert result is not None
        assert result["type"] == "git"

    def test_routes_to_npm_parser(self):
        """npm/pnpm commands are routed to npm parser."""
        result = parse_command("npm run build")
        assert result is not None
        assert result["type"] == "npm"

        result = parse_command("pnpm install")
        assert result is not None
        assert result["type"] == "npm"

    def test_unrecognized_command(self):
        """Unrecognized commands return None."""
        assert parse_command("ls -la") is None
        assert parse_command("cat file.txt") is None
        assert parse_command("echo hello") is None


class TestParseGhCommand:
    """Tests for parse_gh_command() - GitHub CLI parsing."""

    def test_gh_pr_view(self):
        """Parse gh pr view command."""
        result = parse_gh_command("gh pr view 123")
        assert result["type"] == "gh"
        assert result["operation"] == "pr_view"
        assert result["main_command"] == "pr"
        assert result["subcommand"] == "view"
        assert result["pr_number"] == 123

    def test_gh_pr_create(self):
        """Parse gh pr create command."""
        result = parse_gh_command("gh pr create --title 'Test PR'")
        assert result["operation"] == "pr_create"
        assert result["args"]["--title"] == "Test PR"

    def test_gh_pr_merge(self):
        """Parse gh pr merge command."""
        result = parse_gh_command("gh pr merge 456 --squash")
        assert result["operation"] == "pr_merge"
        assert result["pr_number"] == 456
        assert result["args"]["--squash"] is True

    def test_gh_issue_view(self):
        """Parse gh issue view command."""
        result = parse_gh_command("gh issue view 789")
        assert result["operation"] == "issue_view"
        assert result["issue_number"] == 789

    def test_gh_issue_create(self):
        """Parse gh issue create command."""
        result = parse_gh_command("gh issue create --title 'Bug' --label bug")
        assert result["operation"] == "issue_create"
        assert result["args"]["--title"] == "Bug"
        assert result["args"]["--label"] == "bug"

    def test_gh_issue_comment(self):
        """Parse gh issue comment command (Issue #1692)."""
        result = parse_gh_command("gh issue comment 123 --body 'Test comment'")
        assert result["operation"] == "issue_comment"
        assert result["issue_number"] == 123
        assert result["args"]["--body"] == "Test comment"

    def test_gh_api(self):
        """Parse gh api command."""
        result = parse_gh_command("gh api repos/owner/repo/pulls")
        assert result["operation"] == "api"
        assert result["endpoint"] == "repos/owner/repo/pulls"
        assert result["api_type"] == "rest"  # Issue #1269

    def test_gh_api_with_method(self):
        """Parse gh api command with method."""
        result = parse_gh_command("gh api -X POST repos/owner/repo/issues")
        assert result["method"] == "POST"
        assert result["endpoint"] == "repos/owner/repo/issues"
        assert result["api_type"] == "rest"  # Issue #1269

    def test_gh_api_graphql(self):
        """Parse gh api graphql command (Issue #1269)."""
        result = parse_gh_command("gh api graphql -f query='...'")
        assert result["operation"] == "api"
        assert result["endpoint"] == "graphql"
        assert result["api_type"] == "graphql"

    def test_gh_api_type_detection(self):
        """Distinguish GraphQL vs REST API (Issue #1269)."""
        # GraphQL
        graphql_result = parse_gh_command("gh api graphql")
        assert graphql_result["api_type"] == "graphql"

        # REST (various endpoints)
        rest_result = parse_gh_command("gh api /repos/owner/repo")
        assert rest_result["api_type"] == "rest"

        rest_result2 = parse_gh_command("gh api repos/owner/repo/pulls/123/comments")
        assert rest_result2["api_type"] == "rest"

    def test_gh_api_no_endpoint(self):
        """gh api without endpoint should have api_type 'unknown' (Issue #1269)."""
        # Verify api_type is set to 'unknown' when no endpoint is specified
        result = parse_gh_command("gh api --help")
        assert result is not None
        assert result["api_type"] == "unknown"
        assert "endpoint" not in result

    def test_gh_run_view(self):
        """Parse gh run view command."""
        result = parse_gh_command("gh run view 12345")
        assert result["operation"] == "run_view"
        assert result["run_id"] == 12345

    def test_gh_auth_status(self):
        """Parse gh auth status command."""
        result = parse_gh_command("gh auth status")
        assert result["operation"] == "auth_status"

    def test_gh_with_global_flags(self):
        """Parse gh command with global flags."""
        result = parse_gh_command("gh --repo owner/repo pr view 123")
        assert result["pr_number"] == 123

    def test_gh_empty_tokens(self):
        """Parse gh command with only global flags returns None."""
        result = parse_gh_command("gh --help")
        # Global flags only should return None (no actionable operation)
        assert result is None

    def test_gh_issue_with_hash_prefix(self):
        """Parse gh issue with # prefix."""
        result = parse_gh_command("gh issue view #123")
        assert result["issue_number"] == 123

    def test_gh_quoted_strings(self):
        """Parse gh command with quoted strings."""
        result = parse_gh_command('gh pr create --title "Test with spaces"')
        assert result["args"]["--title"] == "Test with spaces"

    def test_gh_with_pipe(self):
        """Parse gh command before pipe."""
        result = parse_gh_command("gh pr list --json number | jq '.[0]'")
        assert result["operation"] == "pr_list"
        assert result["args"]["--json"] == "number"


class TestParseGitCommand:
    """Tests for parse_git_command() - git command parsing."""

    def test_git_push_basic(self):
        """Parse basic git push."""
        result = parse_git_command("git push")
        assert result["type"] == "git"
        assert result["operation"] == "push"

    def test_git_push_with_remote_branch(self):
        """Parse git push with remote and branch."""
        result = parse_git_command("git push origin main")
        assert result["remote"] == "origin"
        assert result["branch"] == "main"

    def test_git_push_force(self):
        """Parse git push --force."""
        result = parse_git_command("git push --force origin feature")
        assert result["args"]["force"] is True
        assert result["remote"] == "origin"

    def test_git_push_set_upstream(self):
        """Parse git push -u."""
        result = parse_git_command("git push -u origin feature")
        assert result["args"]["set_upstream"] is True

    def test_git_pull_basic(self):
        """Parse basic git pull."""
        result = parse_git_command("git pull")
        assert result["operation"] == "pull"

    def test_git_pull_rebase(self):
        """Parse git pull --rebase."""
        result = parse_git_command("git pull --rebase origin main")
        assert result["args"]["rebase"] is True
        assert result["remote"] == "origin"

    def test_git_commit_with_message(self):
        """Parse git commit with message."""
        result = parse_git_command('git commit -m "Fix bug"')
        assert result["operation"] == "commit"
        assert result["message"] == "Fix bug"

    def test_git_commit_amend(self):
        """Parse git commit --amend."""
        result = parse_git_command("git commit --amend --no-verify")
        assert result["args"]["amend"] is True
        assert result["args"]["no_verify"] is True

    def test_git_worktree_add(self):
        """Parse git worktree add."""
        result = parse_git_command("git worktree add .worktrees/issue-123 main")
        assert result["operation"] == "worktree_add"
        assert result["worktree_action"] == "add"
        assert result["path"] == ".worktrees/issue-123"
        assert result["branch"] == "main"

    def test_git_worktree_remove(self):
        """Parse git worktree remove."""
        result = parse_git_command("git worktree remove .worktrees/issue-123")
        assert result["operation"] == "worktree_remove"
        assert result["path"] == ".worktrees/issue-123"

    def test_git_checkout_branch(self):
        """Parse git checkout branch."""
        result = parse_git_command("git checkout feature-branch")
        assert result["operation"] == "checkout"
        assert result["target"] == "feature-branch"

    def test_git_checkout_new_branch(self):
        """Parse git checkout -b."""
        result = parse_git_command("git checkout -b new-feature")
        assert result["args"]["create_branch"] is True
        assert result["target"] == "new-feature"

    def test_git_switch(self):
        """Parse git switch."""
        result = parse_git_command("git switch main")
        assert result["operation"] == "switch"
        assert result["target"] == "main"

    def test_git_merge(self):
        """Parse git merge."""
        result = parse_git_command("git merge feature --no-ff")
        assert result["operation"] == "merge"
        assert result["source"] == "feature"
        assert result["args"]["no_ff"] is True

    def test_git_rebase(self):
        """Parse git rebase."""
        result = parse_git_command("git rebase main")
        assert result["operation"] == "rebase"
        assert result["onto"] == "main"

    def test_git_rebase_continue(self):
        """Parse git rebase --continue."""
        result = parse_git_command("git rebase --continue")
        assert result["args"]["continue"] is True

    def test_git_with_global_flags(self):
        """Parse git with global flags."""
        result = parse_git_command("git -C /path/to/repo push")
        assert result["operation"] == "push"

    def test_git_empty_after_flags(self):
        """Parse git with only global flags returns None."""
        result = parse_git_command("git -C /path --version")
        # --version is a flag not a subcommand, so returns None
        assert result is None

    def test_git_with_pipe(self):
        """Parse git command before pipe."""
        result = parse_git_command("git log --oneline | head -5")
        assert result["operation"] == "log"


class TestParseNpmCommand:
    """Tests for parse_npm_command() - npm/pnpm parsing."""

    def test_npm_run(self):
        """Parse npm run command."""
        result = parse_npm_command("npm run build")
        assert result["type"] == "npm"
        assert result["package_manager"] == "npm"
        assert result["operation"] == "run_build"
        assert result["script"] == "build"

    def test_pnpm_run(self):
        """Parse pnpm run command."""
        result = parse_npm_command("pnpm run test:ci")
        assert result["package_manager"] == "pnpm"
        assert result["script"] == "test:ci"

    def test_npm_install(self):
        """Parse npm install command."""
        result = parse_npm_command("npm install")
        assert result["operation"] == "install"

    def test_npm_install_package(self):
        """Parse npm install with package."""
        result = parse_npm_command("npm install lodash")
        assert result["operation"] == "install"
        assert result["packages"] == ["lodash"]

    def test_npm_install_multiple(self):
        """Parse npm install multiple packages."""
        result = parse_npm_command("npm install lodash axios")
        assert result["packages"] == ["lodash", "axios"]

    def test_pnpm_add(self):
        """Parse pnpm add command."""
        result = parse_npm_command("pnpm add react")
        assert result["operation"] == "install"
        assert result["packages"] == ["react"]

    def test_npm_test(self):
        """Parse npm test command."""
        result = parse_npm_command("npm test")
        assert result["operation"] == "test"

    def test_npm_build(self):
        """Parse npm build command."""
        result = parse_npm_command("npm run build")
        assert result["script"] == "build"

    def test_npm_short_forms(self):
        """Parse npm short forms."""
        result = parse_npm_command("npm i lodash")
        assert result["operation"] == "install"

        result = parse_npm_command("npm t")
        assert result["operation"] == "test"

    def test_npm_with_flags(self):
        """Parse npm with flags."""
        result = parse_npm_command("npm install --save-dev typescript")
        assert result["packages"] == ["typescript"]


class TestExtractResultFromOutput:
    """Tests for extract_result_from_output()."""

    def test_extract_github_url(self):
        """Extract GitHub URL from output."""
        parsed = {"type": "gh", "operation": "pr_create"}
        stdout = "https://github.com/owner/repo/pull/123"
        result = extract_result_from_output(parsed, stdout)
        assert result["url"] == "https://github.com/owner/repo/pull/123"
        assert result["number"] == 123
        assert result["resource_type"] == "pr"

    def test_extract_issue_url(self):
        """Extract issue URL from output."""
        parsed = {"type": "gh", "operation": "issue_create"}
        stdout = "https://github.com/owner/repo/issues/456"
        result = extract_result_from_output(parsed, stdout)
        assert result["number"] == 456
        assert result["resource_type"] == "issue"

    def test_extract_pr_number(self):
        """Extract PR number from # prefix."""
        parsed = {"type": "gh", "operation": "pr_create"}
        stdout = "Created #789"
        result = extract_result_from_output(parsed, stdout)
        assert result["number"] == 789

    def test_extract_merge_result(self):
        """Extract merge result."""
        parsed = {"type": "gh", "operation": "pr_merge"}
        stdout = "PR was merged successfully"
        result = extract_result_from_output(parsed, stdout)
        assert result["merged"] is True

    def test_extract_already_merged(self):
        """Extract already merged result."""
        parsed = {"type": "gh", "operation": "pr_merge"}
        stdout = "PR already merged"
        result = extract_result_from_output(parsed, stdout)
        assert result["already_merged"] is True

    def test_extract_commit_hash(self):
        """Extract commit hash from git commit output."""
        parsed = {"type": "git", "operation": "commit"}
        stdout = "[main abc1234] Fix bug"
        result = extract_result_from_output(parsed, stdout)
        assert result["commit_hash"] == "abc1234"

    def test_extract_push_result(self):
        """Extract push result."""
        parsed = {"type": "git", "operation": "push"}
        stdout = "main -> main"
        result = extract_result_from_output(parsed, stdout)
        assert result["pushed"] is True

    def test_extract_push_up_to_date(self):
        """Extract up-to-date push result."""
        parsed = {"type": "git", "operation": "push"}
        stdout = "Everything up-to-date"
        result = extract_result_from_output(parsed, stdout)
        assert result["already_up_to_date"] is True

    def test_extract_checkout_branch(self):
        """Extract switched branch from checkout."""
        parsed = {"type": "git", "operation": "checkout"}
        stdout = "Switched to branch 'feature'"
        result = extract_result_from_output(parsed, stdout)
        assert result["switched_to"] == "feature"

    def test_extract_npm_errors(self):
        """Extract npm error indicators."""
        parsed = {"type": "npm", "operation": "install"}
        stderr = "npm ERR! Failed to install"
        result = extract_result_from_output(parsed, "", stderr)
        assert result["has_errors"] is True

    def test_extract_npm_warnings(self):
        """Extract npm warning indicators."""
        parsed = {"type": "npm", "operation": "install"}
        stdout = "npm WARN deprecated package"
        result = extract_result_from_output(parsed, stdout)
        assert result["has_warnings"] is True

    def test_empty_parsed(self):
        """Handle empty parsed data."""
        result = extract_result_from_output(None, "output")  # type: ignore
        assert result == {}

        result = extract_result_from_output({}, "output")
        assert result == {}

    def test_extract_issue_comment_result(self):
        """Extract issue comment result (Issue #1692)."""
        parsed = {"type": "gh", "operation": "issue_comment", "issue_number": 123}
        stdout = "https://github.com/owner/repo/issues/123#issuecomment-456789"
        result = extract_result_from_output(parsed, stdout)
        assert result["issue_number"] == 123
        assert result["comment_id"] == 456789
        assert result["comment_added"] is True

    def test_extract_issue_comment_fallback(self):
        """Extract issue number from parsed when URL not in output (Issue #1692)."""
        parsed = {"type": "gh", "operation": "issue_comment", "issue_number": 456}
        stdout = "Comment added"
        result = extract_result_from_output(parsed, stdout)
        assert result["issue_number"] == 456
        assert "comment_id" not in result


class TestIsTargetCommand:
    """Tests for is_target_command().

    Design Intent (Issue #1177):
        Commands with only global flags (e.g., `gh --help`, `git --version`)
        are intentionally excluded because they are not actionable API operations.
        See command_parser.py for full documentation.
    """

    def test_empty_command(self):
        """Empty command returns False."""
        assert is_target_command("") is False
        assert is_target_command(None) is False  # type: ignore

    def test_gh_pr_commands(self):
        """gh pr commands are targets."""
        assert is_target_command("gh pr view 123") is True
        assert is_target_command("gh pr create") is True
        assert is_target_command("gh pr merge 456") is True
        assert is_target_command("gh pr list") is True

    def test_gh_issue_commands(self):
        """gh issue commands are targets."""
        assert is_target_command("gh issue view 123") is True
        assert is_target_command("gh issue create") is True

    def test_gh_api_commands(self):
        """gh api commands are targets."""
        assert is_target_command("gh api repos/owner/repo") is True

    def test_gh_run_commands(self):
        """gh run commands are targets."""
        assert is_target_command("gh run view 12345") is True
        assert is_target_command("gh run watch 12345") is True

    def test_gh_auth_commands(self):
        """gh auth commands are targets."""
        assert is_target_command("gh auth status") is True

    def test_gh_with_global_flags(self):
        """gh commands with global flags before subcommand are targets (Issue #1230)."""
        assert is_target_command("gh --repo owner/repo pr list") is True
        assert is_target_command("gh -R owner/repo pr view 123") is True
        assert is_target_command("gh --repo owner/repo issue view 456") is True
        assert is_target_command("gh -R owner/repo issue create") is True

    def test_gh_non_target_commands(self):
        """gh non-target commands return False."""
        assert is_target_command("gh help") is False
        assert is_target_command("gh version") is False
        assert is_target_command("gh config") is False
        assert is_target_command("gh --help") is False
        assert is_target_command("gh --version") is False

    def test_git_target_commands(self):
        """git target commands."""
        assert is_target_command("git push") is True
        assert is_target_command("git pull") is True
        assert is_target_command("git commit -m 'msg'") is True
        assert is_target_command("git worktree add path") is True
        assert is_target_command("git checkout branch") is True
        assert is_target_command("git switch main") is True
        assert is_target_command("git merge feature") is True
        assert is_target_command("git rebase main") is True

    def test_git_non_target_commands(self):
        """git non-target commands return False."""
        assert is_target_command("git status") is False
        assert is_target_command("git log") is False
        assert is_target_command("git diff") is False
        assert is_target_command("git add .") is False
        assert is_target_command("git branch") is False

    def test_npm_target_commands(self):
        """npm/pnpm target commands."""
        assert is_target_command("npm run build") is True
        assert is_target_command("npm install") is True
        assert is_target_command("npm test") is True
        assert is_target_command("pnpm install") is True
        assert is_target_command("pnpm run test") is True
        assert is_target_command("npm i lodash") is True
        assert is_target_command("npm t") is True
        assert is_target_command("pnpm add react") is True

    def test_npm_non_target_commands(self):
        """npm non-target commands return False."""
        assert is_target_command("npm --version") is False
        assert is_target_command("npm help") is False

    def test_non_target_commands(self):
        """Non-target commands return False."""
        assert is_target_command("ls -la") is False
        assert is_target_command("cat file.txt") is False
        assert is_target_command("echo hello") is False
        assert is_target_command("python script.py") is False


class TestExtractPrMergeState:
    """Tests for PR merge state extraction (Issue #1246)."""

    def test_extract_merge_state_dirty(self):
        """Extract DIRTY merge state from JSON output."""
        parsed = {"type": "gh", "operation": "pr_view"}
        stdout = '{"mergeStateStatus":"DIRTY","number":123}'
        result = extract_result_from_output(parsed, stdout)
        assert result["merge_state"] == "DIRTY"
        assert result["has_merge_issue"] is True

    def test_extract_merge_state_clean(self):
        """Extract CLEAN merge state - no issue flag."""
        parsed = {"type": "gh", "operation": "pr_view"}
        stdout = '{"mergeStateStatus":"CLEAN","number":123}'
        result = extract_result_from_output(parsed, stdout)
        assert result["merge_state"] == "CLEAN"
        assert "has_merge_issue" not in result

    def test_extract_merge_state_blocked(self):
        """Extract BLOCKED merge state."""
        parsed = {"type": "gh", "operation": "pr_view"}
        stdout = '{"mergeStateStatus":"BLOCKED","number":123}'
        result = extract_result_from_output(parsed, stdout)
        assert result["merge_state"] == "BLOCKED"
        assert result["has_merge_issue"] is True

    def test_extract_merge_state_behind(self):
        """Extract BEHIND merge state."""
        parsed = {"type": "gh", "operation": "pr_view"}
        stdout = '{"mergeStateStatus":"BEHIND","number":123}'
        result = extract_result_from_output(parsed, stdout)
        assert result["merge_state"] == "BEHIND"
        assert result["has_merge_issue"] is True

    def test_extract_merge_state_unknown(self):
        """Extract UNKNOWN merge state."""
        parsed = {"type": "gh", "operation": "pr_view"}
        stdout = '{"mergeStateStatus":"UNKNOWN","number":123}'
        result = extract_result_from_output(parsed, stdout)
        assert result["merge_state"] == "UNKNOWN"
        assert result["has_merge_issue"] is True

    def test_extract_merge_state_unstable(self):
        """Extract UNSTABLE merge state - CI issues, not merge issue."""
        parsed = {"type": "gh", "operation": "pr_view"}
        stdout = '{"mergeStateStatus":"UNSTABLE","number":123}'
        result = extract_result_from_output(parsed, stdout)
        assert result["merge_state"] == "UNSTABLE"
        # UNSTABLE is CI issue, not merge conflict
        assert "has_merge_issue" not in result

    def test_extract_merge_state_no_json(self):
        """No merge state when output is not JSON."""
        parsed = {"type": "gh", "operation": "pr_view"}
        stdout = "PR #123: Some title\nOpen - No conflicts"
        result = extract_result_from_output(parsed, stdout)
        assert "merge_state" not in result

    def test_extract_merge_state_from_regex_pattern(self):
        """Extract merge state from regex pattern when not valid JSON."""
        parsed = {"type": "gh", "operation": "pr_view"}
        stdout = 'prefix {"mergeStateStatus": "DIRTY"} suffix'
        result = extract_result_from_output(parsed, stdout)
        assert result["merge_state"] == "DIRTY"
        assert result["has_merge_issue"] is True

    def test_extract_merge_state_other_operation(self):
        """Don't extract merge state for non-pr_view operations."""
        parsed = {"type": "gh", "operation": "pr_list"}
        stdout = '{"mergeStateStatus":"DIRTY","number":123}'
        result = extract_result_from_output(parsed, stdout)
        assert "merge_state" not in result

    def test_extract_merge_state_has_hooks(self):
        """Extract HAS_HOOKS merge state - not a merge issue."""
        parsed = {"type": "gh", "operation": "pr_view"}
        stdout = '{"mergeStateStatus":"HAS_HOOKS","number":123}'
        result = extract_result_from_output(parsed, stdout)
        assert result["merge_state"] == "HAS_HOOKS"
        # HAS_HOOKS means hooks need to run, not a conflict
        assert "has_merge_issue" not in result

    def test_extract_merge_state_jq_output_quoted(self):
        """Extract merge state from jq output (quoted string)."""
        parsed = {"type": "gh", "operation": "pr_view"}
        stdout = '"DIRTY"'
        result = extract_result_from_output(parsed, stdout)
        assert result["merge_state"] == "DIRTY"
        assert result["has_merge_issue"] is True

    def test_extract_merge_state_jq_output_unquoted(self):
        """Extract merge state from jq -r output (unquoted string)."""
        parsed = {"type": "gh", "operation": "pr_view"}
        stdout = "CLEAN"
        result = extract_result_from_output(parsed, stdout)
        assert result["merge_state"] == "CLEAN"
        assert "has_merge_issue" not in result

    def test_extract_merge_state_empty_string(self):
        """Empty mergeStateStatus should not be extracted."""
        parsed = {"type": "gh", "operation": "pr_view"}
        stdout = '{"mergeStateStatus":"","number":123}'
        result = extract_result_from_output(parsed, stdout)
        assert "merge_state" not in result

    def test_extract_merge_state_null_value(self):
        """Null mergeStateStatus should not be extracted."""
        parsed = {"type": "gh", "operation": "pr_view"}
        stdout = '{"mergeStateStatus":null,"number":123}'
        result = extract_result_from_output(parsed, stdout)
        assert "merge_state" not in result

    def test_extract_merge_state_invalid_state(self):
        """Invalid state string should not be extracted from jq output."""
        parsed = {"type": "gh", "operation": "pr_view"}
        stdout = '"INVALID_STATE"'
        result = extract_result_from_output(parsed, stdout)
        assert "merge_state" not in result

    def test_extract_merge_state_invalid_state_from_json(self):
        """Invalid state from JSON should not be extracted."""
        parsed = {"type": "gh", "operation": "pr_view"}
        stdout = '{"mergeStateStatus":"INVALID_STATE","number":123}'
        result = extract_result_from_output(parsed, stdout)
        assert "merge_state" not in result


class TestExtractForcePush:
    """Tests for Issue #1248: Force push detection in extract_result_from_output."""

    def test_force_push_with_force_flag(self):
        """git push --force should set force_push=True."""
        parsed = {"type": "git", "operation": "push", "args": {"force": True}}
        stdout = "To github.com:user/repo.git\n + abc123...def456 main -> main (forced update)"
        result = extract_result_from_output(parsed, stdout)
        assert result["force_push"] is True
        assert result["pushed"] is True

    def test_force_push_with_force_with_lease(self):
        """git push --force-with-lease should set force_push=True."""
        parsed = {"type": "git", "operation": "push", "args": {"force_with_lease": True}}
        stdout = "To github.com:user/repo.git\n + abc123...def456 main -> main (forced update)"
        result = extract_result_from_output(parsed, stdout)
        assert result["force_push"] is True
        assert result["pushed"] is True

    def test_normal_push_no_force_flag(self):
        """Normal git push should not set force_push."""
        parsed = {"type": "git", "operation": "push", "args": {}}
        stdout = "To github.com:user/repo.git\n   abc123..def456  main -> main"
        result = extract_result_from_output(parsed, stdout)
        assert "force_push" not in result
        assert result["pushed"] is True

    def test_push_up_to_date_with_force(self):
        """Force push that's already up-to-date should still set force_push."""
        parsed = {"type": "git", "operation": "push", "args": {"force": True}}
        stdout = "Everything up-to-date"
        result = extract_result_from_output(parsed, stdout)
        assert result["force_push"] is True
        assert result["already_up_to_date"] is True

    def test_push_no_args(self):
        """Push with no args dict should not crash."""
        parsed = {"type": "git", "operation": "push"}
        stdout = "To github.com:user/repo.git\n   abc123..def456  main -> main"
        result = extract_result_from_output(parsed, stdout)
        assert "force_push" not in result

    def test_force_push_with_short_flag(self):
        """git push -f should set force_push=True (Codex review fix)."""
        parsed = {"type": "git", "operation": "push", "args": {"force": True}}
        stdout = "To github.com:user/repo.git\n + abc123...def456 main -> main (forced update)"
        result = extract_result_from_output(parsed, stdout)
        assert result["force_push"] is True

    def test_force_push_with_lease_ref(self):
        """git push --force-with-lease=origin/main should set force_push=True."""
        parsed = {"type": "git", "operation": "push", "args": {"force_with_lease": True}}
        stdout = "To github.com:user/repo.git\n + abc123...def456 main -> main (forced update)"
        result = extract_result_from_output(parsed, stdout)
        assert result["force_push"] is True


class TestExtractRebaseResult:
    """Tests for Issue #1693: git rebase result extraction."""

    def test_rebase_conflict_detected(self):
        """Detect rebase conflict from output."""
        parsed = {"type": "git", "operation": "rebase", "args": {}}
        stdout = """
Auto-merging file.py
CONFLICT (content): Merge conflict in file.py
error: could not apply abc1234... Some commit
"""
        result = extract_result_from_output(parsed, stdout)
        assert result["conflict_detected"] is True
        assert "file.py" in result["conflict_files"]

    def test_rebase_multiple_conflicts(self):
        """Detect multiple conflicting files."""
        parsed = {"type": "git", "operation": "rebase", "args": {}}
        stdout = """
CONFLICT (content): Merge conflict in src/a.py
CONFLICT (content): Merge conflict in src/b.py
"""
        result = extract_result_from_output(parsed, stdout)
        assert result["conflict_detected"] is True
        assert "src/a.py" in result["conflict_files"]
        assert "src/b.py" in result["conflict_files"]

    def test_rebase_completed(self):
        """Detect successful rebase completion."""
        parsed = {"type": "git", "operation": "rebase", "args": {}}
        stdout = "Successfully rebased and updated refs/heads/feature."
        result = extract_result_from_output(parsed, stdout)
        assert result["rebase_completed"] is True
        assert "conflict_detected" not in result

    def test_rebase_aborted(self):
        """Detect rebase abort."""
        parsed = {"type": "git", "operation": "rebase", "args": {"abort": True}}
        stdout = ""
        result = extract_result_from_output(parsed, stdout)
        assert result["rebase_aborted"] is True

    def test_rebase_continued(self):
        """Detect rebase continue."""
        parsed = {"type": "git", "operation": "rebase", "args": {"continue": True}}
        stdout = "Successfully rebased and updated refs/heads/feature."
        result = extract_result_from_output(parsed, stdout)
        assert result["rebase_continued"] is True
        assert result["rebase_completed"] is True


class TestExtractMergeResult:
    """Tests for Issue #1693: git merge result extraction."""

    def test_merge_conflict_detected(self):
        """Detect merge conflict from output."""
        parsed = {"type": "git", "operation": "merge", "args": {}}
        stdout = """
Auto-merging file.py
CONFLICT (content): Merge conflict in file.py
Automatic merge failed; fix conflicts and then commit the result.
"""
        result = extract_result_from_output(parsed, stdout)
        assert result["conflict_detected"] is True
        assert "file.py" in result["conflict_files"]

    def test_merge_completed_recursive(self):
        """Detect successful recursive merge."""
        parsed = {"type": "git", "operation": "merge", "args": {}}
        stdout = """
Merge made by the 'ort' strategy.
 file.py | 10 ++++++++++
 1 file changed, 10 insertions(+)
"""
        result = extract_result_from_output(parsed, stdout)
        assert result["merge_completed"] is True

    def test_merge_completed_fast_forward(self):
        """Detect successful fast-forward merge."""
        parsed = {"type": "git", "operation": "merge", "args": {}}
        stdout = """
Updating abc1234..def5678
Fast-forward
 file.py | 5 +++++
 1 file changed, 5 insertions(+)
"""
        result = extract_result_from_output(parsed, stdout)
        assert result["merge_completed"] is True

    def test_merge_already_up_to_date(self):
        """Detect already up-to-date merge."""
        parsed = {"type": "git", "operation": "merge", "args": {}}
        stdout = "Already up to date."
        result = extract_result_from_output(parsed, stdout)
        assert result["already_up_to_date"] is True


class TestExtractWorktreeResult:
    """Tests for Issue #1693: git worktree result extraction."""

    def test_worktree_add_success(self):
        """Detect successful worktree add."""
        parsed = {
            "type": "git",
            "operation": "worktree_add",
            "worktree_action": "add",
            "path": ".worktrees/issue-123",
        }
        stdout = "Preparing worktree (new branch 'feat/issue-123')"
        result = extract_result_from_output(parsed, stdout)
        assert result["worktree_created"] is True
        assert result["worktree_path"] == ".worktrees/issue-123"

    def test_worktree_remove_success(self):
        """Detect successful worktree remove."""
        parsed = {
            "type": "git",
            "operation": "worktree_remove",
            "worktree_action": "remove",
            "path": ".worktrees/issue-123",
        }
        stdout = ""
        result = extract_result_from_output(parsed, stdout)
        assert result["worktree_removed"] is True
        assert result["worktree_path"] == ".worktrees/issue-123"

    def test_worktree_remove_error(self):
        """Detect failed worktree remove when stderr has error."""
        parsed = {
            "type": "git",
            "operation": "worktree_remove",
            "worktree_action": "remove",
            "path": ".worktrees/issue-123",
        }
        stdout = ""
        stderr = "fatal: '.worktrees/issue-123' is a main working tree"
        result = extract_result_from_output(parsed, stdout, stderr)
        assert "worktree_removed" not in result
        assert result["worktree_path"] == ".worktrees/issue-123"


class TestExtractPrIssueCreateDetails:
    """Tests for Issue #1693: gh pr/issue create details extraction."""

    def test_pr_create_with_title(self):
        """Extract title from pr create."""
        parsed = {
            "type": "gh",
            "operation": "pr_create",
            "args": {"--title": "Fix bug", "--base": "main"},
        }
        stdout = "https://github.com/owner/repo/pull/123"
        result = extract_result_from_output(parsed, stdout)
        assert result["title"] == "Fix bug"
        assert result["base_branch"] == "main"
        assert result["number"] == 123

    def test_issue_create_with_title_and_label(self):
        """Extract title and label from issue create."""
        parsed = {
            "type": "gh",
            "operation": "issue_create",
            "args": {"--title": "Bug report", "--label": "bug"},
        }
        stdout = "https://github.com/owner/repo/issues/456"
        result = extract_result_from_output(parsed, stdout)
        assert result["title"] == "Bug report"
        assert result["label"] == "bug"
        assert result["number"] == 456

    def test_pr_create_with_head_branch(self):
        """Extract head branch from pr create."""
        parsed = {
            "type": "gh",
            "operation": "pr_create",
            "args": {"--title": "Feature", "--head": "feat/new"},
        }
        stdout = "Created #789"
        result = extract_result_from_output(parsed, stdout)
        assert result["title"] == "Feature"
        assert result["head_branch"] == "feat/new"
        assert result["number"] == 789


class TestGitPushParsing:
    """Tests for git push command parsing including force flag variants."""

    def test_parse_git_push_short_force_flag(self):
        """git push -f should be parsed with force=True."""
        result = parse_git_command("git push -f origin main")
        assert result["args"]["force"] is True

    def test_parse_git_push_force_with_lease_equals(self):
        """git push --force-with-lease=origin/main should be parsed."""
        result = parse_git_command("git push --force-with-lease=origin/main origin main")
        assert result["args"]["force_with_lease"] is True

    def test_parse_git_push_combined_flags_uf(self):
        """git push -uf should parse both upstream and force (Codex P2 fix)."""
        result = parse_git_command("git push -uf origin main")
        assert result["args"]["set_upstream"] is True
        assert result["args"]["force"] is True

    def test_parse_git_push_combined_flags_fu(self):
        """git push -fu should parse both force and upstream."""
        result = parse_git_command("git push -fu origin main")
        assert result["args"]["set_upstream"] is True
        assert result["args"]["force"] is True


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_malformed_quotes(self):
        """Handle malformed quotes gracefully (should not raise)."""
        # shlex.split fails on unclosed quotes, fallback to split()
        # This test verifies the function doesn't raise an exception
        result = parse_command("gh pr create --title 'unclosed")
        # May return a result or None depending on fallback behavior
        assert isinstance(result, dict) or result is None

    def test_very_long_command(self):
        """Handle very long commands."""
        long_msg = "x" * 1000
        result = parse_git_command(f'git commit -m "{long_msg}"')
        # Message should be truncated
        assert len(result["message"]) <= 100

    def test_unicode_in_command(self):
        """Handle unicode in commands."""
        result = parse_gh_command("gh issue create --title '日本語タイトル'")
        assert result["args"]["--title"] == "日本語タイトル"

    def test_command_with_env_vars(self):
        """Handle commands with environment variables."""
        result = parse_command("CI=true npm run test")
        assert result is not None
        # npm run test becomes run_test operation
        assert result["operation"] == "run_test"

    def test_command_with_semicolon(self):
        """Parse command with semicolon (shlex treats it as part of token)."""
        result = parse_command("git push; echo done")
        # shlex.split keeps semicolon attached to 'push', resulting in 'push;'
        # Verify the parser handles this gracefully and identifies git push
        assert result is not None
        assert result["type"] == "git"
        # Operation includes 'push' (may be 'push' or 'push;')
        assert "push" in result["operation"]

    def test_command_with_and(self):
        """Parse only first command before &&."""
        result = parse_command("git add . && git commit -m 'msg'")
        # Should parse first command only (git add .)
        assert result is not None
        assert result["type"] == "git"
        assert result["operation"] == "add"


class TestAbsolutePathCommands:
    """Tests for absolute path command support (Issue #1258).

    Commands may be called with absolute paths like /usr/bin/git or
    /opt/homebrew/bin/gh. These should be parsed the same as commands
    called without paths.
    """

    def test_gh_absolute_path(self):
        """gh with absolute path is parsed correctly."""
        result = parse_command("/usr/bin/gh pr list")
        assert result is not None
        assert result["type"] == "gh"
        assert result["operation"] == "pr_list"

    def test_gh_homebrew_path(self):
        """gh with Homebrew path is parsed correctly."""
        result = parse_command("/opt/homebrew/bin/gh issue view 123")
        assert result is not None
        assert result["type"] == "gh"
        assert result["issue_number"] == 123

    def test_git_absolute_path(self):
        """git with absolute path is parsed correctly."""
        result = parse_command("/usr/bin/git push origin main")
        assert result is not None
        assert result["type"] == "git"
        assert result["operation"] == "push"
        assert result["remote"] == "origin"
        assert result["branch"] == "main"

    def test_git_homebrew_path(self):
        """git with Homebrew path is parsed correctly."""
        result = parse_command("/opt/homebrew/bin/git commit -m 'test'")
        assert result is not None
        assert result["type"] == "git"
        assert result["operation"] == "commit"
        assert result["message"] == "test"

    def test_npm_absolute_path(self):
        """npm with absolute path is parsed correctly."""
        result = parse_command("/usr/local/bin/npm run build")
        assert result is not None
        assert result["type"] == "npm"
        assert result["package_manager"] == "npm"
        assert result["script"] == "build"

    def test_pnpm_absolute_path(self):
        """pnpm with absolute path is parsed correctly."""
        result = parse_command("/usr/local/bin/pnpm install")
        assert result is not None
        assert result["type"] == "npm"
        assert result["package_manager"] == "pnpm"
        assert result["operation"] == "install"

    def test_is_target_command_with_absolute_path(self):
        """is_target_command works with absolute paths."""
        assert is_target_command("/usr/bin/gh pr list") is True
        assert is_target_command("/usr/bin/git push") is True
        assert is_target_command("/usr/local/bin/npm run test") is True
        assert is_target_command("/opt/homebrew/bin/pnpm install") is True

    def test_absolute_path_non_target_still_excluded(self):
        """Non-target commands with absolute path are still excluded."""
        assert is_target_command("/usr/bin/gh help") is False
        assert is_target_command("/usr/bin/git status") is False
        assert is_target_command("/usr/local/bin/npm --version") is False

    def test_absolute_path_with_global_flags(self):
        """Absolute path commands with global flags work correctly."""
        result = parse_command("/usr/bin/gh --repo owner/repo pr view 123")
        assert result is not None
        assert result["type"] == "gh"
        assert result["pr_number"] == 123

        result = parse_command("/usr/bin/git -C /path/to/repo push origin main")
        assert result is not None
        assert result["type"] == "git"
        assert result["operation"] == "push"

    def test_argument_path_skipped_for_gh(self):
        """Argument paths like `test -x /usr/bin/gh` should not match (Issue #1258)."""
        # The first /usr/bin/gh is an argument to test, not a command
        # The actual gh command is after the &&
        result = parse_command("test -x /usr/bin/gh && gh pr list")
        assert result is not None
        assert result["type"] == "gh"
        assert result["operation"] == "pr_list"

    def test_argument_path_skipped_for_git(self):
        """Argument paths for git should not match."""
        result = parse_command("test -x /usr/bin/git && git push origin main")
        assert result is not None
        assert result["type"] == "git"
        assert result["operation"] == "push"

    def test_argument_path_skipped_for_npm(self):
        """Argument paths for npm should not match."""
        result = parse_command("test -x /usr/local/bin/npm && npm run build")
        assert result is not None
        assert result["type"] == "npm"
        assert result["operation"] == "run_build"

    def test_is_target_command_skips_argument_paths(self):
        """is_target_command should correctly identify commands after argument paths."""
        assert is_target_command("test -x /usr/bin/gh && gh pr list") is True
        assert is_target_command("test -x /usr/bin/git && git push") is True
        assert is_target_command("test -x /usr/local/bin/npm && npm run test") is True

    def test_argument_path_not_misidentified_as_command(self):
        """Commands with /gh, /git, /npm as arguments should not match (Issue #1258).

        This prevents false positives like 'echo /gh test' being parsed as a gh command.
        """
        # These should NOT be parsed as target commands
        assert parse_command("echo /gh test") is None
        assert parse_command("echo /usr/bin/gh pr list") is None
        assert parse_command("printenv /usr/bin/gh") is None
        assert parse_command("cat /usr/bin/git") is None
        assert parse_command("echo /usr/local/bin/npm install") is None

        # is_target_command should also return False for these
        assert is_target_command("echo /gh test") is False
        assert is_target_command("echo /usr/bin/gh pr list") is False
        assert is_target_command("printenv /usr/bin/git push") is False

    def test_env_var_before_command(self):
        """Commands with environment variable assignments before them should work."""
        result = parse_command("GH_TOKEN=abc /usr/bin/gh pr list")
        assert result is not None
        assert result["type"] == "gh"
        assert result["operation"] == "pr_list"

        result = parse_command("CI=true /usr/bin/git push origin main")
        assert result is not None
        assert result["type"] == "git"
        assert result["operation"] == "push"

        result = parse_command("NODE_ENV=production /usr/local/bin/npm run build")
        assert result is not None
        assert result["type"] == "npm"
        assert result["operation"] == "run_build"

    def test_separator_without_spaces(self):
        """Commands chained without spaces around separators should work."""
        # semicolon without spaces
        result = parse_command("echo foo;gh pr list")
        assert result is not None
        assert result["type"] == "gh"
        assert result["operation"] == "pr_list"

        result = parse_command("echo foo;git push origin main")
        assert result is not None
        assert result["type"] == "git"
        assert result["operation"] == "push"

        result = parse_command("echo foo;npm run build")
        assert result is not None
        assert result["type"] == "npm"

        # pipe without spaces
        result = parse_command("cat file|gh pr view 123")
        assert result is not None
        assert result["type"] == "gh"

        # is_target_command should also work
        assert is_target_command("echo foo;gh pr list") is True
        assert is_target_command("echo foo;git push") is True
        assert is_target_command("echo foo;npm run test") is True

    def test_command_wrappers(self):
        """Commands with wrappers like sudo, time, nohup should work."""
        # sudo wrapper
        result = parse_command("sudo gh pr list")
        assert result is not None
        assert result["type"] == "gh"
        assert result["operation"] == "pr_list"

        result = parse_command("sudo git push origin main")
        assert result is not None
        assert result["type"] == "git"
        assert result["operation"] == "push"

        result = parse_command("sudo npm run build")
        assert result is not None
        assert result["type"] == "npm"

        # time wrapper
        result = parse_command("time gh pr view 123")
        assert result is not None
        assert result["type"] == "gh"

        # Multiple wrappers
        result = parse_command("sudo time gh pr list")
        assert result is not None
        assert result["type"] == "gh"

        # is_target_command should also work
        assert is_target_command("sudo gh pr list") is True
        assert is_target_command("sudo git push") is True
        assert is_target_command("time npm run test") is True
        assert is_target_command("nohup git push &") is True

        # Wrapper + absolute path combination
        result = parse_command("sudo /usr/bin/gh pr list")
        assert result is not None
        assert result["type"] == "gh"
        assert result["operation"] == "pr_list"

        result = parse_command("time /opt/homebrew/bin/git push origin main")
        assert result is not None
        assert result["type"] == "git"
        assert result["operation"] == "push"

        result = parse_command("nohup /usr/local/bin/npm run build &")
        assert result is not None
        assert result["type"] == "npm"
        assert result["operation"] == "run_build"

        # is_target_command with wrapper + absolute path
        assert is_target_command("sudo /usr/bin/gh pr list") is True
        assert is_target_command("time /opt/homebrew/bin/git push") is True

    def test_fallback_to_other_parsers(self):
        """When gh regex matches but parse fails, try other parsers."""
        # The /usr/bin/gh matches gh regex, but it's just an argument
        # The actual command is git push
        result = parse_command("echo /usr/bin/gh && git push origin main")
        assert result is not None
        assert result["type"] == "git"
        assert result["operation"] == "push"

        # Similar with npm
        result = parse_command("test -f /usr/bin/gh && npm run build")
        assert result is not None
        assert result["type"] == "npm"

        # is_target_command should also work
        assert is_target_command("echo /usr/bin/gh && git push") is True
        assert is_target_command("cat /usr/bin/git && npm install") is True

    def test_escaped_separators_not_split(self):
        """Escaped shell separators should not be treated as command separators."""
        # Escaped semicolon - should NOT parse as git command
        # The \; is a literal character, not a separator
        result = parse_command(r"echo foo\;git push")
        assert result is None

        # Escaped pipe - should NOT parse as git command
        result = parse_command(r"echo foo\|git push")
        assert result is None

        # Escaped && - should NOT parse as git command
        result = parse_command(r"echo foo\&\&git push")
        assert result is None

        # Real semicolon after text still works
        result = parse_command("echo foo; git push origin main")
        assert result is not None
        assert result["type"] == "git"
        assert result["operation"] == "push"

        # is_target_command should also respect escaping
        # (git push is a target operation, git status is not)
        assert is_target_command(r"echo foo\;git push") is False
        assert is_target_command("echo foo; git push") is True

    def test_double_backslash_before_separator(self):
        """Double backslash before separator: \\; is literal \\ + real separator."""
        # In shell: \\; means escaped backslash followed by command separator
        # So "echo foo\\;git push" should parse as two commands
        result = parse_command(r"echo foo\\;git push origin main")
        assert result is not None
        assert result["type"] == "git"
        assert result["operation"] == "push"

        # Triple backslash: \\\; = escaped backslash + escaped semicolon
        result = parse_command(r"echo foo\\\;git push")
        assert result is None  # semicolon is escaped

        # Quadruple backslash: \\\\; = two escaped backslashes + real separator
        result = parse_command(r"echo foo\\\\;git push origin main")
        assert result is not None
        assert result["type"] == "git"

        # is_target_command should also work
        assert is_target_command(r"echo foo\\;git push") is True
        assert is_target_command(r"echo foo\\\;git push") is False


class TestExtractWorktreeAddPath:
    """Tests for extract_worktree_add_path function.

    Issue #1543: Unified extraction of worktree path from git worktree add commands.
    """

    def test_simple_add(self):
        """Simple git worktree add command."""
        assert extract_worktree_add_path("git worktree add .worktrees/foo main") == ".worktrees/foo"

    def test_with_branch_option(self):
        """git worktree add with -b option."""
        assert (
            extract_worktree_add_path("git worktree add -b new-branch .worktrees/foo main")
            == ".worktrees/foo"
        )
        assert (
            extract_worktree_add_path("git worktree add -B new-branch .worktrees/bar")
            == ".worktrees/bar"
        )

    def test_with_orphan_option(self):
        """git worktree add with --orphan option."""
        assert (
            extract_worktree_add_path("git worktree add --orphan new-branch .worktrees/baz")
            == ".worktrees/baz"
        )

    def test_with_detach_option(self):
        """git worktree add with --detach option."""
        assert (
            extract_worktree_add_path("git worktree add --detach .worktrees/foo HEAD")
            == ".worktrees/foo"
        )
        assert extract_worktree_add_path("git worktree add -d .worktrees/bar") == ".worktrees/bar"

    def test_with_force_option(self):
        """git worktree add with -f/--force option."""
        assert extract_worktree_add_path("git worktree add -f .worktrees/foo") == ".worktrees/foo"
        assert (
            extract_worktree_add_path("git worktree add --force .worktrees/bar main")
            == ".worktrees/bar"
        )

    def test_with_lock_option(self):
        """git worktree add with --lock option."""
        assert (
            extract_worktree_add_path("git worktree add --lock .worktrees/foo") == ".worktrees/foo"
        )

    def test_with_quiet_option(self):
        """git worktree add with -q/--quiet option."""
        assert extract_worktree_add_path("git worktree add -q .worktrees/foo") == ".worktrees/foo"
        assert (
            extract_worktree_add_path("git worktree add --quiet .worktrees/bar main")
            == ".worktrees/bar"
        )

    def test_with_reason_option(self):
        """git worktree add with --reason option."""
        assert (
            extract_worktree_add_path('git worktree add --reason "testing" .worktrees/foo')
            == ".worktrees/foo"
        )

    def test_with_env_prefix(self):
        """git worktree add with environment variable prefix."""
        assert (
            extract_worktree_add_path("SKIP_PLAN=1 git worktree add .worktrees/foo main")
            == ".worktrees/foo"
        )

    def test_absolute_path(self):
        """git worktree add with absolute path."""
        assert extract_worktree_add_path("git worktree add /tmp/foo main") == "/tmp/foo"

    def test_returns_none_for_list(self):
        """git worktree list should return None."""
        assert extract_worktree_add_path("git worktree list") is None

    def test_returns_none_for_remove(self):
        """git worktree remove should return None."""
        assert extract_worktree_add_path("git worktree remove .worktrees/foo") is None

    def test_returns_none_for_prune(self):
        """git worktree prune should return None."""
        assert extract_worktree_add_path("git worktree prune") is None

    def test_returns_none_for_non_worktree(self):
        """Non-worktree git commands should return None."""
        assert extract_worktree_add_path("git status") is None
        assert extract_worktree_add_path("git push origin main") is None

    def test_quoted_path(self):
        """git worktree add with quoted path containing spaces."""
        assert (
            extract_worktree_add_path('git worktree add ".worktrees/my folder" main')
            == ".worktrees/my folder"
        )

    def test_heredoc_false_positive(self):
        """Should not match worktree add inside heredoc/echo."""
        assert extract_worktree_add_path('echo "git worktree add .worktrees/foo"') is None

    def test_chained_command_with_echo_false_positive(self):
        """git status && echo worktree add foo should return None.

        Issue #1666: 'worktree add' appears in echo argument, not as git subcommand.
        """
        assert extract_worktree_add_path("git status && echo worktree add foo") is None

    def test_echo_git_worktree_add_unquoted(self):
        """echo git worktree add test should return None.

        Issue #1655: 'git worktree add' appears as argument to echo (unquoted),
        not as an actual git command.
        """
        assert extract_worktree_add_path("echo git worktree add test") is None
        assert extract_worktree_add_path("printf git worktree add test") is None
        assert extract_worktree_add_path("cat git worktree add test") is None
        # But chained command with git first should still work
        assert (
            extract_worktree_add_path("echo hello && git worktree add .worktrees/foo")
            == ".worktrees/foo"
        )

    def test_git_worktree_add_with_leading_whitespace(self):
        """git worktree add with leading whitespace should still be detected.

        Issue #1655: Ensure leading whitespace doesn't prevent detection.
        """
        assert extract_worktree_add_path("  git worktree add .worktrees/foo") == ".worktrees/foo"
        assert (
            extract_worktree_add_path("    git worktree add .worktrees/bar main")
            == ".worktrees/bar"
        )

    def test_git_worktree_add_with_wrappers(self):
        """git worktree add with common wrappers should be detected.

        Issue #1655: sudo, env, time, etc. are valid prefixes for git commands.
        """
        assert extract_worktree_add_path("sudo git worktree add .worktrees/foo") == ".worktrees/foo"
        assert extract_worktree_add_path("env git worktree add .worktrees/bar") == ".worktrees/bar"
        assert extract_worktree_add_path("time git worktree add .worktrees/baz") == ".worktrees/baz"
        assert (
            extract_worktree_add_path("command git worktree add .worktrees/qux") == ".worktrees/qux"
        )

    def test_git_worktree_add_with_chained_wrappers(self):
        """git worktree add with multiple chained wrappers should be detected.

        Issue #1655: sudo time git worktree add foo should work.
        """
        assert (
            extract_worktree_add_path("sudo time git worktree add .worktrees/foo")
            == ".worktrees/foo"
        )
        assert (
            extract_worktree_add_path("sudo nice git worktree add .worktrees/bar")
            == ".worktrees/bar"
        )

    def test_git_worktree_add_with_multiple_env_vars(self):
        """git worktree add with multiple environment variables should be detected.

        Issue #1655: VAR1=val1 VAR2=val2 git worktree add foo should work.
        """
        assert (
            extract_worktree_add_path("VAR1=val1 VAR2=val2 git worktree add .worktrees/foo")
            == ".worktrees/foo"
        )
        assert (
            extract_worktree_add_path("GIT_DIR=/path PATH=/bin git worktree add .worktrees/bar")
            == ".worktrees/bar"
        )

    def test_echo_with_env_var_false_positive(self):
        """echo foo VAR=val git worktree add bar should NOT be detected.

        Issue #1655: Prevent false positive when git appears after argument.
        """
        assert extract_worktree_add_path("echo foo VAR=val git worktree add bar") is None

    def test_git_worktree_add_with_absolute_path(self):
        """git worktree add with absolute path to git binary should be detected.

        Issue #1707: /usr/bin/git worktree add foo should work.
        """
        assert (
            extract_worktree_add_path("/usr/bin/git worktree add .worktrees/foo")
            == ".worktrees/foo"
        )
        assert extract_worktree_add_path("/opt/homebrew/bin/git worktree add bar") == "bar"
        # With wrapper
        assert (
            extract_worktree_add_path("sudo /usr/bin/git worktree add .worktrees/baz")
            == ".worktrees/baz"
        )
        # Wrapper command with absolute path to the binary
        assert (
            extract_worktree_add_path("/usr/bin/env git worktree add .worktrees/qux")
            == ".worktrees/qux"
        )
        assert (
            extract_worktree_add_path("/usr/bin/sudo git worktree add .worktrees/quux")
            == ".worktrees/quux"
        )

    def test_git_with_c_option(self):
        """git -C /path worktree add foo should extract foo.

        Issue #1666: git global option -C specifies working directory.
        """
        assert extract_worktree_add_path("git -C /path worktree add foo") == "foo"
        assert (
            extract_worktree_add_path("git -C /some/repo worktree add .worktrees/bar main")
            == ".worktrees/bar"
        )

    def test_git_with_git_dir_option(self):
        """git --git-dir=/path/.git worktree add bar should extract bar.

        Issue #1666: git global option --git-dir specifies the repository.
        """
        assert extract_worktree_add_path("git --git-dir=/path/.git worktree add bar") == "bar"
        assert (
            extract_worktree_add_path("git --git-dir=/repo/.git worktree add .worktrees/baz main")
            == ".worktrees/baz"
        )

    def test_cd_then_git_worktree_add(self):
        """cd /path && git worktree add baz should extract baz.

        Issue #1666: cd command followed by git worktree add in a chain.
        """
        assert extract_worktree_add_path("cd /path && git worktree add baz") == "baz"
        assert (
            extract_worktree_add_path("cd /some/repo && git worktree add .worktrees/foo main")
            == ".worktrees/foo"
        )

    def test_git_worktree_add_with_pipe(self):
        """git worktree add foo | grep bar should extract foo.

        Issue #1666: pipe after git worktree add should not affect path extraction.
        """
        assert extract_worktree_add_path("git worktree add foo | grep bar") == "foo"
        assert (
            extract_worktree_add_path("git worktree add .worktrees/baz main | tee log.txt")
            == ".worktrees/baz"
        )

    def test_path_with_semicolon(self):
        """Paths containing semicolons should not be treated as shell separators.

        Issue #1669: git -C "/path;backup" worktree add foo should extract foo.
        The semicolon in the path argument should not reset the parser state.
        """
        assert extract_worktree_add_path('git -C "/path;backup" worktree add foo') == "foo"
        assert (
            extract_worktree_add_path('git -C "/some;path" worktree add .worktrees/bar main')
            == ".worktrees/bar"
        )
        # Semicolon at end of token should still act as separator
        assert extract_worktree_add_path("echo test; git worktree add foo") == "foo"
