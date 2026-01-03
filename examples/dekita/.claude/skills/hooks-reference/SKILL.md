---
name: hooks-reference
description: Claude Codeフックの詳細仕様、設計原則、設定方法。フック、hook、PreToolUse、Stop時に使用。
---

# フックリファレンス

Claude Codeフックの詳細仕様と設計原則。

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

### 共通ライブラリ関数の詳細仕様（必読）

フック実装前に必ず確認すること。これを怠るとレビューで指摘される。

#### `make_block_result(hook_name, reason)`

**自動的に行われる処理**（手動で行う必要なし）:

| 処理 | 詳細 |
|------|------|
| **ログ記録** | `log_hook_execution(hook_name, "block", reason)` を内部で呼び出す（Issue #2023） |
| **プレフィックス付与** | reasonに `[{hook_name}]` を自動追加 |
| **継続ヒント付与** | reasonに `CONTINUATION_HINT` を自動追加 |
| **systemMessage生成** | ユーザー表示用のsystemMessageを自動生成 |
| **stderr出力** | `❌ {hook_name}: {first_line}` をstderrに出力 |

**アンチパターン（やってはいけない）**:

```python
# ❌ 悪い例: 重複呼び出し
log_hook_execution("my-hook", "block", reason)  # 不要（make_block_resultが呼ぶ）
result = make_block_result("my-hook", reason)

# ❌ 悪い例: 重複プレフィックス
reason = f"[my-hook] {actual_reason}"  # 不要（make_block_resultが付与）
result = make_block_result("my-hook", reason)

# ✅ 良い例: reasonのみを渡す
result = make_block_result("my-hook", "操作がブロックされました")
```

#### `make_approve_result(hook_name, message=None)`

**仕様**:
- `decision: "approve"` と `systemMessage` を含む辞書を返す
- messageがNoneの場合、`✅ {hook_name}: OK` がsystemMessageになる
- **`reason` フィールドは含まれない**（blockと異なる）

#### その他のヘルパー関数

| 関数 | 用途 |
|------|------|
| `print_continue_and_log_skip(hook_name, reason)` | 対象外時の早期リターン（PreToolUse/PostToolUse用） |
| `print_approve_and_log_skip(hook_name, reason)` | 対象外時の早期リターン（Stop hook用） |
| `check_skip_env(hook_name, env_var)` | SKIP環境変数のチェックとログ記録 |

#### `parse_hook_input()` （lib/session.py）

フック入力の標準的な読み取り方法。以下を自動で行う:

1. stdinからJSONを読み取りパース
2. `session_id` があれば自動で `set_hook_session_id()` を呼び出す
3. パースした辞書を返す（エラー時は空辞書）

**戻り値構造**（フックタイプ別）:

| フックタイプ | 主要フィールド |
|-------------|---------------|
| PreToolUse / PostToolUse | `tool_name`, `tool_input`, `session_id`, `cwd` |
| UserPromptSubmit | `user_prompt`, `session_id`, `cwd` |
| SessionStart | `session_id`, `cwd`, `source`, `transcript_path` |
| Stop | `stop_hook_active`, `session_id`, `cwd` |

**使用例**:

```python
from lib.session import parse_hook_input

def main():
    hook_input = parse_hook_input()  # session_idが自動設定される
    tool_name = hook_input.get("tool_name")
    tool_input = hook_input.get("tool_input", {})
    command = tool_input.get("command", "")
    # ... フックロジック
```

**アンチパターン**:

```python
# ❌ 悪い例: 手動でJSONパースしてsession_id設定を忘れる
data = json.loads(sys.stdin.read())
# session_idが設定されない → ログが正しくセッションに紐づかない

# ✅ 良い例: parse_hook_input()を使用
hook_input = parse_hook_input()  # session_id自動設定
```

#### HookContextへの移行（Issue #2449）

`get_claude_session_id()` はグローバル状態を使用するため、テスト困難性とスレッドセーフ性の問題がある。新しいフックは `HookContext` パターンを使用すること。

**移行パターン**:

```python
# ❌ 旧パターン: グローバル状態を使用
from lib.session import parse_hook_input, get_claude_session_id

def main():
    hook_input = parse_hook_input()
    session_id = get_claude_session_id()  # グローバル状態から取得
    # ...

# ✅ 新パターン: HookContext（DI）を使用
from lib.session import parse_hook_input, create_hook_context

def main():
    hook_input = parse_hook_input()
    ctx = create_hook_context(hook_input)
    session_id = ctx.get_session_id()  # コンテキストから取得
    # ...
```

**メリット**:

| 観点 | 旧パターン | 新パターン |
|------|-----------|-----------|
| テスト | モック困難（グローバル状態） | コンテキスト注入で容易 |
| スレッドセーフ | 問題あり | 問題なし |
| 依存関係 | 暗黙的 | 明示的 |

**移行状況**:

- `get_claude_session_id()` はdeprecated（`SHOW_SESSION_DEPRECATION=1` で警告表示）
- 既存フックは段階的に移行中
- 新規フックは必ず `HookContext` パターンを使用すること

#### フック実装前チェックリスト（共通ライブラリ）

- [ ] `lib/results.py` のソースを確認したか
- [ ] `lib/session.py` の `parse_hook_input()` を使用しているか
- [ ] `create_hook_context()` を使用しているか（`get_claude_session_id()` は非推奨）
- [ ] 使用する関数の副作用（ログ記録、プレフィックス付与）を理解したか
- [ ] 重複処理（手動ログ記録、手動プレフィックス）をしていないか

**設計判断の理由**:
- JSON出力はClaudeへのメッセージ表示用 → 重要な情報（ブロック・通知）のみ
- ログ記録は監査・分析用 → 全実行を記録（対象外・許可含む）
- 「全て記録して、利用時にフィルター」の原則

**ログ記録パターン**:

