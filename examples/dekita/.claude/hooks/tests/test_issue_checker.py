"""Tests for issue_checker module.

This module contains Issue/acceptance criteria check functions extracted from merge-check.py.
Full functional tests are in test_merge_check.py.
"""

import sys
from pathlib import Path

# Add parent directory to path for module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)


class TestIssueCheckerImports:
    """Test that issue_checker module can be imported and has expected exports."""

    def test_module_imports(self):
        """Module should be importable."""
        import issue_checker

        assert issue_checker is not None

    def test_get_pr_body_exists(self):
        """get_pr_body function should exist."""
        from issue_checker import get_pr_body

        assert callable(get_pr_body)

    def test_extract_issue_numbers_from_pr_body_exists(self):
        """extract_issue_numbers_from_pr_body function should exist."""
        from issue_checker import extract_issue_numbers_from_pr_body

        assert callable(extract_issue_numbers_from_pr_body)

    def test_extract_issue_numbers_from_commits_exists(self):
        """extract_issue_numbers_from_commits function should exist."""
        from issue_checker import extract_issue_numbers_from_commits

        assert callable(extract_issue_numbers_from_commits)

    def test_fetch_issue_acceptance_criteria_exists(self):
        """fetch_issue_acceptance_criteria function should exist."""
        from issue_checker import fetch_issue_acceptance_criteria

        assert callable(fetch_issue_acceptance_criteria)

    def test_check_incomplete_acceptance_criteria_exists(self):
        """check_incomplete_acceptance_criteria function should exist."""
        from issue_checker import check_incomplete_acceptance_criteria

        assert callable(check_incomplete_acceptance_criteria)

    def test_check_excluded_criteria_without_followup_exists(self):
        """check_excluded_criteria_without_followup function should exist."""
        from issue_checker import check_excluded_criteria_without_followup

        assert callable(check_excluded_criteria_without_followup)

    def test_check_bug_issue_from_review_exists(self):
        """check_bug_issue_from_review function should exist."""
        from issue_checker import check_bug_issue_from_review

        assert callable(check_bug_issue_from_review)

    def test_constants_exist(self):
        """Module should export expected constants."""
        from issue_checker import (
            BUG_ISSUE_TITLE_KEYWORDS,
            ISSUE_CREATION_PATTERN,
        )

        assert BUG_ISSUE_TITLE_KEYWORDS is not None
        assert ISSUE_CREATION_PATTERN is not None

    def test_helper_functions_exist(self):
        """Helper functions should be accessible."""
        from issue_checker import _collect_issue_refs_from_review, _is_bug_issue, _references_pr

        assert callable(_collect_issue_refs_from_review)
        assert callable(_is_bug_issue)
        assert callable(_references_pr)

    def test_check_remaining_task_patterns_exists(self):
        """check_remaining_task_patterns function should exist."""
        from issue_checker import check_remaining_task_patterns

        assert callable(check_remaining_task_patterns)

    def test_remaining_task_pattern_exists(self):
        """REMAINING_TASK_PATTERN constant should exist."""
        from issue_checker import REMAINING_TASK_PATTERN

        assert REMAINING_TASK_PATTERN is not None


