#!/usr/bin/env python3
"""フック実装パターンの一貫性をチェックする。

Why:
    PostToolUseフックのtool_result/tool_response両対応や、
    類似フックの存在を検出し、実装漏れを防ぐため。

What:
    - PATTERN001: PostToolUseフックでtool_response未対応を検出
    - PATTERN002: 類似フック（同トリガー）の存在を警告
    - PATTERN003: コミット対象と同パターンを持つ他ファイルの漏れを警告

Remarks:
    - --all で全フックをチェック
    - # hook-pattern-check: skip でスキップ可能

Changelog:
    - silenvx/dekita#1852: パターン統一チェック機能を追加
"""

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import NamedTuple


class PatternError(NamedTuple):
    """パターンエラーを表す"""

    file: str
    line: int
    code: str
    message: str
    level: str  # "error" or "warning"


# スキップコメントパターン
SKIP_COMMENT = "# hook-pattern-check: skip"

# フックファイル名抽出パターン
HOOK_PATTERN = r"/\.claude/hooks/([^\"]+\.py)"


def load_settings() -> dict:
    """settings.jsonを読み込む"""
    settings_path = Path(".claude/settings.json")
    if not settings_path.exists():
        return {}
    try:
        with open(settings_path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def get_post_tool_use_hooks(settings: dict) -> set[str]:
    """PostToolUseに登録されているフックファイル名を取得"""
    hooks = set()
    post_tool_use = settings.get("hooks", {}).get("PostToolUse", [])

    for matcher_group in post_tool_use:
        for hook in matcher_group.get("hooks", []):
            command = hook.get("command", "")
            # python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/xxx.py からファイル名を抽出
            match = re.search(HOOK_PATTERN, command)
            if match:
                hooks.add(match.group(1))

    return hooks


def get_hooks_by_trigger(settings: dict) -> dict[str, list[str]]:
    """トリガー（matcher）ごとにフックをグループ化"""
    trigger_hooks: dict[str, list[str]] = {}
    post_tool_use = settings.get("hooks", {}).get("PostToolUse", [])

    for matcher_group in post_tool_use:
        matcher = matcher_group.get("matcher", ".*")
        for hook in matcher_group.get("hooks", []):
            command = hook.get("command", "")
            match = re.search(HOOK_PATTERN, command)
            if match:
                hook_name = match.group(1)
                if matcher not in trigger_hooks:
                    trigger_hooks[matcher] = []
                trigger_hooks[matcher].append(hook_name)

    return trigger_hooks


def has_skip_comment(source: str) -> bool:
    """スキップコメントがあるか確認"""
    return SKIP_COMMENT in source


def check_tool_response_fallback(tree: ast.AST, source: str, filepath: str) -> list[PatternError]:
    """tool_result と tool_response の両方に対応しているか確認

    正しいパターン:
        if "tool_result" in input_data:
            tool_result = input_data.get("tool_result") or {}
        else:
            tool_result = input_data.get("tool_response", {})

    問題のあるパターン:
        tool_result = input_data.get("tool_result", {})  # tool_responseを見ていない
    """
    errors = []

    # tool_resultへの参照を探す
    has_tool_result = False
    has_tool_response = False
    tool_result_line = 0

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # input_data.get("tool_result", ...) パターン
            if isinstance(node.func, ast.Attribute) and node.func.attr == "get":
                if node.args:
                    arg = node.args[0]
                    if isinstance(arg, ast.Constant):
                        if arg.value == "tool_result":
                            has_tool_result = True
                            if tool_result_line == 0:
                                tool_result_line = node.lineno
                        elif arg.value == "tool_response":
                            has_tool_response = True

        # "tool_result" in input_data パターン
        if isinstance(node, ast.Compare):
            if isinstance(node.left, ast.Constant) and node.left.value == "tool_result":
                has_tool_result = True
            elif isinstance(node.left, ast.Constant) and node.left.value == "tool_response":
                has_tool_response = True

        # input_data["tool_result"] パターン（サブスクリプトアクセス）
        # 読み込み（Load）のみ対象。書き込み（Store）は除外
        # 例: event["tool_result"] = value は書き込みなので対象外
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
            if isinstance(node.slice, ast.Constant):
                if node.slice.value == "tool_result":
                    has_tool_result = True
                    if tool_result_line == 0:
                        tool_result_line = node.lineno
                elif node.slice.value == "tool_response":
                    has_tool_response = True

    # tool_resultを使っているが、tool_responseへのフォールバックがない
    if has_tool_result and not has_tool_response:
        errors.append(
            PatternError(
                file=filepath,
                line=tool_result_line,
                code="PATTERN001",
                message=(
                    "PostToolUseフックで tool_response に未対応。"
                    "Claude Codeは tool_response キーを使用することがあります。\n"
                    "推奨パターン:\n"
                    '  if "tool_result" in input_data:\n'
                    '      tool_result = input_data.get("tool_result") or {}\n'
                    "  else:\n"
                    '      tool_result = input_data.get("tool_response", {})'
                ),
                level="error",
            )
        )

    return errors


def check_similar_hooks(filepath: str, trigger_hooks: dict[str, list[str]]) -> list[PatternError]:
    """同じトリガーを持つ類似フックを警告"""
    errors = []
    filename = Path(filepath).name

    for trigger, hooks in trigger_hooks.items():
        if filename in hooks and len(hooks) > 1:
            similar = [h for h in hooks if h != filename]
            if similar:
                errors.append(
                    PatternError(
                        file=filepath,
                        line=0,
                        code="PATTERN002",
                        message=(
                            f"同じトリガー '{trigger}' を持つ類似フックがあります: "
                            f"{', '.join(similar[:5])}"
                            + (f" (他{len(similar) - 5}件)" if len(similar) > 5 else "")
                        ),
                        level="warning",
                    )
                )
                # 1ファイルにつき1警告のみ（複数トリガーでも最初の1件のみ報告）
                break

    return errors


# Issue #1852: パターン統一時の対象漏れ防止
# tool_result取得パターンを検出
TOOL_RESULT_PATTERN = re.compile(
    r'(?:input_data|data|hook_input)\.get\(["\']tool_(?:result|response|output)["\']\s*(?:,|\))'
)


def find_tool_result_pattern_files(hooks_dir: Path) -> set[str]:
    """tool_resultパターンを持つフックファイルを検索

    Args:
        hooks_dir: フックディレクトリ

    Returns:
        パターンを含むファイル名のセット
    """
    pattern_files = set()

    if not hooks_dir.exists():
        return pattern_files

    for pyfile in hooks_dir.glob("*.py"):
        # テストファイルはスキップ
        if pyfile.name.startswith("test_"):
            continue

        try:
            content = pyfile.read_text(encoding="utf-8")
            # スキップコメントがあればスキップ
            if SKIP_COMMENT in content:
                continue
            if TOOL_RESULT_PATTERN.search(content):
                pattern_files.add(pyfile.name)
        except OSError:
            continue

    return pattern_files


def check_pattern_coverage(
    modified_files: list[Path],
    all_pattern_files: set[str],
) -> list[PatternError]:
    """コミット対象に含まれていないパターンファイルを警告

    Issue #1852: パターン統一時の対象漏れ防止

    Args:
        modified_files: コミット対象のファイル
        all_pattern_files: 同パターンを持つ全ファイル

    Returns:
        警告のリスト
    """
    errors = []

    # コミット対象のフックファイル名を抽出
    # Note: f.as_posix()を使ってWindowsでもパス比較を正しく動作させる
    modified_hook_names = set()
    for f in modified_files:
        # パスセパレータに依存しない判定（Issue #1852 Codex review feedback）
        posix_path = f.as_posix()
        if ".claude/hooks/" in posix_path and f.name.endswith(".py"):
            modified_hook_names.add(f.name)

    # コミット対象にtool_resultパターンを持つファイルがあるか確認
    modified_pattern_files = modified_hook_names & all_pattern_files

    if not modified_pattern_files:
        # コミット対象にパターンファイルがなければチェック不要
        return []

    # コミット対象に含まれていないパターンファイルを検出
    missing_files = all_pattern_files - modified_hook_names

    if missing_files:
        # 最初のコミット対象ファイルに警告を付ける
        first_modified = sorted(modified_pattern_files)[0]
        for f in modified_files:
            if f.name == first_modified:
                errors.append(
                    PatternError(
                        file=str(f),
                        line=0,
                        code="PATTERN003",
                        message=(
                            f"tool_resultパターンを持つ他のファイルがあります: "
                            f"{', '.join(sorted(missing_files)[:5])}"
                            + (f" (他{len(missing_files) - 5}件)" if len(missing_files) > 5 else "")
                            + "\n同じパターンの修正漏れがないか確認してください。"
                        ),
                        level="warning",
                    )
                )
                break

    return errors


def lint_file(
    filepath: Path,
    post_tool_use_hooks: set[str],
    trigger_hooks: dict[str, list[str]],
) -> list[PatternError]:
    """単一ファイルをチェック"""
    errors = []
    filename = filepath.name

    # テストファイルはスキップ
    if "/tests/" in str(filepath) or filename.startswith("test_"):
        return []

    try:
        with open(filepath, encoding="utf-8") as f:
            source = f.read()
    except OSError as e:
        return [
            PatternError(
                file=str(filepath),
                line=0,
                code="PATTERN000",
                message=f"ファイル読み込みエラー: {e}",
                level="error",
            )
        ]

    # スキップコメントがあればスキップ
    if has_skip_comment(source):
        return []

    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError as e:
        return [
            PatternError(
                file=str(filepath),
                line=e.lineno or 0,
                code="PATTERN000",
                message=f"構文エラー: {e.msg}",
                level="error",
            )
        ]

    # PostToolUseフックのみチェック
    if filename in post_tool_use_hooks:
        errors.extend(check_tool_response_fallback(tree, source, str(filepath)))

    # 類似フックの警告
    errors.extend(check_similar_hooks(str(filepath), trigger_hooks))

    return errors


def parse_args() -> argparse.Namespace:
    """コマンドライン引数をパース"""
    parser = argparse.ArgumentParser(
        description="フック実装パターンチェッカー",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="チェックするファイル（デフォルト: 指定ファイルのみ）",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="全てのPostToolUseフックをチェック",
    )
    parser.add_argument(
        "--warnings",
        action="store_true",
        help="警告も表示（デフォルトはエラーのみ）",
    )
    return parser.parse_args()


def main() -> int:
    """メインエントリーポイント"""
    args = parse_args()

    settings = load_settings()
    post_tool_use_hooks = get_post_tool_use_hooks(settings)
    trigger_hooks = get_hooks_by_trigger(settings)

    # チェック対象ファイルを決定
    if args.all:
        hooks_dir = Path(".claude/hooks")
        if not hooks_dir.exists():
            print(".claude/hooks ディレクトリが見つかりません", file=sys.stderr)
            return 1
        files = [f for f in hooks_dir.glob("*.py") if f.name in post_tool_use_hooks]
    elif args.files:
        files = [Path(f) for f in args.files if f.endswith(".py")]
    else:
        # ファイル指定なしの場合は何もしない
        print("チェック対象ファイルがありません。--all または ファイルを指定してください。")
        return 0

    all_errors: list[PatternError] = []

    for filepath in sorted(files):
        if not filepath.exists():
            continue
        errors = lint_file(filepath, post_tool_use_hooks, trigger_hooks)
        all_errors.extend(errors)

    # Issue #1852: パターンカバレッジチェック（ファイル指定時のみ）
    if args.files and not args.all:
        hooks_dir = Path(".claude/hooks")
        all_pattern_files = find_tool_result_pattern_files(hooks_dir)
        coverage_errors = check_pattern_coverage(files, all_pattern_files)
        all_errors.extend(coverage_errors)

    # 出力
    error_count = 0
    for error in all_errors:
        if error.level == "warning" and not args.warnings:
            continue

        prefix = "⚠️ " if error.level == "warning" else "❌ "
        if error.line > 0:
            print(f"{error.file}:{error.line}: {prefix}[{error.code}] {error.message}")
        else:
            print(f"{error.file}: {prefix}[{error.code}] {error.message}")

        if error.level == "error":
            error_count += 1

    if error_count > 0:
        print(f"\n{error_count}件のエラーが見つかりました", file=sys.stderr)
        return 1

    if all_errors and args.warnings:
        print(f"\n{len(all_errors) - error_count}件の警告があります")

    return 0


if __name__ == "__main__":
    sys.exit(main())
