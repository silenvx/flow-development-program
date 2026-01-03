#!/usr/bin/env python3
"""仕様ドキュメント編集時に関連コード・Issueの確認を促す。

Why:
    ドキュメントを根拠なしに編集すると、誤記や実装との矛盾が生じる。
    「状態確認ファースト原則」をドキュメント編集にも適用する。

What:
    - .claude/skills/, AGENTS.md等の編集を検出
    - 関連コード・Issue確認を促すメッセージを表示
    - セッション内で同一ファイルへの重複警告を防止

State:
    - writes: {TMPDIR}/claude-hooks/doc-edit-confirmed-{session}.json

Remarks:
    - 警告型フック（ブロックしない、systemMessageで警告）
    - PreToolUse:Edit/Writeで発火
    - セッション内で同一ファイルへの重複警告防止
    - .claude/skills/, AGENTS.md等を対象

Changelog:
    - silenvx/dekita#1848: フック追加
"""

import json
import os
import sys
import tempfile
from pathlib import Path

from lib.execution import log_hook_execution
from lib.session import HookContext, create_hook_context, parse_hook_input

# Target path prefixes for specification documents (with .md extension check)
TARGET_PREFIXES = [
    ".claude/skills/",
    ".claude/prompts/",
]

# Exact match files
TARGET_EXACT = [
    "AGENTS.md",
]

# Hook name for logging
HOOK_NAME = "doc-edit-check"


def get_project_root() -> str:
    """Get project root directory."""
    return os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())


def get_confirmation_file_path(ctx: HookContext) -> Path:
    """Get path to session confirmation tracking file.

    Uses a temp file named with session ID to persist state across hook invocations.
    Uses claude-hooks subdirectory for consistency with other hooks.

    Args:
        ctx: HookContext for session information.

    Returns:
        Path to the session confirmation file.
    """
    session_id = ctx.get_session_id()
    # Use claude-hooks subdirectory in temp directory for session-scoped persistence
    base_dir = Path(tempfile.gettempdir()) / "claude-hooks"
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / f"doc-edit-confirmed-{session_id}.json"


def load_confirmed_files(ctx: HookContext) -> set[str]:
    """Load confirmed files from session file.

    Args:
        ctx: HookContext for session information.

    Returns:
        Set of confirmed file paths.
    """
    try:
        conf_file = get_confirmation_file_path(ctx)
        if conf_file.exists():
            data = json.loads(conf_file.read_text())
            return set(data.get("files", []))
    except (OSError, json.JSONDecodeError):
        # File doesn't exist or is corrupted - treat as empty
        pass
    return set()


def save_confirmed_files(ctx: HookContext, files: set[str]) -> None:
    """Save confirmed files to session file.

    Args:
        ctx: HookContext for session information.
        files: Set of confirmed file paths.
    """
    try:
        conf_file = get_confirmation_file_path(ctx)
        conf_file.write_text(json.dumps({"files": list(files)}))
    except OSError:
        pass  # Best effort - don't fail on I/O errors


def get_relative_path(file_path: str) -> str | None:
    """Get relative path from project root.

    Uses Path.relative_to() for safe path comparison to avoid false matches
    (e.g., /project matching /project-other).

    Args:
        file_path: Absolute file path.

    Returns:
        Relative path from project root, or None if outside project.
    """
    project_root_path = Path(get_project_root()).resolve()
    file_path_obj = Path(file_path).resolve()
    try:
        relative = file_path_obj.relative_to(project_root_path)
    except ValueError:
        # file is outside of project_root
        return None
    return str(relative)


def matches_target_pattern(file_path: str) -> bool:
    """Check if file path matches any target pattern.

    Args:
        file_path: Absolute file path to check.

    Returns:
        True if the file matches a target pattern.
    """
    rel_path = get_relative_path(file_path)
    if rel_path is None:
        return False

    # Check exact matches first
    if rel_path in TARGET_EXACT:
        return True

    # Check prefix matches (must be .md files)
    if not rel_path.endswith(".md"):
        return False

    for prefix in TARGET_PREFIXES:
        if rel_path.startswith(prefix):
            return True

    return False


def is_confirmed_in_session(ctx: HookContext, file_path: str) -> bool:
    """Check if file has been confirmed in current session.

    Args:
        ctx: HookContext for session information.
        file_path: Absolute file path to check (normalized with resolve()).

    Returns:
        True if already confirmed in this session.
    """
    # Normalize path to match how mark_as_confirmed stores paths
    normalized_path = str(Path(file_path).resolve())
    confirmed = load_confirmed_files(ctx)
    return normalized_path in confirmed


def mark_as_confirmed(ctx: HookContext, file_path: str) -> None:
    """Mark file as confirmed in current session.

    Note:
        This function has a potential race condition if multiple hook instances
        run concurrently. However, Claude Code executes hooks sequentially,
        so this is not a concern in practice.

    Args:
        ctx: HookContext for session information.
        file_path: Absolute file path to mark (normalized with resolve()).
    """
    # Normalize path to handle symlinks and relative paths consistently
    normalized_path = str(Path(file_path).resolve())
    confirmed = load_confirmed_files(ctx)
    confirmed.add(normalized_path)
    save_confirmed_files(ctx, confirmed)


def main():
    """Check if editing specification documents and warn about verification."""
    result = {"decision": "approve"}
    file_path = ""

    try:
        data = parse_hook_input()

        ctx = create_hook_context(data)
        tool_input = data.get("tool_input", {})
        file_path = tool_input.get("file_path", "")

        if not file_path:
            # No file path provided, skip
            result["systemMessage"] = "✅ doc-edit-check: パス未指定（スキップ）"
        elif not matches_target_pattern(file_path):
            # Not a target document, skip
            pass
        elif is_confirmed_in_session(ctx, file_path):
            # Already confirmed in this session
            result["systemMessage"] = "✅ doc-edit-check: セッション内で確認済み"
        else:
            # First edit to a spec document - show warning
            rel_path = get_relative_path(file_path) or file_path

            result["systemMessage"] = f"""⚠️ 仕様ドキュメント編集の確認 ({rel_path})

このファイルで言及するコード/仕様の根拠を確認しましたか？

**確認すべき項目:**
- 関連コード: Grep/Read で実装を確認
- 関連Issue: gh issue list --search "キーワード"
- 既存ドキュメント: 類似の記載がないか確認

💡 根拠を確認してから編集すると、誤記や矛盾を防げます。"""

            # Mark as confirmed for this session
            mark_as_confirmed(ctx, file_path)

    except Exception as e:
        # Don't block on errors
        print(f"[{HOOK_NAME}] Hook error: {e}", file=sys.stderr)
        result = {"decision": "approve", "reason": f"Hook error: {e}"}

    log_hook_execution(
        HOOK_NAME,
        result.get("decision", "approve"),
        result.get("systemMessage"),
        {"file_path": file_path} if file_path else None,
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
