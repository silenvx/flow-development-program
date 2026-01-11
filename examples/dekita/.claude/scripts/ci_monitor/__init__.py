"""ci-monitor package.

A CI monitoring tool for PRs with auto-rebase and review detection.
Extracted from ci-monitor.py as part of Issue #1754 refactoring.

This package contains all extracted modules:
- models: Enum and dataclass definitions
- constants: Configuration constants
- github_api: Low-level GitHub CLI commands
- state: Monitor state persistence
- rate_limit: Rate limit checking and interval adjustment
- events: Event emission and logging
- worktree: Git worktree management
- ai_review: AI reviewer detection and review management
- review_comments: Review comment fetching, classification, and thread management
- pr_operations: PR validation, state, rebasing, merging, and recreation
- monitor: Core monitoring functions (check_once, monitor_notify_only, monitor_multiple_prs)
- session: Session ID management (Issue #2624)
- main_loop: Main monitoring loop (monitor_pr function) (Issue #2624)

The legacy ci-monitor.py is now a thin wrapper that imports from this package.
"""

# Issue #2624: Import submodules for test compatibility
# This allows tests to patch functions via ci_monitor.submodule.function
from ci_monitor import (
    ai_review,
    constants,
    events,
    github_api,
    main_loop,
    models,
    monitor,
    pr_operations,
    rate_limit,
    review_comments,
    session,
    state,
    worktree,
)
from ci_monitor.ai_review import (
    COPILOT_ERROR_PATTERNS,
    GEMINI_RATE_LIMIT_PATTERNS,
    check_and_report_contradictions,
    get_codex_review_requests,
    get_codex_reviews,
    get_copilot_reviews,
    get_gemini_reviews,
    has_copilot_or_codex_reviewer,
    has_gemini_reviewer,
    is_ai_reviewer,
    is_copilot_review_error,
    is_gemini_rate_limited,
    is_gemini_review_pending,
    request_copilot_review,
)
from ci_monitor.constants import (
    AI_REVIEWER_IDENTIFIERS,
    ASYNC_REVIEWER_CHECK_DELAY_SECONDS,
    COPILOT_CODEX_IDENTIFIERS,
    COPILOT_REVIEWER_LOGIN,
    DEFAULT_COPILOT_PENDING_TIMEOUT,
    DEFAULT_GEMINI_PENDING_TIMEOUT,
    DEFAULT_LOCAL_CHANGES_MAX_WAIT,
    DEFAULT_LOCAL_CHANGES_WAIT_INTERVAL,
    DEFAULT_MAX_COPILOT_RETRY,
    DEFAULT_MAX_MERGE_ATTEMPTS,
    DEFAULT_MAX_PR_RECREATE,
    DEFAULT_MAX_REBASE,
    DEFAULT_MAX_RETRY_WAIT_POLLS,
    DEFAULT_POLLING_INTERVAL,
    DEFAULT_TIMEOUT_MINUTES,
    GEMINI_REVIEWER_LOGIN,
    GITHUB_FILES_LIMIT,
)
from ci_monitor.events import (
    create_event,
    emit_event,
    log,
)
from ci_monitor.github_api import (
    get_repo_info,
    is_rate_limit_error,
    run_gh_command,
    run_gh_command_with_error,
    run_graphql_with_fallback,
)
from ci_monitor.main_loop import (
    log_rate_limit_warning,
    monitor_pr,
)
from ci_monitor.models import (
    CheckStatus,
    ClassifiedComments,
    CodexReviewRequest,
    EventType,
    IntervalDirection,
    MergeState,
    MonitorEvent,
    MonitorResult,
    MultiPREvent,
    PRState,
    RateLimitEventType,
    RebaseResult,
    RetryWaitStatus,
    has_unresolved_threads,
)
from ci_monitor.monitor import (
    check_once,
    check_self_reference,
    get_issue_incomplete_criteria,
    get_observation_issues,
    get_pr_closes_issues,
    get_wait_time_suggestions,
    log_ci_monitor_event,
    monitor_multiple_prs,
    monitor_notify_only,
    show_wait_time_hint,
)
from ci_monitor.pr_operations import (
    format_rebase_summary,
    get_main_last_commit_time,
    get_pr_branch_name,
    get_pr_state,
    has_ai_review_pending,
    has_local_changes,
    is_codex_review_pending,
    merge_pr,
    rebase_pr,
    recreate_pr,
    reopen_pr_with_retry,
    sync_local_after_rebase,
    validate_pr_number,
    validate_pr_numbers,
    wait_for_main_stable,
)
from ci_monitor.rate_limit import (
    check_rate_limit,
    format_reset_time,
    get_adjusted_interval,
    get_rate_limit_reset_time,
    log_rate_limit_event,
    log_rate_limit_warning_to_console,
    print_rate_limit_warning,
    should_prefer_rest_api,
)
from ci_monitor.review_comments import (
    auto_resolve_duplicate_threads,
    classify_review_comments,
    convert_rest_comments_to_thread_format,
    fetch_all_review_threads,
    fetch_review_comments_rest,
    filter_duplicate_comments,
    get_pr_changed_files,
    get_resolved_thread_hashes,
    get_review_comments,
    get_unresolved_ai_threads,
    get_unresolved_threads,
    log_review_comments_to_quality_log,
    normalize_comment_body,
    print_comment,
    resolve_thread_by_id,
    strip_code_blocks,
)

