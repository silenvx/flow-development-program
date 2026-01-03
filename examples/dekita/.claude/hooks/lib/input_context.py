#!/usr/bin/env python3
"""フック入力からのコンテキスト抽出ユーティリティを提供する。

Why:
    ログ・分析のため、フック入力から一貫した形式で
    コンテキスト情報（ツール名、プレビュー等）を抽出する。

What:
    - extract_input_context(): フック入力からコンテキストを抽出
    - merge_details_with_context(): detailsとコンテキストをマージ

Remarks:
    - hook_typeはtool_name/stop_hook_active等から自動推論
    - 入力プレビューは80文字で切り詰め
    - 空入力時はhook_type="Unknown"（SessionStartと区別）

Changelog:
    - silenvx/dekita#1312: hook_typeを常に設定するよう修正
    - silenvx/dekita#1758: common.pyから分離
"""

from typing import Any


def extract_input_context(input_data: dict[str, Any], max_preview_len: int = 80) -> dict[str, Any]:
    """Extract input context from hook input for logging.

    Extracts tool name and a preview of the input to help analyze
    which operations triggered the hook.

    Args:
        input_data: The JSON input received by the hook from stdin.
        max_preview_len: Maximum length for command/input preview.

    Returns:
        A dict with extracted context:
        - tool_name: Name of the tool (e.g., "Bash", "Edit", "Write")
        - input_preview: Truncated preview of the input
        - hook_type: Inferred hook type ("PreToolUse", "PostToolUse", "Stop", etc.)

    Example:
        >>> data = {"tool_name": "Bash", "tool_input": {"command": "gh pr create"}}
        >>> extract_input_context(data)  # doctest: +SKIP
        {'tool_name': 'Bash', 'input_preview': 'gh pr create', 'hook_type': 'PreToolUse'}
    """
    context: dict[str, Any] = {}

    # Extract tool name
    tool_name = input_data.get("tool_name")
    if tool_name:
        context["tool_name"] = tool_name

    # Extract input preview based on tool type
    tool_input = input_data.get("tool_input", {})
    if isinstance(tool_input, dict):
        # Bash command
        if "command" in tool_input:
            cmd = tool_input["command"]
            context["input_preview"] = cmd[:max_preview_len] + (
                "..." if len(cmd) > max_preview_len else ""
            )
        # Edit/Write file path
        elif "file_path" in tool_input:
            context["input_preview"] = tool_input["file_path"]
        # Read file path
        elif "path" in tool_input:
            context["input_preview"] = tool_input["path"]
        # Generic: try to get first string value
        else:
            for _key, value in tool_input.items():
                if isinstance(value, str) and value:
                    preview = value[:max_preview_len]
                    context["input_preview"] = preview + (
                        "..." if len(value) > max_preview_len else ""
                    )
                    break

    # Infer hook type
    # Issue #1312: Ensure hook_type is always set for proper logging
    if tool_name:
        # Has tool_name = PreToolUse or PostToolUse
        if "tool_output" in input_data:
            context["hook_type"] = "PostToolUse"
        else:
            context["hook_type"] = "PreToolUse"
    elif "stop_hook_active" in input_data:
        context["hook_type"] = "Stop"
    elif "notification" in input_data:
        context["hook_type"] = "Notification"
    elif not input_data:
        # Empty input (e.g., from JSON decode error) - use Unknown
        # parse_hook_input() returns {} on decode errors, which should not
        # be conflated with a real SessionStart event
        context["hook_type"] = "Unknown"
    else:
        # Has some data but no tool/stop/notification indicators = SessionStart
        # Real SessionStart events have at least session_id or other context
        context["hook_type"] = "SessionStart"

    return context


def merge_details_with_context(
    details: dict[str, Any] | None,
    input_context: dict[str, Any],
) -> dict[str, Any]:
    """Merge existing details with input context for logging.

    Combines hook-specific details with extracted input context,
    ensuring input context is always included in logs.

    Args:
        details: Existing details dict from the hook (may be None).
        input_context: Context extracted via extract_input_context().

    Returns:
        Merged dict with both details and input context.

    Note:
        If both details and input_context contain the same key,
        the value from details takes precedence.
    """
    result = dict(input_context)
    if details:
        result.update(details)
    return result