| 状況 | decision値 | 例 |
|------|------------|---|
| 対象外 | `skip` | "Not a merge command" |
| 許可 | `approve` | "All checks passed" |
| ブロック | `block` | "auto-merge blocked" |

### systemMessage出力例

各フックのsystemMessage出力例。新規フック開発時の参考に。

**task-start-checklist.py**:
```
📋 **タスク開始前の確認チェックリスト**

以下の点を確認してからタスクを開始してください:

**要件確認**:
  [ ] 要件は明確か？曖昧な点があれば質問する
  [ ] ユーザーの意図を正しく理解しているか？

**設計判断**:
  [ ] 設計上の選択肢がある場合、ユーザーに確認する
  [ ] 既存のコードパターン・規約を把握しているか？

💡 不明点があれば、実装前に必ず質問してください。
```

**dependency-check-reminder.py**:
```
📦 **依存関係追加を検出**

パッケージ `react-query` を追加しようとしています。

**最新情報を確認してください:**

1. **Context7**: `react-query` のドキュメントを参照
   - `mcp__context7__resolve-library-id` でライブラリIDを取得
   - `mcp__context7__get-library-docs` でドキュメントを取得

2. **Web検索**: 最新バージョン・変更履歴を確認
   - 「react-query latest version」で検索

💡 古いAPIや非推奨メソッドの使用を防ぐため、最新情報の確認を推奨します。
```

**open-issue-reminder.py**:
```
🚨 **高優先度Issue（優先対応必須）**:
  → #123: 本番環境でログイン失敗 [P1, bug]

📋 **未アサインのオープンIssue** (対応検討してください):
  - #456: ダークモード対応 [enhancement]
  - #789: パフォーマンス改善 [P2]

詳細: `gh issue list --state open`
```

**出力設計ガイドライン**:

| 項目 | 推奨 |
|------|------|
| **タイトル** | 絵文字 + 太字で目立たせる |
| **構造** | 箇条書き/チェックリスト形式 |
| **長さ** | 5-15行（長すぎると読まれない） |
| **アクション** | 具体的な次のステップを提示 |

## PreToolUse フック一覧

### Edit/Write ブロック

- **トリガー**: ファイル編集前
- **動作**: main/masterでの編集をブロック、worktree外を警告

### オープンIssueリマインド (`open-issue-reminder.py`)

- **目的**: 未アサインIssue表示、競合防止
- **動作**: セッション開始時（1時間間隔）に最初のBashでトリガー

### タスク開始チェックリスト (`task-start-checklist.py`)

- **目的**: タスク開始時の要件・設計確認漏れ防止
- **動作**: セッション開始時（1時間間隔）に最初のEdit/Write/Bashでトリガー
- **表示内容**: 要件確認、設計判断、影響範囲、前提条件のチェックリスト
- **ブロック**: しない（systemMessageでリマインド表示のみ）

### 依存関係チェックリマインド (`dependency-check-reminder.py`)

- **目的**: 依存関係追加時にContext7/Web検索を促す
- **動作**: `pnpm add`, `npm install`, `pip install` 等のコマンド検出時にトリガー
- **表示内容**: Context7でのドキュメント確認、Web検索での最新情報確認を促すメッセージ
- **ブロック**: しない（systemMessageでリマインド表示のみ）
- **重複防止**: 同じパッケージには1セッション1回のみ表示

### Issue自動アサイン (`issue-auto-assign.py`)

- **目的**: 複数エージェントのIssue競合防止
- **動作**: `git worktree add` でブランチ名からIssue番号を検出し自動assign
- **パターン**: `feature/issue-123-desc`, `fix/123-bug`, `#123-feature`

### PRスコープチェック (`pr-scope-check.py`)

- **目的**: 1 Issue = 1 PR ルール強制
- **動作**: `gh pr create` で複数Issue参照をブロック

### Skill使用リマインド・強制フック

worktree作成・PR作成時のSkill参照を促す2つの補完的なフック。

#### `workflow-skill-reminder.py`（リマインド型）

- **目的**: worktree/PR作成時に`development-workflow` Skill参照をリマインド
- **トリガー**: `git worktree add`, `gh pr create` 検出時
- **動作**: **警告のみ**（systemMessageでリマインド表示、ブロックしない）
- **関連Issue**: #2387

**出力例**:
```
📚 workflow-skill-reminder: worktree作成が検出されました。

【development-workflow Skill 参照リマインダー】
worktree作成時は `development-workflow` Skill を参照してください。

**確認すべき内容:**
□ worktree作成直後のチェック（main最新との差分確認）
□ `--lock` オプションの使用（他エージェントの削除防止）
...
```

#### `skill-usage-reminder.py`（強制型）

- **目的**: Skill使用なしでのworktree/PR作成を**ブロック**
- **トリガー**: `git worktree add`, `gh pr create` 検出時
- **動作**: セッション中のtranscriptを確認し、必要なSkillが使用されていなければ**ブロック**
- **関連Issue**: #2355

#### 補完関係

両フックは以下の補完関係にある:

| フック | チェック内容 | 動作 |
|--------|------------|------|
| `workflow-skill-reminder.py` | リマインド表示 | 「Skillを参照すべき」とリマインド |
| `skill-usage-reminder.py` | Skill未使用時にブロック | Skill未使用なら**ブロック** |

**フロー**:

1. `git worktree add` を実行しようとすると、同じ PreToolUse フェーズで2つのフックが起動する
2. `workflow-skill-reminder.py`: 常に `"approve"` を返しつつ、`systemMessage` で「development-workflow Skillを参照してください」とリマインドを表示
3. `skill-usage-reminder.py`: transcript を確認し、指定された Skill が未参照であれば `"block"` を返し、参照済みであれば `"approve"` を返す

両フックは同じコマンドに対して**並行して独立に実行され**、`workflow-skill-reminder.py` のリマインドと `skill-usage-reminder.py` のブロック可否判定の結果が組み合わされて Claude に渡される。