# Issue #2624: New modules for refactoring
from ci_monitor.session import (
    get_session_id,
    set_session_id,
)
from ci_monitor.state import (
    clear_monitor_state,
    get_state_file_path,
    load_monitor_state,
    save_monitor_state,
)
from ci_monitor.worktree import (
    cleanup_worktree_after_merge,
    get_worktree_info,
)

__all__ = [
    # Enums
    "EventType",
    "CheckStatus",
    "MergeState",
    "RetryWaitStatus",
    "RateLimitEventType",
    "IntervalDirection",
    # Dataclasses
    "PRState",
    "MonitorEvent",
    "MonitorResult",
    "ClassifiedComments",
    "RebaseResult",
    "CodexReviewRequest",
    "MultiPREvent",
    # Helper functions
    "has_unresolved_threads",
    # Constants
    "AI_REVIEWER_IDENTIFIERS",
    "ASYNC_REVIEWER_CHECK_DELAY_SECONDS",
    "COPILOT_CODEX_IDENTIFIERS",
    "DEFAULT_COPILOT_PENDING_TIMEOUT",
    "DEFAULT_LOCAL_CHANGES_MAX_WAIT",
    "DEFAULT_LOCAL_CHANGES_WAIT_INTERVAL",
    "DEFAULT_MAX_COPILOT_RETRY",
    "DEFAULT_MAX_MERGE_ATTEMPTS",
    "DEFAULT_MAX_PR_RECREATE",
    "DEFAULT_MAX_REBASE",
    "DEFAULT_MAX_RETRY_WAIT_POLLS",
    "DEFAULT_POLLING_INTERVAL",
    "DEFAULT_TIMEOUT_MINUTES",
    "GITHUB_FILES_LIMIT",
    # GitHub API
    "run_gh_command",
    "run_gh_command_with_error",
    "is_rate_limit_error",
    "run_graphql_with_fallback",
    "get_repo_info",
    # State management
    "get_state_file_path",
    "save_monitor_state",
    "load_monitor_state",
    "clear_monitor_state",
    # Rate limit
    "check_rate_limit",
    "format_reset_time",
    "get_adjusted_interval",
    "get_rate_limit_reset_time",
    "log_rate_limit_warning_to_console",
    "print_rate_limit_warning",
    "should_prefer_rest_api",
    # Events
    "create_event",
    "emit_event",
    "log",
    # Worktree
    "get_worktree_info",
    "cleanup_worktree_after_merge",
    # AI Review
    "is_ai_reviewer",
    "has_copilot_or_codex_reviewer",
    "get_codex_review_requests",
    "get_codex_reviews",
    "get_copilot_reviews",
    "is_copilot_review_error",
    "request_copilot_review",
    "check_and_report_contradictions",
    "COPILOT_ERROR_PATTERNS",
    "COPILOT_REVIEWER_LOGIN",
    # Gemini (Issue #2711)
    "GEMINI_RATE_LIMIT_PATTERNS",
    "GEMINI_REVIEWER_LOGIN",
    "DEFAULT_GEMINI_PENDING_TIMEOUT",
    "get_gemini_reviews",
    "has_gemini_reviewer",
    "is_gemini_rate_limited",
    "is_gemini_review_pending",
    # Review Comments
    "strip_code_blocks",
    "get_review_comments",
    "get_pr_changed_files",
    "classify_review_comments",
    "print_comment",
    "fetch_review_comments_rest",
    "convert_rest_comments_to_thread_format",
    "fetch_all_review_threads",
    "get_unresolved_threads",
    "get_unresolved_ai_threads",
    "normalize_comment_body",
    "get_resolved_thread_hashes",
    "resolve_thread_by_id",
    "auto_resolve_duplicate_threads",
    "filter_duplicate_comments",
    "log_review_comments_to_quality_log",
    # PR Operations
    "validate_pr_number",
    "validate_pr_numbers",
    "get_pr_state",
    "has_local_changes",
    "get_main_last_commit_time",
    "wait_for_main_stable",
    "rebase_pr",
    "merge_pr",
    "get_pr_branch_name",
    "format_rebase_summary",
    "sync_local_after_rebase",
    "reopen_pr_with_retry",
    "recreate_pr",
    "is_codex_review_pending",
    "has_ai_review_pending",
    # Monitor
    "check_once",
    "check_self_reference",
    "get_issue_incomplete_criteria",
    "get_observation_issues",
    "get_pr_closes_issues",
    "get_wait_time_suggestions",
    "log_ci_monitor_event",
    "monitor_multiple_prs",
    "monitor_notify_only",
    "show_wait_time_hint",
    # Session (Issue #2624)
    "get_session_id",
    "set_session_id",
    # Main Loop (Issue #2624)
    "log_rate_limit_event",
    "log_rate_limit_warning",
    "monitor_pr",
    # Submodules (Issue #2624 - for test compatibility)
    "ai_review",
    "constants",
    "events",
    "github_api",
    "main_loop",
    "models",
    "monitor",
    "pr_operations",
    "rate_limit",
    "review_comments",
    "session",
    "state",
    "worktree",
]
