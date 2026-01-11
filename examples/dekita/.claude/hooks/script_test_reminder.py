#!/usr/bin/env python3
"""PostToolUse hook: Remind to add tests when new functions are added to scripts.

Issue #1247: When editing .claude/scripts/*.py files and adding new function
definitions, this hook reminds the developer to add corresponding tests
if a test file already exists.
"""

# - Single responsibility: Remind to add tests when new functions are added
# - Uses systemMessage (non-blocking) as this is a reminder, not enforcement
# - Does not overlap with existing hooks

import json
import re
from pathlib import Path

from lib.execution import log_hook_execution
from lib.results import print_continue_and_log_skip
from lib.session import create_hook_context, parse_hook_input


def detect_new_functions(old_string: str, new_string: str) -> list[str]:
    """Detect newly added function definitions (including async functions).

    Args:
        old_string: Original code content
        new_string: New code content after edit

    Returns:
        List of newly added function names
    """
    # Match both 'def foo(' and 'async def foo('
    pattern = r"^(?:async\s+)?def\s+(\w+)\s*\("
    old_funcs = set(re.findall(pattern, old_string, re.MULTILINE))
    new_funcs = set(re.findall(pattern, new_string, re.MULTILINE))
    return sorted(new_funcs - old_funcs)


def find_test_file(script_path: str) -> Path | None:
    """Find corresponding test file for a script.

    Args:
        script_path: Path to the script file

    Returns:
        Path to test file if it exists, None otherwise
    """
    path = Path(script_path)
    # Convert hyphens to underscores for test file naming convention
    normalized_name = path.stem.replace("-", "_")
    test_file = path.parent / "tests" / f"test_{normalized_name}.py"
    return test_file if test_file.exists() else None


def main() -> None:
    """Main entry point for the hook."""
    result: dict = {"continue": True}

    try:
        input_data = parse_hook_input()
        # Issue #2607: Create context for session_id logging
        ctx = create_hook_context(input_data)
        tool_input = input_data.get("tool_input", {})

        file_path = tool_input.get("file_path", "")

        # Only target .claude/scripts/*.py files
        if ".claude/scripts/" not in file_path or not file_path.endswith(".py"):
            print_continue_and_log_skip("script-test-reminder", "not a script file", ctx=ctx)
            return

        # Exclude files in tests directory
        if "/tests/" in file_path:
            print_continue_and_log_skip("script-test-reminder", "test file excluded", ctx=ctx)
            return

        old_string = tool_input.get("old_string", "")
        new_string = tool_input.get("new_string", "")

        # Detect new function definitions
        new_functions = detect_new_functions(old_string, new_string)

        if not new_functions:
            print_continue_and_log_skip(
                "script-test-reminder", "no new functions detected", ctx=ctx
            )
            return

        # Check if corresponding test file exists
        test_file = find_test_file(file_path)

        if test_file:
            func_list = ", ".join(new_functions)
            result["systemMessage"] = (
                f"💡 テストファイルが存在します: {test_file}\n"
                f"   新しい関数を追加した場合、テストも追加してください。\n"
                f"   追加された関数: {func_list}"
            )
            log_hook_execution(
                "script-test-reminder",
                "remind",
                f"New functions: {func_list}",
                {"file": file_path, "test_file": str(test_file), "functions": new_functions},
            )
    except Exception:
        # Never fail the hook - just skip reminder
        pass

    print(json.dumps(result))


if __name__ == "__main__":
    main()
