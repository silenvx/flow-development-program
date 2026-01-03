---
name: coding-standards
description: コーディング規約、テスト、Lint、型チェック、後方互換性。規約、コーディング、テスト、Lint、型チェック時に使用。
---

# コーディング規約

このプロジェクトのコーディング規約と品質基準。

## 基本ルール

- **TypeScript** を使用
- **Biome** でフォーマット・Lint
- 既存のパターンと規約に従う
- 新しいファイルより既存ファイルの編集を優先

## コメント・コミットメッセージ

**原則**: 「What（何を）」ではなく「Why（なぜ）」を書く

コードは「何をしているか」は読めば分かる。時間と共に失われる「なぜそうしたか」を残す。

### コードコメント

| 書くべき内容 | 書かないべき内容 |
| ------------ | ---------------- |
| なぜこの実装を選んだか | コードを読めば分かること |
| 非自明な制約・エッジケース | 自明な処理の説明 |
| 外部仕様・バグへの対応（リンク付き） | 過度な装飾・フォーマット |
| 警告（順序変更で壊れる等） | |

```typescript
// ❌ What: コードを読めば分かる
// ユーザーIDをハッシュ化する
const hashed = hash(userId);

// ✅ Why: 背景・理由を説明
// プライバシー保護のため、ログにはハッシュ化したIDのみ記録
// 参考: https://example.com/privacy-policy
const hashed = hash(userId);
```

**注意**: コメントはコードと乖離するリスクがある。更新を怠ると害になる。

### 参照スタイルのコメント禁止

**問題**: 他のファイルを参照するコメントは、参照先が変更されると嘘になる。

```python
# ❌ 参照スタイル（禁止）
# pr_related_issue_check.pyと同じ
MIN_KEYWORD_LENGTH = 2  # 実際はpr_related_issue_check.pyは3かもしれない

# Same as utils.ts
TIMEOUT = 10

# ❌ 他の禁止パターン
# common.pyと共通
# utils.pyを参照
# config.tsからコピー
```

**解決策**:

1. **同じ値を使うなら `import` で共有**

   ```python
   from pr_related_issue_check import MIN_KEYWORD_LENGTH
   ```

2. **理由を書く**（参照ではなく「なぜ」を説明）

   ```python
   # ✅ 理由を説明
   # Issueタイトルは短い傾向があるため、PRより短いキーワードも抽出
   MIN_KEYWORD_LENGTH = 2
   ```

**フック**: `reference_comment_check.py` が編集時に警告（非ブロック）。

### コミットメッセージ

| 書くべき内容 | 書かないべき内容 |
| ------------ | ---------------- |
| 変更の動機・背景 | How（diffで分かる） |
| 影響範囲 | 自明な変更内容の列挙 |
| 関連Issue/PRへの参照 | |

```bash
# ❌ What only
Fix bug

# ✅ Why + What
fix: セッション切れ時に無限リダイレクトが発生する問題を修正

原因: トークン更新失敗時にリトライが無限ループしていた
対応: 最大3回のリトライ制限を追加

Fixes #123
```

**利点**: コミットメッセージは `git blame` で追跡でき、コードと分離しているため「腐りにくい」。

### CUSTOMIZEコメント

他プロジェクトへの再利用時に変更が必要な箇所を明示するコメント形式。

**形式**: 1行で「何を変更するか」と「どう変更するか（必要なら理由も含めてよい）」をハイフンで区切る

```python
# CUSTOMIZE: 何を変更するか - どう変更するか（必要なら理由も含める）
VARIABLE = "value"
```

**例**:

