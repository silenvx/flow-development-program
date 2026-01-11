#!/usr/bin/env python3
"""フックファイルと非フックファイルが同時にステージされた際に警告する。

Why:
    フック変更と対応するコード変更が同一PRにあると、mainのフックが実行され
    誤ブロックが発生する（chicken-and-egg問題）。分割PRを促す警告を出す。

What:
    - .claude/hooks/*.py（tests/除く）と他ファイルの混在をチェック
    - パターン検出フック（キーワードリスト含む）の変更時にデータ分析を促す
    - フック修正時にhooks-reference Skill参照をリマインド
    - ブロックせず警告のみ（意図的な混在もあるため）

Remarks:
    - PreToolUseのため`git add && git commit`はadd前のインデックスをチェック
    - 内部開発ツール警告のためAGENTS.mdには未記載

Changelog:
    - silenvx/dekita#1912: パターン検出フックのデータ分析リマインド追加
    - silenvx/dekita#2379: hooks-reference Skillリマインド追加
"""

import json
import os
import re
import subprocess
import sys

from lib.constants import TIMEOUT_MEDIUM
from lib.execution import log_hook_execution
from lib.session import parse_hook_input
from lib.strings import split_command_chain, strip_quoted_strings


def is_git_add_or_commit_command(command: str) -> bool:
    """Check if command contains git add or git commit.

    Handles command chains like:
    - git add && git commit -m "msg"
    - git add .
    """
    stripped = strip_quoted_strings(command)
    subcommands = split_command_chain(stripped)
    for subcmd in subcommands:
        if re.search(r"^git\s+(add|commit)(\s|$)", subcmd):
            return True
    return False


def get_staged_files() -> list[str]:
    """Get list of all staged files.

    For testing purposes, set _TEST_STAGED_FILES to a comma-separated list of files.
    """
    # Test mode: use provided files
    test_files = os.environ.get("_TEST_STAGED_FILES")
    if test_files is not None:
        if not test_files:
            return []
        return test_files.split(",")

    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode != 0:
            return []
        files = result.stdout.strip().split("\n") if result.stdout.strip() else []
        return files
    except Exception:
        return []


def is_hook_file(file_path: str) -> bool:
    """Check if a file is a hook file (excluding tests).

    Hook files are:
    - .claude/hooks/*.py
    - Excluding .claude/hooks/tests/*
    - Excluding .claude/hooks/lib/* (utility modules, less risky)
    """
    # Normalize path
    path = file_path.replace("\\", "/")

    # Check if in hooks directory
    if not path.startswith(".claude/hooks/"):
        return False

    # Exclude test files
    if path.startswith(".claude/hooks/tests/"):
        return False

    # Exclude lib files (utility modules)
    if path.startswith(".claude/hooks/lib/"):
        return False

    # Only Python files
    if not path.endswith(".py"):
        return False

    return True


def classify_staged_files(files: list[str]) -> tuple[list[str], list[str]]:
    """Classify staged files into hook files and non-hook files.

    Returns:
        Tuple of (hook_files, non_hook_files)
    """
    hook_files = []
    non_hook_files = []

    for f in files:
        if is_hook_file(f):
            hook_files.append(f)
        else:
            non_hook_files.append(f)

    return hook_files, non_hook_files


