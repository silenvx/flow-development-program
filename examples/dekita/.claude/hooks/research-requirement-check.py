#!/usr/bin/env python3
"""Issue/PR作成前にWeb調査を強制。

Why:
    十分な調査なしにIssue/PRを作成すると、既存の解決策を見落としたり、
    同様のIssueが既に存在する可能性がある。事前調査を強制する。

What:
    - gh issue create / gh pr create コマンドを検出
    - セッション内のWebSearch/WebFetch履歴を確認
    - 調査なしの場合はブロック
    - バイパス条件: documentation/trivialラベル、十分な探索、本文に「調査不要」

Remarks:
    - ブロック型フック（PreToolUse:Bash）
    - research-trackerがWeb調査を記録、本フックが検証
    - コードベース探索（Grep/Glob 5回以上）もWeb調査の代替として許可

Changelog:
    - silenvx/dekita#xxx: フック追加
    - silenvx/dekita#1957: ラベル抽出をlib/labels.pyに共通化
    - silenvx/dekita#2578: heredocパターンを先にチェック（ネストされた引用符対応）
"""

import json
import re
import sys
from pathlib import Path

# Add hooks directory to path for common imports
sys.path.insert(0, str(Path(__file__).parent))

from common import check_research_done, get_exploration_depth
from lib.execution import log_hook_execution
from lib.labels import extract_labels_from_command, split_comma_separated_labels
from lib.results import make_approve_result, make_block_result
from lib.session import parse_hook_input
from lib.strings import strip_quoted_strings

HOOK_NAME = "research-requirement-check"

# Labels that bypass research requirement (simple tracking issues, chores, docs)
BYPASS_LABELS = {"documentation", "trivial"}

# Keywords in body that bypass research requirement
BYPASS_KEYWORDS = {"調査不要", "no research needed", "skip research", "調査済み"}


def is_gh_issue_create(command: str) -> bool:
    """Check if command is gh issue create."""
    stripped = strip_quoted_strings(command)
    return bool(re.search(r"\bgh\s+issue\s+create\b", stripped))


def is_gh_pr_create(command: str) -> bool:
    """Check if command is gh pr create."""
    stripped = strip_quoted_strings(command)
    return bool(re.search(r"\bgh\s+pr\s+create\b", stripped))


def extract_labels(command: str) -> set[str]:
    """Extract --label values from command.

    Handles:
    - Multiple --label flags: --label bug --label urgent
    - Comma-separated labels: --label "tracking,backend"
    - Comma-separated with spaces: --label "tracking, backend"
    - Single labels: --label tracking

    Issue #1957: Use shared shlex-based implementation from lib/labels.py.
    """
    # Use shared functions for robust label extraction
    raw_labels = extract_labels_from_command(command)
    split_labels = split_comma_separated_labels(raw_labels)
    # Convert to lowercase set (original behavior)
    return {label.lower() for label in split_labels}


def extract_body(command: str) -> str:
    """Extract --body value from command.

    Handles:
    - Heredoc style: --body "$(cat <<'EOF' ... EOF)" (checked first to handle nested quotes)
    - Double-quoted: --body "message" or -b "message"
    - Single-quoted: --body 'message' or -b 'message'
    - Escaped quotes within strings

    Issue #2578: Heredoc pattern must be checked FIRST because:
    - Heredoc content may contain nested quotes (e.g., code examples with --body "...")
    - Simple quote pattern would incorrectly match at the first nested quote
    - This caused "調査不要" keyword to not be detected when it appears after nested quotes
    """
    # Check for heredoc style FIRST: --body "$(cat <<'EOF' ... EOF)"
    # This handles nested quotes within heredoc content correctly
    match = re.search(r"--body\s+\"?\$\(cat\s+<<['\"]?EOF['\"]?\s*([\s\S]*?)EOF", command)
    if match:
        return match.group(1)
    # Match --body "..." (handles escaped quotes with \\")
    match = re.search(r'(?:--body|-b)\s+"((?:[^"\\]|\\.)*)"', command)
    if match:
        return match.group(1).replace('\\"', '"')
    # Match --body '...' (handles escaped quotes with \\')
    match = re.search(r"(?:--body|-b)\s+'((?:[^'\\]|\\.)*)'", command)
    if match:
        return match.group(1).replace("\\'", "'")
    return ""


