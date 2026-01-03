#!/usr/bin/env python3
"""フック設計のSRP遵守と品質チェックを行う。

Why:
    責務が重複するフックが増えると保守性が低下する。新規フック追加時に
    設計チェックを強制し、責務分離を維持する。また、ブロックメッセージに
    対処法セクションがないとユーザーが対処方法を把握できない。

What:
    - フック削除時にセッション再起動が必要な可能性を警告
    - make_block_resultに対処法セクションがない場合に警告
    - log_hook_execution()呼び出しの欠如を検出

Remarks:
    - python-lint-checkはコードスタイル、本フックは設計品質を確認
    - ci-wait-check.pyとci-monitor.pyの責務重複問題を防止

Changelog:
    - silenvx/dekita#193: フック削除時の警告追加
    - silenvx/dekita#1111: 対処法セクション必須化
    - silenvx/dekita#2589: log_hook_execution呼び出しチェック追加
    - silenvx/dekita#2621: Design reviewedコメントチェック機能を削除
"""

import ast
import json
import re
import subprocess
import sys

from lib.constants import TIMEOUT_MEDIUM
from lib.execution import log_hook_execution
from lib.results import make_block_result
from lib.session import parse_hook_input

LOG_EXECUTION_MISSING_MSG = """
⚠️ log_hook_execution() の呼び出しがありません (Issue #2589)

以下のファイルで log_hook_execution() が使用されていません:
{files}

## 対処法

フックのmain()関数内で log_hook_execution() を呼び出してください。

**例**:
```python
from lib.execution import log_hook_execution

def main():
    # ... 処理 ...
    log_hook_execution("hook-name", "approve", "reason")
    # または
    log_hook_execution("hook-name", "block", "reason")
```

**なぜ必要か**:
- セッションメトリクスでフック実行を追跡するため
- フックの動作確認・デバッグに必要
- collect-session-metrics.py で hooks_triggered として記録される
"""

# Keywords that indicate remediation instructions in block messages (Issue #1111)
# If a block message contains any of these, it's considered to have remediation
REMEDIATION_KEYWORDS = [
    "【対処法】",
    "【解決方法】",
    "【回避方法】",
    "対処法:",
    "解決方法:",
    "回避方法:",
    "## 対処法",
    "## 解決方法",
    "## 回避方法",
]

HOOK_DELETION_WARNING = """
⚠️ フックファイルの削除が検出されました。

## 重要な警告 (Issue #193)

セッション中にフックファイルを削除すると、セッション終了時に
Stopフックがそのファイルを見つけられずエラーループに陥ります。

## 正しい手順

1. **このセッションを終了**: /exit または Ctrl+C で終了
2. **新しいセッションで作業**: claude コマンドで新規セッション開始
3. **新しいセッションでフックを削除**: rm や git rm を実行

## 理由

- settings.json のフック設定はセッション開始時に読み込まれる
- セッション中の削除は設定に反映されない
- 結果、存在しないファイルへの参照が残りエラーになる

今すぐフック削除が必要な場合は、上記の手順に従ってください。
"""

SRP_CHECKLIST_WARNING = """
⚠️ 新しいフックファイルが追加されています。設計原則を確認してください。

## 単一責任の原則（SRP）チェックリスト

1. **このフックの責務は1つだけか？**
   - 1つのフックは1つの責務のみを持つ
   - 複数の責務を混ぜない

2. **既存フックと責務が重複していないか？**
   - 既存フックの一覧: .claude/hooks/*.py
   - 重複がある場合は既存フックを拡張するか、責務を再整理

3. **「推奨」ではなく「ブロック」で強制しているか？**
   - systemMessage（推奨）は無視される可能性がある
   - decision: "block" で強制する

4. **AGENTS.mdに責務を明記したか？**
   - 「設定済みのフック」セクションに追加
   - 責務と動作を簡潔に説明
"""

