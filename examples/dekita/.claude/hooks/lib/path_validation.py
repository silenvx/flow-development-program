#!/usr/bin/env python3
"""パストラバーサル防止のためのパス検証ユーティリティを提供する。

Why:
    外部入力（transcript path等）のパストラバーサル攻撃を防止し、
    許可されたディレクトリのみへのアクセスを保証する。

What:
    - is_safe_transcript_path(): transcriptパスの安全性検証

Remarks:
    - 許可ディレクトリ: ホーム、システムtemp、cwd
    - シンボリックリンクは解決後に検証
    - Claude Code内部生成パスでも防御的に検証

Changelog:
    - silenvx/dekita#1914: セキュリティレビュー対応で追加
"""

import os
import tempfile
from pathlib import Path


def is_safe_transcript_path(path_str: str) -> bool:
    """Validate that a transcript path is safe to read.

    Checks that the path:
    1. Is not empty
    2. Is absolute or can be safely resolved
    3. After resolution, is within allowed directories:
       - User's home directory
       - System temp directories
       - Current working directory

    Args:
        path_str: The path string to validate.

    Returns:
        True if the path is safe, False otherwise.

    Note:
        This function is designed for validating transcript paths provided
        by Claude Code's hook system. In practice, these paths are generated
        by Claude Code itself, but validation is good defensive practice.
    """
    if not path_str or not path_str.strip():
        return False

    try:
        # Resolve to absolute path, following symlinks
        resolved = Path(path_str).resolve()

        # Get allowed directories
        allowed_dirs = _get_allowed_directories()

        # Check if resolved path is under any allowed directory
        return any(_is_path_under(resolved, allowed_dir) for allowed_dir in allowed_dirs)
    except (OSError, ValueError):
        # Path resolution failed (e.g., invalid characters)
        return False


def _get_allowed_directories() -> list[Path]:
    """Get list of allowed directories for transcript files.

    Returns:
        List of Path objects representing allowed directories.
    """
    allowed = []

    # User's home directory
    home = Path.home()
    if home.exists():
        allowed.append(home)

    # System temp directories
    tmpdir = os.environ.get("TMPDIR")
    if tmpdir:
        tmp_path = Path(tmpdir)
        if tmp_path.exists():
            allowed.append(tmp_path)

    # System temp directory (cross-platform)
    system_tmp = Path(tempfile.gettempdir())
    if system_tmp.exists() and system_tmp not in allowed:
        allowed.append(system_tmp)

    # Current working directory (for relative paths)
    try:
        cwd = Path.cwd()
        if cwd.exists():
            allowed.append(cwd)
    except OSError:
        pass  # CWD may not exist in edge cases

    return allowed


def _is_path_under(path: Path, directory: Path) -> bool:
    """Check if path is under directory (handles symlinks).

    Args:
        path: The resolved path to check.
        directory: The directory to check against.

    Returns:
        True if path is under directory, False otherwise.
    """
    try:
        resolved_dir = directory.resolve()
        # Use is_relative_to for clean comparison (Python 3.9+)
        return path.is_relative_to(resolved_dir)
    except (OSError, ValueError):
        return False
