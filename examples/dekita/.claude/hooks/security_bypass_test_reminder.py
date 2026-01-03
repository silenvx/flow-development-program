#!/usr/bin/env python3
# - 責務: セキュリティガードファイル編集時にバイパステストの追加を促す
# - 重複なし: バイパステストチェックは他のフックにない
# - ブロック: なし（警告のみ、決定はエージェントに委ねる）
# - AGENTS.md: 「設定済みのフック」セクションに記載予定
"""
セキュリティガードファイル編集時にバイパステストの追加を促す。

Issue #1006の振り返りで発見された教訓:
- branch_rename_guardが--color=alwaysでバイパスできた
- セキュリティガード実装時は「バイパステスト」を意識すべき

このフックは、ガードファイル（*_guard.py, *_block.py, *_check.py）の
編集時に、対応するテストファイルにバイパステストがあるか確認し、
なければ警告メッセージを出力する。

対象ツール: Edit, Write
"""

import json
import re
from pathlib import Path

from lib.execution import log_hook_execution
from lib.input_context import extract_input_context, merge_details_with_context
from lib.session import parse_hook_input

HOOK_NAME = "security-bypass-test-reminder"

# セキュリティガードファイルのパターン
SECURITY_GUARD_PATTERNS = [
    r".*_guard\.py$",
    r".*_block\.py$",
    r".*_check\.py$",
    r".*-guard\.py$",
    r".*-block\.py$",
    r".*-check\.py$",
]

# テストファイルでバイパステストを示すキーワード
BYPASS_TEST_KEYWORDS = [
    "bypass",
    "バイパス",
    "circumvent",
    "evade",
    "escape",
]


def is_security_guard_file(file_path: str) -> bool:
    """ファイルがセキュリティガードファイルかどうか判定する。"""
    for pattern in SECURITY_GUARD_PATTERNS:
        if re.search(pattern, file_path, re.IGNORECASE):
            return True
    return False


def get_test_file_path(guard_file_path: str) -> Path | None:
    """ガードファイルに対応するテストファイルのパスを取得する。"""
    path = Path(guard_file_path)

    # ハイフン付きファイル名をアンダースコアに正規化
    # 例: checkout-block.py -> checkout_block.py
    normalized_name = path.name.replace("-", "_")

    # 両方のパターンを試す（オリジナルと正規化版）
    test_names = [f"test_{path.name}"]
    if normalized_name != path.name:
        test_names.append(f"test_{normalized_name}")

    # .claude/hooks/xxx_guard.py -> .claude/hooks/tests/test_xxx_guard.py
    if path.parent.name == "hooks":
        for test_name in test_names:
            test_path = path.parent / "tests" / test_name
            if test_path.exists():
                return test_path

    # 他のパターンも試す
    for test_name in test_names:
        # xxx_guard.py -> test_xxx_guard.py (同じディレクトリ)
        same_dir_test = path.parent / test_name
        if same_dir_test.exists():
            return same_dir_test

        # tests/test_xxx_guard.py
        tests_dir_test = path.parent / "tests" / test_name
        if tests_dir_test.exists():
            return tests_dir_test

    return None


def has_bypass_test(test_file_path: Path) -> bool:
    """テストファイルにバイパステストがあるか確認する。"""
    try:
        content = test_file_path.read_text(encoding="utf-8").lower()
        return any(keyword in content for keyword in BYPASS_TEST_KEYWORDS)
    except (OSError, UnicodeDecodeError):
        return False  # ファイル読み取りエラー時はFail-open


def main() -> None:
    """セキュリティガードファイル編集時にバイパステストを確認する。"""
    hook_input = parse_hook_input()
    input_context = extract_input_context(hook_input)
    tool_name = hook_input.get("tool_name", "")

    # Edit/Writeツールのみ対象
    if tool_name not in ("Edit", "Write"):
        result = {"decision": "approve"}
        print(json.dumps(result))
        return

    tool_input = hook_input.get("tool_input", {})
    file_path = tool_input.get("file_path", "")

    # セキュリティガードファイル以外は無視
    if not is_security_guard_file(file_path):
        result = {"decision": "approve"}
        print(json.dumps(result))
        return

    # テストファイル自体の編集は無視
    if "test_" in Path(file_path).name:
        result = {"decision": "approve"}
        print(json.dumps(result))
        return

    # 対応するテストファイルを探す
    test_file = get_test_file_path(file_path)

    if test_file is None:
        # テストファイルがない場合は警告（ブロックはしない）
        log_hook_execution(
            HOOK_NAME,
            "approve",
            f"No test file found for {file_path}",
            merge_details_with_context({"file_path": file_path}, input_context),
        )
        result = {
            "decision": "approve",
            "message": f"""[{HOOK_NAME}] ⚠️ セキュリティガードファイルを編集していますが、テストファイルが見つかりません。

**推奨アクション:**
1. テストファイルを作成: test_{Path(file_path).name}
2. バイパステストケースを追加（例: test_*_bypass, test_*_circumvent）

**バイパステストの観点:**
- このガードを回避する方法はないか？
- 正規表現パターンを迂回できないか？
- エッジケースで誤ってapproveしないか？

参考: Issue #1006で--color=alwaysによるバイパスが発見された教訓""",
        }
        print(json.dumps(result))
        return

    # バイパステストがあるか確認
    if has_bypass_test(test_file):
        log_hook_execution(
            HOOK_NAME,
            "approve",
            f"Bypass test found in {test_file}",
            merge_details_with_context(
                {"file_path": file_path, "test_file": str(test_file)},
                input_context,
            ),
        )
        result = {"decision": "approve"}
        print(json.dumps(result))
        return

    # バイパステストがない場合は警告
    log_hook_execution(
        HOOK_NAME,
        "approve",
        f"No bypass test in {test_file}",
        merge_details_with_context(
            {"file_path": file_path, "test_file": str(test_file)},
            input_context,
        ),
    )

    result = {
        "decision": "approve",
        "message": f"""[{HOOK_NAME}] ⚠️ セキュリティガードファイルを編集していますが、バイパステストが見つかりません。

**対象ファイル:** {file_path}
**テストファイル:** {test_file}

**推奨アクション:**
テストファイルに以下のようなバイパステストを追加してください:

```python
def test_<guard_name>_bypass_with_options(self):
    \"\"\"Should block even with unusual options (bypass prevention).\"\"\"
    # このガードをバイパスしようとするケースをテスト
    ...
```

**バイパステストの観点:**
- このガードを回避する方法はないか？
- 正規表現パターンを迂回できないか？（例: --opt=value形式）
- エッジケースで誤ってapproveしないか？

参考: Issue #1006で--color=alwaysによるバイパスが発見された教訓""",
    }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