def get_staged_file_content(file_path: str) -> str | None:
    """Get the staged content of a file.

    For testing purposes, set _TEST_FILE_CONTENT_{filename} to the content.
    """
    # Test mode: use provided content
    safe_name = file_path.replace("/", "_").replace(".", "_")
    test_content = os.environ.get(f"_TEST_FILE_CONTENT_{safe_name}")
    if test_content is not None:
        return test_content

    try:
        result = subprocess.run(
            ["git", "show", f":{file_path}"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode != 0:
            return None
        return result.stdout
    except Exception:
        return None


# Pattern detection patterns - detect hooks that contain keyword/pattern lists
PATTERN_LIST_INDICATORS = [
    # Variable names ending with _KEYWORDS, _PATTERNS, etc.
    r"^[A-Z_]+_KEYWORDS\s*=\s*\[",
    r"^[A-Z_]+_PATTERNS\s*=\s*\[",
    r"^[A-Z_]+_REGEX\s*=\s*\[",
    # Raw string regex patterns in lists
    r'r"[^"]*\\[sdwbBSWDnrt]',  # Regex metacharacters
    r"r'[^']*\\[sdwbBSWDnrt]",
    # re.compile patterns
    r"re\.compile\s*\(",
    # re.search/match/finditer with pattern variable
    r"re\.(search|match|findall|finditer)\s*\(\s*pattern",
]


def is_pattern_detection_hook(content: str) -> bool:
    """Check if a hook file contains pattern detection logic.

    Pattern detection hooks typically contain:
    - *_KEYWORDS, *_PATTERNS, *_REGEX variable definitions
    - Lists of regex patterns (raw strings with regex metacharacters)
    - re.compile() calls
    """
    return any(re.search(pattern, content, re.MULTILINE) for pattern in PATTERN_LIST_INDICATORS)


def detect_pattern_hooks(hook_files: list[str]) -> list[str]:
    """Detect which hook files are pattern-detection hooks."""
    pattern_hooks = []
    for hook_file in hook_files:
        content = get_staged_file_content(hook_file)
        if content and is_pattern_detection_hook(content):
            pattern_hooks.append(hook_file)
    return pattern_hooks


def build_pattern_analysis_warning(pattern_hooks: list[str]) -> str:
    """Build warning message for pattern-detection hooks."""
    hook_list = "\n".join(f"  - {f}" for f in pattern_hooks[:5])
    if len(pattern_hooks) > 5:
        hook_list += f"\n  ... and {len(pattern_hooks) - 5} more"

    return (
        "📊 hook-change-detector: パターン検出フックが変更されています。\n\n"
        "【実データ分析チェックリスト】\n"
        "パターン検出フック作成・変更時は、以下を確認してください:\n\n"
        "□ 実データソースを特定したか\n"
        "  - GitHub PR comments\n"
        "  - Issue comments\n"
        "  - セッションログ\n\n"
        "□ 実データからパターンを抽出したか\n"
        "  - 仮説ベースではなく実際のデータを分析\n"
        "  - 頻度・コンテキストを確認\n\n"
        "□ 作成したパターンをテストしたか\n"
        "  - 検出率（実際に検出すべきものを検出できているか）\n"
        "  - 誤検知率（検出すべきでないものを検出していないか）\n\n"
        f"対象フック:\n{hook_list}\n\n"
        "【分析ツール】\n"
        ".claude/scripts/analyze_pattern_data.py を使用してパターンを分析できます:\n"
        '  python3 analyze_pattern_data.py search --pattern "検索パターン" --show-matches\n'
        '  python3 analyze_pattern_data.py analyze --pattern "分析パターン"\n'
        "  python3 analyze_pattern_data.py validate --patterns-file patterns.txt"
    )


def build_hooks_skill_reminder(hook_files: list[str]) -> str:
    """Build reminder message to reference hooks-reference Skill.

    Issue #2379: Reminds developers to reference hooks-reference Skill
    when modifying hook files, to ensure existing patterns are followed.
    """
    hook_list = "\n".join(f"  - {f}" for f in hook_files[:5])
    if len(hook_files) > 5:
        hook_list += f"\n  ... and {len(hook_files) - 5} more"

    return (
        "📚 hook-change-detector: フックファイルが変更されています。\n\n"
        "【hooks-reference Skill 参照リマインダー】\n"
        "フック修正・新規作成時は `hooks-reference` Skill を参照してください。\n\n"
        "**確認すべき内容:**\n"
        "□ 既存の実装パターン（例: ZoneInfoNotFoundError の例外処理）\n"
        "□ フック出力フォーマット（make_block_result, make_approve_result）\n"
        "□ ログ記録パターン（log_hook_execution）\n"
        "□ SKIP環境変数のサポート\n"
        "□ テストの実装パターン\n\n"
        f"対象フック:\n{hook_list}\n\n"
        "**Skill呼び出し方法:**\n"
        "  /hooks-reference\n\n"
        "💡 「単純な修正だからSkill不要」は誤った判断です。\n"
        "   既存パターンを見落とすリスクを回避するため、常に参照してください。"
    )


def main():
    """PreToolUse hook for Bash commands.

    Warns in the following cases:
    - When hook files and non-hook files are staged together (chicken-and-egg problem)
    - When pattern-detection hooks are modified (reminds to perform data analysis)
    - When any hook files are staged (reminds to reference hooks-reference Skill)
    """
    try:
        data = parse_hook_input()
        tool_input = data.get("tool_input", {})
        command = tool_input.get("command", "")

        # Only check git add/commit commands
        if not is_git_add_or_commit_command(command):
            result = {"decision": "approve"}
            print(json.dumps(result))
            sys.exit(0)

        # Get staged files
        staged_files = get_staged_files()
        if not staged_files:
            result = {"decision": "approve"}
            print(json.dumps(result))
            sys.exit(0)

        # Classify files
        hook_files, non_hook_files = classify_staged_files(staged_files)

        # Collect all warnings
        warnings = []

        # Check for mixed staging
        if hook_files and non_hook_files:
            # Build warning message
            hook_list = "\n".join(f"  - {f}" for f in hook_files[:5])
            if len(hook_files) > 5:
                hook_list += f"\n  ... and {len(hook_files) - 5} more"

            non_hook_list = "\n".join(f"  - {f}" for f in non_hook_files[:5])
            if len(non_hook_files) > 5:
                non_hook_list += f"\n  ... and {len(non_hook_files) - 5} more"

            mixed_warning = (
                "⚠️ hook-change-detector: フックファイルと非フックファイルが同時にステージされています。\n\n"
                "【Chicken-and-egg問題の警告】\n"
                "フックファイルの変更とそれに依存するコードを同じPRに含めると、\n"
                "CIではmainのフックが使用されるため、意図しないブロック/失敗が発生する可能性があります。\n\n"
                f"フックファイル:\n{hook_list}\n\n"
                f"非フックファイル:\n{non_hook_list}\n\n"
                "【推奨対応】\n"
                "1. フックの変更を先に別PRでマージ\n"
                "2. その後、依存するコードをPRに含める\n\n"
                "【安全に続行できるケース】\n"
                "- テストファイルとの混在: 通常は安全（警告は表示されますが問題なし）\n"
                "- フックに影響しない独立した変更: 問題なし\n"
                "- 緊急時: このまま続行可（自己責任）"
            )
            warnings.append(mixed_warning)

        # Check for pattern-detection hooks (Issue #1912)
        if hook_files:
            pattern_hooks = detect_pattern_hooks(hook_files)
            if pattern_hooks:
                pattern_warning = build_pattern_analysis_warning(pattern_hooks)
                warnings.append(pattern_warning)
                log_hook_execution(
                    "hook-change-detector",
                    "approve",
                    None,
                    {
                        "pattern_hooks": pattern_hooks,
                        "warning": "pattern_detection_hook",
                    },
                )

        # Always remind about hooks-reference Skill when hook files are staged (Issue #2379)
        if hook_files:
            skill_reminder = build_hooks_skill_reminder(hook_files)
            warnings.append(skill_reminder)
            log_hook_execution(
                "hook-change-detector",
                "approve",
                None,
                {
                    "hook_files": hook_files,
                    "warning": "hooks_skill_reminder",
                },
            )

        # Return with warnings if any
        if warnings:
            combined_warning = "\n\n---\n\n".join(warnings)
            result = {
                "decision": "approve",
                "systemMessage": combined_warning,
            }
            if hook_files and non_hook_files:
                log_hook_execution(
                    "hook-change-detector",
                    "approve",
                    None,
                    {
                        "hook_files": hook_files,
                        "non_hook_files_count": len(non_hook_files),
                        "warning": "mixed_staging",
                    },
                )
            print(json.dumps(result))
            sys.exit(0)

        # No warnings - all good
        result = {"decision": "approve"}

    except Exception as e:
        # On error, approve to avoid blocking
        print(f"[hook-change-detector] Hook error: {e}", file=sys.stderr)
        result = {"decision": "approve", "reason": f"Hook error: {e}"}

    log_hook_execution(
        "hook-change-detector", result.get("decision", "approve"), result.get("reason")
    )
    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
