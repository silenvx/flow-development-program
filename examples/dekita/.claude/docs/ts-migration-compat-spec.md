# Python → TypeScript フック互換レイヤー仕様書

## 概要

Python/TypeScript間でフックの互換性を維持するための仕様。
両言語のフックは同じ入出力形式を使用し、相互運用可能であること。

---

## 入出力形式

### 入力（stdin）

Claude Codeから渡されるJSON形式のフック入力。

```json
{
  "session_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "source": "PreToolUse|PostToolUse|UserPromptSubmit|Stop",
  "tool_name": "Bash|Edit|Write|...",
  "tool_input": { ... },
  "hook_type": "PreToolUse|PostToolUse|UserPromptSubmit|Stop"
}
```

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `session_id` | string | Claude Codeセッション識別子（UUID形式） |
| `source` | string | フックの発火タイミング |
| `tool_name` | string? | ツール名（PreToolUse/PostToolUseのみ） |
| `tool_input` | object? | ツール入力（PreToolUse/PostToolUseのみ） |
| `hook_type` | string | フックタイプ |

### 出力（stdout）

フック判定結果のJSON。

```json
{
  "decision": "approve|block",
  "reason": "ブロック理由（blockの場合）",
  "systemMessage": "短いメッセージ（UIに表示）"
}
```

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `decision` | string | `"approve"` または `"block"` |
| `reason` | string? | ブロック理由（blockの場合のみ） |
| `systemMessage` | string? | UI表示用の短いメッセージ |

---

## Exit Code

| 終了コード | 意味 | 説明 |
|-----------|------|------|
| `0` | 承認 | ツール実行を許可 |
| `2` | ブロック | ツール実行を拒否 |
| その他 | エラー | フック自体のエラー |

**重要**: Exit code 1はエラーとして扱われる。明示的なブロックにはexit code 2を使用。

---

## 標準エラー出力（stderr）

ブロック時はstderrにも短いメッセージを出力（デバッグ・ログ用）。

```
[hook-name] ブロック理由の1行目
```

---

## 関数シグネチャの対応

### Python（lib/results.py）

```python
def make_block_result(
    hook_name: str,
    reason: str,
    ctx: HookContext | None = None,
) -> dict:
    """ブロック結果を作成"""

def make_approve_result() -> dict:
    """承認結果を作成"""
```

### TypeScript（lib/results.ts）

```typescript
function makeBlockResult(
  hookName: string,
  reason: string,
  ctx?: HookContext | null,
): HookResult

function makeApproveResult(
  hookName?: string,
  message?: string,
): HookResult
```

### ヘルパー関数

| Python | TypeScript | 説明 |
|--------|------------|------|
| `print_block_and_exit()` | `blockAndExit()` | ブロック結果を出力して終了 |
| `print_approve_and_exit()` | `approveAndExit()` | 承認結果を出力して終了 |
| `print_continue_and_log_skip()` | - | 早期リターン用（Python固有） |

---

## ログ形式

### 実行ログ（JSONL）

両言語で同じJSONL形式を使用。

**ファイル**: `.claude/logs/execution/hook-execution-{session_id}.jsonl`

```json
{
  "timestamp": "2026-01-15T11:53:04+09:00",
  "hook": "merge-check",
  "decision": "block",
  "reason": "レビュー未完了",
  "session_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

### 警告ログ

**ファイル**: `.claude/logs/execution/hook-warnings.log`

```
2026-01-15T11:53:04+09:00 [hook-name] 警告メッセージ
```

---

## 定数

### CONTINUATION_HINT

ブロック後のテキストのみ応答を防止するためのヒント。

```
\n\n💡 ブロック後も作業を継続してください。
代替アクションのツール呼び出しを行い、テキストのみの応答で終わらないでください。
```

両言語で同一の文字列を使用（`lib/constants.py`, `lib/constants.ts`）。

---

## セッション管理

### HookContext

両言語で同等の構造体/型。

| フィールド | Python型 | TypeScript型 | 説明 |
|-----------|----------|--------------|------|
| `session_id` | `str | None` | `string | null` | セッションID |
| `source` | `str | None` | `string | null` | フック発火元 |
| `tool_name` | `str | None` | `string | null` | ツール名 |
| `tool_input` | `dict | None` | `Record<string, unknown> | null` | ツール入力 |
| `cwd` | `str | None` | `string | null` | 作業ディレクトリ |

### パース関数

| Python | TypeScript | 説明 |
|--------|------------|------|
| `create_hook_context()` | `parseHookInput()` | stdin JSONをパース |

---

## 実装の差異

### Python固有機能

1. **連続ブロック検出**: 60秒以内に同一フックで2回以上ブロック時に警告
2. **ログ自動記録**: `make_block_result()`がブロックをログに記録
3. **SKIP環境変数**: `SKIP_{HOOK_NAME}=1` でフックをスキップ

### TypeScript実装状況

| 機能 | 実装状況 | 備考 |
|------|----------|------|
| 基本ブロック/承認 | ✅ 完了 | |
| ログ記録 | ❌ 未実装 | execution.tsで実装予定 |
| 連続ブロック検出 | ❌ 未実装 | Phase 2で実装 |
| SKIP環境変数 | ❌ 未実装 | Phase 2で実装 |

---

## 検証方法

### 出力比較

同じ入力に対して、Python/TypeScript両方のフックを実行し、出力を比較。

```bash
# Python版
echo '{"session_id": "test", "tool_name": "Bash"}' | python3 .claude/hooks/some_hook.py

# TypeScript版
echo '{"session_id": "test", "tool_name": "Bash"}' | bun run .claude/hooks/handlers/some_hook.ts

# 差分比較
diff <(python3 ... | jq -S) <(bun run ... | jq -S)
```

### Exit code確認

```bash
python3 .claude/hooks/some_hook.py; echo "Exit: $?"
bun run .claude/hooks/handlers/some_hook.ts; echo "Exit: $?"
```

---

## subprocess互換性検証結果

2026-01-15実行の検証結果（`.claude/scripts/verify-subprocess-compat.ts`）。

### テスト項目

| テスト | コマンド | 結果 |
|--------|----------|------|
| git-rev-parse | `git rev-parse --abbrev-ref HEAD` | ✅ 互換 |
| git-status | `git status --porcelain` | ✅ 互換 |
| echo-unicode | `echo 日本語テスト🎉` | ✅ 互換 |
| nonexistent-command | `nonexistent_command_12345` | ✅ 互換 |
| git-log-format | `git log -1 --format=%H\t%an\t%s` | ✅ 互換 |

### 結論

**Python subprocess.run と Bun.spawn は互換性がある。**

以下の観点で差異なし:
- Exit code
- stdout/stderr出力
- Unicodeエンコーディング
- エラー処理（コマンド不存在時）

TypeScript移行時のsubprocess使用に問題なし。

---

## 参照

- Python lib: `.claude/hooks/lib/`
- TypeScript lib: `.claude/hooks/lib/`
- 計画書: `.claude/plans/rippling-foraging-pony.md`
- Issue #2816: Python → TypeScript移行計画
- 検証スクリプト: `.claude/scripts/verify-subprocess-compat.ts`
