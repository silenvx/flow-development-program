"""
Tests for worktree_manager.py

Note: This module's functions are tested in the split test files
as part of the comprehensive hook test suite. The functions were extracted
from locked-worktree-guard.py and the existing tests verify their behavior.

Covered by split test files:
- get_locked_worktrees: TestGetLockedWorktrees (test_locked_worktree_guard_worktree_basic.py)
- get_pr_for_branch: TestGetPrForBranch (test_locked_worktree_guard_pr_commands.py)
- get_branch_for_pr: TestGetBranchForPr (test_locked_worktree_guard_pr_commands.py)
- get_current_worktree: TestGetCurrentWorktree (test_locked_worktree_guard_worktree_basic.py)
- get_worktree_for_branch: TestGetWorktreeForBranch (test_locked_worktree_guard_worktree_basic.py)
- is_cwd_inside_worktree: TestIsCwdInsideWorktree (test_locked_worktree_guard_worktree_remove.py)
- is_self_session_worktree: TestIsSelfSessionWorktree (test_locked_worktree_guard_worktree_basic.py)
- get_main_repo_dir: TestGetMainRepoDir (test_locked_worktree_guard_worktree_basic.py)
- get_all_locked_worktree_paths: TestGetAllLockedWorktreePaths (test_locked_worktree_guard_worktree_basic.py)
- check_active_work_signs: TestCheckActiveWorkSigns, TestActiveWorkWarningIntegration (test_locked_worktree_guard_active_work.py)
- get_orphan_worktree_directories: TestGetOrphanWorktreeDirectories (test_locked_worktree_guard_rm_commands.py)
"""

# Import from split test files to allow pytest collection
from test_locked_worktree_guard_active_work import (
    TestActiveWorkWarningIntegration,
    TestCheckActiveWorkSigns,
)
from test_locked_worktree_guard_pr_commands import (
    TestGetBranchForPr,
    TestGetPrForBranch,
)
from test_locked_worktree_guard_rm_commands import (
    TestGetOrphanWorktreeDirectories,
)
from test_locked_worktree_guard_worktree_basic import (
    TestGetAllLockedWorktreePaths,
    TestGetCurrentWorktree,
    TestGetLockedWorktrees,
    TestGetMainRepoDir,
    TestGetWorktreeForBranch,
    TestIsSelfSessionWorktree,
)
from test_locked_worktree_guard_worktree_remove import (
    TestIsCwdInsideWorktree,
)

__all__ = [
    "TestGetLockedWorktrees",
    "TestGetPrForBranch",
    "TestGetBranchForPr",
    "TestGetCurrentWorktree",
    "TestGetWorktreeForBranch",
    "TestIsCwdInsideWorktree",
    "TestIsSelfSessionWorktree",
    "TestGetMainRepoDir",
    "TestGetAllLockedWorktreePaths",
    "TestCheckActiveWorkSigns",
    "TestActiveWorkWarningIntegration",
    "TestGetOrphanWorktreeDirectories",
]
