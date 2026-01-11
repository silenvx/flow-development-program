#!/usr/bin/env python3
# - 責務: PRマージ後に動作確認Issueを自動作成
# - 関連: post-merge-flow-completion.py (フローステップ完了) と補完関係
# - 自動化型: マージ成功後に軽量な動作確認Issueを作成
"""
PostToolUse hook to create observation issues after PR merge.

When `gh pr merge` succeeds, this hook:
1. Checks if the PR contains non-docs-only changes
2. Creates a lightweight verification issue with checklist
3. Links the observation issue to the merged PR

This ensures post-implementation verification is tracked systematically.
See Issue #2501 for design rationale.
"""

import json
import re
import subprocess

from lib.constants import TIMEOUT_LIGHT, TIMEOUT_MEDIUM
from lib.execution import log_hook_execution
from lib.github import extract_pr_number as common_extract_pr_number
from lib.hook_input import get_exit_code, get_tool_result
from lib.repo import is_merge_success
from lib.session import parse_hook_input

# File patterns that are docs-only
DOCS_ONLY_PATTERNS = frozenset(
    [
        ".md",
        ".txt",
        ".rst",
        "CLAUDE.md",
        "AGENTS.md",
        "README",
        "CHANGELOG",
        "LICENSE",
        ".claude/prompts/",
        ".claude/skills/",
        ".claude/docs/",
    ]
)

# File patterns for checklist generation
# Each pattern maps to (claude_items, human_items, commands)
FILE_CHECKLIST_PATTERNS: list[tuple[str, list[str], list[str], list[str]]] = [
    # Hooks
    (
        ".claude/hooks/",
        [
            "フックが正しく発火する（該当操作後にログ確認）",
            "エラーハンドリングが正しく動作する",
        ],
        [],
        [
            "# フック実行ログ確認",
            "cat .claude/logs/execution/hook-execution-*.jsonl | grep <フック名> | tail -5",
        ],
    ),
    # Scripts
    (
        ".claude/scripts/",
        [
            "スクリプトが正常に実行できる",
            "ヘルプオプション（--help）が動作する",
        ],
        [],
        [
            "# スクリプト実行テスト",
            "python3 .claude/scripts/<スクリプト名>.py --help",
        ],
    ),
    # Frontend components
    (
        "frontend/src/",
        ["ビルドが成功する（`pnpm build`）"],
        [
            "UI表示が崩れていない（本番URL確認）",
            "モバイル表示に問題がない（実機またはDevTools確認）",
            "アクセシビリティに問題がない（キーボード操作確認）",
        ],
        [
            "# ビルド確認",
            "pnpm build",
            "# 本番URL",
            "https://dekita.app/",
        ],
    ),
    # Worker/API
    (
        "worker/src/",
        [
            "APIが正常にレスポンスを返す",
            "エラーレスポンスが適切に返る",
        ],
        ["レスポンス速度に問題がない（体感確認）"],
        [
            "# API動作確認",
            "curl -s https://api.dekita.app/health | jq .",
        ],
    ),
    # Tests
    (
        ".test.",
        ["テストが全てパスする（`pnpm test:ci`）"],
        [],
        ["pnpm test:ci"],
    ),
    # Shared types
    (
        "shared/",
        ["型定義の変更がfrontend/workerで正しく反映される"],
        [],
        ["pnpm typecheck"],
    ),
    # GitHub workflows
    (
        ".github/workflows/",
        ["CIが正常に動作する"],
        [],
        ["# GitHub Actionsのrun確認", "gh run list --limit 3"],
    ),
    # Config files
    (
        "settings.json",
        ["設定変更が反映される"],
        [],
        [],
    ),
]


def _matches_pattern(path: str, pattern: str) -> bool:
    """Check if a file path matches a pattern.

    Pattern types:
    - Directory patterns (contain "/"): Match as path prefix/substring
    - File extension patterns (start with "."): Match in filename only
    - Filename prefix patterns (end with "_"): Match at start of filename
    - Exact filename patterns: Match exact filename
    """
    basename = path.split("/")[-1]

    if "/" in pattern:
        # Directory pattern - match as path substring
        return pattern in path
    elif pattern.startswith(".") and not pattern.endswith("/"):
        # File extension pattern (e.g., ".test.") - match in filename
        return pattern in basename
    elif pattern.endswith("_"):
        # Filename prefix pattern (e.g., "test_") - match at start of filename
        return basename.startswith(pattern)
    else:
        # Exact filename match (e.g., "settings.json")
        return basename == pattern


