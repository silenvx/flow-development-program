#!/usr/bin/env python3
"""locked-worktree-guardのガードルールと検証ロジック。

Why:
    Worktree関連の危険な操作（自己ブランチ削除、ロック中worktree削除、
    孤立worktree削除等）を検出し、適切なブロックまたは警告を行う。

What:
    - 自己ブランチ削除チェック（gh pr merge --delete-branch）
    - worktree削除の安全性チェック（CWD内、ロック中）
    - rm コマンドによるworktree削除チェック
    - 孤立worktreeの削除チェック
    - PRマージ時の安全な自動実行

Remarks:
    - locked-worktree-guard.pyから呼び出されるモジュール
    - マージ時は--delete-branchを除去して安全に自動実行
    - Issue #855以降、ブロックではなく安全なマージを自動実行

Changelog:
    - silenvx/dekita#649: 自己ブランチ削除チェック追加
    - silenvx/dekita#855: 安全な自動マージ実行機能追加
    - silenvx/dekita#942: マージ後のPR状態検証追加
    - silenvx/dekita#1027: ghコマンドエラーメッセージ改善
    - silenvx/dekita#1676: マージ後worktree自動クリーンアップ追加
    - silenvx/dekita#2340: [IMMEDIATE]タグによる振り返り強制
"""

import os
import subprocess
from pathlib import Path

from command_parser import (
    extract_first_merge_command,
    extract_unlock_targets_from_command,
    extract_worktree_path_from_command,
    find_git_worktree_remove_position,
    get_merge_positional_arg,
    has_delete_branch_flag,
)
from lib.constants import TIMEOUT_LONG, TIMEOUT_MEDIUM
from lib.cwd import get_effective_cwd
from lib.execution import log_hook_execution
from lib.github import parse_gh_pr_command
from lib.results import make_block_result
from worktree_manager import (
    get_all_locked_worktree_paths,
    get_branch_for_pr,
    get_current_branch_name,
    get_current_worktree,
    get_locked_worktrees,
    get_main_repo_dir,
    get_rm_target_orphan_worktrees,
    get_rm_target_worktrees,
    is_cwd_inside_worktree,
)


def check_pr_merged(pr_number: str | None, branch: str | None = None) -> bool:
    """Check if a PR is actually merged.

    Issue #942: After executing merge command, verify the PR state to avoid
    false success reports when other hooks (like merge-check) block the merge.

    Args:
        pr_number: PR number to check. If None, uses branch to find PR.
        branch: Branch name to find PR if pr_number is not provided.

    Returns:
        True if PR is merged, False otherwise.
    """
    try:
        # Determine what to query
        selector = pr_number if pr_number else branch
        if not selector:
            return False

        result = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                selector,
                "--json",
                "state",
                "--jq",
                ".state",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
            check=False,
        )

        if result.returncode == 0:
            state = result.stdout.strip().upper()
            return state == "MERGED"

    except Exception:
        # On error, assume not merged to avoid false positive reports
        pass

    return False


