# フックテンプレート

新規フック作成時のボイラープレート。再作業を防ぐため、テストから先に書く（TDD）。

## 1. テストファイルを先に作成

```python
# .claude/hooks/tests/test_my_hook.py
"""Tests for my_hook.py"""
import json
from unittest.mock import patch

import pytest


class TestMyHook:
    """Test cases for my-hook."""

    def test_approves_when_not_target_command(self):
        """対象外コマンドは許可される."""
        # Given
        hook_input = {"tool_name": "Bash", "tool_input": {"command": "ls -la"}}

        # When
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = json.dumps(hook_input)
            # フックをインポートして実行
            # ...

        # Then: 出力なし（対象外）

    def test_blocks_when_target_command(self):
        """対象コマンドはブロックされる."""
        # Given
        hook_input = {"tool_name": "Bash", "tool_input": {"command": "target command"}}

        # When / Then
        # ...

    def test_approves_when_skip_env_set(self):
        """SKIP_MY_HOOK=1 でスキップ."""
        # Given
        hook_input = {"tool_name": "Bash", "tool_input": {"command": "SKIP_MY_HOOK=1 target command"}}

        # When / Then
        # ...

    def test_handles_empty_input(self):
        """空入力でクラッシュしない."""
        # Given
        hook_input = {}

        # When / Then: 例外なく処理

    def test_handles_invalid_json(self):
        """不正JSONでクラッシュしない."""
        # ...
```

## 2. フック本体を実装

```python
#!/usr/bin/env python3
"""My hook description.

What it does:
- Check A
- Block B
"""

import json
import os

from lib.execution import log_hook_execution
from lib.results import make_block_result
from lib.session import parse_hook_input
from lib.strings import extract_inline_skip_env, is_skip_env_enabled

SKIP_ENV = "SKIP_MY_HOOK"


def should_block(command: str) -> tuple[bool, str]:
    """Check if command should be blocked.

    Args:
        command: The command string to check.

    Returns:
        Tuple of (should_block, reason).
    """
    # 対象コマンドのチェックロジック
    if "target pattern" in command:
        return True, "この操作はブロックされました。"
    return False, ""


def main() -> None:
    """Entry point for the hook."""
    data = parse_hook_input()
    if not data:
        return

    tool_name = data.get("tool_name", "")
    if tool_name != "Bash":
        return  # 対象外

    command = data.get("tool_input", {}).get("command", "")
    if not command:
        return  # 空コマンドは対象外

    # SKIP環境変数チェック
    if is_skip_env_enabled(os.environ.get(SKIP_ENV)):
        log_hook_execution("my-hook", "skip", f"{SKIP_ENV}=1")
        print(json.dumps({"decision": "approve"}))
        return

    inline_value = extract_inline_skip_env(command, SKIP_ENV)
    if is_skip_env_enabled(inline_value):
        log_hook_execution("my-hook", "skip", f"{SKIP_ENV}=1 (inline)")
        print(json.dumps({"decision": "approve"}))
        return

    # メインチェック
    should_block_result, reason = should_block(command)
    if should_block_result:
        result = make_block_result("my-hook", reason)
        log_hook_execution("my-hook", "block", reason, {"command": command})
        print(json.dumps(result))
        return

    # 対象外またはOK: 出力なし


if __name__ == "__main__":
    main()
```

## 3. settings.jsonに登録

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 $CLAUDE_PROJECT_DIR/.claude/hooks/my_hook.py"
          }
        ]
      }
    ]
  }
}
```

## チェックリスト

- [ ] テストを先に書く（TDD）
- [ ] 最低3つのテストケース（正常・境界・エラー）
- [ ] SKIP環境変数のサポート
- [ ] Fail-open設計（エラー時は許可）
- [ ] `log_hook_execution()` でログ記録
- [ ] docstring追加（D101-D103対応）

## TypeScriptフックテンプレート

新規フックはTypeScript（Bun）で実装する。

```typescript
#!/usr/bin/env bun
/**
 * My hook description.
 *
 * What it does:
 * - Check A
 * - Block B
 */

import { logHookExecution } from "../lib/logging";
import { makeApproveResult, makeBlockResult } from "../lib/results";
import { parseHookInput } from "../lib/session";

const SKIP_ENV = "SKIP_MY_HOOK";

interface HookInput {
  tool_name?: string;
  tool_input?: {
    command?: string;
  };
}

function shouldBlock(command: string): [boolean, string] {
  if (command.includes("target pattern")) {
    return [true, "この操作はブロックされました。"];
  }
  return [false, ""];
}

async function main(): Promise<void> {
  const input = await parseHookInput<HookInput>();

  if (input.tool_name !== "Bash") {
    return; // 対象外
  }

  const command = input.tool_input?.command ?? "";
  if (!command) {
    return; // 空コマンドは対象外
  }

  // SKIP環境変数チェック
  if (process.env[SKIP_ENV] === "1") {
    await logHookExecution("my-hook", "skip", `${SKIP_ENV}=1`);
    console.log(JSON.stringify({ decision: "approve" }));
    return;
  }

  // メインチェック
  const [block, reason] = shouldBlock(command);
  if (block) {
    const result = makeBlockResult("my-hook", reason);
    await logHookExecution("my-hook", "block", reason, { command });
    console.log(JSON.stringify(result));
    return;
  }

  // 対象外またはOK: 出力なし
}

main().catch(console.error);
```

## AIレビュー対応ガイドライン

Copilot/Codexレビューで頻繁に指摘されるパターンと、事前検出の仕組み。

### よくある指摘パターン

| パターン | 事前検出 | 対処法 |
|----------|----------|--------|
| **docstring不足** | ruff D101-D103 | pyproject.tomlで有効化済み |
| **シグネチャ変更時のテスト未更新** | signature_change_check.py | pre-pushで警告 |
| **署名なしのスレッド解決** | resolve-thread-guard | 署名フォーマット必須 |

### docstringルール（D101-D103）

pyproject.tomlで以下のruffルールを有効化:

```toml
[tool.ruff.lint]
select = [
    # ... 既存ルール ...
    "D101",   # Missing docstring in public class
    "D102",   # Missing docstring in public method
    "D103",   # Missing docstring in public function
]

[tool.ruff.lint.pydocstyle]
convention = "google"
```

**ローカル確認**:
```bash
uvx ruff check .claude/hooks/ .claude/scripts/ --select D101,D102,D103
```

### シグネチャ変更チェック

`signature_change_check.py` (pre-push hook):

- 関数シグネチャ（引数・戻り値）の変更を検出
- 対応テストファイルが更新されていない場合に警告
- 警告のみ（ブロックしない）

**Known Limitations**:
- 単一行の関数定義のみ検出（複数行は未対応）
- 関数名の変更は検出対象外

### レビュースレッド解決の署名

`resolve-thread-guard` で必須化されている署名フォーマット:

| パターン | 例 |
|----------|-----|
| 範囲外 | `[対象外] 本PRの範囲外のため対応しない` |
| 軽微 | `[軽微] タイポ修正のため今回は見送り` |
| 対応済み | `[対応済み] コミット abc1234 で修正` |
| 別Issue | `[別Issue] #123 で対応予定` |

**署名なしでResolveすると `merge_check.py` でブロック**される。

### 関連Issue

- Issue #1107: Copilotレビューエラー時の対応手順
- Issue #1108: 関数シグネチャ変更時のテスト更新チェック
- Issue #1113: Copilotレビュー指摘パターンの事前検出
