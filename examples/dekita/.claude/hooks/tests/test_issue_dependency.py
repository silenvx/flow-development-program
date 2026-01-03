#!/usr/bin/env python3
"""Tests for lib/issue_dependency.py module."""

import sys
from pathlib import Path

# Add hooks directory to path
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from unittest.mock import patch

from lib.issue_dependency import (
    IssueDependency,
    build_dependency_graph,
    find_independent_issues,
    get_issue_priority,
    suggest_independent_issues,
)
from lib.session_graph import WorktreeInfo


class TestIssueDependency:
    """Tests for IssueDependency dataclass."""

    def test_create_with_defaults(self):
        """Should create IssueDependency with default values."""
        dep = IssueDependency(issue_number=123)

        assert dep.issue_number == 123
        assert dep.worktree is None
        assert dep.changed_files == set()
        assert dep.depends_on == []
        assert dep.depended_by == []
        assert dep.pr_number is None

    def test_create_with_all_fields(self):
        """Should create IssueDependency with all fields."""
        dep = IssueDependency(
            issue_number=123,
            worktree=Path("/path/to/worktree"),
            changed_files={"file1.py", "file2.py"},
            depends_on=[456],
            depended_by=[789],
            pr_number=100,
        )

        assert dep.issue_number == 123
        assert dep.worktree == Path("/path/to/worktree")
        assert dep.changed_files == {"file1.py", "file2.py"}
        assert dep.depends_on == [456]
        assert dep.depended_by == [789]
        assert dep.pr_number == 100


class TestBuildDependencyGraph:
    """Tests for build_dependency_graph function."""

    def test_build_empty_graph(self):
        """Should return empty graph for empty worktree list."""
        result = build_dependency_graph([], {})

        assert result == {}

    def test_build_single_issue_no_dependencies(self):
        """Should build graph with single issue and no dependencies."""
        worktrees = [
            WorktreeInfo(
                path=Path("/path/to/issue-123"),
                branch="feat/issue-123",
                issue_number=123,
                changed_files={"file1.py"},
            )
        ]

        result = build_dependency_graph(worktrees, {})

        assert 123 in result
        assert result[123].issue_number == 123
        assert result[123].changed_files == {"file1.py"}
        assert result[123].depends_on == []

    def test_detect_file_overlap_dependency(self):
        """Should detect dependency when issues modify same files."""
        worktrees = [
            WorktreeInfo(
                path=Path("/path/to/issue-123"),
                branch="feat/issue-123",
                issue_number=123,
                changed_files={"common.py", "file1.py"},
            ),
            WorktreeInfo(
                path=Path("/path/to/issue-456"),
                branch="feat/issue-456",
                issue_number=456,
                changed_files={"common.py", "file2.py"},
            ),
        ]

        result = build_dependency_graph(worktrees, {})

        # Issue 123 depends on 456 and vice versa
        assert 456 in result[123].depends_on
        assert 123 in result[456].depends_on
        assert 456 in result[123].depended_by
        assert 123 in result[456].depended_by

    def test_no_dependency_without_overlap(self):
        """Should not detect dependency when no file overlap."""
        worktrees = [
            WorktreeInfo(
                path=Path("/path/to/issue-123"),
                branch="feat/issue-123",
                issue_number=123,
                changed_files={"file1.py"},
            ),
            WorktreeInfo(
                path=Path("/path/to/issue-456"),
                branch="feat/issue-456",
                issue_number=456,
                changed_files={"file2.py"},
            ),
        ]

        result = build_dependency_graph(worktrees, {})

        assert result[123].depends_on == []
        assert result[456].depends_on == []

    def test_skip_worktrees_without_issue_number(self):
        """Should skip worktrees without issue number."""
        worktrees = [
            WorktreeInfo(
                path=Path("/path/to/feature"),
                branch="feat/no-issue",
                issue_number=None,
                changed_files={"file1.py"},
            ),
            WorktreeInfo(
                path=Path("/path/to/issue-123"),
                branch="feat/issue-123",
                issue_number=123,
                changed_files={"file1.py"},
            ),
        ]

        result = build_dependency_graph(worktrees, {})

        # Only issue 123 should be in the graph
        assert len(result) == 1
        assert 123 in result