**設計意図**: リマインドで気づかせ、無視した場合はブロックで強制。2段階の防御で「手順が身についている」という誤った判断を防止。

### マージ安全性チェック (`merge-check.py`)

4つのチェック:

1. `gh pr merge --auto` をブロック
2. `requested_reviewers` にCopilot/Codexがいたらブロック
3. Issue参照なしで却下されたコメントをブロック
4. コメントなしResolveをブロック

**却下検出キーワード**: 「範囲外」「軽微」「out of scope」「defer」

### CI待機チェック (`ci-wait-check.py`)

- **目的**: CI監視を `ci-monitor.py` に一元化
- **ブロック**: `gh pr checks --watch`, `gh pr view --json mergeStateStatus` 等

### Codex CLIレビューチェック

- **logger**: `codex review` 実行時にブランチ・コミットを記録
- **check**: `gh pr create` / `git push` 時に現在コミットがレビュー済みか確認

### Pythonコードチェック (`python-lint-check.py`)

- **目的**: CI前にPythonスタイル違反を検知
- **動作**: `git commit` でステージされた `.py` を `uvx ruff` でチェック

### フック設計チェック (`hooks-design-check.py`)

- **目的**: フック間の責務重複防止、品質チェック
- **動作**: 新規フック追加時にSRPチェックリストを警告表示、`log_hook_execution()` 未使用をブロック

### UI確認チェック (`ui-check-reminder.py`)

- **目的**: UI変更後の目視確認漏れ防止
- **対象**: `locales/*.json`, `components/**/*.tsx`, `routes/**/*.tsx`, `index.css`
- **確認記録**: `python3 .claude/scripts/confirm-ui-check.py`

### Markdownサイズチェック (`markdown-size-check.py`)

- **目的**: Markdownファイル肥大化防止
- **上限**: 40KB（Claude Codeパフォーマンス影響閾値）

### Worktree削除前チェック (`worktree-removal-check.py`)

- **目的**: worktree削除前にアクティブ作業を検出し、セッション競合・破壊を防止
- **トリガー**: `git worktree remove` コマンド検出時

**2段階チェック**:

| チェック | 対象 | `--force` でバイパス |
|----------|------|----------------------|
| **cwdチェック** | 現在の作業ディレクトリがworktree内にあるか | **不可**（常にブロック） |
| **アクティブ作業チェック** | 最新コミット・未コミット変更・stash | 可能 |

**cwdチェックの重要性**:

cwdがworktree内にある状態で削除すると、以降の全Bashコマンドが `ENOENT` エラーで失敗する。
セッション全体が壊れるため、`--force` でも絶対にバイパスできない。

**設計レビュー結果**:

| 観点 | 判断 | 理由 |
|------|------|------|
| 並行性 | 問題なし | 各セッションは独立してcwdをチェック |
| エッジケース | 対応済み | symlink→`resolve()`、permission denied→fail-close |
| 依存関係 | 問題なし | cwdチェックはgitコマンド不使用（純粋なパス比較） |
| 状態管理 | 適切 | `OSError`でfail-close（ブロック側に倒す） |
| セキュリティ | 対応済み | `resolve()`でパストラバーサル対策 |
| 拡張性 | 良好 | 各チェックが独立関数として実装 |

**Fail-Close設計**:

```python
# check_cwd_inside_worktree() の例
try:
    cwd = Path.cwd().resolve()
    # ... パス比較 ...
except OSError:
    # cwdが取得できない場合は安全側に倒す
    return True  # ブロック
```

不確実な状況（OSError等）では常に「ブロック」を選択。誤ブロックは回復可能だが、誤許可によるセッション破壊は回復不可能なため。

## PostToolUse フック一覧

### Issue AIレビュー (`issue-ai-review.py`)

- **目的**: Issue作成後に自動でAIレビュー（Gemini/Codex）を実行
- **トリガー**: `gh issue create` 成功後
- **動作**: バックグラウンドでGemini/Codexレビューを実行し、結果をIssueコメントとして投稿
- **ブロック**: しない（PostToolUseで非ブロッキング実行）

### Worktree自動セットアップ (`worktree-auto-setup.py`)

- **目的**: worktree作成後の依存関係自動インストール
- **トリガー**: `git worktree add` 成功後
- **動作**: `setup-worktree.sh` を自動実行（pnpm install等）
- **ブロック**: しない（PostToolUseで非ブロッキング実行）

### ブロック改善リマインダー (`block-improvement-reminder.py`)

- **目的**: 同一フックが連続ブロックした際にフック改善を促す
- **トリガー**: Bashツール実行後、セッションのブロック履歴を確認
- **動作**: 同一フックが3回連続でブロックしていたら、改善策をsystemMessageで表示
- **ブロック**: しない（systemMessageでリマインド表示のみ）
- **重複防止**: 同じフックには1セッション1回のみ表示
- **関連Issue**: #2432

**検討すべき改善策の例**:
1. SKIP環境変数のサポート追加
2. 拒否メッセージの改善（具体的な解決策を提示）
3. 誤検知パターンの修正

**出力例**:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 フック改善リマインダー: merge-check
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

このセッションで `merge-check` が3回連続でブロックしています。

**検討すべき改善策:**

1. **SKIP環境変数のサポート追加**
   - `SKIP_MERGE_CHECK=1` でバイパス可能に

2. **拒否メッセージの改善**
   - 具体的な解決策を提示
   - 何をすべきか明確に説明

3. **誤検知パターンの修正**
   - 正当なケースをブロックしていないか確認
   - 検出ロジックの精度を改善

