#!/usr/bin/env python3
"""fork-sessionコラボレーション用のIssue依存関係検出を行う。

Why:
    複数セッション並行作業時のコンフリクトを防ぐため、
    ファイル重複に基づくIssue間依存関係を検出する。

What:
    - build_dependency_graph(): worktreeからファイル重複で依存グラフ構築
    - find_independent_issues(): アクティブIssueと競合しないIssueを検出
    - suggest_independent_issues(): 着手可能なIssueを優先度順で提案

Remarks:
    - ファイル重複=依存関係として双方向リンク作成
    - PR未作成のオープンIssueのみを提案対象に
    - 優先度順（P0>P1>P2>P3）でソート

Changelog:
    - silenvx/dekita#2513: fork-sessionコラボレーション機能追加
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from lib.constants import TIMEOUT_MEDIUM
from lib.session_graph import WorktreeInfo


@dataclass
class IssueDependency:
    """Represents an issue and its dependencies."""

    issue_number: int
    worktree: Path | None = None
    changed_files: set[str] = field(default_factory=set)
    depends_on: list[int] = field(default_factory=list)
    depended_by: list[int] = field(default_factory=list)
    pr_number: int | None = None


def get_open_issues_with_pr() -> dict[int, int]:
    """Get mapping of open issues to their PRs.

    Returns:
        Dict mapping issue number to PR number.
    """
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--state",
                "open",
                "--json",
                "number,title,headRefName",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode != 0:
            return {}

        prs = json.loads(result.stdout)
        issue_to_pr: dict[int, int] = {}

        for pr in prs:
            # Extract issue number from branch name
            from lib.session_graph import extract_issue_number_from_branch

            issue_num = extract_issue_number_from_branch(pr.get("headRefName", ""))
            if issue_num:
                issue_to_pr[issue_num] = pr["number"]

        return issue_to_pr
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return {}


def get_pr_changed_files(pr_number: int) -> set[str]:
    """Get files changed in a PR.

    Args:
        pr_number: The PR number.

    Returns:
        Set of changed file paths.
    """
    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--json", "files"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode != 0:
            return set()

        data = json.loads(result.stdout)
        files = data.get("files", [])
        return {f.get("path", "") for f in files if f.get("path")}
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return set()


def build_dependency_graph(
    worktree_infos: list[WorktreeInfo],
    issue_to_pr: dict[int, int] | None = None,
) -> dict[int, IssueDependency]:
    """Build a dependency graph between issues based on file overlap.

    Two issues are considered dependent if they modify the same files.

    Args:
        worktree_infos: List of WorktreeInfo objects.
        issue_to_pr: Optional mapping of issue numbers to PR numbers.

    Returns:
        Dict mapping issue number to IssueDependency.
    """
    if issue_to_pr is None:
        issue_to_pr = get_open_issues_with_pr()

    # Build issue -> files mapping
    issue_files: dict[int, IssueDependency] = {}

    for info in worktree_infos:
        if info.issue_number is None:
            continue

        dep = IssueDependency(
            issue_number=info.issue_number,
            worktree=info.path,
            changed_files=info.changed_files,
            pr_number=issue_to_pr.get(info.issue_number),
        )
        issue_files[info.issue_number] = dep

    # Find dependencies (file overlap)
    issue_numbers = list(issue_files.keys())
    for i, issue_a in enumerate(issue_numbers):
        files_a = issue_files[issue_a].changed_files
        for issue_b in issue_numbers[i + 1 :]:
            files_b = issue_files[issue_b].changed_files

            # Check for file overlap
            overlap = files_a & files_b
            if overlap:
                # A depends on B and B depends on A (bidirectional)
                issue_files[issue_a].depends_on.append(issue_b)
                issue_files[issue_b].depends_on.append(issue_a)
                issue_files[issue_a].depended_by.append(issue_b)
                issue_files[issue_b].depended_by.append(issue_a)

    return issue_files


def find_independent_issues(
    dependency_graph: dict[int, IssueDependency],
    active_issues: set[int],
) -> list[int]:
    """Find issues that have no dependencies on active issues.

    An independent issue is one that:
    1. Is not currently being worked on (not in active_issues)
    2. Has no file overlap with any active issue

    Args:
        dependency_graph: The dependency graph from build_dependency_graph.
        active_issues: Set of issue numbers currently being worked on.

    Returns:
        List of independent issue numbers, sorted by issue number.
    """
    independent: list[int] = []

    for issue_num, dep in dependency_graph.items():
        # Skip if already active
        if issue_num in active_issues:
            continue

        # Check if any of its dependencies are active
        has_active_dependency = any(d in active_issues for d in dep.depends_on)
        if not has_active_dependency:
            independent.append(issue_num)

    return sorted(independent)


def get_open_issues_without_pr() -> list[dict]:
    """Get list of open issues that don't have an associated PR.

    Returns:
        List of issue dicts with number, title, and labels.
    """
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "list",
                "--state",
                "open",
                "--json",
                "number,title,labels",
                "--limit",
                "50",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode != 0:
            return []

        issues = json.loads(result.stdout)
        issue_to_pr = get_open_issues_with_pr()

        # Filter out issues that already have PRs
        return [issue for issue in issues if issue["number"] not in issue_to_pr]
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return []


def get_issue_priority(issue: dict) -> int:
    """Get priority score for an issue (lower is higher priority).

    Priority order: P0 (0) > P1 (1) > P2 (2) > P3 (3) > no label (4)

    Args:
        issue: Issue dict with labels.

    Returns:
        Priority score (0-4).
    """
    labels = issue.get("labels", [])
    label_names = {label.get("name", "") for label in labels}

    if "P0" in label_names:
        return 0
    if "P1" in label_names:
        return 1
    if "P2" in label_names:
        return 2
    if "P3" in label_names:
        return 3
    return 4


def suggest_independent_issues(
    active_worktree_infos: list[WorktreeInfo],
) -> list[dict]:
    """Suggest issues that can be worked on independently.

    Finds open issues without PRs that are not already being worked on
    in active worktrees.

    Args:
        active_worktree_infos: List of WorktreeInfo for currently active worktrees.

    Returns:
        List of suggested issue dicts, sorted by priority then number.
    """
    # Get issue numbers currently being worked on in worktrees
    active_issue_numbers: set[int] = set()
    for info in active_worktree_infos:
        if info.issue_number is not None:
            active_issue_numbers.add(info.issue_number)

    # Get open issues without PRs
    open_issues = get_open_issues_without_pr()

    # Filter out issues that are already being worked on
    available_issues = [
        issue for issue in open_issues if issue["number"] not in active_issue_numbers
    ]

    # Sort by priority (P0 > P1 > P2 > P3 > no label)
    sorted_issues = sorted(
        available_issues,
        key=lambda i: (get_issue_priority(i), i["number"]),
    )

    return sorted_issues[:10]  # Return top 10
