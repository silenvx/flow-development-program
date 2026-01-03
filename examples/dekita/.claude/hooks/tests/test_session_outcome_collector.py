"""Tests for session outcome collection and task type estimation.

Issue #1158: Tests for outcome-based session evaluation.
"""

from flow_definitions import TaskType, estimate_task_type


class TestEstimateTaskType:
    """Tests for estimate_task_type function."""

    def test_prs_merged_returns_implementation(self):
        """PRs merged should return IMPLEMENTATION."""
        outcomes = {
            "prs_merged": [123],
            "prs_created": [123],
            "prs_pushed": [],
            "issues_created": [],
            "commits_count": 5,
        }
        assert estimate_task_type(outcomes) == TaskType.IMPLEMENTATION

    def test_prs_created_without_merge_returns_wip(self):
        """PRs created but not merged should return IMPLEMENTATION_WIP."""
        outcomes = {
            "prs_merged": [],
            "prs_created": [456],
            "prs_pushed": [],
            "issues_created": [],
            "commits_count": 3,
        }
        assert estimate_task_type(outcomes) == TaskType.IMPLEMENTATION_WIP

    def test_prs_pushed_returns_review_response(self):
        """Pushed to existing PRs should return REVIEW_RESPONSE."""
        outcomes = {
            "prs_merged": [],
            "prs_created": [],
            "prs_pushed": [789],
            "issues_created": [],
            "commits_count": 2,
        }
        assert estimate_task_type(outcomes) == TaskType.REVIEW_RESPONSE

    def test_issues_only_returns_issue_creation(self):
        """Only Issues created (no commits) should return ISSUE_CREATION."""
        outcomes = {
            "prs_merged": [],
            "prs_created": [],
            "prs_pushed": [],
            "issues_created": [100, 101],
            "commits_count": 0,
        }
        assert estimate_task_type(outcomes) == TaskType.ISSUE_CREATION

    def test_no_commits_returns_research(self):
        """No commits and no issues should return RESEARCH."""
        outcomes = {
            "prs_merged": [],
            "prs_created": [],
            "prs_pushed": [],
            "issues_created": [],
            "commits_count": 0,
        }
        assert estimate_task_type(outcomes) == TaskType.RESEARCH

    def test_commits_without_pr_returns_maintenance(self):
        """Commits without PR should return MAINTENANCE."""
        outcomes = {
            "prs_merged": [],
            "prs_created": [],
            "prs_pushed": [],
            "issues_created": [],
            "commits_count": 3,
        }
        assert estimate_task_type(outcomes) == TaskType.MAINTENANCE

    def test_empty_outcomes_returns_research(self):
        """Empty outcomes dict should return RESEARCH."""
        outcomes = {}
        assert estimate_task_type(outcomes) == TaskType.RESEARCH

    def test_priority_merged_over_created(self):
        """Merged PRs should take priority over created PRs."""
        outcomes = {
            "prs_merged": [1],
            "prs_created": [1, 2],  # More created, but merged takes priority
            "prs_pushed": [],
            "issues_created": [],
            "commits_count": 10,
        }
        assert estimate_task_type(outcomes) == TaskType.IMPLEMENTATION

    def test_priority_created_over_pushed(self):
        """Created PRs should take priority over pushed."""
        outcomes = {
            "prs_merged": [],
            "prs_created": [1],
            "prs_pushed": [2, 3],  # More pushed, but created takes priority
            "issues_created": [],
            "commits_count": 5,
        }
        assert estimate_task_type(outcomes) == TaskType.IMPLEMENTATION_WIP


class TestTaskTypeEnum:
    """Tests for TaskType enum values."""

    def test_all_values_are_strings(self):
        """All TaskType values should be strings."""
        for task_type in TaskType:
            assert isinstance(task_type.value, str)

    def test_expected_task_types_exist(self):
        """All expected task types should exist."""
        expected = [
            "implementation",
            "implementation_wip",
            "review_response",
            "issue_creation",
            "research",
            "maintenance",
            "unknown",
        ]
        actual = [t.value for t in TaskType]
        for exp in expected:
            assert exp in actual, f"Missing task type: {exp}"
