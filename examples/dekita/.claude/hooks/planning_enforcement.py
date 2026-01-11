#!/usr/bin/env python3
"""
PreToolUse hook: Enforce plan file before Issue work.

Blocks `git worktree add` for issue-XXX branches if no plan file exists
in .claude/plans/ directory.

Design reviewed: 2025-12-20
- Responsibility: Enforce planning before Issue work
- No duplication: Other hooks don't check plan file existence
- Blocking: PreToolUse, blocks without plan file
- Bypass: SKIP_PLAN=1 env var, Issue labels (documentation/trivial),
         or Issue title prefix (test:/chore:/docs:) (Issue #857)
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Add hooks directory to path for common imports
sys.path.insert(0, str(Path(__file__).parent))

from lib.constants import TIMEOUT_MEDIUM
from lib.execution import log_hook_execution
from lib.results import make_approve_result, make_block_result
from lib.session import parse_hook_input
from lib.strings import extract_inline_skip_env, is_skip_env_enabled, strip_quoted_strings

HOOK_NAME = "planning-enforcement"

# Labels that bypass plan requirement
# P2/P3 = low priority improvements that don't need detailed planning (Issue #1173)
# enhancement = improvements typically don't need detailed planning (Issue #2175)
BYPASS_LABELS = {"documentation", "trivial", "p2", "p3", "enhancement"}

# Title types that bypass plan requirement (Issue #857, #1224, #2169)
# Supports both "type:" and "type(scope):" formats (Conventional Commits)
# "fix" added in Issue #2169: bug fixes typically don't need detailed planning
BYPASS_TITLE_TYPES = ("test", "chore", "docs", "fix")

# Environment variable to bypass plan requirement
SKIP_PLAN_ENV = "SKIP_PLAN"

# Environment variable to bypass already-fixed check
SKIP_ALREADY_FIXED_ENV = "SKIP_ALREADY_FIXED"

# Environment variable to bypass branch existence check
SKIP_BRANCH_CHECK_ENV = "SKIP_BRANCH_CHECK"


def is_worktree_add_command(command: str) -> bool:
    """Check if command is git worktree add."""
    stripped = strip_quoted_strings(command)
    return bool(re.search(r"\bgit\s+worktree\s+add\b", stripped))


def extract_issue_number_from_branch(command: str) -> str | None:
    """Extract issue number from worktree add command's branch name.

    Handles patterns like:
    - git worktree add .worktrees/issue-123 -b fix/issue-123
    - git worktree add .worktrees/issue-123 -b feat/issue-123-feature
    - git worktree add path issue-123
    """
    # Look for issue-XXX pattern in the command
    match = re.search(r"issue-(\d+)", command, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def get_issue_labels(issue_number: str) -> set[str]:
    """Get labels for an issue from GitHub."""
    try:
        result = subprocess.run(
            ["gh", "issue", "view", issue_number, "--json", "labels", "--jq", ".labels[].name"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode == 0:
            return {
                label.lower().strip()
                for label in result.stdout.strip().split("\n")
                if label.strip()
            }
    except (subprocess.TimeoutExpired, OSError):
        # GitHub CLI lookup is best-effort; on failure, return empty set
        pass
    return set()


def get_issue_title(issue_number: str) -> str | None:
    """Get title for an issue from GitHub.

    Returns the issue title or None if lookup fails.
    """
    try:
        result = subprocess.run(
            ["gh", "issue", "view", issue_number, "--json", "title", "--jq", ".title"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        # GitHub CLI lookup is best-effort; on failure, return None
        pass
    return None


def has_bypass_title_prefix(title: str) -> str | None:
    """Check if title starts with a bypass prefix.

    Supports Conventional Commits format (Issue #1224):
    - "chore: description" -> returns "chore:"
    - "chore(ci): description" -> returns "chore:"
    - "test(hooks): description" -> returns "test:"

    The colon must immediately follow the type or scope (no spaces allowed).
    Invalid examples that should NOT match:
    - "chore (ci): description" -> space before scope
    - "chore(ci) update: description" -> text between scope and colon

    Args:
        title: The issue title to check.

    Returns:
        The matched type with colon (e.g., "chore:") if found, None otherwise.
    """
    # Build regex pattern for Conventional Commits format
    # ^(type)(\(scope\))?:
    types_pattern = "|".join(re.escape(t) for t in BYPASS_TITLE_TYPES)
    pattern = rf"^({types_pattern})(\([^)]*\))?:"

    match = re.match(pattern, title.lower())
    if match:
        return f"{match.group(1)}:"

    return None


def check_plan_file_exists(issue_number: str) -> bool:
    """Check if a plan file exists for the issue.

    Looks for (in order):
    1. .claude/plans/issue-{number}.md (exact match in project)
    2. Any .md file in .claude/plans/ containing "issue-{number}" in filename (project)
    3. ~/.claude/plans/ - checks both filename and file content for "issue-{number}"
       (user home directory where EnterPlanMode saves plan files with random names)
    """
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
    plans_dir = Path(project_dir) / ".claude" / "plans"
    pattern = f"issue-{issue_number}"

    # Check project .claude/plans/ directory
    if plans_dir.exists():
        # Check for exact match first (most common case)
        if (plans_dir / f"issue-{issue_number}.md").exists():
            return True

        # Check for any file containing issue-{number} pattern (case-insensitive)
        # This covers: amazing-feature-issue-123.md, Issue-123-implementation.md, etc.
        for plan_file in plans_dir.glob("*.md"):
            if pattern in plan_file.name.lower():
                return True

    # Check user ~/.claude/plans/ directory (EnterPlanMode saves here)
    # Issue #881: EnterPlanMode creates files with random names in ~/.claude/plans/
    user_plans_dir = Path.home() / ".claude" / "plans"
    if user_plans_dir.exists():
        for plan_file in user_plans_dir.glob("*.md"):
            # Check filename for issue number
            if pattern in plan_file.name.lower():
                return True
            # Also check file content for issue reference
            try:
                content = plan_file.read_text(encoding="utf-8")[:2000]  # Read first 2000 chars
                if (
                    f"issue #{issue_number}" in content.lower()
                    or f"issue-{issue_number}" in content.lower()
                ):
                    return True
            except (OSError, UnicodeDecodeError):
                continue

    return False


def has_skip_plan_env(command: str) -> bool:
    """Check if SKIP_PLAN environment variable is set with truthy value.

    Handles both:
    - Exported: export SKIP_PLAN=1 && git worktree add ...
    - Inline: SKIP_PLAN=1 git worktree add ... (including SKIP_PLAN="1")

    Only "1", "true", "True" are considered truthy (Issue #956).
    """
    # Check exported environment variable with value validation
    if is_skip_env_enabled(os.environ.get(SKIP_PLAN_ENV)):
        return True

    # Check inline env var (handles quoted values like SKIP_PLAN="1")
    inline_value = extract_inline_skip_env(command, SKIP_PLAN_ENV)
    if is_skip_env_enabled(inline_value):
        return True

    return False


def has_skip_already_fixed_env(command: str) -> bool:
    """Check if SKIP_ALREADY_FIXED environment variable is set with truthy value.

    Handles both:
    - Exported: export SKIP_ALREADY_FIXED=1 && git worktree add ...
    - Inline: SKIP_ALREADY_FIXED=1 git worktree add ... (including quoted values)

    Only "1", "true", "True" are considered truthy (Issue #956).
    """
    # Check exported environment variable with value validation
    if is_skip_env_enabled(os.environ.get(SKIP_ALREADY_FIXED_ENV)):
        return True

    # Check inline env var (handles quoted values)
    inline_value = extract_inline_skip_env(command, SKIP_ALREADY_FIXED_ENV)
    if is_skip_env_enabled(inline_value):
        return True

    return False


def has_skip_branch_check_env(command: str) -> bool:
    """Check if SKIP_BRANCH_CHECK environment variable is set with truthy value.

    Handles both:
    - Exported: export SKIP_BRANCH_CHECK=1 && git worktree add ...
    - Inline: SKIP_BRANCH_CHECK=1 git worktree add ... (including quoted values)

    Only "1", "true", "True" are considered truthy (Issue #956).
    """
    # Check exported environment variable with value validation
    if is_skip_env_enabled(os.environ.get(SKIP_BRANCH_CHECK_ENV)):
        return True

    # Check inline env var (handles quoted values)
    inline_value = extract_inline_skip_env(command, SKIP_BRANCH_CHECK_ENV)
    if is_skip_env_enabled(inline_value):
        return True

    return False


def extract_branch_name_from_command(command: str) -> str | None:
    """Extract the branch name from a git worktree add command.

    Handles patterns like:
    - git worktree add path -b branch-name
    - git worktree add path --branch branch-name
    - git worktree add path existing-branch (no -b flag)
    """
    # Match -b or --branch flag followed by branch name
    match = re.search(r"\s-b\s+(\S+)", command)
    if match:
        return match.group(1)

    match = re.search(r"\s--branch\s+(\S+)", command)
    if match:
        return match.group(1)

    # If no -b flag, check for existing branch pattern after path
    # git worktree add .worktrees/name existing-branch
    # Look for pattern: worktree add <path> <branch>
    # where <branch> doesn't start with - (not a flag)
    match = re.search(r"\bworktree\s+add\s+\S+\s+(?!-)([\w/.-]+)\s*$", command)
    if match:
        return match.group(1)

    return None


def has_create_branch_flag(command: str) -> bool:
    """Check if the command has -b or --branch flag for creating a new branch.

    This uses proper flag detection to avoid false positives when branch names
    contain '-b' as a substring (e.g., 'fix/issue-123-bug').

    Args:
        command: The git worktree add command.

    Returns:
        True if -b or --branch flag is present, False otherwise.
    """
    # Match -b flag: must be preceded by whitespace and followed by whitespace + branch name
    if re.search(r"\s-b\s+\S+", command):
        return True
    # Match --branch flag
    if re.search(r"\s--branch\s+\S+", command):
        return True
    return False


def check_local_branch_exists(branch_name: str) -> bool:
    """Check if a local branch exists.

    Args:
        branch_name: The branch name to check.

    Returns:
        True if the branch exists, False otherwise.
    """
    try:
        result = subprocess.run(
            ["git", "branch", "--list", branch_name],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        return bool(result.stdout.strip())
    except (subprocess.TimeoutExpired, OSError):
        return False


def get_branch_info(branch_name: str) -> dict | None:
    """Get information about an existing branch.

    Args:
        branch_name: The branch name to check.

    Returns:
        Dict with branch info, or None if branch doesn't exist.
    """
    info: dict = {"branch": branch_name}

    try:
        # Get commits ahead of main
        result = subprocess.run(
            ["git", "rev-list", "--count", f"main..{branch_name}"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode == 0:
            info["commits_ahead"] = int(result.stdout.strip())

        # Get last commit info
        # Use ::: as separator to avoid being flagged by subprocess_lint_check
        result = subprocess.run(
            ["git", "log", "-1", "--format=%ar:::%s", branch_name],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split(":::", 1)
            if len(parts) == 2:
                info["last_commit_time"] = parts[0]
                info["last_commit_msg"] = parts[1][:50]

        # Check if there's a worktree for this branch
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode == 0:
            current_worktree = None
            for line in result.stdout.strip().split("\n"):
                if line.startswith("worktree "):
                    current_worktree = line.split(" ", 1)[1]
                elif line.startswith("branch ") and current_worktree:
                    wt_branch = line.split(" ", 1)[1]
                    # Compare branch name (refs/heads/xxx -> xxx)
                    wt_branch_short = wt_branch.replace("refs/heads/", "")
                    if wt_branch_short == branch_name:
                        info["worktree_path"] = current_worktree
                        break

        # Check if there's an open PR for this branch
        result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--head",
                branch_name,
                "--state",
                "open",
                "--json",
                "number,title,url",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode == 0 and result.stdout.strip():
            prs = json.loads(result.stdout)
            if prs:
                info["open_pr"] = prs[0]

        return info
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
        return None


def get_merged_prs_for_issue(issue_number: str) -> list[dict]:
    """Get merged PRs that reference this issue.

    Returns list of dicts with 'number' and 'title' keys.
    """
    try:
        # Search for PRs that fix/close this issue
        result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--state",
                "merged",
                "--search",
                f"Fixes #{issue_number} OR Closes #{issue_number} OR Fix #{issue_number} OR Close #{issue_number}",
                "--json",
                "number,title",
                "--limit",
                "5",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
        # Best-effort check; failures silently return empty list
        pass
    return []


def search_issue_in_code(issue_number: str) -> list[str]:
    """Search for Issue #XXX references in .claude/ directory.

    Searches *.py and *.sh files, skipping test files (/tests/ directory,
    test_ prefix, or _test.py/_test.sh suffix).

    Returns up to 3 file:line references where the issue is mentioned.
    """
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
    claude_dir = Path(project_dir) / ".claude"

    if not claude_dir.exists():
        return []

    refs = []
    pattern = re.compile(rf"Issue\s*#?\s*{issue_number}\b", re.IGNORECASE)

    try:
        for ext in ("*.py", "*.sh"):
            for file_path in claude_dir.rglob(ext):
                # Skip test files (only by directory name or file prefix/suffix)
                file_name = file_path.name.lower()
                if (
                    "/tests/" in str(file_path).lower()
                    or file_name.startswith("test_")
                    or file_name.endswith("_test.py")
                    or file_name.endswith("_test.sh")
                ):
                    continue
                try:
                    content = file_path.read_text(encoding="utf-8")
                    for i, line in enumerate(content.splitlines(), 1):
                        if pattern.search(line):
                            rel_path = file_path.relative_to(project_dir)
                            refs.append(f"{rel_path}:{i}")
                            break  # One ref per file is enough
                except OSError:
                    continue
    except OSError:
        # Directory traversal errors are non-fatal; return partial results
        pass

    return refs[:3]  # Limit to 3 references


def check_already_fixed(issue_number: str) -> dict | None:
    """Check if issue appears to be already fixed.

    Returns dict with evidence if found, None otherwise.
    """
    evidence = {}

    merged_prs = get_merged_prs_for_issue(issue_number)
    if merged_prs:
        evidence["merged_prs"] = merged_prs

    code_refs = search_issue_in_code(issue_number)
    if code_refs:
        evidence["code_refs"] = code_refs

    return evidence if evidence else None


def main() -> None:
    """Main entry point for the hook."""
    try:
        input_data = parse_hook_input()
    except json.JSONDecodeError:
        # Invalid input - approve silently
        print(json.dumps({"decision": "approve"}))
        return

    tool_name = input_data.get("tool_name", "")

    # Only check Bash commands
    if tool_name != "Bash":
        print(json.dumps({"decision": "approve"}))
        return

    command = input_data.get("tool_input", {}).get("command", "")

    # Check if this is git worktree add
    if not is_worktree_add_command(command):
        print(json.dumps({"decision": "approve"}))
        return

    # Extract issue number from branch name
    issue_number = extract_issue_number_from_branch(command)

    if not issue_number:
        # Not an issue-related worktree, approve
        log_hook_execution(
            HOOK_NAME,
            "approve",
            reason="worktree作成を許可（Issue以外）",
            details={"command_preview": command[:80]},
        )
        result = make_approve_result(HOOK_NAME)
        print(json.dumps(result))
        return

    # Check if trying to create a branch that already exists (Issue #833)
    # This check runs early to detect potential conflicts with other sessions
    branch_name = extract_branch_name_from_command(command)
    if branch_name and has_create_branch_flag(command) and not has_skip_branch_check_env(command):
        # Only check for -b/--branch flag (creating new branch)
        # If no -b flag, user intends to use existing branch
        if check_local_branch_exists(branch_name):
            branch_info = get_branch_info(branch_name)
            info_lines = []

            if branch_info:
                if "commits_ahead" in branch_info:
                    info_lines.append(f"- mainから {branch_info['commits_ahead']} コミット先行")
                if "last_commit_time" in branch_info:
                    msg = branch_info.get("last_commit_msg", "")
                    info_lines.append(f"- 最終コミット: {branch_info['last_commit_time']}")
                    if msg:
                        info_lines.append(f'  → "{msg}"')
                if "worktree_path" in branch_info:
                    info_lines.append(f"- worktree: {branch_info['worktree_path']}")
                if "open_pr" in branch_info:
                    pr = branch_info["open_pr"]
                    info_lines.append(f'- オープンPR: #{pr["number"]} "{pr["title"]}"')

            info_text = "\n".join(info_lines) if info_lines else "（詳細情報取得失敗）"

            block_message = f"""⚠️ ブランチ '{branch_name}' は既に存在します。

【ブランチ情報】
{info_text}

【競合リスク】
別のセッションが同じブランチで作業中の可能性があります。

【対応】
1. 既存ブランチの状態を確認: git log {branch_name} --oneline -5
2. 別セッションの作業中なら作業を中止
3. 自分の作業を再開するなら既存ブランチを使用:
   git worktree add .worktrees/issue-XXX {branch_name}
4. 新規作成が必要なら別名で作成するか、SKIP_BRANCH_CHECK=1 で続行

例: SKIP_BRANCH_CHECK=1 git worktree add ..."""

            log_hook_execution(
                HOOK_NAME,
                "block",
                reason=f"ブランチ '{branch_name}' は既に存在（競合リスク）",
                details={"branch": branch_name, "info": branch_info},
            )
            result = make_block_result(HOOK_NAME, block_message)
            print(json.dumps(result))
            return

    # Check if issue is already fixed (unless bypassed)
    # This check runs before SKIP_PLAN to ensure duplicate work is always detected
    # Issue #1768: Only block if there are merged PRs; code refs alone don't block
    if not has_skip_already_fixed_env(command):
        already_fixed = check_already_fixed(issue_number)
        if already_fixed:
            has_merged_prs = "merged_prs" in already_fixed
            evidence_lines = []
            if has_merged_prs:
                for pr in already_fixed["merged_prs"]:
                    evidence_lines.append(f'- マージ済みPR: #{pr["number"]} "{pr["title"]}"')
            if "code_refs" in already_fixed:
                for ref in already_fixed["code_refs"]:
                    evidence_lines.append(f"- コード内参照: {ref}")

            evidence_text = "\n".join(evidence_lines)

            # Only block if there are merged PRs (strong evidence)
            # Code refs alone are just informational (Issue #1768)
            if has_merged_prs:
                block_message = f"""⚠️ Issue #{issue_number} は既に解決済みの可能性があります。

【検出された証拠】
{evidence_text}

【対応】
1. Issueを確認して本当に追加作業が必要か判断
2. 不要なら作業を中止
3. 必要なら SKIP_ALREADY_FIXED=1 で続行

例: SKIP_ALREADY_FIXED=1 git worktree add ..."""

                log_hook_execution(
                    HOOK_NAME,
                    "block",
                    reason=f"Issue #{issue_number} は既に解決済みの可能性",
                    details={"evidence": already_fixed},
                )
                result = make_block_result(HOOK_NAME, block_message)
                print(json.dumps(result))
                return
            else:
                # Code refs only - warn but don't block
                log_hook_execution(
                    HOOK_NAME,
                    "approve",
                    reason=f"Issue #{issue_number} worktree作成を許可（コード参照のみ、警告表示）",
                    details={"code_refs": already_fixed.get("code_refs", [])},
                )
                # ユーザーに対してコード参照のみであることを明示的に警告する
                warning_message = (
                    f"[{HOOK_NAME}] Issue #{issue_number}: 関連するコード参照が既にmainブランチに存在します。"
                    "Issueが既に解決済みでないか確認してから作業を開始してください。"
                )
                print(warning_message, file=sys.stderr)
                # Continue to plan file check (don't return here)

    # Issue #2169: Skip plan check when using existing branch (work resumption)
    # If no -b flag is present and branch exists, user intends to use an existing branch,
    # which means they're resuming work that was previously planned
    if branch_name and not has_create_branch_flag(command):
        if check_local_branch_exists(branch_name):
            log_hook_execution(
                HOOK_NAME,
                "approve",
                reason=f"Issue #{issue_number} worktree作成を許可（既存ブランチ使用）",
                details={"command_preview": command[:80], "branch": branch_name},
            )
            result = make_approve_result(
                HOOK_NAME,
                f"Issue #{issue_number} worktree作成を許可（既存ブランチ '{branch_name}' を使用）",
            )
            print(json.dumps(result))
            return
        # Branch doesn't exist - warn and continue to plan check
        log_hook_execution(
            HOOK_NAME,
            "info",
            reason=f"Issue #{issue_number} 指定ブランチ '{branch_name}' がローカルに存在しない",
            details={"command_preview": command[:80], "branch": branch_name},
        )
        warning_message = (
            f"[{HOOK_NAME}] Issue #{issue_number}: 指定されたブランチ '{branch_name}' は"
            " ローカルに存在しない可能性があります。ブランチ名を確認するか、"
            "新規ブランチを作成する場合は `-b` フラグを使用してください。"
        )
        print(warning_message, file=sys.stderr)
        # Continue to plan file check

    # Check bypass conditions for plan requirement
    if has_skip_plan_env(command):
        log_hook_execution(
            HOOK_NAME,
            "approve",
            reason=f"Issue #{issue_number} worktree作成を許可（SKIP_PLAN）",
            details={"command_preview": command[:80]},
        )
        result = make_approve_result(
            HOOK_NAME, f"Issue #{issue_number} worktree作成を許可（SKIP_PLAN）"
        )
        print(json.dumps(result))
        return

    # Check issue labels
    labels = get_issue_labels(issue_number)
    if labels & BYPASS_LABELS:
        log_hook_execution(
            HOOK_NAME,
            "approve",
            reason=f"Issue #{issue_number} worktree作成を許可（バイパスラベル）",
            details={"command_preview": command[:80], "labels": list(labels)},
        )
        result = make_approve_result(
            HOOK_NAME,
            f"Issue #{issue_number} worktree作成を許可（{', '.join(labels & BYPASS_LABELS)}ラベル）",
        )
        print(json.dumps(result))
        return

    # Check issue title prefix (Issue #857)
    title = get_issue_title(issue_number)
    if title:
        matched_prefix = has_bypass_title_prefix(title)
        if matched_prefix:
            log_hook_execution(
                HOOK_NAME,
                "approve",
                reason=f"Issue #{issue_number} worktree作成を許可（タイトルプレフィックス）",
                details={"command_preview": command[:80], "title": title, "prefix": matched_prefix},
            )
            result = make_approve_result(
                HOOK_NAME,
                f"Issue #{issue_number} worktree作成を許可（{matched_prefix}タイトル）",
            )
            print(json.dumps(result))
            return

    # Check if plan file exists
    if check_plan_file_exists(issue_number):
        log_hook_execution(
            HOOK_NAME,
            "approve",
            reason=f"Issue #{issue_number} worktree作成を許可（plan file存在）",
            details={"command_preview": command[:80]},
        )
        result = make_approve_result(HOOK_NAME, f"Issue #{issue_number} plan file確認済み")
        print(json.dumps(result))
        return

    # Block: No plan file found
    log_hook_execution(
        HOOK_NAME,
        "block",
        reason=f"Issue #{issue_number} worktree作成をブロック（plan fileなし）",
        details={"command_preview": command[:80]},
    )

    block_message = f"""Plan fileが見つかりません。

**軽微なタスク（バグ修正、ドキュメント、リファクタリング）の場合:**
  SKIP_PLAN=1 git worktree add .worktrees/issue-{issue_number} -b refactor/issue-{issue_number} main

**または以下のいずれかでバイパス:**
  - Issue に `trivial` / `documentation` / `p2` ラベルを付与
  - Issue タイトルを `chore:` / `docs:` / `test:` で開始

**計画が必要な場合（新機能、設計変更）:**
  1. EnterPlanMode で計画を作成
  2. ユーザー承認後に作業開始
  Plan file: .claude/plans/issue-{issue_number}.md"""

    result = make_block_result(HOOK_NAME, block_message)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