def improve_gh_error_message(error: str, command: str) -> str:
    """Improve gh command error messages for better user experience.

    Issue #1027: Raw gh errors like "accepts at most 1 arg(s), received 2"
    are not user-friendly. This function translates known error patterns
    into clearer messages.

    Args:
        error: The raw error message from gh command.
        command: The original command that was executed.

    Returns:
        Improved error message with context.
    """
    error_lower = error.lower()

    # Pattern: argument count error (e.g., "accepts at most 1 arg(s), received 2")
    # Note: Original error not included to avoid redundancy (Copilot review feedback)
    if "accepts at most" in error_lower and "arg" in error_lower:
        return (
            "コマンド引数エラー: gh pr merge は1つのPR指定のみ受け付けます\n"
            f"実行コマンド: {command}"
        )

    # Pattern: PR/branch not found or could not be resolved
    # Combined as per Copilot review feedback - both require similar user actions
    if "no pull requests found" in error_lower or "could not resolve" in error_lower:
        return (
            "PR/ブランチが見つかりません: "
            "指定されたPR番号やブランチ名が存在しない、リモートにプッシュされていない、"
            "または既にクローズ済みの可能性があります。\n"
            "対処法: PR番号・ブランチ名を再確認し、必要に応じて `git push` や "
            "PR の再作成を行ってください。"
        )

    # Pattern: not mergeable
    if "not mergeable" in error_lower or "cannot be merged" in error_lower:
        return "マージ不可: PRにコンフリクトがあるか、マージ条件を満たしていません"

    # Pattern: authentication/permission error (includes 403 Forbidden)
    if "unauthorized" in error_lower or "permission" in error_lower or "forbidden" in error_lower:
        return (
            "認証/権限エラー: GitHub への認証または権限に問題があります\n"
            "対処法: ターミナルで `gh auth status` を実行して認証状態を確認してください"
        )

    # Default: return original error with command context
    return f"{error}\n実行コマンド: {command}"


def execute_safe_merge(command: str, hook_cwd: str | None = None) -> tuple[bool, str]:
    """Execute a merge command safely (without --delete-branch).

    IMPORTANT: This only executes the first gh pr merge command, NOT any
    chained commands that may follow (like && echo done). This is critical
    for security and preventing unintended side effects.

    Args:
        command: The original gh pr merge command.
        hook_cwd: Current working directory.

    Returns:
        Tuple of (success, output_message).
    """
    # Extract only the first merge command - do NOT run chained commands
    safe_command = extract_first_merge_command(command)

    try:
        result = subprocess.run(
            ["bash", "-c", safe_command],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LONG,  # Use standard long timeout for merge operations
            cwd=hook_cwd,
        )

        if result.returncode == 0:
            return True, result.stdout.strip() or "Merge completed successfully."
        else:
            raw_error = result.stderr.strip() or result.stdout.strip() or "Unknown error"
            # Issue #1027: Improve error messages for better UX
            improved_error = improve_gh_error_message(raw_error, safe_command)
            return False, improved_error

    except subprocess.TimeoutExpired:
        return False, f"Merge command timed out ({TIMEOUT_LONG} seconds)."
    except OSError as e:
        return False, f"Failed to execute merge: {e}"


def try_auto_cleanup_worktree(
    main_repo: Path, current_worktree: Path, pr_branch: str
) -> tuple[bool, str]:
    """Try to auto-cleanup the worktree after successful merge.

    Issue #1676: Automatically remove worktree after merge to prevent accumulation.

    Args:
        main_repo: Path to the main repository.
        current_worktree: Path to the current worktree.
        pr_branch: The branch name of the merged PR.

    Returns:
        Tuple of (success, message).
    """
    # Check if the worktree is locked
    locked_worktrees = get_locked_worktrees()
    try:
        worktree_resolved = current_worktree.resolve()
    except OSError:
        # Path resolution failed, skip auto-cleanup for safety
        return (False, "worktreeパス解決エラー")

    for locked_path, _ in locked_worktrees:
        try:
            if locked_path.resolve() == worktree_resolved:
                return (
                    False,
                    "worktreeがロック中（別セッションが作業中の可能性）",
                )
        except OSError:
            continue

    # Try to remove the worktree from main repo
    # Use -- separator to prevent argument injection (security fix)
    try:
        result = subprocess.run(
            ["git", "worktree", "remove", "--", str(current_worktree)],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
            cwd=str(main_repo),
        )
    except subprocess.TimeoutExpired:
        return (False, "worktree削除タイムアウト")
    except OSError as e:
        return (False, f"worktree削除エラー: {e}")

    if result.returncode != 0:
        error = result.stderr.strip() or result.stdout.strip() or "Unknown error"
        return (False, f"worktree削除失敗: {error}")

    # worktree deletion succeeded
    # Note: Remote branch is automatically deleted by GitHub's "delete_branch_on_merge" setting
    return (True, "worktree削除 成功")