```python
# ✅ 良い例: 1行形式で情報が完結
# CUSTOMIZE: Production hostnames - Set these to your project's production domain(s)
PRODUCTION_HOSTNAMES = ["dekita.app", "api.dekita.app"]

# CUSTOMIZE: Default priority labels - Modify if your project uses different priority labels
DEFAULT_PRIORITY_LABELS = {"P0", "P1", "P2", "P3"}

# ❌ 悪い例: 説明が不十分
# CUSTOMIZE: ホスト名
PRODUCTION_HOSTNAMES = ["dekita.app"]

# ❌ 悪い例: 2行に分割（一貫性がない）
# CUSTOMIZE: Production hostnames
# Set these to your project's production domain(s)
PRODUCTION_HOSTNAMES = ["dekita.app"]
```

**Markdown内での形式**:

```markdown
<!-- CUSTOMIZE: パッケージマネージャ - 自分のプロジェクトに合わせて変更（npm/yarn/pnpm） -->
```

## コマンド

<!-- CUSTOMIZE: パッケージマネージャ - 自分のプロジェクトに合わせて変更（npm/yarn/pnpm） -->

| 用途       | コマンド           |
| ---------- | ------------------ |
| ビルド     | `pnpm build`       |
| テスト     | `pnpm test:ci`     |
| Lint       | `pnpm lint`        |
| 型チェック | `pnpm typecheck`   |

## 共通パターン修正時の網羅チェック

**背景**: #2054 で `json.dumps` に `ensure_ascii=False` を追加した際、2箇所の修正漏れがあり #2065 で再修正が必要になった。

### ルール

共通関数・設定値・定数を変更する場合、**リポジトリ全体を検索して影響範囲を網羅的に確認**する。

### チェック手順

```bash
# 1. 修正対象のパターンを検索（例: json.dumps の場合）
rg "<修正対象のパターン>" --type py

# 2. 全ての呼び出し箇所を確認
# 3. 漏れなく修正
```

### 適用対象

| 変更内容                 | 検索対象               |
| ------------------------ | ---------------------- |
| 関数の引数追加           | その関数の全呼び出し   |
| 定数の変更               | その定数の全参照       |
| 設定値の変更             | その設定の全使用箇所   |
| 共通ユーティリティ変更   | 全import箇所           |

### PRレビュー時の確認項目

AIレビュー（Copilot/Codex）は以下を確認:

- [ ] この変更は他のファイルに影響しないか？
- [ ] 同様のパターンが他のファイルに存在しないか？
- [ ] 修正漏れがないか？

**関連Issue**: #2054, #2065, #2069

## レフトシフト（早期検証）

**コミット前にローカルで検証**:

```bash
# CUSTOMIZE: パッケージマネージャを自分のプロジェクトに合わせて変更
pnpm lint && pnpm typecheck && pnpm test:ci
```

CIで失敗してから修正するのではなく、事前に検証。

## Pythonツール

`uvx` で実行（npxのPython版）:

```bash
# ✅ 正しい
uvx ruff check
uvx ruff format

# ❌ 間違い
python3 -m ruff check
```

## Pythonフックベストプラクティス

フック実装時のコーディングパターン。Issue #1634で明文化。

### インポート

```python
# ✅ モジュールレベルでインポート
import time
from pathlib import Path

def main():
    start = time.time()

# ❌ 関数内インポート（呼び出し毎にインポート処理が走りパフォーマンスに影響し、依存関係も分かりにくくなるため避ける）
def main():
    import time  # パフォーマンスと可読性の観点から避ける
```

### 定数化

```python
# ✅ マジックナンバーを定数化
BLOCK_CLEANUP_WINDOW_SECONDS = 300
MAX_RETRY_COUNT = 3

if elapsed > BLOCK_CLEANUP_WINDOW_SECONDS:
    cleanup()

# ❌ マジックナンバーを直接使用
if elapsed > 300:  # 何の数値か不明
    cleanup()
```

### Fail-Close設計

不確実な状況ではブロック側に倒す（セキュリティ優先）:

```python
# ✅ 不確実な場合はブロック（安全側）
try:
    if marker_path.exists():
        return marker_path.read_text().strip()
except OSError:
    return None  # 不確実 → ブロック側に倒す

# ❌ エラー時に許可（危険）
except OSError:
    return "approved"  # エラー時に許可は危険
```

