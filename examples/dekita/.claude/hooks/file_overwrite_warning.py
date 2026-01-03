#!/usr/bin/env python3
"""Bashでの既存ファイル上書き時に警告を表示する。

Why:
    `Write`ツールはファイル読み込み必須で保護されているが、
    Bashの`cat >`や`echo >`には保護がない。意図せず既存ファイルを
    上書きして重要なコードを失うリスクがある。

What:
    - Bashコマンドからリダイレクト先（cat >, echo >, tee等）を抽出
    - 対象ファイルが既存かを確認
    - 既存ファイルへの上書きの場合は警告メッセージを表示

Remarks:
    - teeの-a/--appendオプションは追記なので除外
    - ブロックはせず警告のみ
    - 新規ファイル作成は警告なし

Changelog:
    - silenvx/dekita#1018: テストファイル上書き事件を機にフック追加
"""

import json
import os
import re
import shlex
from pathlib import Path

from lib.cwd import get_effective_cwd
from lib.execution import log_hook_execution
from lib.input_context import extract_input_context, merge_details_with_context
from lib.session import parse_hook_input

HOOK_NAME = "file-overwrite-warning"

# ファイルへのリダイレクトパターン
# グループ1: ファイルパス（リダイレクト演算子は非キャプチャグループ）
REDIRECT_PATTERNS = [
    # cat > file, cat >> file (>> を先にマッチさせる)
    r"cat\s+(?:<<\s*['\"]?\w+['\"]?\s+)?(?:>>|>)\s*([^\s;|&]+)",
    # cat << 'EOF' > file (ヒアドキュメント)
    r"cat\s+<<\s*['\"]?\w+['\"]?\s*>\s*([^\s;|&]+)",
    # echo "..." > file, echo "..." >> file (>> を先にマッチさせる)
    r"echo\s+.*?(?:>>|>)\s*([^\s;|&]+)",
    # printf "..." > file (>> を先にマッチさせる)
    r"printf\s+.*?(?:>>|>)\s*([^\s;|&]+)",
]

# teeコマンド用パターン（引数全体をキャプチャ）
# 注意:
# - tee の引数は次のパイプ(|)、セミコロン(;)、アンパサンド(&)、改行の直前までを対象とする
# - 例: `echo hello | tee file.txt | grep hello` の場合、`tee` の引数としては `file.txt` のみを想定する
#   → 次のパイプ以降の `| grep hello` は tee の引数ではなく、後続コマンドとして扱うためキャプチャしない
# - この挙動を前提としているため、[^\n;|&]+ で「tee の引数が次のパイプ等の前で終わる」ことを明示的に表現している
TEE_PATTERN = r"\|\s*tee\s+([^\n;|&]+)"


def parse_tee_arguments(args_str: str) -> list[str]:
    """teeコマンドの引数をパースしてファイル名リストを返す。

    appendモード（-a, --append）の場合は空リストを返す。
    オプションとファイル名を区別し、ファイル名のみを返す。

    Note:
        この関数はteeコマンド専用。リダイレクトパターン（cat > file等）の
        引用符付きファイル名は別途対応が必要（現状は単純な正規表現で処理）。

    Args:
        args_str: teeコマンドの引数文字列（例: "-ai file1 file2"）

    Returns:
        上書きされるファイル名のリスト。appendモードの場合は空リスト。
        パースエラー時（閉じられていない引用符など）も空リストを返す。

    Examples:
        parse_tee_arguments("file") → ["file"]
        parse_tee_arguments("-a file") → []
        parse_tee_arguments("-ai file") → []
        parse_tee_arguments("--append file") → []
        parse_tee_arguments("file1 file2") → ["file1", "file2"]
        parse_tee_arguments('"file name.txt"') → ["file name.txt"]
    """
    try:
        args = shlex.split(args_str)
    except ValueError:
        # 閉じられていない引用符などのパースエラー時は空リストを返す。
        # 空リストを返すと警告が出ないが、ファイルは上書きされる（fail-open）。
        # これは意図的な設計: 警告フックがエラーで処理をブロックするより、
        # 最悪でも警告なしで処理が進む方が安全。
        return []
    if not args:
        return []

    files = []
    append_mode = False
    options_ended = False

    for arg in args:
        if options_ended:
            # オプション解析終了後は、以降の引数をすべてファイル名として扱う
            files.append(arg)
        elif arg == "--":
            # -- 自体はファイル名には含めず、この後の引数をすべてファイル名扱い
            options_ended = True
        elif not arg.startswith("-"):
            # 初めての非オプション引数 → ここからファイル名モードに移行
            files.append(arg)
            options_ended = True
        elif arg == "-a" or arg == "--append":
            append_mode = True
        elif arg.startswith("--"):
            # 他のロングオプションは無視
            pass
        elif arg.startswith("-"):
            # 短縮オプション（-ai, -ia など）
            # オプション文字に 'a' が含まれていればappendモード
            option_chars = arg[1:]  # "-" を除去
            if "a" in option_chars:
                append_mode = True

    if append_mode:
        return []

    return files