REMEDIATION_MISSING_WARNING = """
⚠️ make_block_result() に対処法セクションがありません (Issue #1111)

以下のファイルでブロックメッセージに対処法が見つかりませんでした:
{files}

## 推奨アクション

ブロック時のエラーメッセージには「対処法」セクションを含めてください。

**例**:
```python
reason = (
    "マージできません: CIが失敗しています\\n\\n"
    "【対処法】\\n"
    "1. CIログを確認: gh run list\\n"
    "2. 失敗を修正してプッシュ\\n"
    "3. CIが成功したら再度マージを試行"
)
make_block_result("hook-name", reason)
```

**認識されるキーワード**: 【対処法】, 【解決方法】, 【回避方法】, または ## 対処法 など

**補足**: これは警告のみでブロックしません。既存コードへの影響を避けるため。
"""


class BlockResultVisitor(ast.NodeVisitor):
    """AST visitor to find make_block_result calls and check for remediation."""

    def __init__(self):
        self.issues: list[tuple[int, str]] = []  # (line_number, reason_excerpt)

    def visit_Call(self, node: ast.Call) -> None:
        """Visit function call nodes."""
        # Check if this is a make_block_result call
        func_name = ""
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr

        if func_name == "make_block_result" and len(node.args) >= 2:
            # Get the reason argument (second positional arg)
            reason_node = node.args[1]
            reason_str = self._extract_string_value(reason_node)

            if reason_str and not self._has_remediation(reason_str):
                # Get first 50 chars of reason for context
                excerpt = (
                    reason_str[:50].replace("\n", " ") + "..."
                    if len(reason_str) > 50
                    else reason_str.replace("\n", " ")
                )
                self.issues.append((node.lineno, excerpt))

        self.generic_visit(node)

    def _extract_string_value(self, node: ast.expr) -> str | None:
        """Extract string value from AST node (handles constants, f-strings, concatenation)."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        elif isinstance(node, ast.JoinedStr):
            # f-string: Issue #1125 - 動的部分があればNoneを返す（誤検知防止）
            for value in node.values:
                if isinstance(value, ast.FormattedValue):
                    # 動的部分があるので静的解析不可
                    return None
            # 完全に静的なf-stringのみ抽出
            parts = []
            for value in node.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    parts.append(value.value)
            return "".join(parts) if parts else None
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            # String concatenation: "a" + "b" or "a" + variable
            left = self._extract_string_value(node.left)
            right = self._extract_string_value(node.right)
            # Issue #1128: 片方でも静的に解析できなければ連結結果も不明
            if left is None or right is None:
                return None
            return left + right
        elif isinstance(node, ast.Name):
            # Variable reference - can't statically analyze, assume it's OK
            return None
        return None

    def _has_remediation(self, text: str) -> bool:
        """Check if text contains remediation keywords."""
        return any(keyword in text for keyword in REMEDIATION_KEYWORDS)


class LogExecutionVisitor(ast.NodeVisitor):
    """AST visitor to check if log_hook_execution is called."""

    def __init__(self):
        self.has_call = False

    def visit_Call(self, node: ast.Call) -> None:
        """Visit function call nodes."""
        func_name = ""
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr

        if func_name == "log_hook_execution":
            self.has_call = True

        self.generic_visit(node)


def check_log_execution_usage(hook_files: list[str]) -> list[str]:
    """
    Check if hook files use log_hook_execution.

    Args:
        hook_files: List of hook file paths to check

    Returns:
        List of file paths that don't use log_hook_execution
    """
    missing_files = []

    for filepath in hook_files:
        try:
            # Read file content from staging area
            result = subprocess.run(
                ["git", "show", f":{filepath}"],
                capture_output=True,
                text=True,
                timeout=TIMEOUT_MEDIUM,
            )
            if result.returncode != 0:
                continue

            content = result.stdout

            # Parse and visit AST
            try:
                tree = ast.parse(content, filename=filepath)
                visitor = LogExecutionVisitor()
                visitor.visit(tree)

                if not visitor.has_call:
                    missing_files.append(filepath)
            except SyntaxError:
                # Skip files with syntax errors (will be caught by other checks)
                continue

        except Exception:
            continue

    return missing_files


def get_staged_modified_hooks() -> list[str]:
    """Get list of modified hook files in staging area."""
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-status", "--diff-filter=M"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode != 0:
            return []

        modified_hooks = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) >= 2:
                filepath = parts[1]
                if (
                    filepath.startswith(".claude/hooks/")
                    and filepath.endswith(".py")
                    and "/tests/" not in filepath
                    and "/lib/" not in filepath
                    and "/scripts/" not in filepath
                ):
                    modified_hooks.append(filepath)
        return modified_hooks
    except Exception:
        return []


def check_remediation_in_hooks(hook_files: list[str]) -> list[tuple[str, list[tuple[int, str]]]]:
    """
    Check for missing remediation sections in make_block_result calls.

    Args:
        hook_files: List of hook file paths to check

    Returns:
        List of (filepath, [(line_number, reason_excerpt), ...]) for files with issues
    """
    results = []

    for filepath in hook_files:
        try:
            # Read file content from staging area
            result = subprocess.run(
                ["git", "show", f":{filepath}"],
                capture_output=True,
                text=True,
                timeout=TIMEOUT_MEDIUM,
            )
            if result.returncode != 0:
                continue

            content = result.stdout

            # Parse and visit AST
            try:
                tree = ast.parse(content, filename=filepath)
                visitor = BlockResultVisitor()
                visitor.visit(tree)

                if visitor.issues:
                    results.append((filepath, visitor.issues))
            except SyntaxError:
                # Skip files with syntax errors (will be caught by other checks)
                continue

        except Exception:
            continue

    return results


def check_hook_deletion(command: str) -> list[str]:
    """
    Check if command is attempting to delete hook files.

    Returns list of hook files being deleted, or empty list if none.

    Handles edge cases (Issue #198):
    - Quoted paths: rm ".claude/hooks/foo.py" or rm '.claude/hooks/foo.py'
    - Non-standard flag positions: rm file.py -f (flags after path)
    - Various rm variants: rm, rm -f, rm -rf, git rm, git rm -f
    """
    deleted_hooks = []

    # First check if this is a rm command (rm or git rm)
    # This handles flags in any position: rm -f file, rm file -f, etc.
    # Also handles shell operators without space: &&rm, ;rm, ||rm, |rm, (rm
    if not re.search(r"(?:^|\s|&&|\|\||[;|(])(?:git\s+)?rm(?:\s|$)", command):
        return deleted_hooks

    # Extract hook file paths with proper quote matching (Issue #363)
    # Three separate patterns to ensure matching quotes:
    # - Unquoted: no quotes on either side (uses negative lookbehind/lookahead)
    # - Single-quoted: same single quote on both sides
    # - Double-quoted: same double quote on both sides
    # This rejects mismatched quotes like 'path" or "path'
    #
    # Regex assertions explained:
    # - (?<!['\"]): Negative lookbehind - ensures no quote immediately before
    # - (?!['\"]): Negative lookahead - ensures no quote immediately after
    hook_file_patterns = [
        r"(?<!['\"])\.claude/hooks/([\w-]+\.py)(?!['\"])",  # Unquoted (no quotes before/after)
        r"'\.claude/hooks/([\w-]+\.py)'",  # Single-quoted
        r'"\.claude/hooks/([\w-]+\.py)"',  # Double-quoted
    ]
    hook_matches = []
    for pattern in hook_file_patterns:
        hook_matches.extend(re.findall(pattern, command))
    if hook_matches:
        deleted_hooks.extend(hook_matches)

    # Check for directory deletion: .claude/hooks/ or .claude/hooks
    # Uses same quote matching logic as file patterns
    # Only triggers if no specific files were found (avoid double detection)
    dir_patterns = [
        r"(?<!['\"])\.claude/hooks/?(?!['\"])(?:\s|$)",  # Unquoted
        r"'\.claude/hooks/?'(?:\s|$)",  # Single-quoted
        r'"\.claude/hooks/?"(?:\s|$)',  # Double-quoted
    ]
    if any(re.search(p, command) for p in dir_patterns) and not hook_matches:
        deleted_hooks.append(".claude/hooks/ (directory)")

    return deleted_hooks


def get_staged_new_hooks() -> list[str]:
    """Get list of newly added hook files in staging area."""
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-status", "--diff-filter=A"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode != 0:
            return []

        new_hooks = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            # Format: "A\tpath/to/file"
            parts = line.split("\t")
            if len(parts) >= 2:
                filepath = parts[1]
                # Check if it's a hook file (not test/lib/scripts)
                if (
                    filepath.startswith(".claude/hooks/")
                    and filepath.endswith(".py")
                    and "/tests/" not in filepath
                    and "/lib/" not in filepath
                    and "/scripts/" not in filepath
                ):
                    new_hooks.append(filepath)
        return new_hooks
    except Exception:
        return []


def main():
    """Entry point for the hooks design check."""
    try:
        input_json = parse_hook_input()
        command = input_json.get("tool_input", {}).get("command", "")

        # Check for hook file deletion (Issue #193)
        # Warn but don't block - user needs to see the warning and decide
        # Blocking would make hook deletion impossible even in new sessions
        deleted_hooks = check_hook_deletion(command)
        if deleted_hooks:
            files_list = ", ".join(deleted_hooks)
            result = {
                "decision": "approve",
                "systemMessage": f"フックファイル削除を検出: {files_list}\n{HOOK_DELETION_WARNING}",
            }
            log_hook_execution(
                "hooks-design-check", "approve", None, {"deletion_detected": deleted_hooks}
            )
            print(json.dumps(result))
            return

        # Only check design review on git commit
        if not re.search(r"git\s+commit", command):
            result = {"decision": "approve"}
            log_hook_execution("hooks-design-check", "approve")
            print(json.dumps(result))
            return

        # Get newly added and modified hook files
        new_hooks = get_staged_new_hooks()
        modified_hooks = get_staged_modified_hooks()
        all_hooks = new_hooks + modified_hooks

        # If no hook files are being committed, approve
        if not all_hooks:
            result = {"decision": "approve"}
            log_hook_execution("hooks-design-check", "approve")
            print(json.dumps(result))
            return

        # Collect warnings
        warnings = []

        # Show SRP checklist for new hooks (warning, not block)
        if new_hooks:
            warnings.append(SRP_CHECKLIST_WARNING)

        # Check for missing remediation in all hook files (Issue #1111)
        # This is a warning, not a block
        remediation_issues = check_remediation_in_hooks(all_hooks)
        if remediation_issues:
            files_detail = []
            for filepath, issues in remediation_issues:
                for line_no, excerpt in issues:
                    files_detail.append(f"  - {filepath}:{line_no}: {excerpt}")
            files_str = "\n".join(files_detail)
            warnings.append(REMEDIATION_MISSING_WARNING.format(files=files_str))
            log_hook_execution(
                "hooks-design-check",
                "approve",
                f"Remediation warning: {len(remediation_issues)} files with issues",
                {"remediation_issues": [f[0] for f in remediation_issues]},
            )

        # Check if all hooks use log_hook_execution (Issue #2589)
        # This is a blocking check for new hooks
        if new_hooks:
            missing_log_execution = check_log_execution_usage(new_hooks)
            if missing_log_execution:
                files_list = "\n".join(f"  - {f}" for f in missing_log_execution)
                reason = LOG_EXECUTION_MISSING_MSG.format(files=files_list)
                if warnings:
                    reason += "\n\n" + "\n\n".join(warnings)
                result = make_block_result("hooks-design-check", reason)
                log_hook_execution(
                    "hooks-design-check",
                    "block",
                    "Missing log_hook_execution",
                    {"missing_log_execution": missing_log_execution},
                )
                print(json.dumps(result))
                return

        # No blocking issues, but may have warnings
        if warnings:
            result = {
                "decision": "approve",
                "systemMessage": "\n\n".join(warnings),
            }
        else:
            result = {"decision": "approve"}

        log_hook_execution(
            "hooks-design-check",
            "approve",
            None,
            {"new_hooks": new_hooks, "modified_hooks": modified_hooks},
        )
        print(json.dumps(result))

    except Exception as e:
        # On error, approve to avoid blocking legitimate commits
        print(f"[hooks-design-check] Hook error: {e}", file=sys.stderr)
        result = {"decision": "approve", "reason": f"Hook error: {e}"}
        log_hook_execution("hooks-design-check", "approve", f"Hook error: {e}")
        print(json.dumps(result))


if __name__ == "__main__":
    main()
