# Python ↔ TypeScript フック互換レイヤー仕様書

Issue #2814: TypeScript移行における互換性保証のための仕様書。

## 概要

PythonフックをTypeScriptに移行する際、以下の互換性を維持する必要がある:

1. **入力形式**: stdin JSON（Claude Code → フック）
2. **出力形式**: stdout JSON（フック → Claude Code）
3. **終了コード**: approve=0, block=2
4. **ログ形式**: JSONL（セッション単位）
5. **副作用**: stderr出力、ファイル書き込み

---

## 1. 入力形式（stdin JSON）

### 1.1 共通フィールド

| フィールド | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `session_id` | string | ✅ | セッションID（UUID形式） |
| `tool_name` | string | △ | ツール名（PreToolUse/PostToolUseで必須） |
| `tool_input` | object | △ | ツール固有の入力 |
| `cwd` | string | - | 現在の作業ディレクトリ |
| `source` | string | △ | セッションソース（SessionStartで必須） |
| `transcript_path` | string | - | transcriptファイルパス |
| `hook_event_name` | string | - | フックイベント名 |

### 1.2 イベント別入力例

#### SessionStart

```json
{
  "session_id": "5f161882-326a-458f-bb93-d600bbe0fd64",
  "source": "resume",
  "transcript_path": "/Users/xxx/.claude/projects/xxx/5f161882-326a-458f-bb93-d600bbe0fd64.jsonl",
  "cwd": "/Users/xxx/project"
}
```

#### PreToolUse (Bash)

```json
{
  "session_id": "5f161882-326a-458f-bb93-d600bbe0fd64",
  "tool_name": "Bash",
  "tool_input": {
    "command": "git status"
  },
  "cwd": "/Users/xxx/project"
}
```

#### Stop

```json
{
  "session_id": "5f161882-326a-458f-bb93-d600bbe0fd64",
  "stop_hook_active": true
}
```

**注**: `stop_hook_active` はStop hookでのみ使用されるフィールド。`true`の場合、セッション終了処理中であることを示す。

### 1.3 Python実装

```python
from lib.session import parse_hook_input, create_hook_context

def main():
    hook_input = parse_hook_input()  # stdinからJSON読み取り
    ctx = create_hook_context(hook_input)
    session_id = ctx.get_session_id()
    # ...
```

### 1.4 TypeScript実装

```typescript
import { parseHookInput, createContext } from "./lib/session";

async function main() {
  const hookInput = await parseHookInput();  // stdinからJSON読み取り
  const ctx = createContext(hookInput);
  const sessionId = ctx.sessionId;
  // ...
}
```

---

## 2. 出力形式（stdout JSON）

### 2.1 判定結果

| 判定 | 必須フィールド | オプションフィールド |
|------|---------------|---------------------|
| **approve** | `decision: "approve"` | `systemMessage` |
| **block** | `decision: "block"`, `reason` | `systemMessage` |
| **continue** | `continue: true` | - |

### 2.2 approve出力

**Python版**:
```json
{
  "decision": "approve",
  "systemMessage": "✅ hook-name: OK"
}
```

**TypeScript版**:
```json
{
  "decision": "approve"
}
```

**差異**: Python版は絵文字付きの`systemMessage`を返す。TypeScript版は`systemMessage`を省略可能。

### 2.3 block出力

**Python版**:
```json
{
  "decision": "block",
  "reason": "[hook-name] ブロック理由...\n\n💡 ブロック後も作業を継続してください。\n代替アクションのツール呼び出しを行い、テキストのみの応答で終わらないでください。",
  "systemMessage": "❌ hook-name: ブロック理由（1行目のみ）"
}
```

**TypeScript版**:
```json
{
  "decision": "block",
  "reason": "[hook-name] ブロック理由...\n\n💡 ブロック後も作業を継続してください。\n代替アクションのツール呼び出しを行い、テキストのみの応答で終わらないでください。",
  "systemMessage": "[hook-name] ブロック理由（1行目のみ）"
}
```

**差異**: Python版は`systemMessage`に絵文字（❌）を含む。TypeScript版は絵文字なし。stderrへの絵文字出力はセクション4参照。

### 2.4 continue出力（PreToolUse早期リターン用）

```json
{
  "continue": true
}
```

### 2.5 Python実装

```python
from lib.results import make_block_result, make_approve_result

# ブロック
result = make_block_result("hook-name", "理由", ctx=ctx)
print(json.dumps(result))
sys.exit(2)  # 重要: ブロックは終了コード2

# 承認
result = make_approve_result("hook-name", "メッセージ")
print(json.dumps(result))
sys.exit(0)

# 早期リターン（PreToolUse）
from lib.results import print_continue_and_log_skip
print_continue_and_log_skip("hook-name", "スキップ理由", ctx=ctx)
return
```

