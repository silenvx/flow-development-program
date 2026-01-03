#!/usr/bin/env python3
"""Bash失敗時にドキュメント参照の古さを検出して警告する。

Why:
    削除されたスクリプトがドキュメントに参照として残っていると、
    そのドキュメントを信じて実行したコマンドが失敗する。早期検知が必要。

What:
    - Bashコマンド失敗時にトランスクリプトを分析
    - 最近読み込んだ.mdファイルで失敗コマンドを検索
    - ドキュメントに記載されていた場合は古い可能性を警告

Remarks:
    - 警告型フック（ブロックしない、systemMessageで警告）
    - PostToolUse:Bashで発火（exit_code != 0時のみ）
    - "No such file or directory"エラー時のみ動作
    - .claude/scripts/, .claude/hooks/のパターンを検索

Changelog:
    - silenvx/dekita#2213: 発端となった問題
    - silenvx/dekita#2220: フック追加
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from lib.execution import log_hook_execution
from lib.hook_input import get_tool_result
from lib.session import parse_hook_input


def read_transcript(transcript_path: str) -> list[dict]:
    """Read and parse the JSONL transcript file."""
    entries = []
    try:
        path = Path(transcript_path)
        if not path.exists():
            return entries
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except Exception:
        pass  # File read errors are non-fatal; return empty list
    return entries


def extract_read_md_files(transcript: list[dict]) -> list[str]:
    """Extract .md file paths from Read tool calls in transcript."""
    md_files = []
    for entry in transcript:
        # Look for tool_use entries with Read tool
        if entry.get("type") == "tool_use" and entry.get("name") == "Read":
            tool_input = entry.get("input", {})
            file_path = tool_input.get("file_path", "")
            if file_path.endswith(".md"):
                md_files.append(file_path)
    return md_files


def extract_command_pattern(command: str) -> str | None:
    """Extract a searchable pattern from the failed command.

    Focus on script paths like:
    - .claude/scripts/xxx.py
    - .claude/hooks/xxx.py
    - scripts/xxx.sh
    """
    # Match .claude/scripts/*.py or .claude/scripts/*.sh
    match = re.search(r"\.claude/scripts/[\w-]+\.(py|sh)", command)
    if match:
        return match.group(0)

    # Match .claude/hooks/*.py
    match = re.search(r"\.claude/hooks/[\w-]+\.py", command)
    if match:
        return match.group(0)

    # Match scripts/*.sh at root
    match = re.search(r"scripts/[\w-]+\.sh", command)
    if match:
        return match.group(0)

    # Match any python3 .claude/... path
    match = re.search(r"\.claude/[\w/\-]+\.(py|sh)", command)
    if match:
        return match.group(0)

    return None


def search_pattern_in_file(file_path: str, pattern: str) -> list[int]:
    """Search for pattern in file and return matching line numbers."""
    matching_lines = []
    try:
        path = Path(file_path)
        if not path.exists():
            return matching_lines
        with open(path, encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                if pattern in line:
                    matching_lines.append(line_num)
    except Exception:
        pass  # File read errors are non-fatal; return empty list
    return matching_lines


def get_project_root() -> Path:
    """Get project root from CLAUDE_PROJECT_DIR or fallback."""
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if project_dir:
        return Path(project_dir)
    # Fallback to script's parent directories
    return Path(__file__).parent.parent.parent.resolve()


def main():
    """PostToolUse hook for Bash commands.

    Detects when failed commands are referenced in recently read documentation.
    """
    result = {"continue": True}

    try:
        input_data = parse_hook_input()
        tool_result = get_tool_result(input_data) or {}
        tool_input = input_data.get("tool_input", {})

        exit_code = tool_result.get("exit_code", 0)
        command = tool_input.get("command", "")
        stderr = tool_result.get("stderr", "")

        # Only process failed commands
        if exit_code == 0:
            log_hook_execution("doc-reference-warning", "approve")
            print(json.dumps(result))
            return

        # Check for "No such file or directory" type errors
        if "No such file or directory" not in stderr and "not found" not in stderr.lower():
            log_hook_execution("doc-reference-warning", "approve")
            print(json.dumps(result))
            return

        # Extract searchable pattern from command
        pattern = extract_command_pattern(command)
        if not pattern:
            log_hook_execution("doc-reference-warning", "approve")
            print(json.dumps(result))
            return

        # Read transcript
        transcript_path = input_data.get("transcript_path", "")
        if not transcript_path:
            log_hook_execution("doc-reference-warning", "approve")
            print(json.dumps(result))
            return

        transcript = read_transcript(transcript_path)
        if not transcript:
            log_hook_execution("doc-reference-warning", "approve")
            print(json.dumps(result))
            return

        # Extract recently read .md files
        md_files = extract_read_md_files(transcript)
        if not md_files:
            log_hook_execution("doc-reference-warning", "approve")
            print(json.dumps(result))
            return

        # Search for the pattern in read .md files
        project_root = get_project_root()
        found_references = []

        for md_file in md_files:
            # Make path relative for display
            md_path = Path(md_file)
            matching_lines = search_pattern_in_file(md_file, pattern)
            if matching_lines:
                try:
                    display_path = md_path.relative_to(project_root)
                except ValueError:
                    display_path = md_path
                for line_num in matching_lines:
                    found_references.append(f"  - {display_path}:{line_num}")

        if found_references:
            # Deduplicate references
            unique_refs = list(dict.fromkeys(found_references))
            message = (
                f"[doc-reference-warning] ドキュメント参照の確認が必要かもしれません\n\n"
                f"失敗したコマンド内のパス `{pattern}` は以下のドキュメントに記載されています:\n"
                + "\n".join(unique_refs[:5])  # Limit to 5 references
                + "\n\nドキュメントが古い可能性があります。確認・修正を検討してください。"
            )
            result["systemMessage"] = message
            log_hook_execution(
                "doc-reference-warning",
                "approve",
                reason="outdated_doc_reference_detected",
                details={"pattern": pattern, "references": unique_refs[:5]},
            )
        else:
            log_hook_execution("doc-reference-warning", "approve")

    except Exception:
        # Don't block Claude Code on hook failure
        log_hook_execution("doc-reference-warning", "approve", reason="hook_error")

    print(json.dumps(result))


if __name__ == "__main__":
    main()
