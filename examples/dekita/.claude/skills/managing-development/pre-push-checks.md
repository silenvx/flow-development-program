# プッシュ前チェック

ローカルテスト・Lint、Codexレビュー、並列レビューの手順。

## ローカルテスト・Lint（PR作成前必須）

**目的**: CIでの失敗を事前に検出し、手戻りを防ぐ。

### 実行コマンド

```bash
# 1. Lint（TypeScript/JavaScript）
pnpm lint

# 2. 型チェック
pnpm typecheck

# 3. Python Lint（フック変更時）
uvx ruff check .claude/hooks/
uvx ruff format --check .claude/hooks/

# 4. テスト（変更に関連するもの）
pnpm test:ci

# Pythonフック変更時
uv run pytest .claude/hooks/tests/
```

### チェックリスト

| 変更対象 | 必須チェック |
|----------|-------------|
| TypeScript/JavaScript | `pnpm lint && pnpm typecheck` |
| Pythonフック | `uvx ruff check && uv run pytest` |
| React コンポーネント | 上記 + ブラウザでの目視確認 |
| API エンドポイント | 上記 + 手動リクエストテスト |

### なぜローカルで先に実行するか

| CI依存のみ | ローカル実行 |
|-----------|-------------|
| 失敗に気づくまで数分〜十数分 | 即座に問題を検出 |
| CI待ち中に他作業を始めて文脈スイッチ | 文脈を保ったまま即修正 |
| 手戻り遷移が発生（implementation→pre_check） | 手戻りなし |

**重要**: `pnpm lint && pnpm typecheck` を実行してから `gh pr create` を行う。

## Codexレビュー（プッシュ前必須）

**目的**: AIコードレビューでバグや設計問題を事前に検出。プッシュ時に `codex-review-check` フックがブロックするため、必ず実行する。

### 実行タイミング

| タイミング | 必須 | 理由 |
|-----------|------|------|
| PR作成前（初回プッシュ前） | ✅ | プッシュ時にブロックされる |
| レビュー対応後のコミット追加後 | ✅ | 再レビューが必要（ブロック対象） |
| typo修正など軽微な変更後 | ✅ | フックは区別しない |

### コマンド

```bash
# 基本実行
codex review --base main

# バックグラウンド実行（CI待ちと並行）
codex review --base main  # Claude CodeのBashツールで run_in_background=true を指定して実行
```

### よくある問題

| 問題 | 原因 | 対策 |
|------|------|------|
| 「レビュー未実行」でブロック | `codex review` を実行していない | PR作成前に必ず実行 |
| 「レビュー後に新コミット」でブロック | レビュー後にコミットを追加した | 再度 `codex review` を実行 |

### チェックリスト

プッシュ前に以下を確認:

- [ ] `codex review --base main` を実行した
- [ ] P0/P1の指摘があれば対応済み
- [ ] レビュー後にコミットを追加していない（追加した場合は再レビュー）

**重要**: レビュー対応でコミットを追加したら、再度 `codex review --base main` を実行する。

## 並列レビュー（推奨）

codex reviewとgemini /code-reviewを並列実行して時間を短縮する。

### コマンド

```bash
# 並列レビュー（codex + gemini を同時実行）
bun run .claude/scripts/parallel_review.ts

# ブランチ指定
bun run .claude/scripts/parallel_review.ts --base develop

# 詳細出力
bun run .claude/scripts/parallel_review.ts --verbose
```

### 個別実行（必要な場合）

```bash
# Codex単体
codex review --base main

# Gemini単体（非対話モード）
gemini "/code-review" --yolo -e code-review
```

### Gemini CLIの非対話モード

`gemini /code-review` は通常、対話モード内のスラッシュコマンドだが、以下のフラグで非対話実行が可能:

| フラグ | 意味 |
|--------|------|
| `"/code-review"` | スラッシュコマンドを引数として渡す |
| `--yolo` | ツール実行の自動承認（非対話に必須） |
| `-e code-review` | code-review拡張を有効化 |

### 使い分け

| 状況 | 推奨コマンド |
|------|-------------|
| 通常のPR作成前 | `bun run .claude/scripts/parallel_review.ts`（両方実行） |
| 軽微な修正後 | `codex review --base main`（Codexのみ） |
| Geminiのみ必要 | `gemini "/code-review" --yolo -e code-review` |
