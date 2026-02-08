---
name: implementing-hooks
description: Provides detailed specifications, design principles, and configuration guides for Claude Code hooks. Use when implementing, modifying, or debugging PreToolUse, PostToolUse, or Stop hooks.
---

# フックリファレンス

Claude Codeフックの詳細仕様と設計原則。

## 目次

| ファイル | 内容 |
|----------|------|
| [hook-list.md](hook-list.md) | PreToolUse/PostToolUse/Stopフック一覧 |
| [implementation.md](implementation.md) | 実装ガイド、チェックリスト、共通ライブラリ |
| [patterns.md](patterns.md) | パターン検出、ブロックパターン追加 |
| [templates.md](templates.md) | フックテンプレート集 |
| [advanced.md](advanced.md) | Session ID、プロセス間状態共有、Block評価 |

## クイックスタート: 新規フック実装

新しいフックを作成する前に必ず確認すること。これを怠るとCIでブロックされる。

### 関数シグネチャ（よくある間違い）

| 関数 | 正しい呼び出し | よくある間違い |
|------|---------------|---------------|
| `make_approve_result` | `make_approve_result("hook-name")` | `make_approve_result()` ← 引数必須 |
| `make_block_result` | `make_block_result("hook-name", "理由")` | `make_block_result("理由")` ← 2引数必須 |
| `log_hook_execution` | `log_hook_execution(..., decision="block")` | `log_hook_execution(..., result="block")` ← `decision=`が正しい |

### JSON出力（必須）

```python
# ❌ 悪い例: Python辞書をそのまま出力
print(make_approve_result("my-hook"))  # {'decision': 'approve', ...}

# ✅ 良い例: json.dumpsでJSON文字列に変換
print(json.dumps(make_approve_result("my-hook")))  # {"decision": "approve", ...}
```

**理由**: Claude CodeはJSON形式を期待している。Python辞書形式（シングルクォート）は解析エラーになる。

### テストファイル（CI必須）

新規フックには必ずテストファイルを作成する:

- **ファイル名**: `.claude/hooks/tests/test_<hook名（アンダースコア区切り）>.py`
- **例**: `issue-branch-check.py` → `test_issue_branch_check.py`

CIの `hook-test-requirement-check` がテストなしの新規フックをブロックする。

### 最小実装テンプレート

```python
#!/usr/bin/env python3
"""フックの説明."""
from __future__ import annotations

import json

from lib.execution import log_hook_execution
from lib.results import make_approve_result, make_block_result
from lib.session import parse_hook_input


def main() -> None:
    """メイン処理."""
    hook_input = parse_hook_input()
    tool_name = hook_input.get("tool_name", "")

    # 対象外はスキップ
    if tool_name != "Bash":
        print(json.dumps(make_approve_result("my-hook")))
        return

    # チェックロジック
    # ...

    # ブロック時
    print(json.dumps(make_block_result("my-hook", "ブロック理由")))
    log_hook_execution(
        hook_name="my-hook",
        decision="block",
        reason="ブロック理由",
    )


if __name__ == "__main__":
    main()
```

**詳細は以下を参照**:

- [実装ガイド](implementation.md) - 共通ライブラリ関数の詳細仕様、**エッジケースチェックリスト**
- [テンプレート](templates.md) - 完全なテンプレート集

## フック出力フォーマット

フックはJSON形式で結果を返す:

| フィールド | 説明 |
|-----------|------|
| `decision` | `"approve"` または `"block"` |
| `reason` | ブロック理由（Claudeに送信） |
| `systemMessage` | ユーザー表示メッセージ |

### 出力パターン設計

フックの出力は以下のパターンに従う（不要な出力を最小化）:

| 状況 | JSON出力 | exit code | 理由 |
|------|----------|-----------|------|
| **対象外** | なし | 0 | 対象外で出力するとログが煩雑 |
| **許可** | なし | 0 | 正常動作時は沈黙が原則 |
| **ブロック** | `{"decision": "block", "reason": "..."}` | 0 | 明示的な拒否メッセージが必要 |
| **通知** | `{"decision": "approve", "systemMessage": "..."}` | 0 | 許可しつつ情報を伝達 |

**ヘルパー関数（lib/results.py）**:
- `make_block_result(hook_name, reason)`: ブロック結果を生成
- `make_approve_result(hook_name, message=None)`: 許可結果を生成

## フック設計原則

1. **単一責任**: 1フック = 1責務
2. **疎結合**: フック間の依存最小化
3. **パス解決**: `$CLAUDE_PROJECT_DIR` を使用
4. **SKIP環境変数**: 全ブロッキングフックは `SKIP_*` 環境変数でバイパス可能にする

### 警告 vs ブロックの判断基準

| 条件 | 選択 | 理由 |
|------|------|------|
| **副作用のある操作を防ぎたい** | ブロック | 警告後もコマンドが実行され、副作用は発生してしまう |
| **操作失敗でフロー問題が発生** | ブロック | 例: exit code 1でPostToolUseフックがスキップされる |
| **情報提供のみで操作は許可** | 警告 | ユーザーに判断を委ねる場合 |
| **軽微な問題の通知** | 警告 | 操作自体は問題なく完了する場合 |

## フック統計

| イベント | フック数 |
| -------- | -------- |
| SessionStart | 5 |
| PreToolUse (Navigation) | 1 |
| PreToolUse (Edit/Write) | 3 |
| PreToolUse (Bash) | 34 |
| PostToolUse (Bash) | 17 |
| PostToolUse (Edit) | 2 |
| PostToolUse (Read/Glob/Grep) | 2 |
| PostToolUse (WebSearch/WebFetch) | 1 |
| Stop | 13 |
| **合計** | **78** |

> **注**: ユニークフック数は75種類。一部のフック（task-start-checklist等）は複数のトリガーで発動するため、発動ポイント数（78）とは異なる。
