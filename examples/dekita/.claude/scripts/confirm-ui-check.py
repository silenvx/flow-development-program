#!/usr/bin/env python3
"""UI確認完了を記録しコミットをアンブロックする。

Why:
    UI変更時のブラウザ確認完了を記録し、
    commit-msg-checkerのブロックを解除するため。

What:
    - main(): 確認完了マーカーファイルを作成

State:
    - writes: .claude/logs/markers/ui-check-{branch}.done

Remarks:
    - feature branchでのみ実行可能
    - main/masterでは警告して終了

Changelog:
    - silenvx/dekita#1050: UI確認完了記録機能を追加
"""

import sys
from pathlib import Path

# Import from hooks directory
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))
from common import MARKERS_LOG_DIR, get_current_branch, sanitize_branch_name


def main():
    """Confirm UI verification was completed for current branch."""
    branch = get_current_branch()

    if branch is None:
        print("Error: Could not determine current branch.", file=sys.stderr)
        print("Make sure you are in a git repository.", file=sys.stderr)
        sys.exit(1)

    if branch in ("main", "master"):
        print(f"Warning: Currently on {branch} branch.", file=sys.stderr)
        print("UI verification confirmation is only needed for feature branches.", file=sys.stderr)
        sys.exit(1)

    # Create log directory if needed
    MARKERS_LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Create confirmation file
    safe_branch = sanitize_branch_name(branch)
    log_file = MARKERS_LOG_DIR / f"ui-check-{safe_branch}.done"
    log_file.write_text(branch)

    print(f"UI verification confirmed for branch: {branch}")
    print(f"Confirmation file: {log_file}")
    print()
    print("You can now commit your locale file changes.")


if __name__ == "__main__":
    main()