### 早期リターン

```python
# ✅ 早期リターンで条件をフラット化
def main():
    if not is_target_tool(tool_name):
        print_continue_and_log_skip("my-hook", "not target tool")
        return

    if not meets_condition():
        print_continue_and_log_skip("my-hook", "condition not met")
        return

    # メイン処理
    process()

# ❌ 深いネスト
def main():
    if is_target_tool(tool_name):
        if meets_condition():
            process()
```

### 関連ドキュメント

- `hooks-reference` Skill: 「プロセス間状態共有」セクション
- Issue #1617: インメモリ状態管理の問題
- Issue #1634: コーディングパターンの明文化

## 並列処理

独立したタスクは並列で実行:

- 独立したファイルの読み取り・検索
- ビルド・テスト・Lintの並列実行
- CI監視中の他タスク実行

## テスト

### テスト必須

- 新機能・バグ修正には対応テストを追加
- APIエンドポイント変更時は `worker/src/index.test.ts` にテスト追加
- エラーケース（400, 403, 404, 409等）も必ずテスト

### 条件分岐追加時のテストケース

**ルール**: 新しい条件分岐を追加したら、その条件のテストも追加する

| 変更内容 | 必要なテスト |
|----------|-------------|
| 除外条件を追加 | 除外が正しく機能することを確認するテスト |
| 新しい分岐を追加 | その分岐を通るテストケース |
| エラーハンドリング追加 | エラー時の動作を確認するテスト |

**例**: Issue #1835で`ast.Subscript`の`ctx`属性チェックを追加した際、Storeコンテキストが誤検知されないテストを追加すべきだった（Copilotに指摘されてから追加）

```python
# ❌ 条件分岐を追加したがテストなし
if isinstance(node.ctx, ast.Load):  # 新しい条件
    # ...処理...

# ✅ 条件分岐と対応するテストを追加
def test_ignores_subscript_store():
    """辞書への書き込み（Store）は誤検知しない"""
    source = 'event["tool_result"] = value'
    errors = check_tool_response_fallback(...)
    assert len(errors) == 0  # 書き込みは対象外
```

**チェックリスト**（コミット前）:
- [ ] 新しい条件分岐を追加したか？
- [ ] その条件を通る/通らないテストケースがあるか？
- [ ] 既存テストだけでなく、新しい動作をカバーするテストを追加したか？

### テスト実行

```bash
# 全テスト
pnpm test:ci

# Worker テスト
pnpm test:ci:worker
```

## テスト駆動開発（TDD）

**目的**: 同一ファイルへの再作業（短時間内の複数編集）を減らす。

### なぜTDDが必要か

| 問題 | 原因 | TDDによる解決 |
|------|------|--------------|
| 5分以内に同じファイルを5回編集 | テストなしで実装→失敗→修正の繰り返し | 先にテストを書き、1回で正しく実装 |
| CIで失敗してから修正 | ローカルテストせずにCIに依存 | ローカルでテストを通してからコミット |
| エッジケースの見落とし | 実装後にエッジケースを発見 | テストでエッジケースを事前に洗い出し |

### TDDの基本フロー

```bash
# 1. テストを書く（失敗する）
# 2. 実装する（テストが通る）
# 3. リファクタリング（テストが通ったまま）
```

### フック開発でのTDD例

```python
# tests/test_my_hook.py
def test_basic_case():
    """正常系: 期待通りの入力で期待通りの出力"""
    result = process_input({"command": "git status"})
    assert result["decision"] == "approve"

def test_edge_case_empty_command():
    """エッジケース: 空のコマンド"""
    result = process_input({"command": ""})
    assert result["decision"] == "approve"  # フェイルオープン

def test_error_case_invalid_json():
    """エラーケース: 不正なJSON"""
    # JSONエラー時もフェイルオープン
    ...
```

