#!/usr/bin/env python3
"""test-deletion-check.py フックのテスト。

Issue #1868: 機能削除時のテスト更新漏れを検出するpre-commitフックのテスト。
"""

import subprocess

import pytest
from conftest import load_hook_module

# test-deletion-check.py モジュールをロード
_module = load_hook_module("test-deletion-check")

extract_deleted_symbols = _module.extract_deleted_symbols
find_test_references = _module.find_test_references
get_staged_diff = _module.get_staged_diff


class TestGetStagedDiff:
    """get_staged_diff関数のテスト。"""

    def test_ステージされた変更がない場合は空文字を返す(self, monkeypatch):
        """ステージされた変更がない場合は空文字列を返す。"""

        def mock_run(*args, **kwargs):
            result = subprocess.CompletedProcess(args[0], 0)
            result.stdout = ""
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)

        result = get_staged_diff()
        assert result == ""


class TestExtractDeletedSymbols:
    """extract_deleted_symbols関数のテスト。"""

    def test_削除された関数を抽出する(self):
        """差分から削除された関数を抽出する。"""
        diff = """--- a/src/module.py
+++ b/src/module.py
@@ -10,7 +10,0 @@
-def my_function():
-    pass
"""
        result = extract_deleted_symbols(diff)
        assert ("function", "my_function", "src/module.py") in result

    def test_削除されたasync関数を抽出する(self):
        """差分から削除されたasync関数を抽出する。"""
        diff = """--- a/src/module.py
+++ b/src/module.py
@@ -10,7 +10,0 @@
-async def my_async_function():
-    pass
"""
        result = extract_deleted_symbols(diff)
        assert ("function", "my_async_function", "src/module.py") in result

    def test_削除されたクラスを抽出する(self):
        """差分から削除されたクラスを抽出する。"""
        diff = """--- a/src/module.py
+++ b/src/module.py
@@ -10,7 +10,0 @@
-class MyClass:
-    pass
"""
        result = extract_deleted_symbols(diff)
        assert ("class", "MyClass", "src/module.py") in result

    def test_テストファイル内の削除は無視する(self):
        """テストファイル内の削除は無視する。"""
        diff = """--- a/tests/test_module.py
+++ b/tests/test_module.py
@@ -10,7 +10,0 @@
-def test_something():
-    pass
"""
        result = extract_deleted_symbols(diff)
        assert len(result) == 0

    def test_複数のシンボルを抽出する(self):
        """複数の削除されたシンボルを抽出する。"""
        diff = """--- a/src/module.py
+++ b/src/module.py
@@ -10,7 +10,0 @@
-def func_one():
-    pass
-
-class ClassTwo:
-    pass
-
-def func_three():
-    pass
"""
        result = extract_deleted_symbols(diff)
        names = [name for _, name, _ in result]
        assert "func_one" in names
        assert "ClassTwo" in names
        assert "func_three" in names

    def test_追加されたシンボルは無視する(self):
        """新しく追加されたシンボルは無視する（削除のみ検出）。"""
        diff = """--- a/src/module.py
+++ b/src/module.py
@@ -10,0 +10,7 @@
+def new_function():
+    pass
"""
        result = extract_deleted_symbols(diff)
        assert len(result) == 0

    def test_継承を持つクラスを処理する(self):
        """継承を持つクラス定義を処理する。"""
        diff = """--- a/src/module.py
+++ b/src/module.py
@@ -10,7 +10,0 @@
-class MyClass(BaseClass):
-    pass
"""
        result = extract_deleted_symbols(diff)
        assert ("class", "MyClass", "src/module.py") in result

    def test_重複するシンボルは1回だけ抽出する(self):
        """同じシンボルが複数回削除されても1回だけ抽出する。"""
        diff = """--- a/src/module.py
+++ b/src/module.py
@@ -10,7 +10,0 @@
-def same_func():
-    pass
--- a/src/module.py
+++ b/src/module.py
@@ -50,7 +50,0 @@
-def same_func():
-    pass
"""
        result = extract_deleted_symbols(diff)
        count = sum(1 for _, name, _ in result if name == "same_func")
        assert count == 1


