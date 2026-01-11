#!/usr/bin/env python3
"""フック環境の整合性をチェックする。

Why:
    settings.jsonに登録されたフックファイルが存在しないと、
    他のフックが期待通りに動作しない。セッション開始時に
    不足を検出し、早期に問題を認識させる。

What:
    - settings.jsonから登録済みスクリプトパスを抽出
    - 各ファイルの存在を確認
    - 不足ファイルがあれば復旧手順を含む警告を表示

Remarks:
    - ブロックはせず警告のみ（他の作業は継続可能）
"""

import json
import re
import sys
from pathlib import Path

# Add hooks directory to path for imports
HOOKS_DIR = Path(__file__).parent
sys.path.insert(0, str(HOOKS_DIR))

from lib.execution import log_hook_execution
from lib.session import parse_hook_input

PROJECT_DIR = HOOKS_DIR.parent.parent
SETTINGS_FILE = HOOKS_DIR.parent / "settings.json"


def get_registered_scripts() -> list[str]:
    """Extract all script paths from settings.json (relative to project root)."""
    if not SETTINGS_FILE.exists():
        return []

    try:
        with open(SETTINGS_FILE) as f:
            settings = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []

    hooks = settings.get("hooks", {})
    script_paths: list[str] = []

    # Pattern to match paths after $CLAUDE_PROJECT_DIR (quoted or unquoted)
    # Matches: "$CLAUDE_PROJECT_DIR"/path or $CLAUDE_PROJECT_DIR/path
    pattern = re.compile(
        r'"\$CLAUDE_PROJECT_DIR"/?([^\s"]+)|(?<!["])\$CLAUDE_PROJECT_DIR/?([^\s"]+)'
    )

    for hook_type in hooks.values():
        if isinstance(hook_type, list):
            for entry in hook_type:
                for hook in entry.get("hooks", []):
                    cmd = hook.get("command", "")
                    if not cmd:
                        continue

                    # Find all script paths in the command
                    for match in pattern.finditer(cmd):
                        path = match.group(1) or match.group(2)
                        if path:
                            # Clean up the path (remove trailing quotes, etc.)
                            path = path.strip('"').strip()
                            # Only include actual script files (not directories)
                            if path.endswith((".py", ".sh")):
                                script_paths.append(path)

    return list(set(script_paths))


def check_script_files() -> tuple[list[str], list[str]]:
    """Check which script files exist and which are missing."""
    registered = get_registered_scripts()
    missing: list[str] = []
    found: list[str] = []

    for script_path in registered:
        full_path = PROJECT_DIR / script_path
        if full_path.exists():
            found.append(script_path)
        else:
            missing.append(script_path)

    return found, missing


def main() -> None:
    """Check environment integrity and warn about missing files."""
    # セッションIDの取得のためparse_hook_inputを呼び出す
    parse_hook_input()

    found, missing = check_script_files()

    if missing:
        log_hook_execution(
            "environment-integrity-check",
            "warn",
            f"Missing hook files detected: {len(missing)} files",
            {"missing_files": missing},
        )
        warning = f"""[environment-integrity-check] フック環境に問題があります

**不足ファイル** ({len(missing)}件):
{chr(10).join(f"  - {f}" for f in sorted(missing))}

**復旧方法**:
```bash
# メインリポジトリを最新に同期
git checkout main
git pull origin main

# または不足ファイルを復元
git restore .claude/hooks/ scripts/
```

**原因**: 別セッションで追加されたフックがマージされていない可能性があります。
"""
        print(warning, file=sys.stderr)
    else:
        log_hook_execution(
            "environment-integrity-check",
            "approve",
            f"All {len(found)} hook files present",
        )

    # Always continue (don't block) - just warn
    print(json.dumps({"continue": True}))


if __name__ == "__main__":
    main()