### 実装前に考慮すべきテストケース

| カテゴリ | 例 |
|----------|-----|
| **正常系** | 期待通りの入力 |
| **境界値** | 空文字、ゼロ、最大値 |
| **エッジケース** | 特殊文字、長い文字列、Unicode |
| **エラーケース** | 不正な入力、タイムアウト、外部コマンド失敗 |
| **並行性** | 複数プロセスからの同時アクセス |

### TDD実践のチェックリスト

- [ ] 実装前にテストファイルを作成
- [ ] 最低3つのテストケース（正常・境界・エラー）を書く
- [ ] テストが失敗することを確認
- [ ] 実装してテストが通ることを確認
- [ ] 追加のエッジケースを洗い出してテスト追加

## 新機能のライフサイクルチェックリスト

新しい機能（特にグローバル状態を持つもの）を実装する際は、以下を確認する。

### 背景

PR #1633でErrorContextManagerを実装した際、`flush_pending()`がセッション終了時に呼ばれないバグがCodex CLIレビューで発見された。原因は、実装時にセッション終了フローを確認しなかったため。

### ライフサイクルチェック

| フェーズ | 確認項目 | 例 |
|----------|----------|-----|
| **初期化** | どこで初期化されるか？ | セッション開始時、最初の呼び出し時 |
| **実行中** | いつ・どこで呼び出されるか？ | 各フック実行時、特定イベント時 |
| **終了** | 終了処理はどこで行われるか？ | session_metrics_collector.py |
| **異常終了** | エラー時のクリーンアップは？ | try/finally、シグナルハンドラ |

### 統合ポイントチェック

| チェック項目 | 確認方法 |
|--------------|----------|
| 既存フックとの統合 | `grep -r "session_id" .claude/hooks/` で関連フック特定 |
| セッション開始フック | SessionStart フックに初期化が必要か |
| セッション終了フック | `session_metrics_collector.py` に終了処理が必要か |
| グローバル状態の永続化 | 適切なタイミングでファイル書き込みされるか |

### 実装前チェックリスト

新機能を実装する**前**に確認:

- [ ] この機能はグローバル状態（シングルトン、モジュール変数）を持つか？
- [ ] 持つ場合、初期化タイミングは明確か？
- [ ] 持つ場合、終了時のflush/cleanup処理は実装されているか？
- [ ] セッション開始/終了フックへの統合が必要か確認したか？
- [ ] 異常終了時（SIGTERM、例外）でもデータが失われないか？

### 実装後チェックリスト

新機能を実装した**後**に確認:

- [ ] 正常終了時のテストを追加したか？
- [ ] 異常終了時のテストを追加したか？
- [ ] 複数セッション並行時のテストを追加したか？
- [ ] `session_metrics_collector.py`への統合が必要な場合、追加したか？

### 関連Issue

- Issue #1636: flush_pending未呼び出しバグ
- Issue #1641: このチェックリストの追加

## 後方互換性

### KVスキーマ変更時

- 古いデータとの互換性を保つ
- 新フィールドは optional + フォールバック処理
- **72時間後**に必須化・削除可能（データTTL=24h + 安全マージン）

### TODOコメント

```typescript
// TODO: 2025-12-17以降に削除可能（データTTL=24h、安全マージン72時間）
statusUpdatedAt?: string
```

## 情報の鮮度確認

バージョン情報やライブラリの使い方は学習済み知識で回答しない。

**情報取得手段**（優先順）:

1. **Context7** - ライブラリドキュメント
2. **WebSearch** - 最新リリース情報
3. **WebFetch** - 公式ドキュメント

### 依存関係追加時のワークフロー

`pnpm add` 等で依存関係を追加する際は、以下を確認（`dependency-check-reminder` フックが自動リマインド）:

