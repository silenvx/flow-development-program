---
name: managing-development
description: Manages development workflow using Git Worktree, including PR creation, CI monitoring, and merge procedures. Use when creating worktrees, branches, PRs, monitoring CI, or merging changes.
---

# 開発ワークフロー

Git Worktreeを使用した開発フローの詳細手順。

**オリジナルは常にmainを維持、全ての作業はworktreeで行う。**

## 目次

| ファイル | 内容 |
|----------|------|
| [worktree-management.md](worktree-management.md) | Worktree作成・管理、チェックリスト |
| [commit-pr.md](commit-pr.md) | コミットメッセージ規約、PRボディ、段階的実装 |
| [pre-push-checks.md](pre-push-checks.md) | ローカルテスト・Lint、Codexレビュー、並列レビュー |
| [ci-merge.md](ci-merge.md) | CI監視、マージ手順、本番確認、Dependabot |

## 基本フロー

1. `git worktree add --lock .worktrees/<name> -b <branch>` - Worktree作成
2. `.claude/scripts/setup_worktree.sh .worktrees/<name>` - 依存インストール（自動検出）
3. `cd .worktrees/<name>` - worktreeに移動
4. `git fetch origin main && git diff HEAD..origin/main -- <変更予定ファイル>` - **main最新確認**
5. 作業・コミット
6. **ローカルテスト・Lint（必須）** - CI前に問題を検出
7. `/simplifying-code` - コード簡素化（**長時間セッション後に推奨**）
8. `codex review --base main` - ローカルレビュー（**コミット追加後は再実行**）
9. `gh pr create` - PR作成（**UI変更時はスクリーンショット必須**）
10. `ci_monitor`（TypeScript版）でCI監視 + AIレビュー確認・対応
11. `gh pr merge --squash` - マージ（必ず実行）
12. worktree削除・main更新

**重要**: タスク完了時は必ずマージまで実行する。PR作成で止まらない。

**マージ後**:

```bash
cd <オリジナル> && git worktree unlock .worktrees/<name> && git worktree remove .worktrees/<name> && git pull
```

## タスク要件確認（毎回実行）

**実装前に必ず確認する**（`task-start-checklist` フックが自動リマインド）:

### 要件確認

- [ ] 要件は明確か？曖昧な点があれば質問する
- [ ] ユーザーの意図を正しく理解しているか？
- [ ] 「〜したい」の背景・目的は何か？

### 設計判断

- [ ] 設計上の選択肢がある場合、ユーザーに確認する
- [ ] 既存のコードパターン・規約を把握しているか？
- [ ] 事前に決めておくべきことはないか？

### 影響範囲

- [ ] 変更の影響範囲を把握しているか？
- [ ] 破壊的変更はないか？あれば事前に確認する

### 前提条件

- [ ] 必要な環境・依存関係は整っているか？
- [ ] Context7/Web検索で最新情報を確認すべきか？

**重要**: 不明点があれば、実装前に必ず質問する。実装後の手戻りを防ぐ。

## クイックリファレンス

| やりたいこと | コマンド/参照 |
|-------------|--------------|
| Worktree作成 | `git worktree add --lock .worktrees/<name> -b <branch>` |
| CI監視 | `bun run .claude/scripts/ci_monitor_ts/main.ts {PR} --session-id <SESSION_ID>` |
| Codexレビュー | `codex review --base main` |
| 並列レビュー | `bun run .claude/scripts/parallel_review.ts` |
| マージ | `gh pr merge {PR} --squash` |

## トラブルシューティング

問題発生時は `troubleshooting` Skill を参照。