詳細は `hooks-reference` Skill を参照してください。
```

## Stop フック

### cwd-check

- **目的**: カレントディレクトリ消失検知
- **動作**: セッション終了時にcwd存在確認

### git-status-check

- **目的**: 未コミット変更検知
- **動作**: mainに未コミット変更があれば警告

### reflection-prompt

五省ベースの自己評価（`prompt`型）:

- **評価基準**: 要件理解、実装品質、検証、対応、効率 + 仕組み化
- **動作**:
  - 重大な未完了タスク → `block`
  - 教訓が見つかったが仕組み化されていない → `block`
  - タスク完了 → `approve`

## Session ID取得の仕組み

フックはセッションIDを使用してセッション固有の状態を管理する。

### 取得優先順位

Issue #777でClaude Codeが直接session_idを提供するようになったため、シンプルな2段階:

| 優先度 | ソース | 説明 |
|--------|--------|------|
| 1 | hook/statusline input JSON `.session_id` | Claude Codeが提供（最も信頼性が高い） |
| 2 | fallback | Python: `ppid-{PPID}` / Bash: 空文字列 |

**廃止済み**: 環境変数（`CLAUDE_SESSION_ID`, `CLAUDE_CONVERSATION_ID`）、marker file

### 実装

**Python（正式実装）**: `common.py` の `get_claude_session_id()`

```python
from common import get_claude_session_id

session_id = get_claude_session_id()
```

**Bash**: `statusline.sh` の `get_session_id()`
（パフォーマンス上、Python実装と同様の方針でbashに実装。ただしfallback時の挙動は異なり、Pythonは `ppid-{PPID}`、Bashは空文字列を返す）

### DEBUGログ

`CLAUDE_DEBUG=1` 環境変数を設定すると、取得元がstderrに出力される:

```
[session_id] source=hook_input, value=abc123...
```

### 関連Issue

- Issue #734: セッションごとのstate file分離
- Issue #756: hook inputからsession_id取得
- Issue #777: Claude Codeによるsession_id提供
- Issue #779: 取得ロジックの統一・ドキュメント化

## プロセス間状態共有

### 重要な制約

フックは**別プロセス**で実行されるため、以下の制約がある:

| 方式 | 動作 | 使用可否 |
|-----|-----|---------|
| グローバル変数 | プロセス終了で消失 | ❌ |
| モジュールレベル辞書 | プロセス間で共有不可 | ❌ |
| ファイルベース | 永続化・共有可能 | ✅ |
| 環境変数 | 読み取り専用 | △ (読み取りのみ) |

**よくある間違い**（Issue #1617）:

```python
# ❌ 動作しない: プロセス終了で消失
_cache = {}

def get_cached_value(key):
    if key not in _cache:
        _cache[key] = expensive_operation()
    return _cache[key]
```

### 実装前チェックリスト

フック/スクリプト実装前に確認:

- [ ] プロセス間で状態共有が必要か？
- [ ] 必要ならファイルベース永続化を使用
- [ ] 複数プロセスからの同時書き込みは？ → ファイルロック検討

### ファイルベース永続化パターン

**推奨**: JSON Lines形式（`.jsonl`）で追記

```python
from __future__ import annotations

import json
from pathlib import Path
from common import METRICS_LOG_DIR

# ディレクトリを事前に作成
METRICS_LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = METRICS_LOG_DIR / "my-data.jsonl"

def append_event(event: dict) -> None:
    """イベントを追記（必要に応じてファイルロックを併用）"""
    # 注意:
    #   - 小規模なログの追記であれば、通常はファイルシステムの書き込みアトミック性で
    #     十分なことが多く、必ずしもロックを導入する必要はありません。
    #   - 複数プロセスから高頻度に同一ファイルへ書き込む場合は、fcntl や filelock などに
    #     よるファイルロック、あるいは専用のログ集約プロセスや外部ストレージの利用を
    #     検討してください。
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(event) + "\n")

def read_events() -> list[dict]:
    """全イベントを読み込み"""
    if not LOG_FILE.exists():
        return []
    events = []
    # 大きなログファイルでもメモリ効率よく読み込むため、行単位で処理する
    with open(LOG_FILE, "r") as f:
        for line in f:
            if line.strip():
                events.append(json.loads(line))
    return events
```

**既存の実装例**:
- `.claude/logs/metrics/block-patterns.jsonl` - ブロック→成功パターン
- `.claude/logs/execution/hook-execution.log` - フック実行ログ

### 関連Issue

- Issue #1617: インメモリ状態管理の問題
- Issue #1634: プロセス間状態共有の教訓

## フック設計原則

1. **単一責任**: 1フック = 1責務
2. **疎結合**: フック間の依存最小化
3. **パス解決**: `$CLAUDE_PROJECT_DIR` を使用
4. **SKIP環境変数**: 全ブロッキングフックは `SKIP_*` 環境変数でバイパス可能にする

**SKIP環境変数の命名規則**:

| フック | 環境変数 |
|--------|----------|
| worktree-removal-check | `SKIP_WORKTREE_CHECK` |
| issue-incomplete-close-check | `SKIP_INCOMPLETE_CHECK` |
| issue-review-response-check | `SKIP_REVIEW_RESPONSE` |
| planning-enforcement | `SKIP_PLAN` |
| codex-review-check | `SKIP_CODEX_REVIEW` |

**実装パターン**:
```python
from common import extract_inline_skip_env, is_skip_env_enabled

SKIP_HOOK_NAME_ENV = "SKIP_HOOK_NAME"

# main()関数内でチェック（エクスポートされた環境変数とインライン両方をサポート）
# 1. エクスポートされた環境変数をチェック
if is_skip_env_enabled(os.environ.get(SKIP_HOOK_NAME_ENV)):
    log_hook_execution("hook-name", "skip", "SKIP_HOOK_NAME=1: チェックをスキップ")
    print(json.dumps({"decision": "approve"}))
    return

# 2. インライン環境変数をチェック（例: SKIP_HOOK_NAME=1 gh issue close）
inline_value = extract_inline_skip_env(command, SKIP_HOOK_NAME_ENV)
if is_skip_env_enabled(inline_value):
    log_hook_execution("hook-name", "skip", "SKIP_HOOK_NAME=1: チェックをスキップ（インライン）")
    print(json.dumps({"decision": "approve"}))
    return
