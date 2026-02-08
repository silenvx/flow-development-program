# Shell Script → TypeScript 移行計画

Issue: #2875

## 背景

現在、開発フロー関連のスクリプトは Shell Script (bash) と TypeScript (Bun) が混在している。
TypeScriptへの統一により以下のメリットが得られる:

- **型安全性**: 実行時エラーの防止
- **外部コマンド依存の排除**: jq, sed, grep等のサブプロセス生成を削減（真の高速化要因）
- **統一性**: コードベースの統一
- **テスト容易性**: TypeScriptテストフレームワークの活用

> **注意**: 純粋な起動時間だけではBashの方が高速な可能性がある。TypeScript化の真のメリットは外部コマンド呼び出しをネイティブ実装に置換することで得られるサブプロセス生成の削減にある。

## 対象スクリプト

`.claude/scripts/` 配下のシェルスクリプト（6ファイル）:

| スクリプト | 行数 | 主な機能 | 移行優先度 |
|-----------|------|----------|-----------|
| statusline.sh | 326 | Claude Code ステータスライン設定 | **高** |
| update_codex_marker_on_rebase.sh | 127 | リベース時のCodexマーカー更新 | 中 |
| codex_design_review.sh | 127 | Codex設計レビュー実行 | **低（維持）** |
| setup_agent_cli.sh | 169 | Agent CLI セットアップ | **低（維持）** |
| setup_worktree.sh | 82 | Worktreeセットアップ | **低（維持）** |

## 移行判断基準

### TypeScriptへ移行すべきスクリプト

1. **複雑なロジック**: 条件分岐、ループ、データ変換が多い
2. **JSON/API操作**: 型安全性が重要
3. **エラーハンドリング**: 細かいエラー処理が必要
4. **テスト必要性**: ユニットテストで品質担保したい

### Shellのまま維持すべきスクリプト

1. **単純なコマンド実行**: pnpm install, bun installなど
2. **環境設定**: シンボリックリンク、環境変数設定
3. **CLIラッパー**: 引数を転送するだけ

## Phase 1: 高優先度（複雑なロジック）

### statusline.sh → statusline.ts

**現状分析**:
- 326行の最大スクリプト
- 複雑なJSON解析（jq使用）
- 多言語対応（日本語/英語）
- 多数の条件分岐

**移行のメリット**:
- Bunの型安全なJSON操作（jq依存を排除）
- エラーハンドリングの改善
- テスト追加が容易

**実装方針**:
- jq, sed, grep, tr等の外部コマンドはBunネイティブ実装に置換
- `Bun.$`でシェルコマンドを叩くだけの実装は避ける
- タイムアウト処理は `AbortSignal` を使用して確実に実装
```typescript
// .claude/scripts/statusline.ts

interface StatusLineConfig {
  enabled: boolean;
  format: string;
  // ...
}

async function main() {
  // ネイティブAPIを使用（jq依存を排除）
  const configFile = Bun.file("~/.claude/settings.json");
  const config: StatusLineConfig = await configFile.json();
  await updateStatusLine(config);
}
```

**互換性要件**:
- 既存の引数インターフェースを維持
- 出力フォーマットを変更しない

## Phase 2: 中優先度（API呼び出し）

### update_codex_marker_on_rebase.sh → update_codex_marker_on_rebase.ts

**現状分析**:
- 127行
- Git操作とファイル更新の組み合わせ

**移行のメリット**:
- エラーハンドリングの改善
- ファイル操作の型安全性

**Git操作の方針**:
- Git操作については、`Bun.$`を通した`git`コマンドの呼び出しを許容する
- 代替として`simple-git`等のライブラリ利用も検討
- ファイル操作（読み書き）はBunネイティブAPIを使用

## Phase 3: 維持判断

以下のスクリプトはシェルスクリプトのまま維持する。

### setup_worktree.sh（維持）

**理由**:
- 単純なコマンド実行のみ（cd, pnpm install, bun install）
- TypeScript化しても複雑さが増すだけ
- エラー時はシェルの`set -e`で十分

### setup_agent_cli.sh（維持）

**理由**:
- 環境変数設定、シンボリックリンク作成
- OS依存の操作はShellの方が自然
- 一度実行したら終わりのセットアップスクリプト

### codex_design_review.sh（維持）

**理由**:
- Codex CLIの単純なラッパー
- 引数を転送するだけ
- TypeScript化のメリットがない

## 移行手順

各スクリプトの移行は以下の手順で行う:

1. **TypeScriptファイル作成**: `.claude/scripts/<name>.ts`
2. **型定義追加**: 入出力の型を定義
3. **実装**: 既存ロジックをTypeScriptで再実装
4. **テスト追加**: `.claude/scripts/__tests__/<name>.test.ts`
5. **互換性テスト**: 既存の使用箇所で動作確認
6. **古いスクリプト削除**: シェルスクリプトを削除
7. **呼び出し元更新**: 必要に応じてパスを更新

## ディレクトリ構造

```
.claude/scripts/
├── ts/
│   ├── statusline.ts           # Phase 1
│   ├── update_codex_marker_on_rebase.ts  # Phase 2
│   └── __tests__/
│       ├── statusline.test.ts
│       └── update_codex_marker_on_rebase.test.ts
├── setup_worktree.sh           # 維持
├── setup_agent_cli.sh          # 維持
└── codex_design_review.sh      # 維持
```

## リスクと対策

| リスク | 対策 |
|--------|------|
| 互換性問題 | 既存の入出力インターフェースを厳密に維持 |
| 依存関係 | Bunの組み込みAPIを優先、外部依存を最小化 |
| パフォーマンス | 外部コマンド呼び出しをネイティブ実装に置換、サブプロセス生成を削減 |
| 環境依存性 | 移行後のTypeScriptスクリプトは `bun` パスが必要。呼び出し元で `command -v bun` チェックを検討 |
| タイムアウト | ghコマンド等の外部呼び出しは `AbortSignal` でタイムアウト実装（プロンプト表示のブロック防止） |
| ロールバック | 古いスクリプトは移行完了後に削除 |

## スケジュール（目安）

- **Phase 1**: statusline.ts（1 PR）
- **Phase 2**: update_codex_marker_on_rebase.ts（1 PR）
- **Phase 3**: 維持判断の最終確認

## 成功基準

- [ ] 移行後のスクリプトが既存の使用箇所で正常動作
- [ ] テストカバレッジ80%以上
- [ ] 典型的なタスクにおける総実行時間がShell版と同等以下
- [ ] 外部コマンド呼び出し・サブプロセス生成回数がShell版より削減されている
- [ ] エラーメッセージが改善されている