def check_self_branch_deletion(command: str, hook_cwd: str | None = None) -> dict | None:
    """Check if gh pr merge --delete-branch would delete the current worktree's branch.

    This fixes Issue #649: When merging a PR with --delete-branch from within the
    worktree that's using that branch, the worktree becomes invalid and breaks
    the shell session.

    Issue #855: Now automatically executes a safe merge (without --delete-branch)
    instead of just blocking. The merge is performed, and cleanup instructions
    are returned in the block message.

    Blocks:
    - gh pr merge 123 --delete-branch (when PR's branch is current worktree's branch)
    - gh pr merge 123 -d (same)

    Args:
        command: The gh pr merge command.
        hook_cwd: Current working directory from hook input.

    Returns:
        Block result dict if should block, None if should approve.
    """
    subcommand, pr_number = parse_gh_pr_command(command)

    # Only check gh pr merge commands
    if subcommand != "merge":
        return None

    # Check if --delete-branch flag is present
    if not has_delete_branch_flag(command):
        return None

    # Get current worktree and branch
    # Issue #1025: Use effective cwd (considering cd in command) instead of hook_cwd directly
    # This allows "cd /main/repo && gh pr merge" to work correctly
    # Also handles cases where hook_cwd is None by falling back to environment variables
    # Issue #1035: Pass hook_cwd as base_cwd so relative cd paths are resolved correctly
    effective_cwd = str(get_effective_cwd(command, hook_cwd)) if command else hook_cwd

    current_worktree = get_current_worktree(effective_cwd)
    if not current_worktree:
        return None

    # Check if we're in a worktree (not main repo)
    main_repo = get_main_repo_dir()
    if not main_repo:
        return None

    try:
        if current_worktree.resolve() == main_repo.resolve():
            # We're in the main repo, not a worktree - safe to proceed
            return None
    except OSError:
        # Continue check on error to prevent accidental deletion
        pass

    # Get current branch
    current_branch = get_current_branch_name(effective_cwd)
    if not current_branch:
        return None

    # Get PR's branch
    if pr_number:
        pr_branch = get_branch_for_pr(pr_number)
    else:
        # No PR number extracted - need to determine if this targets current branch
        # Cases:
        # 1. gh pr merge --delete-branch (no selector) -> targets current branch -> block
        # 2. gh pr merge feature-branch --delete-branch -> check if feature-branch == current branch
        # 3. gh pr merge https://... --delete-branch -> can't determine -> fail open
        #
        # Check if there's a positional argument (branch name/URL) after 'merge'
        positional_arg = get_merge_positional_arg(command)
        if positional_arg:
            # Check if this looks like a branch name (not a URL or other selector)
            if positional_arg.startswith("http"):
                # URL selector - can't determine which branch, fail open
                return None
            # Compare with current branch - if same, it's self-branch deletion
            if positional_arg == current_branch:
                pr_branch = current_branch
            else:
                # Different branch specified - safe to proceed
                return None
        else:
            # No selector provided - gh pr merge uses current branch
            pr_branch = current_branch

    if not pr_branch:
        return None

    # Check if PR's branch matches current worktree's branch
    if pr_branch == current_branch:
        # Issue #948: Run merge-check --dry-run before auto-merging to respect safety checks
        # Get numeric PR number (required by merge-check.py --dry-run)
        effective_pr_number = pr_number
        if not effective_pr_number:
            # Try to get PR number from current branch using gh pr view
            try:
                pr_view_result = subprocess.run(
                    ["gh", "pr", "view", "--json", "number", "--jq", ".number"],
                    capture_output=True,
                    text=True,
                    timeout=TIMEOUT_MEDIUM,
                    cwd=effective_cwd,
                )
                if pr_view_result.returncode == 0 and pr_view_result.stdout.strip():
                    effective_pr_number = pr_view_result.stdout.strip()
            except subprocess.TimeoutExpired:
                # Issue #952: Log timeout for debugging
                log_hook_execution(
                    "locked-worktree-guard",
                    "warn",
                    "gh pr view timed out while getting PR number, skipping merge-check dry-run",
                )
            except OSError as e:
                # Issue #952: Log error for debugging
                log_hook_execution(
                    "locked-worktree-guard",
                    "warn",
                    f"gh pr view failed while getting PR number: {e}, skipping merge-check dry-run",
                )

        if effective_pr_number:
            project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
            # Skip merge-check if CLAUDE_PROJECT_DIR is not set or empty
            if not project_dir:
                log_hook_execution(
                    "locked-worktree-guard",
                    "warn",
                    "CLAUDE_PROJECT_DIR not set, skipping merge-check dry-run",
                )
            else:
                merge_check_script = Path(project_dir) / ".claude" / "hooks" / "merge_check.py"

                if merge_check_script.exists():
                    try:
                        dry_run_result = subprocess.run(
                            [
                                "python3",
                                str(merge_check_script),
                                "--dry-run",
                                str(effective_pr_number),
                            ],
                            capture_output=True,
                            text=True,
                            timeout=TIMEOUT_LONG,
                            cwd=effective_cwd,
                        )

                        if dry_run_result.returncode != 0:
                            # merge-check found issues - don't auto-merge
                            # Include both stdout and stderr for debugging
                            # Add newline separator if both stdout and stderr have content
                            stdout = dry_run_result.stdout.strip()
                            stderr = dry_run_result.stderr.strip()
                            if stdout and stderr:
                                error_output = f"{stdout}\n{stderr}"
                            elif stdout or stderr:
                                error_output = stdout or stderr
                            else:
                                error_output = (
                                    f"(merge-check exited with code {dry_run_result.returncode})"
                                )
                            reason = (
                                f"⚠️ 自動マージをスキップしました: PR #{effective_pr_number}\n\n"
                                f"worktree内からのマージを検出しましたが、マージ前の安全チェックで問題が見つかりました。\n\n"
                                f"{error_output}\n"
                                f"問題を解決してから再度マージを実行してください。"
                            )
                            return make_block_result("locked-worktree-guard", reason)
                        else:
                            # Issue #952: Log success for debugging
                            log_hook_execution(
                                "locked-worktree-guard",
                                "info",
                                f"merge-check --dry-run passed for PR #{effective_pr_number}",
                            )
                    except subprocess.TimeoutExpired:
                        # Timeout - proceed with auto-merge (fail open)
                        log_hook_execution(
                            "locked-worktree-guard",
                            "warn",
                            f"merge-check --dry-run timed out for PR #{effective_pr_number}",
                        )
                    except OSError as e:
                        # Script execution failed - proceed with auto-merge (fail open)
                        log_hook_execution(
                            "locked-worktree-guard",
                            "warn",
                            f"merge-check --dry-run failed: {e}",
                        )

        # Issue #855: Automatically execute safe merge instead of just blocking
        success, output = execute_safe_merge(command, effective_cwd)

        if success:
            # Issue #942: Verify the PR is actually merged
            # The subprocess may return 0 even if other hooks (like merge-check) blocked it
            actually_merged = check_pr_merged(pr_number, pr_branch)

            if actually_merged:
                # Issue #1676: Try auto-cleanup of worktree after successful merge
                # Note: Auto-cleanup is only safe when NOT running from inside
                # the worktree being deleted. Check if effective_cwd is inside
                # the worktree - if so, skip auto-cleanup to avoid session corruption.
                try:
                    effective_cwd_resolved = Path(effective_cwd).resolve()
                    current_worktree_resolved = current_worktree.resolve()
                    is_inside_worktree = (
                        effective_cwd_resolved == current_worktree_resolved
                        or current_worktree_resolved in effective_cwd_resolved.parents
                    )
                except OSError:
                    # On error, assume we're inside to be safe
                    is_inside_worktree = True

                if is_inside_worktree:
                    # Cannot auto-cleanup: session is inside the worktree
                    # Provide manual instructions as before
                    # Note: Remote branch is automatically deleted by GitHub's "delete_branch_on_merge" setting
                    # Issue #2340: [IMMEDIATE]タグの優先順位を明確化
                    reason = (
                        f"✅ マージ完了（自動実行）: PR #{pr_number or '(current branch)'}\n\n"
                        f"worktree内からのマージを検出し、--delete-branch なしで自動実行しました。\n"
                        f"ブランチ: {pr_branch}\n"
                        f"worktree: {current_worktree}\n\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        "⚠️ **最優先: 振り返りを先に実行してください**\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        "[IMMEDIATE: /reflect]\n"
                        "振り返り（五省）を行い、教訓をIssue化してください。\n\n"
                        "【振り返り完了後のステップ】\n"
                        f"1. メインリポジトリに移動: cd {main_repo}\n"
                        f"2. worktree削除: git worktree remove {current_worktree}\n\n"
                        "⚠️ 重要（Issue #1002）: 上記のステップは**別々のコマンド**として実行してください。\n"
                        "`cd ... && git worktree remove ...` のように && で連結すると、\n"
                        "セッションのcwdは変わらず、worktree削除後にセッションが破損します。\n\n"
                        "（リモートブランチはGitHub設定により自動削除されます）\n\n"
                        f"出力: {output}"
                    )
                else:
                    # Safe to auto-cleanup: session is outside the worktree
                    cleanup_success, cleanup_msg = try_auto_cleanup_worktree(
                        main_repo, current_worktree, pr_branch
                    )

                    if cleanup_success:
                        # Issue #2340: [IMMEDIATE]タグの優先順位を明確化
                        reason = (
                            f"✅ マージ完了 + クリーンアップ成功: PR #{pr_number or '(current branch)'}\n\n"
                            f"worktree内からのマージを検出し、--delete-branch なしで自動実行しました。\n"
                            f"ブランチ: {pr_branch}\n"
                            f"worktree: {current_worktree}\n\n"
                            f"🧹 自動クリーンアップ: {cleanup_msg}\n\n"
                            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            "⚠️ **最優先: 振り返りを先に実行してください**\n"
                            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                            "[IMMEDIATE: /reflect]\n"
                            "振り返り（五省）を行い、教訓をIssue化してください。\n\n"
                            f"出力: {output}"
                        )
                    else:
                        # Note: Remote branch is automatically deleted by GitHub's "delete_branch_on_merge" setting
                        # Issue #2340: [IMMEDIATE]タグの優先順位を明確化
                        reason = (
                            f"✅ マージ完了（自動実行）: PR #{pr_number or '(current branch)'}\n\n"
                            f"worktree内からのマージを検出し、--delete-branch なしで自動実行しました。\n"
                            f"ブランチ: {pr_branch}\n"
                            f"worktree: {current_worktree}\n\n"
                            f"⚠️ 自動クリーンアップ失敗: {cleanup_msg}\n\n"
                            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            "⚠️ **最優先: 振り返りを先に実行してください**\n"
                            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                            "[IMMEDIATE: /reflect]\n"
                            "振り返り（五省）を行い、教訓をIssue化してください。\n\n"
                            "【振り返り完了後のステップ】\n"
                            f"1. メインリポジトリに移動: cd {main_repo}\n"
                            f"2. worktree削除: git worktree remove {current_worktree}\n\n"
                            "（リモートブランチはGitHub設定により自動削除されます）\n\n"
                            f"出力: {output}"
                        )
                return make_block_result("locked-worktree-guard", reason)
            else:
                # Merge command returned success but PR is not merged
                # This can happen when another hook (like merge-check) blocked the merge
                reason = (
                    f"⚠️ マージ未完了: PR #{pr_number or '(current branch)'}\n\n"
                    f"worktree内からのマージを検出しましたが、PRはまだマージされていません。\n"
                    f"他のフック（merge-check等）がブロックした可能性があります。\n\n"
                    f"ブランチ: {pr_branch}\n"
                    f"worktree: {current_worktree}\n\n"
                    "【対処法】\n"
                    f"1. 他のフックのエラーメッセージを確認\n"
                    f"2. 問題を解決してから再試行\n"
                    f"3. または手動でマージ:\n"
                    f"   cd {main_repo}\n"
                    f"   gh pr merge {pr_number or current_branch} --squash"
                )
                return make_block_result("locked-worktree-guard", reason)
        else:
            reason = (
                f"❌ マージ失敗: PR #{pr_number or '(current branch)'}\n\n"
                f"worktree内からのマージを検出しましたが、実行に失敗しました。\n"
                f"エラー: {output}\n\n"
                "【対処法】\n"
                f"1. エラー内容を確認\n"
                f"2. 問題を解決してから再試行\n"
                f"3. または手動でマージ:\n"
                f"   cd {main_repo}\n"
                f"   gh pr merge {pr_number or current_branch} --squash"
            )
            return make_block_result("locked-worktree-guard", reason)

    return None