### 2.6 TypeScript実装

```typescript
import { makeBlockResult, makeApproveResult, outputResult, blockAndExit, approveAndExit } from "./lib/results";

// ブロック
blockAndExit("hook-name", "理由", ctx);

// 承認
approveAndExit("hook-name");

// 手動出力
const result = makeBlockResult("hook-name", "理由", ctx);
outputResult(result);
```

---

## 3. 終了コード

| 判定 | 終了コード | 意味 |
|------|-----------|------|
| approve | 0 | 操作を許可 |
| block | 2 | 操作をブロック |
| continue | 0 | PreToolUse早期リターン（許可） |
| error | 1 | フック実行エラー（fail-open） |

**重要**: Claude Codeは終了コード2をブロックとして解釈する。それ以外は許可。

### Python実装

```python
import sys
sys.exit(0)  # approve
sys.exit(2)  # block
```

### TypeScript実装

```typescript
process.exit(0);  // approve
process.exit(2);  // block

// または outputResult() が自動処理
outputResult(result);  // decision="block" なら exit(2)
```

---

## 4. stderr出力

### 4.1 目的

- ユーザーへの可視化（Claude Codeはstdoutを表示しない場合がある）
- デバッグ情報の出力

### 4.2 形式（Python/TypeScript差異あり）

**Python版**:
```
❌ hook-name: ブロック理由（1行目）
```

**TypeScript版**:
```
[hook-name] ブロック理由（1行目）
```

**差異**: Python版は絵文字（❌）を含む。TypeScript版は`systemMessage`をそのままstderrに出力するため絵文字なし。

### 4.3 Python実装

```python
print(f"❌ {hook_name}: {first_line}", file=sys.stderr)
```

### 4.4 TypeScript実装

```typescript
// systemMessageをそのままstderrに出力
console.error(systemMessage);  // "[hook-name] ..." 形式
```

**注**: この差異は視覚的なもののみで、互換性に影響しない。将来的にTypeScript版に絵文字を追加して統一することを推奨。

---

## 5. CONTINUATION_HINT

### 5.1 目的

ブロック後にClaudeがテキストのみの応答で終わらないよう促す。

### 5.2 内容

```
💡 ブロック後も作業を継続してください。
代替アクションのツール呼び出しを行い、テキストのみの応答で終わらないでください。
```

### 5.3 Python実装

```python
from lib.constants import CONTINUATION_HINT
reason = f"[{hook_name}] {reason}{CONTINUATION_HINT}"
```

### 5.4 TypeScript実装

```typescript
import { CONTINUATION_HINT } from "./constants";
const fullReason = `[${hookName}] ${reason}${CONTINUATION_HINT}`;
```

---

## 6. HookContext

### 6.1 目的

依存性注入パターンでセッション情報を管理し、テスタビリティを向上。

### 6.2 Python実装

```python
@dataclass
class HookContext:
    session_id: str | None = None

    def get_session_id(self) -> str | None:
        return self.session_id
```

### 6.3 TypeScript実装

```typescript
interface HookContext {
  sessionId: string | null;
  cwd: string | null;
  rawInput: HookInput;
}
```

**実装上の差異**: TypeScriptは内部的に `rawInput`（入力JSON全体）を保持するが、Pythonは保持しない。この差異は外部インターフェースおよび互換レイヤーの期待される動作には影響しない。

---

## 7. 機能差分一覧

### 7.1 Python固有機能（TypeScript未実装）

| 機能 | Python関数 | 優先度 | 備考 |
|------|-----------|--------|------|
| **fork-session検出** | `is_fork_session()` | 高 | date_context_injector移行に必要 |
| 連続ブロック検出 | `_count_recent_blocks()` | 中 | 警告表示 |
| ブロックログ記録 | `log_hook_execution()` | 中 | セッション分析用 |
| セッションマーカー | `check_and_update_session_marker()` | 低 | 1回/セッション処理 |
| transcript解析 | `get_session_ancestry()` | 低 | fork解析用 |

### 7.2 TypeScript未実装の詳細

#### is_fork_session()

```python
def is_fork_session(
    current_session_id: str,
    source: str,
    transcript_path: str | None = None,
) -> bool:
    """fork-session（新session_idで会話履歴継承）を検出"""
    # 1. source="compact" → False（コンテキスト圧縮）
    # 2. source="resume" → 複数の検出方法:
    #    a. hook session_id vs transcript filename
    #    b. transcript内の異なるsessionId検出
    #    c. fork transcript探索
```

