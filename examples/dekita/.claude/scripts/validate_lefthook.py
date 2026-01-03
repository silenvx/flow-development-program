#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""lefthook.yml設定を検証する。

Why:
    lefthook設定の誤りを事前に検出し、
    pre-pushでの{staged_files}使用等のミスを防ぐため。

What:
    - validate(): 設定を検証
    - check_pre_push_staged_files(): pre-pushでの{staged_files}使用を検出

Remarks:
    - LEFTHOOK001: pre-pushで{staged_files}は無意味
    - ファイル指定なしで./lefthook.ymlをチェック

Changelog:
    - silenvx/dekita#1100: lefthook設定検証機能を追加
"""

import sys
from pathlib import Path
from typing import NamedTuple

import yaml


class LintError(NamedTuple):
    """Represents a lint error."""

    file: str
    line: int
    code: str
    message: str


def find_line_number(content: str, search_text: str) -> int:
    """Find the line number of a text in content."""
    lines = content.split("\n")
    for i, line in enumerate(lines, 1):
        if search_text in line:
            return i
    return 0


def check_staged_files_in_pre_push(config: dict, content: str, filepath: str) -> list[LintError]:
    """Check that pre-push commands don't use {staged_files}."""
    errors = []

    pre_push = config.get("pre-push", {})
    commands = pre_push.get("commands", {})

    for cmd_name, cmd_config in commands.items():
        if not isinstance(cmd_config, dict):
            continue

        run_cmd = cmd_config.get("run", "")
        if "{staged_files}" in run_cmd:
            line = find_line_number(content, run_cmd[:50])
            errors.append(
                LintError(
                    file=filepath,
                    line=line,
                    code="LEFTHOOK001",
                    message=f"pre-push command '{cmd_name}' uses {{staged_files}} which is meaningless. "
                    "In pre-push context, there are no staged files. "
                    "Consider using {{push_files}} or removing the variable.",
                )
            )

    return errors


def lint_lefthook(filepath: Path) -> list[LintError]:
    """Lint lefthook.yml file."""
    try:
        content = filepath.read_text()
    except OSError as e:
        return [
            LintError(
                file=str(filepath),
                line=0,
                code="LEFTHOOK000",
                message=f"Failed to read file: {e}",
            )
        ]

    try:
        config = yaml.safe_load(content)
    except yaml.YAMLError as e:
        return [
            LintError(
                file=str(filepath),
                line=0,
                code="LEFTHOOK000",
                message=f"YAML parse error: {e}",
            )
        ]

    if not config:
        return []

    errors = []
    errors.extend(check_staged_files_in_pre_push(config, content, str(filepath)))

    return errors


def main() -> int:
    """Main entry point."""
    if len(sys.argv) > 1:
        filepath = Path(sys.argv[1])
    else:
        filepath = Path("lefthook.yml")

    if not filepath.exists():
        print(f"File not found: {filepath}", file=sys.stderr)
        return 1

    errors = lint_lefthook(filepath)

    for error in errors:
        print(f"{error.file}:{error.line}: [{error.code}] {error.message}")

    if errors:
        print(f"\nFound {len(errors)} error(s)", file=sys.stderr)
        return 1

    print(f"Checked {filepath}, no errors found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
