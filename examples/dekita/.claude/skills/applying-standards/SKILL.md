---
name: applying-standards
description: Applies coding standards, testing guidelines, linting rules, type checking, and backward compatibility requirements. Use when writing code, running tests, linting, type checking, or ensuring code quality compliance.
---

# コーディング規約

このプロジェクトのコーディング規約と品質基準。

## 目次

| ファイル | 内容 |
|----------|------|
| [comments-commits.md](comments-commits.md) | コメント規約、CUSTOMIZEコメント、コミットメッセージ |
| [code-quality.md](code-quality.md) | 共通パターン修正、Pythonフック、並列処理、後方互換性 |
| [testing.md](testing.md) | テスト必須、TDD、新機能ライフサイクル |
| [frontend-guidelines.md](frontend-guidelines.md) | UIコンポーネント、デバッグログ、Sentry |

## 基本ルール

- **TypeScript** を使用
- **Biome** でフォーマット・Lint
- 既存のパターンと規約に従う
- 新しいファイルより既存ファイルの編集を優先

## コマンド

<!-- CUSTOMIZE: パッケージマネージャ - 自分のプロジェクトに合わせて変更（npm/yarn/pnpm） -->

| 用途       | コマンド           |
| ---------- | ------------------ |
| ビルド     | `pnpm build`       |
| テスト     | `pnpm test:ci`     |
| Lint       | `pnpm lint`        |
| 型チェック | `pnpm typecheck`   |

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

## 並列処理

独立したタスクは並列で実行:

- 独立したファイルの読み取り・検索
- ビルド・テスト・Lintの並列実行
- CI監視中の他タスク実行

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

### Context7 vs Web検索の使い分け

| 用途 | Context7 | Web検索 |
| ---- | -------- | -------- |
| APIリファレンス | ✅ | |
| コード例 | ✅ | |
| 最新バージョン | | ✅ |
| 変更履歴 | | ✅ |
| 比較検討 | | ✅ |
| トラブルシューティング | | ✅ |
