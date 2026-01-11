#!/usr/bin/env python3
"""フック種類に応じた返却形式の誤用を検出する。

Why:
    フック種類によって期待される返却形式が異なる。誤った形式を使用すると
    フックが正しく動作しない。PR #1632でStop hookに誤った形式を適用した
    問題の再発を防止する。

What:
    - Stop hookでprint_continue_and_log_skip使用を検出
    - フック種類と期待される返却形式の対応をチェック
    - 不一致時に警告（ブロックしない）

Remarks:
    - 非ブロック型（警告のみ）
    - Stop: print_approve_and_log_skip、PostToolUse: print_continue_and_log_skip

Changelog:
    - silenvx/dekita#1635: フック追加
"""

import json
import os
import sys
import traceback
from pathlib import Path

from lib.execution import log_hook_execution
from lib.results import print_continue_and_log_skip
from lib.session import create_hook_context, parse_hook_input


def load_settings() -> dict:
    """Load settings.json from project directory."""
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not project_dir:
        # Try to find it relative to this file
        hook_dir = Path(__file__).parent
        settings_path = hook_dir.parent / "settings.json"
    else:
        settings_path = Path(project_dir) / ".claude" / "settings.json"

    try:
        return json.loads(settings_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def get_hook_type_for_file(file_path: str, settings: dict) -> str | None:
    """Determine which hook type a file belongs to.

    Args:
        file_path: Path to the hook file (e.g., .claude/hooks/session_end_main_check.py)
        settings: Parsed settings.json

    Returns:
        Hook type (SessionStart, PreToolUse, PostToolUse, Stop) or None if not found
    """
    # Extract filename from path
    file_name = Path(file_path).name

    hooks_config = settings.get("hooks", {})

    for hook_type in ["SessionStart", "PreToolUse", "PostToolUse", "Stop"]:
        hook_list = hooks_config.get(hook_type, [])
        for hook_group in hook_list:
            # Handle both direct hooks and nested hooks structure
            hooks = hook_group.get("hooks", [hook_group])
            for hook in hooks:
                command = hook.get("command", "")
                # コマンド文字列を空白区切りでトークン化し、
                # 各トークンをパスとして解釈してベース名で完全一致判定する。
                # これにより "pre-check.py" / "check.py" のような
                # 部分一致による誤検出を防ぐ。
                for token in str(command).split():
                    try:
                        token_name = Path(token.strip('"').strip("'")).name
                    except (TypeError, ValueError):
                        # tokenがパスとして解釈できない場合はスキップ
                        continue
                    if token_name == file_name:
                        return hook_type

    return None


def check_return_format_usage(content: str) -> dict[str, list[int]]:
    """Check which return format functions are used in the file.

    Args:
        content: Python source code content

    Returns:
        Dict mapping function name to list of line numbers where it's used

    Note:
        This uses simple heuristics to detect function calls.
        It may have false positives for function names in strings/docstrings,
        but since this is a warning-only hook, that's acceptable.
        The goal is to catch the common mistake, not be 100% accurate.
    """
    usage = {
        "print_continue_and_log_skip": [],
        "print_approve_and_log_skip": [],
    }

    in_docstring = False
    docstring_delimiter = None

    for i, line in enumerate(content.split("\n"), start=1):
        stripped = line.strip()

        # Track docstring state (triple quotes)
        if not in_docstring:
            if stripped.startswith('"""') or stripped.startswith("'''"):
                docstring_delimiter = stripped[:3]
                # Check if docstring ends on the same line
                if stripped.count(docstring_delimiter) >= 2:
                    continue  # Single-line docstring, skip
                in_docstring = True
                continue
        else:
            if docstring_delimiter in stripped:
                in_docstring = False
                docstring_delimiter = None
            continue

        # Skip comments and imports
        if (
            stripped.startswith("#")
            or stripped.startswith("from ")
            or stripped.startswith("import ")
        ):
            continue

        # Check for function calls (with opening parenthesis)
        for func_name in usage.keys():
            # Look for pattern: func_name( - actual function call
            if f"{func_name}(" in line:
                usage[func_name].append(i)

    return usage


def analyze_hook_file(file_path: str, content: str, settings: dict) -> list[dict]:
    """Analyze a hook file for return format mismatches.

    Args:
        file_path: Path to the hook file
        content: File content
        settings: Parsed settings.json

    Returns:
        List of issues found
    """
    issues = []

    hook_type = get_hook_type_for_file(file_path, settings)
    if not hook_type:
        return issues  # Not a registered hook, skip

    usage = check_return_format_usage(content)

    if hook_type == "Stop":
        # Stop hooks should NOT use print_continue_and_log_skip
        if usage["print_continue_and_log_skip"]:
            lines = usage["print_continue_and_log_skip"]
            issues.append(
                {
                    "file": file_path,
                    "lines": lines,
                    "hook_type": hook_type,
                    "message": (
                        f"Stop hook uses `print_continue_and_log_skip` at line(s) {lines}. "
                        f'Stop hooks must return {{"decision": "approve"}}, not {{"continue": true}}. '
                        f"Use `print_approve_and_log_skip` instead."
                    ),
                    "severity": "error",
                }
            )

    elif hook_type == "PostToolUse":
        # PostToolUse hooks should typically use print_continue_and_log_skip
        # print_approve_and_log_skip is unusual but not strictly wrong
        if usage["print_approve_and_log_skip"]:
            lines = usage["print_approve_and_log_skip"]
            issues.append(
                {
                    "file": file_path,
                    "lines": lines,
                    "hook_type": hook_type,
                    "message": (
                        f"PostToolUse hook uses `print_approve_and_log_skip` at line(s) {lines}. "
                        f'PostToolUse hooks typically return {{"continue": true}}. '
                        f"Consider using `print_continue_and_log_skip` unless you have a specific reason."
                    ),
                    "severity": "warning",
                }
            )

    # PreToolUse and SessionStart can use either, so no check needed

    return issues


def main():
    """PreToolUse hook for Edit/Write commands.

    Checks hook files for return format function mismatches.

    このフック自身も PreToolUse フックとして登録されており、Edit/Write 以外のツールや
    対象外のファイルに対しては `print_continue_and_log_skip` を用いてスキップします。
    """
    result = {"decision": "approve"}

    try:
        data = parse_hook_input()
        # Issue #2607: Create context for session_id logging
        ctx = create_hook_context(data)
        tool_name = data.get("tool_name", "")
        tool_input = data.get("tool_input", {})

        # Only check Edit and Write operations
        if tool_name not in ("Edit", "Write"):
            print_continue_and_log_skip(
                "hook-return-format-check", f"not Edit/Write: {tool_name}", ctx=ctx
            )
            return

        file_path = tool_input.get("file_path", "")

        # Only check hook Python files
        if not file_path.endswith(".py"):
            print_continue_and_log_skip("hook-return-format-check", "not Python file", ctx=ctx)
            return

        if ".claude/hooks/" not in file_path:
            print_continue_and_log_skip(
                "hook-return-format-check", "not in .claude/hooks/", ctx=ctx
            )
            return

        # Skip test files
        if "/tests/" in file_path:
            print_continue_and_log_skip("hook-return-format-check", "test file", ctx=ctx)
            return

        # Get the new content
        if tool_name == "Write":
            content = tool_input.get("content", "")
        else:  # Edit
            old_string = tool_input.get("old_string", "")
            new_string = tool_input.get("new_string", "")

            try:
                current_content = Path(file_path).read_text()
            except FileNotFoundError:
                print_continue_and_log_skip("hook-return-format-check", "file not found", ctx=ctx)
                return

            content = current_content.replace(old_string, new_string, 1)

        # Load settings and analyze
        settings = load_settings()
        issues = analyze_hook_file(file_path, content, settings)

        if issues:
            warnings = []
            for issue in issues:
                prefix = "❌" if issue["severity"] == "error" else "⚠️"
                warnings.append(f"{prefix} {issue['file']}: {issue['message']}")

            result["systemMessage"] = (
                "Hook return format check:\n" + "\n".join(warnings) + "\n\n"
                "Reference:\n"
                '- Stop hooks: must return {"decision": "approve"} → use print_approve_and_log_skip\n'
                '- PostToolUse hooks: should return {"continue": true} → use print_continue_and_log_skip\n'
                "- PreToolUse hooks: either format is valid\n"
                "- SessionStart hooks: typically no return value needed"
            )
            log_hook_execution(
                "hook-return-format-check",
                "approve",
                f"Found {len(issues)} issue(s)",
                {"file": file_path, "issues": [i["message"] for i in issues]},
            )
        else:
            log_hook_execution("hook-return-format-check", "approve", None, {"file": file_path})

    except Exception as e:
        # Fail-open design: フックエラー時は操作を許可する
        # エラーを隠蔽するのではなく、stderrとログに記録した上で許可
        # これにより、フックの問題がユーザーの作業をブロックしない
        stack_trace = traceback.format_exc()
        error_msg = f"Hook error: {e}"
        print(f"[hook-return-format-check] {error_msg}\n{stack_trace}", file=sys.stderr)
        log_hook_execution(
            "hook-return-format-check",
            "approve",
            error_msg,
            {"traceback": stack_trace},
        )

    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
