"""
Tests for shell_tokenizer.py

Note: This module's functions are tested in the split test files
as part of the comprehensive hook test suite. The functions were extracted
from locked-worktree-guard.py and the existing tests verify their behavior.

Covered by split test files:
- is_shell_redirect: TestIsShellRedirect (test_locked_worktree_guard_utils.py)
- is_bare_redirect_operator: TestIsBareRedirectOperator (test_locked_worktree_guard_utils.py)
- extract_cd_target_before_git: TestExtractCdTargetBeforeGit (test_locked_worktree_guard_cd_path.py)
- extract_rm_paths: TestExtractRmPaths (test_locked_worktree_guard_rm_commands.py)
- check_single_git_worktree_remove: TestIsWorktreeRemoveCommand (test_locked_worktree_guard_worktree_remove.py)
- extract_base_dir_from_git_segment: TestExtractWorktreePathFromCommand (test_locked_worktree_guard_worktree_remove.py)
"""

# Import from split test files to allow pytest collection
from test_locked_worktree_guard_cd_path import (
    TestExtractCdTargetBeforeGit,
)
from test_locked_worktree_guard_rm_commands import (
    TestExtractRmPaths,
)
from test_locked_worktree_guard_utils import (
    TestIsBareRedirectOperator,
    TestIsShellRedirect,
)
from test_locked_worktree_guard_worktree_remove import (
    TestExtractWorktreePathFromCommand,
    TestIsWorktreeRemoveCommand,
)

__all__ = [
    "TestIsShellRedirect",
    "TestIsBareRedirectOperator",
    "TestExtractCdTargetBeforeGit",
    "TestExtractRmPaths",
    "TestIsWorktreeRemoveCommand",
    "TestExtractWorktreePathFromCommand",
]