class TestFindIndependentIssues:
    """Tests for find_independent_issues function."""

    def test_find_independent_when_no_overlap(self):
        """Should find independent issues when no file overlap."""
        graph = {
            123: IssueDependency(issue_number=123, depends_on=[]),
            456: IssueDependency(issue_number=456, depends_on=[]),
        }
        active_issues = {123}

        result = find_independent_issues(graph, active_issues)

        assert result == [456]

    def test_exclude_dependent_issues(self):
        """Should exclude issues that depend on active issues."""
        graph = {
            123: IssueDependency(issue_number=123, depends_on=[]),
            456: IssueDependency(issue_number=456, depends_on=[123]),
        }
        active_issues = {123}

        result = find_independent_issues(graph, active_issues)

        # Issue 456 depends on 123, so it's not independent
        assert result == []

    def test_exclude_active_issues(self):
        """Should exclude issues that are already active."""
        graph = {
            123: IssueDependency(issue_number=123, depends_on=[]),
            456: IssueDependency(issue_number=456, depends_on=[]),
        }
        active_issues = {123, 456}

        result = find_independent_issues(graph, active_issues)

        assert result == []

    def test_return_sorted_list(self):
        """Should return sorted list of independent issues."""
        graph = {
            789: IssueDependency(issue_number=789, depends_on=[]),
            123: IssueDependency(issue_number=123, depends_on=[]),
            456: IssueDependency(issue_number=456, depends_on=[]),
        }
        active_issues = set()

        result = find_independent_issues(graph, active_issues)

        assert result == [123, 456, 789]


class TestGetIssuePriority:
    """Tests for get_issue_priority function."""

    def test_p0_highest_priority(self):
        """P0 should have highest priority (score 0)."""
        issue = {"labels": [{"name": "P0"}]}

        assert get_issue_priority(issue) == 0

    def test_p1_priority(self):
        """P1 should have score 1."""
        issue = {"labels": [{"name": "P1"}]}

        assert get_issue_priority(issue) == 1

    def test_p2_priority(self):
        """P2 should have score 2."""
        issue = {"labels": [{"name": "P2"}]}

        assert get_issue_priority(issue) == 2

    def test_p3_priority(self):
        """P3 should have score 3."""
        issue = {"labels": [{"name": "P3"}]}

        assert get_issue_priority(issue) == 3

    def test_no_priority_label(self):
        """Issues without priority label should have score 4."""
        issue = {"labels": [{"name": "enhancement"}]}

        assert get_issue_priority(issue) == 4

    def test_no_labels(self):
        """Issues without labels should have score 4."""
        issue = {"labels": []}

        assert get_issue_priority(issue) == 4

    def test_multiple_labels_returns_highest_priority(self):
        """Should return highest priority when multiple labels."""
        issue = {"labels": [{"name": "P2"}, {"name": "P0"}, {"name": "bug"}]}

        assert get_issue_priority(issue) == 0


class TestSuggestIndependentIssues:
    """Tests for suggest_independent_issues function."""

    @patch("lib.issue_dependency.get_open_issues_without_pr")
    def test_excludes_active_worktree_issues(self, mock_get_issues):
        """Should exclude issues already being worked on in worktrees."""
        # Mock open issues
        mock_get_issues.return_value = [
            {"number": 100, "title": "Issue 100", "labels": []},
            {"number": 200, "title": "Issue 200", "labels": []},
            {"number": 300, "title": "Issue 300", "labels": []},
        ]

        # Worktree working on issue 200
        active_worktrees = [
            WorktreeInfo(
                path=Path("/path/issue-200"),
                branch="feat/issue-200",
                issue_number=200,
                changed_files={"file.py"},
            )
        ]

        result = suggest_independent_issues(active_worktrees)

        # Issue 200 should be excluded
        result_numbers = [issue["number"] for issue in result]
        assert 100 in result_numbers
        assert 200 not in result_numbers
        assert 300 in result_numbers

    @patch("lib.issue_dependency.get_open_issues_without_pr")
    def test_excludes_multiple_active_issues(self, mock_get_issues):
        """Should exclude all issues being worked on in multiple worktrees."""
        mock_get_issues.return_value = [
            {"number": 100, "title": "Issue 100", "labels": []},
            {"number": 200, "title": "Issue 200", "labels": []},
            {"number": 300, "title": "Issue 300", "labels": []},
        ]

        # Multiple worktrees working on issues
        active_worktrees = [
            WorktreeInfo(
                path=Path("/path/issue-100"),
                branch="feat/issue-100",
                issue_number=100,
                changed_files=set(),
            ),
            WorktreeInfo(
                path=Path("/path/issue-200"),
                branch="feat/issue-200",
                issue_number=200,
                changed_files=set(),
            ),
        ]

        result = suggest_independent_issues(active_worktrees)

        result_numbers = [issue["number"] for issue in result]
        assert 100 not in result_numbers
        assert 200 not in result_numbers
        assert 300 in result_numbers

    @patch("lib.issue_dependency.get_open_issues_without_pr")
    def test_handles_worktree_without_issue_number(self, mock_get_issues):
        """Should handle worktrees that don't have issue numbers."""
        mock_get_issues.return_value = [
            {"number": 100, "title": "Issue 100", "labels": []},
        ]

        # Worktree without issue number
        active_worktrees = [
            WorktreeInfo(
                path=Path("/path/feature"),
                branch="feat/no-issue",
                issue_number=None,
                changed_files=set(),
            )
        ]

        result = suggest_independent_issues(active_worktrees)

        # All open issues should be included
        result_numbers = [issue["number"] for issue in result]
        assert 100 in result_numbers