def check_rm_orphan_worktree(command: str, hook_cwd: str | None = None) -> dict | None:
    """Check if rm command targets an orphan worktree directory.

    Blocks rm commands that would delete orphan worktree directories
    (directories in .worktrees/ that are not registered with git).

    This fixes Issue #795: Block rm -rf on orphan worktree directories.

    Args:
        command: The rm command.
        hook_cwd: Current working directory from hook input.

    Returns:
        Block result dict if should block, None if should approve.
    """
    target_orphans = get_rm_target_orphan_worktrees(command, hook_cwd)
    if not target_orphans:
        return None

    # Block deletion of ANY orphan worktree directory
    _rm_target, orphan_path = target_orphans[0]
    main_repo = get_main_repo_dir()
    main_repo_str = str(main_repo) if main_repo else "/path/to/main/repo"

    reason = (
        f"⚠️ 孤立worktreeディレクトリの削除をブロックしました。\n\n"
        f"対象: {orphan_path}\n\n"
        "このディレクトリは .worktrees/ 内に存在しますが、\n"
        "git worktree list に登録されていません（孤立状態）。\n\n"
        "別のセッションが作業中か、git worktree の状態が壊れている可能性があります。\n\n"
        "【対処法】以下を**1つずつ順番に**実行してください:\n\n"
        f"**Step 1**: 内容を確認\n"
        f"```\n"
        f"ls -la {orphan_path}\n"
        f"```\n\n"
        f"**Step 2**: git worktree として再登録（推奨）\n"
        f"```\n"
        f"cd {main_repo_str}\n"
        f"```\n\n"
        f"```\n"
        f"git worktree repair\n"
        f"```\n\n"
        f"**Step 3**: 不要な場合は git worktree prune で整理\n"
        f"```\n"
        f"cd {main_repo_str}\n"
        f"```\n\n"
        f"```\n"
        f"git worktree prune\n"
        f"```\n\n"
        f"**最終手段**: それでも削除が必要な場合（データ損失注意）\n"
        f"```\n"
        f"FORCE_RM_ORPHAN=1 rm -rf {orphan_path}\n"
        f"```\n\n"
        "⚠️ 注意: rm -rf ではなく git worktree repair/prune を優先してください。"
    )
    return make_block_result("locked-worktree-guard", reason)


