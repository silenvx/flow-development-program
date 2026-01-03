"""
Tests for guard_rules.py

Note: This module's functions are tested in the split test files
as part of the comprehensive hook test suite. The functions were extracted
from locked-worktree-guard.py and the existing tests verify their behavior.

Covered by split test files:
- check_self_branch_deletion: TestCheckSelfBranchDeletion, TestMergeCheckDryRunIntegration (test_locked_worktree_guard_merge.py)
- check_worktree_remove: TestCheckWorktreeRemove (test_locked_worktree_guard_worktree_remove.py)
- check_rm_worktree: TestCheckRmWorktree, TestIsRmWorktreeCommand (test_locked_worktree_guard_rm_commands.py)
- check_rm_orphan_worktree: TestCheckRmOrphanWorktree (test_locked_worktree_guard_rm_commands.py)
- execute_safe_merge: TestExecuteSafeMerge (test_locked_worktree_guard_merge.py)
- try_auto_cleanup_worktree: TestTryAutoCleanupWorktree (test_locked_worktree_guard_utils.py)
"""

# Import from split test files to allow pytest collection
from test_locked_worktree_guard_merge import (
    TestCheckSelfBranchDeletion,
    TestExecuteSafeMerge,
    TestMergeCheckDryRunIntegration,
)
from test_locked_worktree_guard_rm_commands import (
    TestCheckRmOrphanWorktree,
    TestCheckRmWorktree,
    TestIsRmWorktreeCommand,
)
from test_locked_worktree_guard_utils import (
    TestTryAutoCleanupWorktree,
)
from test_locked_worktree_guard_worktree_remove import (
    TestCheckWorktreeRemove,
)

__all__ = [
    "TestCheckSelfBranchDeletion",
    "TestCheckWorktreeRemove",
    "TestCheckRmWorktree",
    "TestIsRmWorktreeCommand",
    "TestCheckRmOrphanWorktree",
    "TestExecuteSafeMerge",
    "TestMergeCheckDryRunIntegration",
    "TestTryAutoCleanupWorktree",
]