class TestFindTestReferences:
    """find_test_references関数のテスト。"""

    def test_参照が見つからない場合は空リストを返す(self, monkeypatch):
        """参照が見つからない場合は空リストを返す。"""

        def mock_run(*args, **kwargs):
            result = subprocess.CompletedProcess(args[0], 1)
            result.stdout = ""
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)

        # Issue #1958: source_file引数を追加
        result = find_test_references("nonexistent_function", "src/module.py")
        assert result == []

    def test_git_grepの出力を正しくパースする(self, monkeypatch):
        """git grepの出力を正しくパースする。"""

        call_count = 0

        def mock_run(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            result = subprocess.CompletedProcess(args[0], 0)
            if call_count == 1:
                # git grep
                result.stdout = "tests/test_module.py:42:from module import my_function\n"
            else:
                # git show for import check - return content with module import
                result.stdout = "from module import my_function"
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)

        # Issue #1958: source_file引数を追加
        result = find_test_references("my_function", "module.py")
        assert len(result) == 1
        assert result[0] == ("tests/test_module.py", 42, "from module import my_function")

    def test_複数の参照を処理する(self, monkeypatch):
        """複数の参照を処理する。"""

        call_count = 0

        def mock_run(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            result = subprocess.CompletedProcess(args[0], 0)
            if call_count == 1:
                # git grep
                result.stdout = """tests/test_a.py:10:import my_func
tests/test_b.py:20:my_func()
tests/test_c.py:30:assert my_func
"""
            else:
                # git show for import check - return content with module import
                result.stdout = "from my_module import my_func"
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)

        # Issue #1958: source_file引数を追加
        result = find_test_references("my_func", "my_module.py")
        assert len(result) == 3

    def test_例外発生時は空リストを返す(self, monkeypatch):
        """例外発生時は空リストを返す（fail open）。"""

        def mock_run(*args, **kwargs):
            raise OSError("git not found")

        monkeypatch.setattr(subprocess, "run", mock_run)

        # Issue #1958: source_file引数を追加
        result = find_test_references("my_function", "src/module.py")
        assert result == []

    def test_タイムアウト時は空リストを返す(self, monkeypatch):
        """タイムアウト時は空リストを返す（fail open）。"""

        def mock_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="git", timeout=10)

        monkeypatch.setattr(subprocess, "run", mock_run)

        # Issue #1958: source_file引数を追加
        result = find_test_references("my_function", "src/module.py")
        assert result == []


