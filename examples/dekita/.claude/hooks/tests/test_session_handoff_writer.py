#!/usr/bin/env python3
"""Tests for session-handoff-writer.py hook."""

import sys
from pathlib import Path

# Add hooks directory to path
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))


class TestExtractLessonsLearned:
    """Tests for extract_lessons_learned function."""

    def setup_method(self):
        """Load the module for testing."""
        from conftest import load_hook_module

        self.hook = load_hook_module("session-handoff-writer")

    def test_empty_block_reasons(self):
        """Should return empty list for empty input."""
        result = self.hook.extract_lessons_learned([])
        assert result == []

    def test_worktree_related_block(self):
        """Should extract worktree lesson from worktree-related block."""
        block_reasons = ["worktree path must be under .worktrees/"]
        result = self.hook.extract_lessons_learned(block_reasons)
        assert len(result) == 1
        assert "worktree" in result[0].lower()

    def test_merge_related_block(self):
        """Should extract merge lesson from merge-related block."""
        block_reasons = ["マージ前にレビュースレッドを解決してください"]
        result = self.hook.extract_lessons_learned(block_reasons)
        assert len(result) == 1
        assert "マージ" in result[0]

    def test_push_related_block(self):
        """Should extract push lesson from push-related block."""
        block_reasons = ["pushする前にcodex reviewを実行してください"]
        result = self.hook.extract_lessons_learned(block_reasons)
        assert len(result) == 1
        assert "push" in result[0].lower()

    def test_main_branch_block(self):
        """Should extract main branch lesson."""
        block_reasons = ["mainブランチでの編集はできません"]
        result = self.hook.extract_lessons_learned(block_reasons)
        assert len(result) == 1
        assert "main" in result[0].lower()

    def test_lock_related_block(self):
        """Should extract lock lesson from lock-related block."""
        block_reasons = ["worktree is locked by another session"]
        result = self.hook.extract_lessons_learned(block_reasons)
        assert len(result) == 1
        assert "ロック" in result[0]

    def test_codex_review_block(self):
        """Should extract codex review lesson."""
        block_reasons = ["Codex review not executed before push"]
        result = self.hook.extract_lessons_learned(block_reasons)
        assert len(result) == 1
        assert "codex" in result[0].lower()

    def test_multiple_block_reasons(self):
        """Should extract lessons from multiple block reasons."""
        block_reasons = [
            "worktree path must be under .worktrees/",
            "マージ前にレビュースレッドを解決してください",
            "pushする前にcodex reviewを実行してください",
        ]
        result = self.hook.extract_lessons_learned(block_reasons)
        assert len(result) == 3

    def test_duplicate_pattern_deduplicated(self):
        """Should deduplicate lessons with same pattern."""
        block_reasons = [
            "worktree path must be under .worktrees/",
            "worktree cannot be deleted while CWD is inside",
        ]
        result = self.hook.extract_lessons_learned(block_reasons)
        # Both are worktree-related, so should only get 1 lesson
        assert len(result) == 1

    def test_max_five_lessons(self):
        """Should limit to maximum 5 lessons."""
        block_reasons = [
            "worktree error",
            "merge error",
            "push error",
            "main error",
            "edit error",
            "branch error",
            "lock error",
            "review error",
        ]
        result = self.hook.extract_lessons_learned(block_reasons)
        assert len(result) <= 5

    def test_unrecognized_block_reason(self):
        """Should return empty for unrecognized block reasons."""
        block_reasons = ["unknown error that doesn't match any pattern"]
        result = self.hook.extract_lessons_learned(block_reasons)
        assert result == []

    def test_case_insensitive_matching(self):
        """Should match patterns case-insensitively."""
        block_reasons = ["WORKTREE path error", "MERGE blocked"]
        result = self.hook.extract_lessons_learned(block_reasons)
        assert len(result) == 2
