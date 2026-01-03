"""Constants for ci-monitor.

This module contains all configuration constants used by ci-monitor.
Extracted from ci-monitor.py as part of Issue #1754 refactoring.
"""

import re

# Default configuration constants
DEFAULT_MAX_REBASE = 3
DEFAULT_POLLING_INTERVAL = 30
DEFAULT_TIMEOUT_MINUTES = 20
DEFAULT_MAX_COPILOT_RETRY = 3
DEFAULT_MAX_RETRY_WAIT_POLLS = 4
DEFAULT_COPILOT_PENDING_TIMEOUT = 300
DEFAULT_MAX_PR_RECREATE = 1
DEFAULT_MAX_MERGE_ATTEMPTS = 3

# Local changes wait configuration (Issue #1307)
DEFAULT_LOCAL_CHANGES_MAX_WAIT = 5
DEFAULT_LOCAL_CHANGES_WAIT_INTERVAL = 60

# Wait strategy for main branch stability (Issue #1239)
DEFAULT_STABLE_WAIT_MINUTES = 5
DEFAULT_STABLE_CHECK_INTERVAL = 30
DEFAULT_STABLE_WAIT_TIMEOUT = 30

# Merge error constants
MERGE_ERROR_BEHIND = "BEHIND"

# Rate limit thresholds (Issue #896)
RATE_LIMIT_WARNING_THRESHOLD = 100
RATE_LIMIT_CRITICAL_THRESHOLD = 50
RATE_LIMIT_ADJUST_THRESHOLD = 500
RATE_LIMIT_EXHAUSTED = 0
RATE_LIMIT_REST_PRIORITY_THRESHOLD = 200

# Rate limit cache configuration (Issue #1347, #1291)
RATE_LIMIT_CACHE_TTL = 60  # Cache TTL in seconds

# Async reviewer check delay
ASYNC_REVIEWER_CHECK_DELAY_SECONDS = 5

# GitHub API pagination limit for PR files
GITHUB_FILES_LIMIT = 100

# Pattern to match fenced code blocks and inline code
# Used to strip code content before checkbox detection to avoid false positives
CODE_BLOCK_PATTERN = re.compile(
    r"```[\s\S]*?```"
    r"|"
    r"`[^`\n]+`",
    re.MULTILINE,
)

# Known AI reviewer identifiers (Issue #1109)
AI_REVIEWER_IDENTIFIERS = ["copilot", "codex", "openai", "chatgpt"]

# Copilot reviewer login name for API requests
COPILOT_REVIEWER_LOGIN = "copilot-pull-request-reviewer[bot]"