def check_rm_worktree(command: str, hook_cwd: str | None = None) -> dict | None:
    """Check if rm command targeting worktree is safe to execute.

    Blocks rm commands that would delete a worktree while CWD is inside it,
    which would break the shell session.

    This fixes Issue #289: rm -rf deleting worktree breaks shell

    Note: This function checks ALL rm targets, not just the first one.
    A command like `rm -rf .worktrees/old .worktrees/current` will be blocked
    if CWD is inside either target worktree.

    Args:
        command: The rm command.
        hook_cwd: Current working directory from hook input.

    Returns:
        Block result dict if should block, None if should approve.
    """
    # Get ALL worktrees that would be deleted by this rm command
    target_worktrees = get_rm_target_worktrees(command, hook_cwd)
    if not target_worktrees:
        return None

    # Check if CWD is inside ANY of the target worktrees
    cwd = Path(hook_cwd) if hook_cwd else None

    for _rm_target, worktree_path in target_worktrees:
        if is_cwd_inside_worktree(worktree_path, cwd, command):
            main_repo = get_main_repo_dir()
            main_repo_str = str(main_repo) if main_repo else "/path/to/main/repo"

            reason = (
                f"⚠️ rm コマンドでworktreeを削除しようとしています。\n\n"
                f"対象: {worktree_path}\n"
                f"CWD: {cwd or 'unknown'}\n\n"
                "現在のディレクトリがworktree内にある状態でworktreeを削除すると、\n"
                "シェルセッションが破損し、以降のすべてのコマンドが失敗します。\n\n"
                "【対処法】\n"
                f"1. メインリポジトリに移動: cd {main_repo_str}\n"
                f"2. 正しい方法で削除: git worktree remove {worktree_path}\n"
                f"   または: ./scripts/cleanup-worktrees.sh --force\n\n"
                "【注意】\n"
                f"rm -rf ではなく git worktree remove を使用してください。"
            )
            return make_block_result("locked-worktree-guard", reason)

    return None


