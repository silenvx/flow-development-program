#!/usr/bin/env python3
"""Claude Code hooksライブラリの統一エントリポイント。

Why:
    フックから共通機能への一貫したアクセスを提供し、
    サブモジュールの構成を隠蔽してAPIを安定化させる。

What:
    - 定数: タイムアウト、ログ設定、セッション設定
    - cwd: カレントディレクトリ検出・検証
    - git: ブランチ、コミット、差分操作
    - github: PR/Issue操作、ghコマンドパース
    - logging: ログレベル管理、エラーコンテキスト
    - strings: 文字列操作、コマンド分割

Remarks:
    - サブモジュールから直接インポートも可能（lib.cwd等）
    - 循環インポート防止のため依存順序に注意
    - __all__で公開APIを明示的に制御

Changelog:
    - silenvx/dekita#1337: command_utils分離で循環インポート解消
    - silenvx/dekita#1367: loggingサブモジュール追加
    - silenvx/dekita#1842: hook_input追加
    - silenvx/dekita#1914: path_validation追加

Example:
    from lib import get_effective_cwd, get_current_branch
    # or
    from lib.cwd import get_effective_cwd
    from lib.git import get_current_branch
"""

# Constants
# Command parsing utilities
from .command_parser import extract_worktree_add_path
from .constants import (
    CONTINUATION_HINT,
    DEBUG_LOG_FILE,
    ERROR_CONTEXT_BUFFER_SIZE,
    ERROR_CONTEXT_DIR,
    ERROR_CONTEXT_RETENTION_DAYS,
    ERROR_LOG_FILE,
    LOG_LEVEL_DEBUG_DECISIONS,
    LOG_LEVEL_ERROR_DECISIONS,
    LOG_LEVEL_WARN_DECISIONS,
    LOG_MAX_ROTATED_FILES,
    LOG_MAX_SIZE_BYTES,
    MIN_EXPLORATION_FOR_BYPASS,
    RECENT_COMMIT_THRESHOLD_SECONDS,
    SESSION_GAP_THRESHOLD,
    TIMEOUT_EXTENDED,
    TIMEOUT_HEAVY,
    TIMEOUT_LIGHT,
    TIMEOUT_LONG,
    TIMEOUT_MEDIUM,
    WARN_LOG_FILE,
)

# CWD utilities
from .cwd import (
    check_cwd_inside_path,
    extract_cd_target_from_command,
    get_effective_cwd,
)

# Git utilities
from .git import (
    check_recent_commits,
    check_uncommitted_changes,
    get_commits_since_default_branch,
    get_current_branch,
    get_default_branch,
    get_diff_hash,
    get_head_commit,
)

# GitHub CLI utilities
from .github import (
    extract_pr_number,
    get_pr_number_for_branch,
    is_pr_merged,
    parse_gh_pr_command,
)

# Hook input utilities (Issue #1842)
from .hook_input import get_tool_result

# Logging utilities (Issue #1367)
from .logging import (
    LOG_LEVEL_DEBUG,
    LOG_LEVEL_ERROR,
    LOG_LEVEL_INFO,
    LOG_LEVEL_WARN,
    ErrorContextManager,
    cleanup_old_context_files,
    get_error_context_manager,
    get_log_level,
    log_to_level_file,
)

# Path validation utilities (Issue #1914)
from .path_validation import is_safe_transcript_path

# String utilities
from .strings import (
    extract_inline_skip_env,
    is_skip_env_enabled,
    sanitize_branch_name,
    split_command_chain,
    strip_quoted_strings,
)

__all__ = [
    # Constants
    "CONTINUATION_HINT",
    "DEBUG_LOG_FILE",
    "ERROR_CONTEXT_BUFFER_SIZE",
    "ERROR_CONTEXT_DIR",
    "ERROR_CONTEXT_RETENTION_DAYS",
    "ERROR_LOG_FILE",
    "LOG_LEVEL_DEBUG_DECISIONS",
    "LOG_LEVEL_ERROR_DECISIONS",
    "LOG_LEVEL_WARN_DECISIONS",
    "LOG_MAX_ROTATED_FILES",
    "LOG_MAX_SIZE_BYTES",
    "MIN_EXPLORATION_FOR_BYPASS",
    "RECENT_COMMIT_THRESHOLD_SECONDS",
    "SESSION_GAP_THRESHOLD",
    "TIMEOUT_EXTENDED",
    "TIMEOUT_HEAVY",
    "TIMEOUT_LIGHT",
    "TIMEOUT_LONG",
    "TIMEOUT_MEDIUM",
    "WARN_LOG_FILE",
    # Logging utilities
    "LOG_LEVEL_DEBUG",
    "LOG_LEVEL_ERROR",
    "LOG_LEVEL_INFO",
    "LOG_LEVEL_WARN",
    "ErrorContextManager",
    "cleanup_old_context_files",
    "get_error_context_manager",
    "get_log_level",
    "log_to_level_file",
    # CWD
    "check_cwd_inside_path",
    "extract_cd_target_from_command",
    "get_effective_cwd",
    # Git
    "check_recent_commits",
    "check_uncommitted_changes",
    "get_commits_since_default_branch",
    "get_current_branch",
    "get_default_branch",
    "get_diff_hash",
    "get_head_commit",
    # GitHub
    "extract_pr_number",
    "get_pr_number_for_branch",
    "is_pr_merged",
    "parse_gh_pr_command",
    # Strings
    "extract_inline_skip_env",
    "is_skip_env_enabled",
    "sanitize_branch_name",
    "split_command_chain",
    "strip_quoted_strings",
    # Command parsing
    "extract_worktree_add_path",
    # Hook input utilities
    "get_tool_result",
    # Path validation utilities
    "is_safe_transcript_path",
]