```

**アンチパターン**:
- ❌ 既存フックに「ついでに」別機能を追加
- ❌ 1フックで複数の無関係なチェック
- ❌ ブロッキングフックでSKIP環境変数をサポートしない
- ✅ 新責務は新フックとして実装

### 警告 vs ブロックの判断基準

フック設計時に「警告で許可」か「ブロックで拒否」かを判断する基準。

| 条件 | 選択 | 理由 |
|------|------|------|
| **副作用のある操作を防ぎたい** | ブロック | 警告後もコマンドが実行され、副作用は発生してしまう |
| **操作失敗でフロー問題が発生** | ブロック | 例: exit code 1でPostToolUseフックがスキップされる |
| **情報提供のみで操作は許可** | 警告 | ユーザーに判断を委ねる場合 |
| **軽微な問題の通知** | 警告 | 操作自体は問題なく完了する場合 |

**実例（Issue #2286, #2293）**:

worktree内から `gh pr merge --delete-branch` を実行すると:
1. マージは成功する（副作用発生）
2. ブランチ削除は失敗する（使用中のため）
3. exit code 1が返る
4. PostToolUseフックがスキップされる

当初は「警告」で実装したが、これでは問題が解決しない：
- 警告が表示されても、コマンドは実行される
- 副作用（マージ）は既に発生済み
- PostToolUseフック（振り返り等）が発火しない

**判断フローチャート**:

- **副作用のある操作を防ぎたいか？**
  - **はい**: `ブロック`
  - **いいえ**:
    - **操作失敗時にフロー問題が起きるか？**
      - **はい**: `ブロック`
      - **いいえ**: `警告`（または通知なし）

## コマンド実行パターン

フック内で外部コマンドを実行する際の標準パターン。

### shell=True vs shell=False の使い分け

| 状況 | shell | 理由 |
|------|-------|------|
| リダイレクト（`2>&1`）を含む | `True` | シェルがリダイレクトを解釈 |
| パイプ（`\|`）を含む | `True` | シェルがパイプを解釈 |
| 環境変数展開（`$VAR`）が必要 | `True` | シェルが展開 |
| 上記以外 | `False` | セキュリティと明確さのため |

### 推奨パターン

**✅ shell=False（デフォルト）**:
```python
# リストでコマンドを渡す（推奨）
result = subprocess.run(
    ["gh", "pr", "list", "--json", "number,title"],
    capture_output=True,
    text=True,
    timeout=30
)
```

**✅ shell=True（リダイレクト・パイプが必要な場合）**:
```python
# 文字列でコマンドを渡す
result = subprocess.run(
    "gh pr list --search 'author:@me' 2>&1",
    shell=True,
    capture_output=True,
    text=True,
    timeout=30
)
```

### アンチパターン

**❌ shell=Falseでリダイレクトを含む**:
```python
# 悪い例: 2>&1がghの引数として渡される
result = subprocess.run(
    "gh pr list 2>&1".split(),  # split()でリストに変換
    capture_output=True,
    text=True
)
# エラー: "2>&1" が gh のコマンド引数として解釈される
```

**❌ shell=Falseで文字列を渡す**:
```python
# 悪い例: shell=Falseで文字列は非推奨
result = subprocess.run(
    "gh pr list",  # 文字列
    shell=False,   # shell=False
    ...
)
# エラー: FileNotFoundError: [Errno 2] No such file or directory: 'gh pr list'
```

### common.pyのヘルパー関数

フック共通モジュールにはコマンド実行のヘルパーがあります:

```python
from common import run_command

# 基本的な使用法（shell=False、リストで渡す）
result = run_command(["gh", "pr", "list", "--json", "number"])

# シェル機能が必要な場合
result = run_command("gh pr list 2>&1 | grep error", shell=True)
```

### 関連Issue

- Issue #1106: locked-worktree-guardがシェルリダイレクトを誤認識

## フック実装チェックリスト

新しいフックを実装する際は、以下のチェックリストを確認する。

### 正規表現設計

コマンド検出の正規表現を設計する際の考慮事項:

- [ ] **環境変数プレフィックス**: `SKIP_PLAN=1 git worktree add` のようなインライン環境変数
- [ ] **パイプ連結**: `cmd1 | cmd2` パターン
- [ ] **シェル連結**: `&&`, `;`, `||` によるコマンド連結
- [ ] **引用符内のコマンド**: `echo "git commit"` のような文字列内のコマンド（誤検知防止）
  - `lib/strings.py` の `strip_quoted_strings()` を使用して引用符内を除去してから検査
- [ ] **サブシェル**: `$(cmd)` や `` `cmd` `` 内のコマンド

**参考パターン**:
```python
# 環境変数プレフィックスとシェル連結を考慮した例
# (?:^|&&|\|\||;|\s+) でコマンド開始位置を特定（\s+ で複数空白に対応）
pattern = r"(?:^|&&|\|\||;|\s+)(?:\w+=\S+\s+)*git\s+worktree\s+add"
```

### 入力処理

- [ ] **空入力**: `tool_input` が空の場合の処理
- [ ] **不正形式**: JSON構造が期待と異なる場合
- [ ] **必須フィールド欠落**: `tool_input.command` 等が存在しない場合
- [ ] **Fail-Close設計**: 不確実な状況ではブロック側に倒す
- [ ] **hook_cwd取得**: cwdに依存するフックは `input_data.get("cwd")` を使用（Issue #1172）

**hook_cwdパターン**:
```python
input_data = parse_hook_input()
hook_cwd = input_data.get("cwd")  # Claude Codeが提供するセッションの実cwd
# hook_cwd を base_cwd パラメータとして渡す（環境変数より優先される）
cwd = get_effective_cwd(command, base_cwd=hook_cwd)
```

### テスト

- [ ] **正常系**: 期待するコマンドを正しく検出
- [ ] **異常系**: 不正入力でクラッシュしない
- [ ] **エッジケース**: 環境変数プレフィックス、パイプ連結等
- [ ] **誤検知防止**: 類似コマンドや引用符内を誤検出しない
- [ ] **テスト手法の確認**: `run_hook`（subprocess）かdirect callか事前確認

**テスト手法の選択**:

| 方式 | モック可否 | 用途 |
|------|-----------|------|
| `run_hook()`（subprocess） | ❌ 効かない | E2Eテスト、実際の動作確認 |
| `hook_module.main()` 直接呼び出し | ✅ 効く | ユニットテスト、例外ハンドリング確認 |

```python
# モックが必要なテストは直接呼び出しを使用
from unittest.mock import patch