def check_worktree_remove(command: str, hook_cwd: str | None = None) -> dict | None:
    """Check if worktree remove command is safe to execute.

    Checks:
    1. CWD is inside the target worktree (would break shell)
    2. Target worktree is locked (owned by another session)

    Args:
        command: The git worktree remove command.
        hook_cwd: Current working directory from hook input.

    Returns:
        Block result dict if should block, None if should approve.
    """
    worktree_path_str, base_dir = extract_worktree_path_from_command(command)
    if not worktree_path_str:
        return None

    # Resolve the path, considering -C flag, cd command, and hook_cwd
    worktree_path = Path(worktree_path_str)
    resolved_base_dir: str | None = None  # For use in get_all_locked_worktree_paths
    if not worktree_path.is_absolute():
        try:
            # Priority 1: base_dir from -C flag or cd command
            if base_dir:
                base_dir_path = Path(base_dir)
                # If base_dir is relative (e.g., from "cd .."), resolve it against hook_cwd
                # This fixes the Codex review issue: relative cd targets need anchoring
                if not base_dir_path.is_absolute():
                    if hook_cwd:
                        base_dir_path = Path(hook_cwd) / base_dir_path
                    else:
                        # Fallback to hook's cwd when hook_cwd is not available
                        base_dir_path = Path.cwd() / base_dir_path
                worktree_path = base_dir_path / worktree_path
                # Store resolved base_dir for lock check (resolve to normalize ".." etc)
                try:
                    resolved_base_dir = str(base_dir_path.resolve())
                except OSError:
                    resolved_base_dir = str(base_dir_path)
            # Priority 2: hook_cwd from Claude Code (caller's actual working directory)
            elif hook_cwd:
                worktree_path = Path(hook_cwd) / worktree_path
            # Priority 3: Fallback to hook's process cwd (least reliable)
            else:
                worktree_path = Path.cwd() / worktree_path
        except Exception:
            return None
    else:
        # For absolute worktree paths, still resolve base_dir if present.
        # This is needed for get_all_locked_worktree_paths() which uses git -C <base_dir>
        # to list worktrees. Without resolving, relative base_dir would run in wrong directory.
        if base_dir:
            base_dir_path = Path(base_dir)
            if not base_dir_path.is_absolute():
                if hook_cwd:
                    combined_path = Path(hook_cwd) / base_dir_path
                else:
                    # Fallback to hook's cwd when hook_cwd is not available
                    try:
                        combined_path = Path.cwd() / base_dir_path
                    except OSError:
                        # If we cannot determine a reliable cwd, avoid using a
                        # potentially relative base_dir; fall back to no base_dir.
                        resolved_base_dir = None
                        combined_path = None
                # Resolve to normalize ".." etc
                if combined_path is not None:
                    try:
                        resolved_base_dir = str(combined_path.resolve())
                    except OSError:
                        resolved_base_dir = str(combined_path)
            else:
                resolved_base_dir = base_dir

    # resolve() can raise OSError on some systems (e.g., broken symlinks, permission issues)
    # This fixes Issue #313: resolve() exception handling
    try:
        worktree_path = worktree_path.resolve()
    except OSError:
        # Fall back to using the path as-is
        pass

    # Check 1: CWD inside target worktree (would break shell)
    # Issue #682: Pass command to detect 'cd <path> &&' patterns
    cwd = Path(hook_cwd) if hook_cwd else None
    if is_cwd_inside_worktree(worktree_path, cwd, command):
        main_repo = get_main_repo_dir()
        main_repo_str = str(main_repo) if main_repo else "/path/to/main/repo"

        reason = (
            f"⚠️ 現在のディレクトリがworktree内です。\n\n"
            f"対象: {worktree_path}\n"
            f"CWD: {cwd or Path.cwd()}\n\n"
            "worktree内でworktreeを削除すると、カレントディレクトリが無効になり、\n"
            "以降のすべてのコマンドが失敗します。\n\n"
            "【対処法】以下のコマンドを**1つずつ順番に**実行してください:\n\n"
            f"```\n"
            f"cd {main_repo_str}\n"
            f"```\n\n"
            f"```\n"
            f"git worktree remove {worktree_path}\n"
            f"```\n\n"
            "⚠️ 重要: `cd ... && git worktree remove ...` のように && で連結しないでください。\n"
            "連結するとセッションのcwdが変わらず、削除後にセッションが破損します。"
        )
        return make_block_result("locked-worktree-guard", reason)

    # Check 2: Locked worktree (owned by another session)
    # Use resolved_base_dir to ensure git -C runs in the correct directory
    locked_paths = get_all_locked_worktree_paths(resolved_base_dir)

    # Issue #700: Check if unlock is part of the same chained command
    # e.g., "git worktree unlock path && git worktree remove path"
    # In this case, skip lock check since unlock will run first
    # P2 fix: Only consider unlocks that appear BEFORE the remove command
    remove_position = find_git_worktree_remove_position(command)
    unlock_targets = extract_unlock_targets_from_command(
        command, hook_cwd, before_position=remove_position
    )

    for locked_path in locked_paths:
        try:
            locked_resolved = locked_path.resolve()
            if worktree_path == locked_resolved:
                # Check if this path is being unlocked in the same command
                if locked_resolved in unlock_targets:
                    # Skip lock check - unlock will run before remove
                    continue

                # Get main repo for the hint message
                main_repo = get_main_repo_dir()
                main_repo_str = str(main_repo) if main_repo else "/path/to/main/repo"

                reason = (
                    f"⚠️ ロックされたworktreeの削除をブロックしました。\n\n"
                    f"対象: {worktree_path}\n\n"
                    "このworktreeは別のセッションが使用中の可能性があります。\n\n"
                    "【対処法】以下のいずれかを選択:\n\n"
                    "**オプション1**: 該当セッションが完了するのを待つ\n\n"
                    "**オプション2**: ロック解除してから削除（以下を**1つずつ順番に**実行）:\n\n"
                    f"```\n"
                    f"cd {main_repo_str}\n"
                    f"```\n\n"
                    f"```\n"
                    f"git worktree unlock {worktree_path}\n"
                    f"```\n\n"
                    f"```\n"
                    f"git worktree remove {worktree_path}\n"
                    f"```\n\n"
                    "⚠️ 注意:\n"
                    "- --force オプションでもロックされたworktreeの削除はブロックされます\n"
                    "- && で連結せず、1コマンドずつ実行してください"
                )
                return make_block_result("locked-worktree-guard", reason)
        except OSError:
            continue

    return None
