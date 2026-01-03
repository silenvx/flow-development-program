#!/usr/bin/env python3
"""Python関数/クラス削除時のテスト参照漏れを検出。

Why:
    関数やクラスを削除しても、テストファイルに参照が残っているとCIで失敗する。
    コミット前に参照漏れを検出してブロックすることで、CI失敗を防止する。

What:
    - git diffでステージ済みの関数/クラス削除を検出
    - 削除されたシンボルへのテストファイル参照をgit grepで検索
    - 参照が残っている場合はコミットをブロック

Remarks:
    - pre-commitフックとして使用
    - リファクタリング（同名追加）や移動は除外
    - プライベート関数（_prefix）はスキップ
    - 削除元モジュールからインポートしているテストのみ対象（Issue #1958）

Changelog:
    - silenvx/dekita#1868: フック追加
    - silenvx/dekita#1915: 別ファイルへの移動検出追加
    - silenvx/dekita#1958: インポート元モジュールフィルタリング追加
"""

import os
import re
import subprocess
import sys

# gitコマンドのタイムアウト（秒）
TIMEOUT_MEDIUM = 10


def get_staged_diff() -> str:
    """ステージされた差分を取得する。

    Returns:
        差分内容の文字列。エラー時は空文字列。
    """
    try:
        # 注意: --find-renames は意図的に指定しない。
        # 関数/クラスのリネームは「旧名の削除」として扱う。
        # テストに旧名の参照が残っていればブロックする（Issue #1868）。
        result = subprocess.run(
            ["git", "diff", "--cached", "-U0"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode != 0:
            return ""
        return result.stdout
    except subprocess.TimeoutExpired:
        print("[test-deletion-check] 警告: git diffがタイムアウトしました", file=sys.stderr)
        return ""
    except Exception as e:
        # Fail open: エラー時はコミットをブロックしない
        print(f"[test-deletion-check] 警告: git diff実行エラー: {e}", file=sys.stderr)
        return ""


def extract_deleted_symbols(diff_content: str) -> list[tuple[str, str, str]]:
    """差分から削除された関数/クラス名を抽出する。

    リファクタリング（型ヒント追加など）で同じ名前が追加行にもある場合は
    削除とは見なさない。別ファイルへの移動も検出する（Issue #1915）。

    Args:
        diff_content: git diffの出力内容。

    Returns:
        (symbol_type, symbol_name, file_path)のタプルのリスト。
        symbol_typeは'function'または'class'。
    """
    deleted_symbols: list[tuple[str, str, str]] = []
    # ファイルパスなしのシンボル（type, name）を記録
    added_symbol_names: set[tuple[str, str]] = set()
    current_file = ""

    # 差分ヘッダーからファイルパスを抽出するパターン
    file_pattern = re.compile(r"^--- a/(.+\.py)$")

    # 削除行（-で始まる行）のパターン
    # 関数定義: def function_name( または async def function_name(
    func_del_pattern = re.compile(r"^-\s*(?:async\s+)?def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(")
    # クラス定義: class ClassName: または class ClassName(
    class_del_pattern = re.compile(r"^-\s*class\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*[:\(]")

    # 追加行（+で始まる行）のパターン
    func_add_pattern = re.compile(r"^\+\s*(?:async\s+)?def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(")
    class_add_pattern = re.compile(r"^\+\s*class\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*[:\(]")

    seen_deleted: set[tuple[str, str, str]] = set()

    for line in diff_content.split("\n"):
        # 現在のファイルを追跡
        file_match = file_pattern.match(line)
        if file_match:
            current_file = file_match.group(1)
            continue

        # テストファイル自体の変更は無視
        if current_file and "tests/" in current_file:
            continue

        # 追加された関数/クラスを記録（リファクタリング・移動検出用）
        # ファイルパスなしで記録し、別ファイルへの移動も検出できるようにする
        func_add_match = func_add_pattern.match(line)
        if func_add_match and current_file:
            added_symbol_names.add(("function", func_add_match.group(1)))
            continue

        class_add_match = class_add_pattern.match(line)
        if class_add_match and current_file:
            added_symbol_names.add(("class", class_add_match.group(1)))
            continue

        # 削除された関数をチェック
        func_del_match = func_del_pattern.match(line)
        if func_del_match and current_file:
            symbol = ("function", func_del_match.group(1), current_file)
            if symbol not in seen_deleted:
                deleted_symbols.append(symbol)
                seen_deleted.add(symbol)
            continue

        # 削除されたクラスをチェック
        class_del_match = class_del_pattern.match(line)
        if class_del_match and current_file:
            symbol = ("class", class_del_match.group(1), current_file)
            if symbol not in seen_deleted:
                deleted_symbols.append(symbol)
                seen_deleted.add(symbol)

    # リファクタリングまたは別ファイルへの移動（追加行に同名シンボルがある）を除外
    # ファイルパスを無視して名前のみで比較することで、移動も検出できる
    return [s for s in deleted_symbols if (s[0], s[1]) not in added_symbol_names]


def get_module_name_from_file(file_path: str) -> str:
    """ファイルパスからモジュール名（拡張子なし）を取得する。

    Args:
        file_path: Pythonファイルのパス（例: .claude/hooks/reflection-quality-check.py）

    Returns:
        モジュール名（例: reflection-quality-check）
    """
    basename = os.path.basename(file_path)
    if basename.endswith(".py"):
        return basename[:-3]
    return basename


def check_test_imports_from_module(test_file: str, module_name: str) -> bool:
    """テストファイルが指定モジュールからインポートしているかを確認する。

    Args:
        test_file: テストファイルのパス
        module_name: 削除元モジュール名（例: reflection-quality-check）

    Returns:
        テストがそのモジュールからインポートしている場合True
    """
    # モジュール名を正規化（ハイフンをアンダースコアに）
    # Pythonの慣習に従い、ハイフン付きモジュール名の変換パターンをチェック
    normalized_name = module_name.replace("-", "_")

    try:
        result = subprocess.run(
            ["git", "show", f":{test_file}"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode != 0:
            # ファイルがステージされていない場合は直接読む
            try:
                with open(test_file, encoding="utf-8") as f:
                    content = f.read()
            except Exception:
                return True  # Fail open: 読めない場合は参照ありと見なす
        else:
            content = result.stdout

        # コメントや文字列リテラル内の誤検知を避けるため、
        # 行頭（空白を除く）から始まる import 文 / from 文 / 代入文のみを正規表現で検出する。
        escaped_name = re.escape(normalized_name)
        import_regex = re.compile(
            rf"^\s*(from\s+{escaped_name}\s+import\b|import\s+{escaped_name}\b(?:\s+as\b)?)",
            re.MULTILINE,
        )
        assign_regex = re.compile(rf"^\s*{escaped_name}\s*=", re.MULTILINE)

        # import / from 文、あるいは行頭での代入があれば参照ありとみなす
        if import_regex.search(content) or assign_regex.search(content):
            return True

        # 動的インポートなど、モジュール名を文字列として参照しているケースも検出する
        if f'"{module_name}"' in content or f"'{module_name}'" in content:
            return True

        return False
    except Exception:
        return True  # Fail open


def find_test_references(
    symbol_name: str, source_file: str, test_dir: str = ".claude/hooks/tests"
) -> list[tuple[str, int, str]]:
    """テストファイル内のシンボル参照を検索する。

    Issue #1958: 削除元モジュールからインポートしているテストのみを対象とする。
    同名の別モジュール関数への参照は誤検知として除外する。

    Args:
        symbol_name: 検索する関数/クラス名。
        source_file: シンボルが削除されたファイルパス。
        test_dir: テストファイルを含むディレクトリ。

    Returns:
        (file_path, line_number, line_content)のタプルのリスト。
    """
    references: list[tuple[str, int, str]] = []
    module_name = get_module_name_from_file(source_file)

    try:
        # git grepで高速検索（追跡されているファイルのみ）
        # -wで単語境界を使用し、部分一致を防止
        # サブディレクトリも含めて検索するためディレクトリパスのみを指定
        result = subprocess.run(
            [
                "git",
                "grep",
                "-n",
                "-w",
                symbol_name,
                "--",
                f"{test_dir}/",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )

        # git grepは一致あり=0、一致なし=1を返す
        if result.returncode == 0 and result.stdout.strip():
            # ファイルごとにグループ化
            files_with_refs: dict[str, list[tuple[int, str]]] = {}
            for line in result.stdout.strip().split("\n"):
                # 形式: file:line_number:content
                parts = line.split(":", 2)
                if len(parts) >= 3:
                    file_path = parts[0]
                    try:
                        line_num = int(parts[1])
                    except ValueError:
                        continue
                    content = parts[2]
                    if file_path not in files_with_refs:
                        files_with_refs[file_path] = []
                    files_with_refs[file_path].append((line_num, content))

            # 削除元モジュールからインポートしているファイルのみを対象
            for file_path, refs in files_with_refs.items():
                if check_test_imports_from_module(file_path, module_name):
                    for line_num, content in refs:
                        references.append((file_path, line_num, content))
    except subprocess.TimeoutExpired:
        # Fail open: タイムアウト時は空リストを返す
        print("[test-deletion-check] 警告: git grepがタイムアウトしました", file=sys.stderr)
    except Exception as e:
        # Fail open: エラー時はコミットをブロックしない
        print(f"[test-deletion-check] 警告: git grep実行エラー: {e}", file=sys.stderr)

    return references


def main() -> int:
    """メイン関数。

    Returns:
        問題なし=0、古い参照検出=1。
    """
    # ステージされた差分を取得
    diff = get_staged_diff()
    if not diff:
        return 0

    # 削除されたシンボルを抽出
    deleted_symbols = extract_deleted_symbols(diff)
    if not deleted_symbols:
        return 0

    # テストファイル内の古い参照をチェック
    issues: list[tuple[str, str, str, list[tuple[str, int, str]]]] = []

    for symbol_type, symbol_name, source_file in deleted_symbols:
        # 先頭が単一の '_' のプライベート関数はスキップする。
        # '__init__' などの特殊メソッドや '__private' など '__' で始まる名前は
        # 明示的にテストされる可能性があるため、ここではスキップしない。
        if symbol_name.startswith("_") and not symbol_name.startswith("__"):
            continue

        # Issue #1958: 削除元モジュールからインポートしているテストのみを対象
        references = find_test_references(symbol_name, source_file)
        if references:
            issues.append((symbol_type, symbol_name, source_file, references))

    if not issues:
        return 0

    # 問題を報告
    print("エラー: 削除されたシンボルがテストファイルに残っています。")
    print("")
    print("以下の削除された関数/クラスがテストで参照されています:")
    print("")

    for symbol_type, symbol_name, source_file, references in issues:
        type_ja = "関数" if symbol_type == "function" else "クラス"
        print(f"  {type_ja} '{symbol_name}' ({source_file}から削除):")
        for file_path, line_num, content in references[:3]:  # 最大3件表示
            print(f"    - {file_path}:{line_num}: {content.strip()[:60]}")
        if len(references) > 3:
            print(f"    ... 他{len(references) - 3}件")
        print("")

    print("削除されたシンボルへの参照をテストファイルから削除してください。")
    print("")
    print("このチェックをスキップするには（非推奨）:")
    print("  git commit --no-verify")

    return 1


if __name__ == "__main__":
    sys.exit(main())
