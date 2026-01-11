#!/usr/bin/env python3
"""ci_monitor.pyのsession-idオプション指定を検出する。

Why:
    session-idが指定されないと、ci_monitor.pyはppidベースのセッション特定に
    フォールバックし、ログがClaude Codeセッションと正しく紐付かなくなる。

What:
    - ci_monitor.py呼び出しを検出
    - --session-idオプションの有無を確認
    - 未指定の場合は警告メッセージを表示

Remarks:
    - 警告型フック（ブロックしない、警告のみ）
    - PreToolUse:Bashで発火
    - AGENTS.mdにsession-id指定が必須と記載されている

Changelog:
    - silenvx/dekita#2389: フック追加
"""

import json
import re
import sys

from lib.execution import log_hook_execution
from lib.results import make_approve_result
from lib.session import parse_hook_input


def main():
    """Entry point for the CI monitor session ID check hook."""
    try:
        input_json = parse_hook_input()
        tool_input = input_json.get("tool_input") or {}
        command = tool_input.get("command") or ""

        # Check if this is a ci_monitor.py call
        if not re.search(r"ci_monitor\.py", command):
            # Not a ci_monitor.py call, approve
            result = make_approve_result("ci-monitor-session-id-check")
            print(json.dumps(result))
            return

        # Check if --session-id is provided (supports both space and = forms)
        # --session-id abc123 or --session-id=abc123 (multiple spaces allowed)
        # Note: =\S+ requires value immediately after =, \s+\S+ allows spaces before value
        if re.search(r"--session-id(?:=\S+|\s+\S+)", command):
            # --session-id is provided, approve
            result = make_approve_result("ci-monitor-session-id-check")
            log_hook_execution("ci-monitor-session-id-check", "approve", "--session-id provided")
            print(json.dumps(result))
            return

        # ci_monitor.py called without --session-id - warn
        warning = (
            "[ci-monitor-session-id-check] 警告: --session-id が指定されていません\n\n"
            "ログが正しいセッションと紐付かなくなります。\n"
            "--session-id を追加してください:\n\n"
            "  python3 .claude/scripts/ci_monitor.py {PR} --session-id <SESSION_ID>\n\n"
            "※ <SESSION_ID>はUserPromptSubmit hookで提供されるセッションID\n"
            "  例: 3f03a042-a9ef-44a2-839a-d17badc44b0a"
        )
        result = make_approve_result("ci-monitor-session-id-check", warning)
        log_hook_execution(
            "ci-monitor-session-id-check",
            "approve_with_warning",
            "--session-id missing",
            {"command": command[:100]},
        )
        print(json.dumps(result))

    except Exception as e:
        # On error, approve to avoid blocking legitimate commands
        print(f"[ci-monitor-session-id-check] Hook error: {e}", file=sys.stderr)
        result = make_approve_result("ci-monitor-session-id-check", f"Hook error: {e}")
        log_hook_execution("ci-monitor-session-id-check", "approve", f"Hook error: {e}")
        print(json.dumps(result))


if __name__ == "__main__":
    main()
