#!/usr/bin/env python3
"""PostToolUseフック用の入力ユーティリティを提供する。

Why:
    PostToolUseフックでツール実行結果へのアクセスを標準化し、
    フィールド名の不一致（tool_result/tool_response/tool_output）を吸収する。

What:
    - get_exit_code(): tool_resultからexit_codeを一貫した方法で取得
    - get_tool_result(): 複数フィールド名に対応したツール結果取得

Remarks:
    - exit_codeのデフォルトは0（成功）
    - フィールド優先順: tool_result > tool_response > tool_output
    - Noneの明示的な設定は尊重（フォールバックしない）

Changelog:
    - silenvx/dekita#1842: tool_result/tool_response/tool_outputパターン統一
    - silenvx/dekita#2203: exit_codeデフォルト値の統一
"""

from __future__ import annotations

from typing import Any


def get_exit_code(tool_result: dict | Any | None, *, default: int = 0) -> int:
    """Get exit code from tool result with consistent default.

    Issue #2203: Unified function to avoid inconsistent default values
    across hooks. Previously some hooks used default=0 (success) while
    others used default=1 (failure), causing merge detection failures.

    The default is 0 (success) because:
    - Claude Code typically only omits exit_code for successful commands
    - This matches api-operation-logger and flow-progress-tracker behavior
    - Conservative default=1 caused post-merge-reflection-enforcer to fail

    Args:
        tool_result: The tool result dictionary (or None/other types)
        default: Override default value if needed (default: 0)

    Returns:
        The exit code as integer (0 typically means success)
    """
    if not isinstance(tool_result, dict):
        return default
    return tool_result.get("exit_code", default)


def get_tool_result(hook_input: dict) -> dict | str | Any | None:
    """Get tool execution result from PostToolUse hook input.

    Checks fields in order of priority:
    1. tool_result (standard field)
    2. tool_response (Claude Code docs mention this)
    3. tool_output (fallback for compatibility)

    Note: The key presence takes priority. If "tool_result" key exists but value is None,
    it returns None (not fallback to tool_response). This preserves explicit null intent.

    Args:
        hook_input: The hook input dictionary from PostToolUse hook.

    Returns:
        The tool result. Most commonly:
        - dict: Structured result with fields like exit_code, stdout, stderr
        - str: Simple text output
        - None: If no result field is present, or if explicit None value
        - Any: Claude Code API may return other types; callers should handle gracefully
    """
    if "tool_result" in hook_input:
        return hook_input.get("tool_result")
    elif "tool_response" in hook_input:
        return hook_input.get("tool_response")
    else:
        return hook_input.get("tool_output")