**移行時の注意**: この機能がないと、fork-sessionで `source: "fork"` を設定できない。`date_context_injector` はこの関数を使用してfork-sessionを検出し、適切なコンテキスト（Session開始情報）を注入する。TypeScript版に `is_fork_session()` を実装するまで、`date_context_injector` はPython版を使用し続ける必要がある。

#### 連続ブロック検出

```python
def _count_recent_blocks(hook_name: str, session_id: str | None) -> int:
    """60秒以内の同一フックブロック回数をカウント"""
```

警告メッセージ:
```
⚠️ 【警告】このフックで3回連続ブロック中！
上記のメッセージを必ず読み、指示に従ってください。
```

---

## 8. シャドウモード仕様

### 8.1 目的

Python/TypeScript両方を実行し、出力差異を検証してから切り替える。

### 8.2 実行フロー

```
stdin → Python hook → stdout1 (+ stderr1)
     ↘
       TypeScript hook → stdout2 (+ stderr2)

比較: stdout1 vs stdout2
差異あり → 警告ログ出力
差異なし → 切り替え可能
```

### 8.3 差分比較ツール（Phase1で作成）

```bash
# 使用例
echo '{"session_id":"xxx","source":"resume"}' | python3 diff_hook_output.py \
  --python .claude/hooks/date_context_injector.py \
  --typescript .claude/hooks/handlers/date_context_injector.ts
```

### 8.4 比較対象

| 項目 | 比較方法 |
|------|---------|
| stdout JSON | JSONパース後に比較（キー順序無視） |
| 終了コード | 完全一致 |
| stderr | 警告のみ（差異許容） |

### 8.5 副作用の扱い

| 副作用分類 | シャドウ時の扱い |
|-----------|-----------------|
| 安全（読み取り専用） | 両方実行 |
| 注意（ファイル書き込み） | TypeScriptはdry-run |
| 禁止（外部API） | TypeScriptは実行しない |

---

## 9. 定数同期

### 9.1 同期が必要な定数

| 定数 | Python | TypeScript |
|------|--------|------------|
| CONTINUATION_HINT | ✅ 同一 | ✅ 同一 |
| TIMEOUT_* | ✅ 同一 | ✅ 同一 |
| SESSION_GAP_THRESHOLD | ✅ 同一 | ✅ 同一 |
| LOG_* | ✅ 同一 | ✅ 同一 |

### 9.2 同期確認コマンド

```bash
# 差分確認
diff <(grep -E "^[A-Z_]+ = " .claude/hooks/lib/constants.py) \
     <(grep -E "^export const [A-Z_]+" .claude/hooks/lib/constants.ts)
```

**CI検証の推奨**: 定数の同期漏れを防ぐため、CIでの自動検証を推奨。特に `CONTINUATION_HINT` のような重要な定数は、両言語で同一であることをCIで保証することで、移行時の不整合を防止できる。

---

## 10. テスト戦略

### 10.1 単体テスト

| 言語 | フレームワーク | 場所 |
|------|---------------|------|
| Python | pytest | `.claude/hooks/tests/` |
| TypeScript | Vitest | `.claude/hooks/tests/` |

### 10.2 互換性テスト

```bash
# 同一入力で両言語の出力を比較
echo '{"session_id":"test","source":"resume"}' | tee \
  >(python3 hook.py > /tmp/py.json) \
  >(bun run hook.ts > /tmp/ts.json) \
  > /dev/null

diff /tmp/py.json /tmp/ts.json
```

---

## 11. 移行チェックリスト

フックをTypeScriptに移行する際のチェックリスト:

- [ ] 入力パース: `parseHookInput()` 使用
- [ ] コンテキスト: `createContext()` 使用
- [ ] 出力形式: `makeBlockResult()` / `makeApproveResult()` 使用
- [ ] 終了コード: `outputResult()` または明示的な `process.exit()`
- [ ] stderr出力: `console.error()` でブロック理由表示
- [ ] CONTINUATION_HINT: ブロック理由に含める
- [ ] 副作用分類: 安全/注意/禁止をドキュメント化
- [ ] シャドウテスト: Python版と出力比較
- [ ] 機能パリティ: fork-session等の特殊機能対応

---

## 参照

- Python実装: `.claude/hooks/lib/results.py`, `session.py`, `constants.py`
- TypeScript実装: `.claude/hooks/lib/results.ts`, `session.ts`, `constants.ts`, `types.ts`
- 移行計画: Issue #2814 を参照