def mock_func(*args, **kwargs):
    raise FileNotFoundError("simulated error")

with patch.object(hook_module, "get_effective_cwd", side_effect=mock_func):
    hook_module.main()  # 例外発生時の動作をテスト
```

### 出力フォーマット設計

systemMessage出力を含むフックを実装する場合のチェックリスト:

- [ ] **出力目的の明確化**: 何を伝えたいか1文で説明できるか
- [ ] **出力構造の設計**: 絵文字タイトル + 箇条書き/チェックリスト形式
- [ ] **出力長の確認**: 5-15行を目安（長すぎると読まれない）
- [ ] **アクション提示**: 次に何をすべきか具体的に示す
- [ ] **hooks-referenceへの追記**: 出力例をドキュメントに追加

**出力テンプレート**:
```python
def get_message() -> str:
    """Generate the systemMessage content."""
    lines = [
        "📋 **[タイトル]**",
        "",
        "[説明文]",
        "",
        "**[セクション1]**:",
        "  - [項目1]",
        "  - [項目2]",
        "",
        "💡 [アクション/ヒント]",
    ]
    return "\n".join(lines)
```

**精度向上のポイント**:
- 研究によると、出力例を含めると精度が向上する場合がある
- 曖昧な指示より具体的なフォーマット指定が効果的
- 箇条書き/チェックリスト形式は解釈のばらつきを減らす

### Skill整合性チェック（Issue #1196）

フック設計時に関連Skillのルールとの整合性を確認する。これを怠ると、Skillに記載されたルールがフックで強制されず、ルール違反が発生する。

**必須チェック項目**:

- [ ] **関連Skill特定**: このフックが関連するSkillを特定したか？
  - 例: `bug-issue-creation-guard.py` → `code-review` Skill
  - 例: `merge-check.py` → `code-review` Skill
  - 例: `worktree-removal-check.py` → `development-workflow` Skill
- [ ] **ルール網羅**: 関連Skillに記載されたルールを全てカバーしているか？
  - 例: `code-review` Skillに「テスト不足は同じPRで対応」とあれば、「テスト」パターンも検出必須
- [ ] **パターン確認**: フックの検出パターンがSkillの記述と一致するか？
  - 例: Skillに「バグ、テスト不足、エッジケース」とあれば、全てパターンに含める

**確認手順**:

1. フックの目的を明確化（何を防止/検出するか）
2. 関連するSkillを `.claude/skills/` から特定
3. Skillに記載されたルール/条件を抽出
4. フックのパターン/ロジックが全ルールをカバーしているか確認
5. 不足があればフックを拡張

**失敗事例**:

| 問題 | 原因 | 対策 |
|------|------|------|
| テスト不足Issueがフックをすり抜け | `code-review` Skillの「テスト不足」ルールを検出パターンに含めていなかった | パターンに「テスト」を追加 |
| エッジケースIssueがフックをすり抜け | `code-review` Skillの「エッジケース」ルールを検出パターンに含めていなかった | パターンに「エッジケース」を追加 |
| コード品質Issueがフックをすり抜け | Skillルール整合性チェックなしでフックを設計した | Skillルール整合性チェックを導入 |

**docstringテンプレート**:

```python
"""
Hook to [目的].

Related Skills:
- code-review: [関連ルール]
- development-workflow: [関連ルール]

Detection patterns based on Skill rules:
- Pattern A: [Skillルール1]
- Pattern B: [Skillルール2]
"""
```

### 関連Issue

- Issue #1085: 正規表現で環境変数プレフィックスを考慮していなかった事例
- Issue #1172: hook_cwdを使用していなかったためcwd検出が失敗
- Issue #1196: フック設計時のSkillルール整合性チェック

## パターン検出フック作成ガイドライン

キーワードリストや正規表現パターンを使用してテキストを検出するフック（例: `defer-keyword-check.py`）を作成・変更する際のガイドライン。

### 実データ分析の重要性

仮説ベースでパターンを選定すると:
- **誤検知が多い**: 実際には問題ないケースをブロック
- **漏れが多い**: 本当に検出すべきパターンを見逃す
- **メンテナンス負荷**: 後から修正が必要になる

### 必須チェックリスト

パターン検出フックの作成・変更時は以下を確認:

- [ ] **実データソースを特定したか**
  - GitHub PR comments: `gh api repos/{owner}/{repo}/pulls/{pr}/comments`
  - Issue comments: `gh api repos/{owner}/{repo}/issues/{issue}/comments`
  - セッションログ: `~/.claude/logs/*.jsonl`

- [ ] **実データからパターンを抽出したか**
  - 仮説ベースではなく実際のデータを分析
  - 頻度・コンテキストを確認
  - 最低10件以上の実例を収集

- [ ] **作成したパターンをテストしたか**
  - 検出率（実際に検出すべきものを検出できているか）
  - 誤検知率（検出すべきでないものを検出していないか）
  - 目標: 検出率 > 90%、誤検知率 < 10%

### 分析ツール

`.claude/scripts/analyze-pattern-data.py` を使用:

```bash
# パターン検索（実データからマッチを確認）
python3 .claude/scripts/analyze-pattern-data.py search \
  --pattern "後で|将来|フォローアップ" \
  --show-matches

# 頻度分析（パターンの出現頻度を確認）
python3 .claude/scripts/analyze-pattern-data.py analyze \
  --pattern "スコープ外" \
  --days 30

# パターンリスト検証（複数パターンの精度を一括チェック）
python3 .claude/scripts/analyze-pattern-data.py validate \
  --patterns-file my-patterns.txt
```

### 実装時のベストプラクティス

1. **パターンリスト変数の命名**:
   ```python
   # 明確な命名で目的を示す
   DEFER_KEYWORDS = [...]  # 「後で」系キーワード
   SCOPE_OUT_PATTERNS = [...]  # スコープ外パターン
   ```

2. **実データ分析の証跡をコメントに残す**:
   ```python
   # 実データ分析: PR comments from 2025-12-30
   # 検出対象: Issue参照なしで使われると問題になるパターン
   # 分析結果: 30件中28件検出、誤検知2件
   DEFER_KEYWORDS = [...]
   ```

3. **除外コンテキストを考慮**:
   ```python
   # 誤検知防止: コードブロック、ドキュメント参照、ルール説明
   EXCLUDE_CONTEXTS = [
       r"```",  # コードブロック
       r"AGENTS\.md",  # ドキュメント参照
   ]
   ```

### 自動検出

`hook-change-detector.py` がパターン検出フックの変更を検知し、実データ分析チェックリストをリマインドします。

検出条件:
- `*_KEYWORDS`, `*_PATTERNS`, `*_REGEX` 変数を含む
- 正規表現パターンリストを含む
- `re.compile()` を含む

### 関連Issue

- Issue #1910: AskUserQuestion検出フック
- Issue #1911: 「後で」キーワード検出フック
- Issue #1912: パターン検出フック作成時の実データ分析強制

## ブロックパターン追加時のチェックリスト

新しいブロックパターンをフックに追加する際のチェックリスト。引用符内での誤検知問題を踏まえた防止策。

### 誤検知防止

- [ ] **引用符内のパターン**: `--body "..."` や `--title "..."` 内での言及を除外
  - 対策: 引用符内のコンテンツを除去するヘルパー関数を使用（下記実装例参照）
  - ❌ 悪い例: `if "gh run watch" in command:`
  - ✅ 良い例: `if "gh run watch" in strip_quoted_content(command):`

- [ ] **コメント内**: `# command は使わない` のような文脈
  - 対策: 行頭が `#` の場合は無視する処理を検討

- [ ] **変数展開**: `$command` が対象パターンを含む場合
  - 対策: `\b` で単語境界を明確にする

- [ ] **パイプ/リダイレクト**: `echo "..." | grep ...`
  - 対策: `strip_quoted_content()` でカバー

### パターン設計

- [ ] 正規表現を使用する場合、エスケープ漏れがないか
- [ ] 大文字小文字の区別が必要か確認
- [ ] 複数行コマンド対応が必要か

### テスト

- [ ] **正常系**: ブロックすべきコマンドがブロックされる
- [ ] **誤検知テスト**: 引用符内での言及がブロックされない
  ```python
  def test_approves_quoted_mention(self):
      """引用符内でのパターン言及は承認される."""
      command = 'gh pr comment --body "使用禁止: gh run watch"'
      result = should_block(command)
      assert result is False
  ```
- [ ] テスト追加先: `.claude/hooks/tests/test_<hook名>.py`

### 実装例

```python
import re

# NOTE: この関数はドキュメント用の簡略化サンプルです。
# 実際のフック実装では、.claude/hooks/ci-wait-check.py 内の strip_quoted_content を参照してください。
# そちらは文字単位のパースにより、エスケープされた引用符や未閉じ引用符などのエッジケースに対応しています。
def strip_quoted_content(text: str) -> str:
    """引用符内のコンテンツを簡易的に除去する.

    ダブルクォート/シングルクォートで囲まれた内容を空文字に置き換える。
    例: 'gh pr comment --body "使用禁止: gh run watch"' -> 'gh pr comment --body ""'

    注意: この実装は正規表現による簡略版であり、以下のエッジケースには対応していません:
        - 文字列外のエスケープされた引用符（例: \"foo\"）
        - 閉じられていない引用符
    実運用時は .claude/hooks/ci-wait-check.py の実装を使用してください。
    """
    return re.sub(r'(["\'])(?:\\.|(?!\1).)*\1', r'\1\1', text)

def should_block(command: str) -> bool:
    """Check if command should be blocked."""
    # 引用符内のコンテンツを除去してからチェック
    clean_command = strip_quoted_content(command)

    # ブロック対象パターン
    blocked_patterns = [
        r"\bgh\s+run\s+watch\b",
        r"\bgh\s+pr\s+checks\s+--watch\b",
    ]

    for pattern in blocked_patterns:
        if re.search(pattern, clean_command):
            return True
    return False
```

### 関連Issue

- Issue #1621: ブロックパターンチェックリストの追加

## git rev-list差分チェックのガイドライン

`git rev-list` でブランチ間の差分をチェックする場合、以下の3ケースを必ずテストする。

### 必須テストケース

| ケース | 状態 | 期待動作 |
|--------|------|----------|
| **ahead** | ローカルがリモートより進んでいる | 通常は許可 |
| **behind** | ローカルがリモートより遅れている | ブロックまたは警告 |
| **same** | 同一コミット | 許可 |

### アンチパターン

❌ **ハッシュ不一致でブロック**:
```python
# 悪い例: aheadでも誤ってブロック
if local_hash != remote_hash:
    return block()
```

✅ **behind_countでブロック**:
```python
# 良い例: behindの場合のみブロック
behind_count = get_behind_count()  # git rev-list main..origin/main
if behind_count > 0:
    return block()
```

### テストコード例

```python
def test_approves_when_local_is_ahead(self):
    """ローカルが進んでいる場合は許可"""
    with patch.object(hook, "get_behind_count", return_value=0):
        # behind=0 means local is same or ahead
        result = hook.main()
        self.assertEqual(result["decision"], "approve")

def test_blocks_when_local_is_behind(self):
    """ローカルが遅れている場合はブロック"""
    with patch.object(hook, "get_behind_count", return_value=3):
        result = hook.main()
        self.assertEqual(result["decision"], "block")

def test_approves_when_same(self):
    """同一コミットの場合は許可（behind_count=0で判定）"""
    # Note: behind_countベースの実装では、aheadとsameは同じ条件（behind_count=0）
    with patch.object(hook, "get_behind_count", return_value=0):
        result = hook.main()
        self.assertEqual(result["decision"], "approve")
```

### 関連Issue
- Issue #755: worktree-main-freshness-checkでの発見
- Issue #760: 本ガイドライン追加

## フックコード更新タイミング

フックはセッション開始時にロードされ、セッション中の修正は反映されない。

| タイミング | 動作 |
|----------|------|
| **セッション開始時** | フックコードがロードされる |
| **セッション中** | フックを修正・マージしても**現セッションには反映されない** |
| **次セッション** | 修正済みコードが適用される |

### 影響

- フックの修正後も、現セッションでは旧コードが動作し続ける
- 修正を検証する場合、新しいセッションを開始する必要がある
- 誤検知が発生しても、現セッション内で「なぜまだ動くのか」と混乱しやすい

### 対処法

- **修正検証**: 新セッションで動作確認
- **現セッション**: 誤検知は無視して作業続行（修正済みなら問題なし）

### 関連Issue

- Issue #2120: lesson-issue-checkの誤検知修正（この問題を発見したきっかけ）
- Issue #2124: 本ドキュメント追加

## CWD問題の対処

`cd` でフックが見つからなくなる問題:

1. **フック設定**: `$CLAUDE_PROJECT_DIR` を使用（設定済み）
2. **`cd` を避ける**: `-C` / `--dir` オプションを使用
   - ✅ `pnpm add xxx -C frontend`
   - ❌ `cd frontend && pnpm add xxx`
3. **問題発生時**: セッション再起動が必要

## Block評価・改善サイクル

フックによるブロックが妥当だったか評価し、誤検知の場合はフックを改善するサイクル。

### ワークフロー

```
[Block発生] → [ログ記録] → [評価] → [分析] → [改善]
```

### 1. Blockログの確認

```bash
# 最近のブロック一覧
python3 .claude/scripts/block-evaluator.py list

# 特定のblockを詳細表示
python3 .claude/scripts/block-evaluator.py evaluate <block_id>
```

### 2. Block妥当性の評価

評価オプション:
- `valid` - ブロックは正しかった（本来止めるべき操作）
- `false_positive` - 誤検知（止めるべきではなかった）
- `unclear` - 判断できない

```bash
# 対話的に評価
python3 .claude/scripts/block-evaluator.py evaluate <block_id>

# ワンライナーで評価
python3 .claude/scripts/block-evaluator.py evaluate <block_id> \
  -e false_positive \
  -r "テストファイルなのにブロックされた" \
  -i "テストファイルを除外すべき"
```

### 3. 評価サマリーの確認

```bash
python3 .claude/scripts/block-evaluator.py summary
```

出力例:
```
Hook                           Valid   False+  Unclear   FP Rate
----------------------------------------------------------------------
ci-wait-check                      5        3        0     37.5%
codex-review-check                10        1        0      9.1%
```

### 4. 誤検知パターンの分析

```bash
python3 .claude/scripts/analyze-false-positives.py
# 特定のフックのみ分析
python3 .claude/scripts/analyze-false-positives.py --hook ci-wait-check
```

### 5. フック改善

分析結果に基づいてフックを改善:

1. 改善用worktree作成
2. フックコード修正
3. テスト追加
4. PR作成・マージ

### ログファイル

| ファイル | 内容 |
|---------|------|
| `.claude/logs/hook-execution.log` | 全フック実行ログ |
| `.claude/logs/block-evaluations.log` | Block評価記録 |

### 評価タイミング

- **推奨**: セッション終了時に未評価ブロックを確認
- **必須**: 「誤検知では？」と感じた時に即評価

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
uvx ruff@0.14.9 check .claude/hooks/ .claude/scripts/ --select D101,D102,D103
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

**署名なしでResolveすると `merge-check.py` でブロック**される。

### 関連Issue

- Issue #1107: Copilotレビューエラー時の対応手順
- Issue #1108: 関数シグネチャ変更時のテスト更新チェック
- Issue #1113: Copilotレビュー指摘パターンの事前検出

## フックテンプレート

新規フック作成時のボイラープレート。再作業を防ぐため、テストから先に書く（TDD）。

### 1. テストファイルを先に作成

```python
# .claude/hooks/tests/test_my_hook.py
"""Tests for my-hook.py"""
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

### 2. フック本体を実装

```python
#!/usr/bin/env python3
"""My hook description.

What it does:
- Check A
- Block B
"""

import json
import os
import sys

from common import (
    extract_inline_skip_env,
    is_skip_env_enabled,
    log_hook_execution,
    make_block_result,
)

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
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        # Fail-open: JSONエラーは許可
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

### 3. settings.jsonに登録

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 $CLAUDE_PROJECT_DIR/.claude/hooks/my-hook.py"
          }
        ]
      }
    ]
  }
}
```

### チェックリスト

- [ ] テストを先に書く（TDD）
- [ ] 最低3つのテストケース（正常・境界・エラー）
- [ ] SKIP環境変数のサポート
- [ ] Fail-open設計（エラー時は許可）
- [ ] `log_hook_execution()` でログ記録
- [ ] docstring追加（D101-D103対応）

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