class TestFindRemainingTaskPatterns:
    """Test _find_remaining_task_patterns_without_issue_ref function."""

    def test_detects_phase_pattern_without_issue_ref(self):
        """Should detect '第2段階' pattern without Issue reference."""
        from issue_checker import _find_remaining_task_patterns_without_issue_ref

        body = "この機能は第2段階で実装予定です。"
        patterns = _find_remaining_task_patterns_without_issue_ref(body)
        assert len(patterns) == 1
        assert "第2段階" in patterns[0]

    def test_detects_separate_pr_pattern_without_issue_ref(self):
        """Should detect '別PRで' pattern without Issue reference."""
        from issue_checker import _find_remaining_task_patterns_without_issue_ref

        body = "残りの機能は別PRで対応します。"
        patterns = _find_remaining_task_patterns_without_issue_ref(body)
        assert len(patterns) == 1
        assert "別PRで" in patterns[0]

    def test_detects_remaining_task_pattern(self):
        """Should detect '残タスク' pattern."""
        from issue_checker import _find_remaining_task_patterns_without_issue_ref

        body = "残タスク: テストの追加が必要です。"
        patterns = _find_remaining_task_patterns_without_issue_ref(body)
        assert len(patterns) == 1
        assert "残タスク" in patterns[0]

    def test_detects_followup_pattern(self):
        """Should detect 'follow-up' pattern."""
        from issue_checker import _find_remaining_task_patterns_without_issue_ref

        body = "This is a follow-up task for later."
        patterns = _find_remaining_task_patterns_without_issue_ref(body)
        assert len(patterns) == 1

    def test_ignores_pattern_with_nearby_issue_ref(self):
        """Should ignore pattern if Issue reference is nearby."""
        from issue_checker import _find_remaining_task_patterns_without_issue_ref

        body = "第2段階として #123 で対応予定です。"
        patterns = _find_remaining_task_patterns_without_issue_ref(body)
        assert len(patterns) == 0

    def test_ignores_pattern_with_issue_ref_before(self):
        """Should ignore pattern if Issue reference comes before."""
        from issue_checker import _find_remaining_task_patterns_without_issue_ref

        body = "詳細は #456 を参照。残タスクとして登録済み。"
        patterns = _find_remaining_task_patterns_without_issue_ref(body)
        assert len(patterns) == 0

    def test_ignores_pattern_in_code_block(self):
        """Should ignore patterns inside code blocks."""
        from issue_checker import _find_remaining_task_patterns_without_issue_ref

        body = "概要\n\n```\n第2段階で実装\n```\n\n終わり"
        patterns = _find_remaining_task_patterns_without_issue_ref(body)
        assert len(patterns) == 0

    def test_detects_multiple_patterns(self):
        """Should detect multiple patterns without Issue references."""
        from issue_checker import _find_remaining_task_patterns_without_issue_ref

        body = "第2段階で対応。別PRで実装。残タスクあり。"
        patterns = _find_remaining_task_patterns_without_issue_ref(body)
        assert len(patterns) == 3

    def test_empty_body_returns_empty_list(self):
        """Should return empty list for empty body."""
        from issue_checker import _find_remaining_task_patterns_without_issue_ref

        patterns = _find_remaining_task_patterns_without_issue_ref("")
        assert patterns == []

    def test_detects_future_work_pattern(self):
        """Should detect 'future work' pattern."""
        from issue_checker import _find_remaining_task_patterns_without_issue_ref

        body = "This is future work for the next sprint."
        patterns = _find_remaining_task_patterns_without_issue_ref(body)
        assert len(patterns) == 1

    def test_detects_next_step_pattern(self):
        """Should detect '次のステップ' pattern."""
        from issue_checker import _find_remaining_task_patterns_without_issue_ref

        body = "次のステップとして実装します。"
        patterns = _find_remaining_task_patterns_without_issue_ref(body)
        assert len(patterns) == 1

    def test_case_insensitive_english_patterns(self):
        """Should match patterns case-insensitively for English."""
        from issue_checker import _find_remaining_task_patterns_without_issue_ref

        body = "This is a FOLLOW-UP task."
        patterns = _find_remaining_task_patterns_without_issue_ref(body)
        assert len(patterns) == 1


class TestRemainingTaskPattern:
    """Test REMAINING_TASK_PATTERN regex."""

    def test_matches_phase_patterns(self):
        """Should match phase/stage patterns (excluding phase 1 which is likely current work)."""
        from issue_checker import REMAINING_TASK_PATTERN

        test_cases = [
            # Phase 1 / Stage 1 / 第1段階 are excluded - they likely refer to current work
            ("第1段階", False),
            ("第2段階", True),
            ("第10段階", True),
            ("Phase 1", False),
            ("Phase 2", True),
            ("Stage 1", False),
            ("Stage 3", True),
        ]

        for text, should_match in test_cases:
            match = REMAINING_TASK_PATTERN.search(text)
            assert (match is not None) == should_match, f"Failed for: {text}"

    def test_matches_separate_pr_patterns(self):
        """Should match separate PR patterns."""
        from issue_checker import REMAINING_TASK_PATTERN

        test_cases = [
            ("別PRで", True),
            ("別PRとして", True),
            ("別のPR", True),
            ("後続PR", True),
            ("後続のPR", True),
            ("another PR", True),
            ("separate PR", True),
        ]

        for text, should_match in test_cases:
            match = REMAINING_TASK_PATTERN.search(text)
            assert (match is not None) == should_match, f"Failed for: {text}"

    def test_matches_remaining_task_patterns(self):
        """Should match remaining task patterns."""
        from issue_checker import REMAINING_TASK_PATTERN

        test_cases = [
            ("残タスク", True),
            ("今後の対応", True),
            ("将来の対応", True),
            ("follow-up", True),
            ("followup", True),
            ("future work", True),
            ("next step", True),
        ]

        for text, should_match in test_cases:
            match = REMAINING_TASK_PATTERN.search(text)
            assert (match is not None) == should_match, f"Failed for: {text}"

    def test_does_not_match_unrelated_text(self):
        """Should not match unrelated text."""
        from issue_checker import REMAINING_TASK_PATTERN

        test_cases = [
            "This is a normal comment.",
            "Fixed the bug.",
            "実装完了",
            "テスト追加",
        ]

        for text in test_cases:
            match = REMAINING_TASK_PATTERN.search(text)
            assert match is None, f"Should not match: {text}"
