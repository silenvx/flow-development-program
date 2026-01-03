#!/usr/bin/env python3
"""フック共通の定数を一元管理する。

Why:
    複数のフックで使用する定数を一箇所で管理し、循環インポートを防止する。

What:
    - タイムアウト定数（TIMEOUT_LIGHT/MEDIUM/HEAVY/EXTENDED/LONG）
    - ログローテーション設定（LOG_MAX_SIZE_BYTES, LOG_MAX_ROTATED_FILES）
    - セッションマーカー設定（SESSION_MARKER_FILE, SESSION_GAP_THRESHOLD）
    - ログレベル分離設定（ERROR_LOG_FILE等）

Remarks:
    - 全フックからimportされる基礎モジュール
    - 循環インポート防止のためフック固有ロジックは含めない
    - 定数追加時は関連フックへの影響を考慮

Changelog:
    - silenvx/dekita#559: タイムアウト定数を追加
    - silenvx/dekita#710: ログローテーション設定を追加
    - silenvx/dekita#729: CONTINUATION_HINTを追加
    - silenvx/dekita#1367: ログレベル分離設定を追加
    - silenvx/dekita#1436: SESSION_MARKER_FILEを追加
    - silenvx/dekita#1840: SESSION_LOG_DIRSを追加
"""

# Threshold in seconds for "recent" commits (1 hour)
RECENT_COMMIT_THRESHOLD_SECONDS = 3600

# Session gap threshold (seconds) - if last activity was more than this ago,
# treat it as a new session. Used by session marker mechanism.
SESSION_GAP_THRESHOLD = 3600  # 1 hour

# Timeout constants for subprocess calls (Issue #559)
# Standardized timeouts based on operation type
TIMEOUT_LIGHT = 5  # Light operations: git rev-parse, git status, git symbolic-ref
TIMEOUT_MEDIUM = 10  # Medium operations: gh api (single), git log, gh issue view
TIMEOUT_HEAVY = 30  # Heavy operations: gh api --paginate, GraphQL queries, lint
TIMEOUT_EXTENDED = 60  # Extended operations: batch processing, metrics collection
TIMEOUT_LONG = 180  # Long operations: AI review (Gemini/Codex), may take 2-3 minutes

# Log rotation settings (Issue #710)
LOG_MAX_SIZE_BYTES = 10 * 1024 * 1024  # 10MB - rotate when log exceeds this size
LOG_MAX_ROTATED_FILES = 5  # Keep 5 rotated files (log.1, log.2, ..., log.5)

# Continuation hint for block messages (Issue #729)
# This message reminds Claude to continue with alternative actions after a block
CONTINUATION_HINT = (
    "\n\n💡 ブロック後も作業を継続してください。\n"
    "代替アクションのツール呼び出しを行い、テキストのみの応答で終わらないでください。"
)

# Exploration depth threshold for bypassing research requirement
MIN_EXPLORATION_FOR_BYPASS = 5

# Session marker file name (Issue #1436)
# This file is created in worktrees to track which Claude session owns them.
# Used by: locked-worktree-guard.py, worktree-creation-marker.py,
#          worktree-session-guard.py, session-worktree-status.py
SESSION_MARKER_FILE = ".claude-session"

# =============================================================================
# Log Level Separation Settings (Issue #1367)
# =============================================================================

# Log level file names
ERROR_LOG_FILE = "hook-errors.log"
WARN_LOG_FILE = "hook-warnings.log"
DEBUG_LOG_FILE = "hook-debug.log"

# Error context settings
ERROR_CONTEXT_BUFFER_SIZE = 10  # Number of operations before error
ERROR_CONTEXT_AFTER_SIZE = 5  # Number of operations after error to capture
ERROR_CONTEXT_DIR = "error-context"
ERROR_CONTEXT_RETENTION_DAYS = 7  # Auto-delete context files older than this

# Log level mapping from decision values
# These are the decision values used in hooks that map to each log level
LOG_LEVEL_ERROR_DECISIONS = frozenset(["block", "error"])
LOG_LEVEL_WARN_DECISIONS = frozenset(["warn", "warning"])
LOG_LEVEL_DEBUG_DECISIONS = frozenset(["monitor_start", "monitor_complete", "info", "rebase"])
# All other decisions (approve, skip, track, success) are INFO level

# =============================================================================
# File Size Warning Thresholds (行数)
# =============================================================================

# AIがファイルを読み込む際、この閾値を超えるとリファクタリングを促す警告を表示
FILE_SIZE_THRESHOLD_TS = 400  # TypeScript/JavaScript
FILE_SIZE_THRESHOLD_PY = 500  # Python
FILE_SIZE_THRESHOLD_DEFAULT = 500  # その他

# =============================================================================
# Session Log Settings (Issue #1840)
# =============================================================================

# Directories containing session-specific log files
# These directories will be cleaned up by the unified cleanup mechanism
SESSION_LOG_DIRS = frozenset(
    [
        "flow",  # state-*.json, events-*.jsonl
        "flows",  # flow-progress-*.jsonl, worktree-integrity-*.jsonl
        "execution",  # api-operations-*.jsonl
        "metrics",  # review-quality-*.jsonl, codex-reviews-*.jsonl,
        # block-patterns-*.jsonl, behavior-anomalies-*.jsonl
        "reflections",  # session-reflections-*.jsonl
        "outcomes",  # session-outcomes-*.jsonl
    ]
)