```bash
# 1. Context7でドキュメント確認
mcp__context7__resolve-library-id  # ライブラリID取得
mcp__context7__get-library-docs    # ドキュメント取得

# 2. 必要に応じてWeb検索
# - 最新バージョン確認
# - 変更履歴・破壊的変更の確認
# - 既知の問題の確認
```

**確認が必要なタイミング**:

| タイミング | 確認内容 |
| ---------- | -------- |
| パッケージ追加 | 最新APIの使い方、型定義の有無 |
| バージョン更新 | 破壊的変更、マイグレーション手順 |
| 外部API使用 | 正しいメソッド・パラメータ、認証方法 |

### Context7 vs Web検索の使い分け

| 用途 | Context7 | Web検索 |
| ---- | -------- | -------- |
| APIリファレンス | ✅ | |
| コード例 | ✅ | |
| 最新バージョン | | ✅ |
| 変更履歴 | | ✅ |
| 比較検討 | | ✅ |
| トラブルシューティング | | ✅ |

## UIコンポーネント設計

同じ視覚表現は共通コンポーネントを作成:

```tsx
// ❌ インラインスタイル重複
<div className="w-6 h-6 rounded-full bg-success" />  // マーカー
<div className="w-3 h-3 rounded-full bg-success" />  // 凡例（不整合リスク）

// ✅ 共通コンポーネント
<StatusIndicator status="done" size="md" />
<StatusIndicator status="done" size="sm" />
```

## デバッグログ

### 方針

| 環境 | 対応 |
|------|------|
| **開発環境** | `console.log` で詳細ログ（条件付き） |
| **本番環境** | Sentryでエラーコンテキスト強化 |

**理由**: 本番Workerログは永続保存されない（Logpushは有料）

### ログを追加すべき箇所

以下の処理には必ずログを追加:

1. **状態変更** - ステータス更新、座席変更、参加者追加/削除
2. **管理者アクション** - リセット、キック、削除
3. **エラーハンドリング** - catch句でコンテキスト付きログ
4. **外部連携** - KV操作、WebSocket接続/切断

### 実装パターン

```typescript
// 開発環境のみログ（Frontend）
if (import.meta.env.DEV) {
  console.log('[ModuleName] State changed', { from, to, context });
}

// 開発環境のみログ（Worker）
if (env.ENVIRONMENT !== 'production') {
  console.log('[RoomDO] Operation', { details });
}

// エラー時は本番でもログ（Sentryに送信される）
console.error('[ModuleName] Operation failed', {
  operation: 'operation_name',
  error: error instanceof Error ? error.message : String(error),
  context: { participantId, roomId }
});
```

### ログフォーマット

- **プリフィックス必須**: `[ModuleName]` で始める
- **構造化データ**: オブジェクトでコンテキストを渡す
- **PII除去**: ユーザー名・メールアドレスをログに含めない

## Sentry使用ガイドライン（Worker）

Cloudflare Workersではisolateモデルのため、**グローバルスコープへの状態設定は厳禁**。

### 禁止パターン

```typescript
// ❌ スコープリーク：タグがリクエスト間でリークする
Sentry.setTag("error.type", "app_error");
Sentry.setContext("request", { path: "/api" });
Sentry.setUser({ id: "123" });
Sentry.setExtra("debug", data);
```

### 正しいパターン

```typescript
// ✅ withScopeでスコープを分離
Sentry.withScope((scope) => {
  scope.setTag("error.type", "unexpected");
  scope.setContext("request", { path: c.req.path });
  Sentry.captureException(err);
});
```

### CI検査

`check-sentry-usage.py` がCI時に自動検査。禁止パターンがあるとCIが失敗。

## UI変更の確認

UI変更は必ずChrome DevTools MCPで確認:

1. `pnpm dev:frontend` + `pnpm dev:worker`
2. `mcp__chrome-devtools__take_screenshot`
3. ライト/ダークモード両方確認

**UI変更対象**:

- CSS/スタイル変更
- コンポーネント追加・変更
- i18n翻訳ファイル変更