class TestMain:
    """main()関数の統合テスト。"""

    def test_削除された関数がテストに参照されている場合はブロックする(self, monkeypatch):
        """削除された関数がテストで参照されている場合は1を返す。"""

        def mock_get_staged_diff():
            return """--- a/src/module.py
+++ b/src/module.py
@@ -10,7 +10,0 @@
-def my_function():
-    pass
"""

        def mock_run(*args, **kwargs):
            cmd = args[0]
            if cmd[0] == "git" and cmd[1] == "grep":
                result = subprocess.CompletedProcess(cmd, 0)
                result.stdout = "tests/test_module.py:10:from module import my_function\n"
                return result
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

        monkeypatch.setattr(_module, "get_staged_diff", mock_get_staged_diff)
        monkeypatch.setattr(subprocess, "run", mock_run)

        result = _module.main()
        assert result == 1

    def test_削除された関数のテスト参照も更新済みの場合はパスする(self, monkeypatch):
        """削除された関数のテスト参照がない場合は0を返す。"""

        def mock_get_staged_diff():
            return """--- a/src/module.py
+++ b/src/module.py
@@ -10,7 +10,0 @@
-def old_function():
-    pass
"""

        def mock_run(*args, **kwargs):
            cmd = args[0]
            if cmd[0] == "git" and cmd[1] == "grep":
                # 参照なし
                result = subprocess.CompletedProcess(cmd, 1)
                result.stdout = ""
                return result
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

        monkeypatch.setattr(_module, "get_staged_diff", mock_get_staged_diff)
        monkeypatch.setattr(subprocess, "run", mock_run)

        result = _module.main()
        assert result == 0

    def test_プライベート関数の削除はスキップする(self, monkeypatch):
        """_で始まるプライベート関数は検査をスキップする。"""

        def mock_get_staged_diff():
            return """--- a/src/module.py
+++ b/src/module.py
@@ -10,7 +10,0 @@
-def _private_helper():
-    pass
"""

        # find_test_referencesが呼ばれないことを確認するためにモック
        call_count = {"grep": 0}

        def mock_run(*args, **kwargs):
            cmd = args[0]
            if cmd[0] == "git" and cmd[1] == "grep":
                call_count["grep"] += 1
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

        monkeypatch.setattr(_module, "get_staged_diff", mock_get_staged_diff)
        monkeypatch.setattr(subprocess, "run", mock_run)

        result = _module.main()
        assert result == 0
        assert call_count["grep"] == 0  # git grepは呼ばれない

    def test_ダンダーメソッドの削除は検査対象とする(self, monkeypatch):
        """__init__など__で始まるメソッドは検査する。"""

        def mock_get_staged_diff():
            return """--- a/src/module.py
+++ b/src/module.py
@@ -10,7 +10,0 @@
-def __init__(self):
-    pass
"""

        def mock_run(*args, **kwargs):
            cmd = args[0]
            if cmd[0] == "git" and cmd[1] == "grep":
                result = subprocess.CompletedProcess(cmd, 0)
                result.stdout = "tests/test_module.py:20:obj.__init__()\n"
                return result
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

        monkeypatch.setattr(_module, "get_staged_diff", mock_get_staged_diff)
        monkeypatch.setattr(subprocess, "run", mock_run)

        result = _module.main()
        assert result == 1

    def test_差分がない場合はパスする(self, monkeypatch):
        """ステージされた差分がない場合は0を返す。"""

        def mock_get_staged_diff():
            return ""

        monkeypatch.setattr(_module, "get_staged_diff", mock_get_staged_diff)

        result = _module.main()
        assert result == 0

    def test_削除されたシンボルがない場合はパスする(self, monkeypatch):
        """シンボルの削除がない差分の場合は0を返す。"""

        def mock_get_staged_diff():
            # 関数/クラスの削除ではなく、単なるコード変更
            return """--- a/src/module.py
+++ b/src/module.py
@@ -10,1 +10,1 @@
-    x = 1
+    x = 2
"""

        monkeypatch.setattr(_module, "get_staged_diff", mock_get_staged_diff)

        result = _module.main()
        assert result == 0

    def test_複数のシンボル削除で一部のみ参照がある場合はブロックする(self, monkeypatch):
        """複数のシンボルが削除され、一部だけテストで参照されている場合はブロック。"""

        def mock_get_staged_diff():
            return """--- a/src/module.py
+++ b/src/module.py
@@ -10,7 +10,0 @@
-def func_one():
-    pass
-
-def func_two():
-    pass
"""

        def mock_run(*args, **kwargs):
            cmd = args[0]
            if cmd[0] == "git" and cmd[1] == "grep":
                symbol = cmd[4]  # -w の次の引数
                if symbol == "func_one":
                    result = subprocess.CompletedProcess(cmd, 0)
                    result.stdout = "tests/test_module.py:10:func_one()\n"
                    return result
                # func_twoは参照なし
                result = subprocess.CompletedProcess(cmd, 1)
                result.stdout = ""
                return result
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

        monkeypatch.setattr(_module, "get_staged_diff", mock_get_staged_diff)
        monkeypatch.setattr(subprocess, "run", mock_run)

        result = _module.main()
        assert result == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
