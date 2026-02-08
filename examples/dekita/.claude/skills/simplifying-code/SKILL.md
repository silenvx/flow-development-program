---
name: simplifying-code
description: Simplifies and refines code for clarity, consistency, and maintainability while preserving all functionality. Focuses on recently modified code with strict scope limitations. Use when PR creation is blocked by code-simplifier-check or when explicitly requested.
---

# Code Simplifier

**対象モデル**: Claude Opus 4.5, Sonnet 4
**最終更新**: 2026-01-24

AI生成コードの肥大化・複雑化を検出し、シンプルで保守しやすいコードへ改善するSkill。

---

## クイックスタート

```bash
# PR作成前に実行（code-simplifier-checkフックがブロックした場合）
/simplifying-code
```

---

## 重要な制限事項

### 1. スコープ制限（最重要）

**このPR/ブランチでmainから変更した部分のみ対象**。変更していない箇所には一切触れない。

```bash
# 対象範囲の確認（mainとの差分）
git diff origin/main...HEAD --name-only
```

### 2. 機能保持（必須）

- テスト結果が変わる変更は禁止
- 既存の動作を一切変えない
- エッジケースの処理を削除しない

### 3. パターン維持

- プロジェクトの既存コードスタイルを尊重
- 既存の命名規則に従う
- フックの既存パターン（`approveAndExit`、`blockAndExit`等）を維持

### 4. 禁止事項

| 禁止 | 理由 |
| ---- | ---- |
| 関数シグネチャの変更 | 呼び出し元に影響 |
| エラーハンドリングの削除 | 安全性低下 |
| 型定義の変更 | 型安全性に影響 |
| インポートの大幅な変更 | 依存関係に影響 |
| コメントの一括削除 | 理由があって書かれたコメント |

---

## ワークフロー

### 1. 対象ファイルの特定

```bash
# 変更されたコードファイルを確認
git diff origin/main...HEAD --name-only | grep -iE '\.(ts|tsx|js|jsx|mjs|cjs|py)$'
```

### 2. 各ファイルの分析

変更部分のみを対象に以下を確認:

- 冗長なコード（同じ処理の繰り返し）
- 過度なネスト（3段階以上）
- 不要な抽象化（使われていないヘルパー関数）
- 過度なエラーハンドリング（ありえないケースの処理）

### 3. 改善提案

**大きな変更は提案のみ**。自動適用は小さな修正に限定。

| 変更規模 | 対応 |
| -------- | ---- |
| 1-2行の修正 | 自動適用可 |
| 3-10行の修正 | 提案して確認 |
| 10行超の修正 | 提案のみ（自動適用禁止） |

### 4. 検証

変更後は必ず:

```bash
pnpm typecheck && pnpm lint && pnpm test:ci
```

---

## チェックリスト

- [ ] 変更はPR/ブランチの差分範囲内のみか
- [ ] 既存の動作は保持されているか
- [ ] プロジェクトのパターンに従っているか
- [ ] 大きな変更は提案のみにしたか
- [ ] typecheck/lint/testが通るか

---

## 適用しない場合

以下の場合はcode-simplifier-check（PR作成前フック）をスキップ:

```bash
SKIP_CODE_SIMPLIFIER=1 gh pr create ...
```

スキップ推奨ケース:
- ドキュメントのみの変更
- 設定ファイルのみの変更
- 緊急hotfix

---

## トラブルシューティング

| 問題 | 解決策 |
| ---- | ------ |
| 変更範囲が広すぎる | スコープを再確認、変更していない部分は除外 |
| テストが失敗 | 変更を取り消し、より小さな範囲で再試行 |
| 既存パターンと異なる | プロジェクトの既存コードを参照して合わせる |

---

## 関連ファイル

- `.claude/hooks/handlers/code_simplifier_check.ts` - PR作成前チェック
- `.claude/hooks/handlers/code_simplifier_logger.ts` - 実行ログ記録