def has_bypass_label(command: str) -> bool:
    """Check if command has a bypass label."""
    labels = extract_labels(command)
    return bool(labels & BYPASS_LABELS)


def has_bypass_keyword(command: str) -> bool:
    """Check if command body contains bypass keyword."""
    body = extract_body(command).lower()
    return any(keyword.lower() in body for keyword in BYPASS_KEYWORDS)


def main() -> None:
    """Main entry point for the hook."""
    try:
        input_data = parse_hook_input()
    except json.JSONDecodeError:
        # Invalid input - approve silently
        print(json.dumps({"decision": "approve"}))
        return

    tool_name = input_data.get("tool_name", "")

    # Only check Bash commands
    if tool_name != "Bash":
        print(json.dumps({"decision": "approve"}))
        return

    command = input_data.get("tool_input", {}).get("command", "")

    # Check if this is gh issue create or gh pr create
    is_issue = is_gh_issue_create(command)
    is_pr = is_gh_pr_create(command)

    if not (is_issue or is_pr):
        print(json.dumps({"decision": "approve"}))
        return

    action_type = "Issue" if is_issue else "PR"

    # Check bypass conditions
    if has_bypass_label(command):
        log_hook_execution(
            HOOK_NAME,
            "approve",
            reason=f"{action_type}作成を許可（バイパスラベル）",
            details={"command_preview": command[:80]},
        )
        result = make_approve_result(HOOK_NAME, f"{action_type}作成を許可（バイパスラベル）")
        print(json.dumps(result))
        return

    if has_bypass_keyword(command):
        log_hook_execution(
            HOOK_NAME,
            "approve",
            reason=f"{action_type}作成を許可（バイパスキーワード）",
            details={"command_preview": command[:80]},
        )
        result = make_approve_result(HOOK_NAME, f"{action_type}作成を許可（調査不要）")
        print(json.dumps(result))
        return

    # Check if research was done
    if check_research_done():
        log_hook_execution(
            HOOK_NAME,
            "approve",
            reason=f"{action_type}作成を許可（調査済み）",
            details={"command_preview": command[:80]},
        )
        result = make_approve_result(HOOK_NAME, f"{action_type}作成を許可（調査済み）")
        print(json.dumps(result))
        return

    # Check if exploration is sufficient (alternative to web research)
    exploration = get_exploration_depth()
    if exploration.get("sufficient", False):
        log_hook_execution(
            HOOK_NAME,
            "approve",
            reason=f"{action_type}作成を許可（十分な探索）",
            details={
                "command_preview": command[:80],
                "exploration_total": exploration.get("total", 0),
            },
        )
        result = make_approve_result(
            HOOK_NAME,
            f"{action_type}作成を許可（探索{exploration.get('total', 0)}回）",
        )
        print(json.dumps(result))
        return

    # Block: No research or exploration done
    log_hook_execution(
        HOOK_NAME,
        "block",
        reason=f"{action_type}作成をブロック（調査なし）",
        details={
            "command_preview": command[:80],
            "exploration_total": exploration.get("total", 0),
        },
    )

    block_message = f"""Web検索/情報収集が行われていません。

{action_type}作成前に、以下のツールで調査してください:

**推奨ツール:**
- Context7: 最新ライブラリドキュメントの取得
  → mcp__context7__resolve-library-id, mcp__context7__get-library-docs
- WebSearch: 最新情報・ベストプラクティスの検索
- Grep/Glob: 既存コードベースのパターン調査（現在 {exploration.get("total", 0)}/5回）
- Task(Explore): コードベース構造の理解

**調査不要の場合:**
- --label documentation または --label trivial を付与
- 本文に「調査不要」を含める"""

    result = make_block_result(HOOK_NAME, block_message)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
