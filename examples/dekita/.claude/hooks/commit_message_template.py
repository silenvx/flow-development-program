#!/usr/bin/env python3
"""git commit時にコミットメッセージテンプレートを挿入する。

Why:
    「なぜ」の背景情報が欠けたコミットメッセージが多い。テンプレートで
    Why/What/Refsの構造を提示することで、背景を書く習慣を促す。

What:
    - git commit（-mオプションなし）時にテンプレートを挿入
    - 既にユーザーコンテンツがある場合はスキップ
    - merge/squash/amend等ではスキップ

Remarks:
    - lefthook経由でprepare-commit-msgとして実行
    - Claude Codeフックではなく、Gitフック
    - -m/-F/--amend等オプション時は発火しない
    - commit-message-why-check.pyとセットで使用

Changelog:
    - silenvx/dekita#1535: フック追加
"""

from __future__ import annotations

import sys
from pathlib import Path

# Template prompting for background context (Japanese, simple)
TEMPLATE = """\
# なぜ: この変更が必要な理由
#

# 何を: 変更内容（箇条書き推奨）
#

# 参照: 関連Issue/PR
# Fixes #
"""


def should_skip_template(source: str | None) -> bool:
    """Check if template insertion should be skipped.

    Args:
        source: The source of the commit message from Git.
                - "message": -m or -F option was given
                - "template": -t option or commit.template config
                - "merge": merge commit
                - "squash": squash commit
                - "commit": amend commit (-c, -C, --amend)

    Returns:
        True if template should be skipped, False otherwise.
    """
    skip_sources = {"message", "template", "merge", "squash", "commit"}
    return source in skip_sources if source else False


def has_user_content(content: str) -> bool:
    """Check if the content has user-written message (not just comments).

    Args:
        content: The current content of the commit message file.

    Returns:
        True if there's user content (non-comment, non-whitespace lines).
    """
    for line in content.split("\n"):
        stripped = line.strip()
        # Skip empty lines and comment lines
        if stripped and not stripped.startswith("#"):
            return True
    return False


def get_template() -> str:
    """Get the commit message template.

    Returns:
        The template string with sections for Why/What/Refs.
    """
    return TEMPLATE


def insert_template(filepath: str) -> None:
    """Insert template into the commit message file if appropriate.

    Args:
        filepath: Path to the commit message file (COMMIT_EDITMSG).
    """
    path = Path(filepath)

    # Read existing content with proper encoding
    try:
        current_content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        current_content = ""
    except OSError:
        # Permission error or other OS error - fail silently
        return

    # Don't insert if user already has content
    if has_user_content(current_content):
        return

    # Insert template at the beginning, preserve existing content (Git comments)
    new_content = get_template() + "\n" + current_content

    try:
        path.write_text(new_content, encoding="utf-8")
    except OSError:
        # Disk full, permission error, etc. - fail silently
        # Git will still proceed with the original message
        pass


def main() -> int:
    """Main entry point for the hook.

    Returns:
        Exit code (0 for success).
    """
    if len(sys.argv) < 2:
        # No arguments - nothing to do
        return 0

    msg_file = sys.argv[1]
    source = sys.argv[2] if len(sys.argv) > 2 else None

    if should_skip_template(source):
        return 0

    insert_template(msg_file)
    return 0


if __name__ == "__main__":
    sys.exit(main())