def extract_redirect_targets(command: str) -> list[str]:
    """コマンドからリダイレクト先のファイルパスを抽出する。"""
    targets = []

    # 通常のリダイレクトパターン
    for pattern in REDIRECT_PATTERNS:
        matches = re.findall(pattern, command, re.IGNORECASE)
        targets.extend(matches)

    # teeコマンドは別処理（appendモード検出のため）
    tee_matches = re.findall(TEE_PATTERN, command, re.IGNORECASE)
    for tee_args in tee_matches:
        targets.extend(parse_tee_arguments(tee_args))

    return targets


def resolve_path(file_path: str, command: str | None = None) -> Path:
    """ファイルパスを解決する（環境変数展開、相対パス解決）。

    Args:
        file_path: 解決するファイルパス
        command: Bashコマンド（cdコマンドを含む場合、その効果を考慮する）

    Returns:
        解決されたPath
    """
    # 環境変数を展開
    expanded = os.path.expandvars(file_path)
    # ~を展開
    expanded = os.path.expanduser(expanded)

    path = Path(expanded)
    if path.is_absolute():
        return path.resolve()

    # 相対パスの場合、Bashの実効cwdを考慮
    effective_cwd = get_effective_cwd(command)
    return (effective_cwd / path).resolve()


def main() -> None:
    """Bashでのファイル上書き時に警告を表示する。"""
    hook_input = parse_hook_input()
    input_context = extract_input_context(hook_input)
    tool_name = hook_input.get("tool_name", "")

    # Bashツールのみ対象
    if tool_name != "Bash":
        result = {"decision": "approve"}
        print(json.dumps(result))
        return

    tool_input = hook_input.get("tool_input", {})
    command = tool_input.get("command", "")

    # リダイレクト先を抽出
    targets = extract_redirect_targets(command)

    if not targets:
        result = {"decision": "approve"}
        print(json.dumps(result))
        return

    # 既存ファイルをチェック
    existing_files = []
    for target in targets:
        try:
            resolved = resolve_path(target, command)
            if resolved.exists() and resolved.is_file():
                existing_files.append(str(resolved))
        except (OSError, ValueError):
            # パス解決エラーは無視（Fail-open）
            pass

    if not existing_files:
        # 新規ファイルへの書き込みは警告なし
        log_hook_execution(
            HOOK_NAME,
            "approve",
            f"New file(s): {targets}",
            merge_details_with_context({"targets": targets}, input_context),
        )
        result = {"decision": "approve"}
        print(json.dumps(result))
        return

    # 既存ファイルへの上書きを警告
    log_hook_execution(
        HOOK_NAME,
        "approve",
        f"Existing file(s) will be overwritten: {existing_files}",
        merge_details_with_context(
            {"existing_files": existing_files, "command": command[:100]},
            input_context,
        ),
    )

    files_list = "\n".join(f"  - {f}" for f in existing_files)
    result = {
        "decision": "approve",
        "message": f"""[{HOOK_NAME}] ⚠️ 既存ファイルを上書きしようとしています。

**対象ファイル:**
{files_list}

**確認事項:**
- 本当にこのファイルを上書きしますか？
- 既存の内容を確認しましたか？
- `Write`ツールを使用すると、既存内容の確認が必須になります。

**推奨:**
- 既存ファイルの編集には `Edit` または `Write` ツールを使用
- 内容を追記する場合は `>>` を使用

Issue #1018: テストファイル上書き事件を防止するための警告です。""",
    }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
