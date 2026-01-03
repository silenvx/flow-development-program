#!/usr/bin/env python3
"""settings.json内のフックファイル参照を検証する。

Why:
    削除されたフックへの参照やtypoを検出し、
    実行時エラーを事前に防ぐため。

What:
    - extract_hook_paths(): settings.jsonからフックパスを抽出
    - validate_paths(): ファイル存在を確認

State:
    - reads: .claude/settings.json
    - reads: .claude/hooks/*.py

Remarks:
    - Exit 0: 全参照が有効、Exit 1: 欠落ファイル検出
    - Claude Codeは設定をキャッシュするため、削除後もエラーが継続する問題を防止

Changelog:
    - silenvx/dekita#1300: フック設定検証機能を追加
"""

import json
import re
import sys
from pathlib import Path


def extract_hook_paths(settings: dict, project_dir: Path) -> list[tuple[str, Path]]:
    """Extract all hook file paths from settings.json.

    Args:
        settings: Dictionary with hooks configuration, typically loaded from settings.json.
        project_dir: Path to the project root directory.

    Returns:
        list of (raw_command, resolved_path) tuples.
    """
    hook_paths: list[tuple[str, Path]] = []
    hooks_config = settings.get("hooks", {})

    for hook_type in ["PreToolUse", "PostToolUse", "Stop"]:
        hook_list = hooks_config.get(hook_type, [])
        for hook_group in hook_list:
            for hook in hook_group.get("hooks", []):
                if hook.get("type") == "command":
                    command = hook.get("command", "")
                    # Extract file path from command
                    # Pattern: python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/xxx.py
                    # or: python3 .claude/hooks/xxx.py
                    # Use non-greedy pattern to stop at .py (not capturing args)
                    match = re.search(
                        r'python3\s+"?\$CLAUDE_PROJECT_DIR"?/([^\s"\']+\.py)', command
                    )
                    if match:
                        relative_path = match.group(1)
                        full_path = project_dir / relative_path
                        hook_paths.append((command, full_path))
                    else:
                        # Fallback: handle direct paths without $CLAUDE_PROJECT_DIR
                        # e.g., "python3 .claude/hooks/xxx.py" or absolute paths
                        # Currently unused but kept for future compatibility
                        match = re.search(r'python3\s+"?([^\s"\']+\.py)', command)
                        if match:
                            path_str = match.group(1)
                            if path_str.startswith("$CLAUDE_PROJECT_DIR"):
                                path_str = path_str.replace("$CLAUDE_PROJECT_DIR", str(project_dir))
                            full_path = Path(path_str)
                            if not full_path.is_absolute():
                                full_path = project_dir / path_str
                            hook_paths.append((command, full_path))

    return hook_paths


def main() -> int:
    # Find project directory (where settings.json is located)
    script_dir = Path(__file__).parent
    project_dir = script_dir.parent.parent  # .claude/scripts -> .claude -> project root

    settings_path = project_dir / ".claude" / "settings.json"

    if not settings_path.exists():
        print(f"⚠️  No settings.json found at {settings_path}")
        return 0  # Not an error - settings might not exist

    try:
        with open(settings_path) as f:
            settings = json.load(f)
    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON in settings.json: {e}")
        return 1

    hook_paths = extract_hook_paths(settings, project_dir)

    if not hook_paths:
        print("✅ No hook file references found in settings.json")
        return 0

    missing_files: list[tuple[str, Path]] = []
    for command, path in hook_paths:
        if not path.exists():
            missing_files.append((command, path))

    if missing_files:
        print("❌ Missing hook files detected!")
        print("")
        print("The following hook references in settings.json point to non-existent files:")
        print("")
        for command, path in missing_files:
            print(f"  File: {path}")
            print(f"  Command: {command}")
            print("")
        print("To fix:")
        print("  1. Remove the reference from .claude/settings.json, OR")
        print("  2. Create the missing file")
        print("")
        print("Note: After fixing, you may need to restart Claude Code session")
        print("      to clear the cached hook settings.")
        return 1

    print(f"✅ All {len(hook_paths)} hook file references are valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