def generate_checklist_items(
    files: list[dict],
) -> tuple[list[str], list[str], list[str]]:
    """Generate checklist items based on changed files.

    Returns:
        Tuple of (claude_items, human_items, commands) - deduplicated lists
    """
    claude_items: list[str] = []
    human_items: list[str] = []
    commands: list[str] = []

    # Track what we've already added to avoid duplicates
    seen_patterns: set[str] = set()

    for file in files:
        path = file.get("path", "")
        for pattern, c_items, h_items, cmds in FILE_CHECKLIST_PATTERNS:
            if _matches_pattern(path, pattern) and pattern not in seen_patterns:
                seen_patterns.add(pattern)
                claude_items.extend(c_items)
                human_items.extend(h_items)
                commands.extend(cmds)

    # Deduplicate while preserving order
    claude_items = list(dict.fromkeys(claude_items))
    human_items = list(dict.fromkeys(human_items))
    commands = list(dict.fromkeys(commands))

    return claude_items, human_items, commands


def is_pr_merge_command(tool_input: str) -> bool:
    """Check if the command is a PR merge command."""
    return "gh pr merge" in tool_input


def extract_pr_number(command: str) -> int | None:
    """Extract PR number from merge command or current branch PR."""
    pr_str = common_extract_pr_number(command)
    if pr_str:
        return int(pr_str)

    # If no PR number in command, get PR for current branch
    try:
        result = subprocess.run(
            ["gh", "pr", "view", "--json", "number"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return data.get("number")
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        # gh CLI not available, network error, or invalid JSON
        # Fall through to return None
        pass

    return None


def get_pr_details(pr_number: int) -> dict | None:
    """Get PR details including files changed and title."""
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                str(pr_number),
                "--json",
                "title,body,files,headRefName",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode != 0:
            return None

        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        # gh CLI not available, network error, or invalid JSON
        return None


def is_docs_only(files: list[dict]) -> bool:
    """Check if all changed files are documentation only."""
    if not files:
        return False

    for file in files:
        path = file.get("path", "")
        is_doc = False

        for pattern in DOCS_ONLY_PATTERNS:
            # Check directory patterns first (they may start with ".")
            if "/" in pattern:
                # Directory match
                if pattern in path:
                    is_doc = True
                    break
            elif pattern.startswith("."):
                # Extension match (e.g., ".md", ".txt")
                if path.endswith(pattern):
                    is_doc = True
                    break
            else:
                # Filename match (e.g., "README", "CHANGELOG", "LICENSE")
                # Match only the basename to avoid false positives like
                # "src/my_README_parser.py"
                basename = path.split("/")[-1]
                if pattern == basename or basename.startswith(f"{pattern}."):
                    is_doc = True
                    break

        if not is_doc:
            return False

    return True


def extract_issue_number(pr_details: dict) -> int | None:
    """Extract linked issue number from PR details."""
    body = pr_details.get("body") or ""
    title = pr_details.get("title") or ""
    branch = pr_details.get("headRefName") or ""

    # Search for issue references
    for text in [body, title]:
        match = re.search(r"(?:closes?|fixes?|resolves?)\s+#(\d+)", text, re.IGNORECASE)
        if match:
            return int(match.group(1))

    # Check branch name
    match = re.search(r"issue-(\d+)", branch, re.IGNORECASE)
    if match:
        return int(match.group(1))

    return None


def has_existing_observation_issue(pr_number: int) -> bool:
    """Check if an observation issue already exists for this PR."""
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "list",
                "--label",
                "observation",
                "--state",
                "all",  # Include closed issues to prevent recreation
                "--search",
                f"動作確認: #{pr_number} in:title",
                "--json",
                "number",
                "--limit",
                "1",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
        )
        if result.returncode != 0:
            return False

        issues = json.loads(result.stdout)
        return len(issues) > 0
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        # On error, allow creation (fail-open)
        return False


def create_observation_issue(
    pr_number: int,
    pr_title: str,
    linked_issue: int | None,
    files: list[dict],
) -> int | None:
    """Create an observation issue for post-merge verification."""
    # Sanitize PR title: remove newlines (Issue title cannot contain newlines)
    sanitized_title = re.sub(r"[\r\n]+", " ", pr_title).strip()
    title = f"動作確認: {sanitized_title} (#{pr_number})"

    # Sanitize PR title to prevent markdown injection in body
    safe_pr_title = f"`{sanitized_title}`"

    # Generate checklist items based on changed files
    claude_items, human_items, commands = generate_checklist_items(files)

    body_lines = [
        "## 概要",
        "",
        f"PR #{pr_number} ({safe_pr_title}) のマージ後確認。",
        "",
    ]

    # Claude Code verification items
    body_lines.extend(
        [
            "## Claude Code確認項目",
            "",
        ]
    )
    if claude_items:
        for item in claude_items:
            body_lines.append(f"- [ ] {item}")
    else:
        body_lines.append("- [ ] 新セッションで変更が反映されている")
        body_lines.append("- [ ] 期待通りの動作をしている")
    body_lines.append("")

    # Human verification items (if any)
    if human_items:
        body_lines.extend(
            [
                "## 人間確認項目（Claude Code確認不可）",
                "",
            ]
        )
        for item in human_items:
            body_lines.append(f"- [ ] {item}")
        body_lines.append("")

    # Verification commands (if any)
    if commands:
        body_lines.extend(
            [
                "## 確認コマンド",
                "",
                "```bash",
            ]
        )
        body_lines.extend(commands)
        body_lines.extend(
            [
                "```",
                "",
            ]
        )

    # Related links
    body_lines.extend(
        [
            "## 関連",
            "",
            f"- マージしたPR: #{pr_number}",
        ]
    )
    if linked_issue:
        body_lines.append(f"- 関連Issue: #{linked_issue}")
    body_lines.extend(
        [
            "",
            "## 備考",
            "",
            "- 確認完了後、このIssueをクローズしてください",
            "- 問題があった場合は別途Issueを作成してください",
            "",
            "---",
            "*このIssueは post-merge-observation-issue フックにより自動作成されました*",
        ]
    )
    body = "\n".join(body_lines)

    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "create",
                "--title",
                title,
                "--body",
                body,
                "--label",
                "observation,P3",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode != 0:
            return None

        # Extract issue number from output
        # Output format: https://github.com/owner/repo/issues/123
        match = re.search(r"/issues/(\d+)", result.stdout)
        if match:
            return int(match.group(1))

        return None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        # gh CLI not available or network error
        return None


def main() -> None:
    """Main hook logic."""
    input_data = parse_hook_input()
    if not input_data:
        return

    tool_name = input_data.get("tool_name", "")
    if tool_name != "Bash":
        return

    tool_input = input_data.get("tool_input", {})
    command = tool_input.get("command", "")

    if not is_pr_merge_command(command):
        return

    tool_output = input_data.get("tool_output", "")
    tool_result = get_tool_result(input_data) or {}
    exit_code = get_exit_code(tool_result)

    if not is_merge_success(exit_code, tool_output, command):
        log_hook_execution(
            "post-merge-observation-issue",
            "skip",
            f"is_merge_success returned False (exit_code={exit_code})",
            {"output_preview": tool_output[:200] if tool_output else ""},
        )
        return

    pr_number = extract_pr_number(command)
    if not pr_number:
        log_hook_execution(
            "post-merge-observation-issue",
            "approve",
            "skipped: could not extract PR number",
        )
        return

    pr_details = get_pr_details(pr_number)
    if not pr_details:
        log_hook_execution(
            "post-merge-observation-issue",
            "approve",
            f"skipped: could not get PR #{pr_number} details",
        )
        return

    # Skip docs-only changes
    files = pr_details.get("files", [])
    if is_docs_only(files):
        log_hook_execution(
            "post-merge-observation-issue",
            "approve",
            f"skipped: PR #{pr_number} is docs-only",
        )
        return

    # Check for existing observation issue to prevent duplicates
    if has_existing_observation_issue(pr_number):
        log_hook_execution(
            "post-merge-observation-issue",
            "approve",
            f"skipped: observation issue already exists for PR #{pr_number}",
        )
        return

    pr_title = pr_details.get("title", "")
    linked_issue = extract_issue_number(pr_details)

    observation_issue = create_observation_issue(pr_number, pr_title, linked_issue, files)
    if observation_issue:
        print(f"\n[post-merge-observation-issue] 動作確認Issue #{observation_issue} を作成しました")
        print(f"  - 対象PR: #{pr_number}")
        print("  - 新セッションで動作確認し、問題なければクローズしてください")
        log_hook_execution(
            "post-merge-observation-issue",
            "approve",
            f"created observation issue #{observation_issue} for PR #{pr_number}",
        )
    else:
        print(f"\n[post-merge-observation-issue] 動作確認Issue作成に失敗しました (PR #{pr_number})")
        log_hook_execution(
            "post-merge-observation-issue",
            "approve",
            f"failed to create observation issue for PR #{pr_number}",
        )


if __name__ == "__main__":
    main()
