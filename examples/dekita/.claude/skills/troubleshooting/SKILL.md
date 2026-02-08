---
name: troubleshooting
description: Provides solutions for common development issues including worktree failures, hook errors, CI failures, and deployment problems. Use when errors occur, commands fail, or unexpected behavior is encountered.
---

# トラブルシューティング

開発中に発生する可能性のある問題とその解決策。

## 目次

| ファイル | 内容 |
|----------|------|
| [worktree-issues.md](worktree-issues.md) | Worktree削除後の操作不能、cd問題、gh pr mergeエラー |
| [tool-issues.md](tool-issues.md) | Claude Codeフックエラー、Gemini CLI 404、Codexレート制限 |
| [ci-cd-issues.md](ci-cd-issues.md) | デプロイエラー、パッケージマネージャー移行、CIが開始されない |

## クイックスタート: 症状別ガイド

| 症状 | 原因の可能性 | 参照 |
|------|-------------|------|
| シェルが操作不能 | Worktree削除、cwdが存在しない | [worktree-issues.md](worktree-issues.md) |
| フックが動作しない | cdでcwd変更、フック設定キャッシュ | [worktree-issues.md](worktree-issues.md)、[tool-issues.md](tool-issues.md) |
| `gh pr merge`でエラー | Worktree使用中のブランチ削除試行 | [worktree-issues.md](worktree-issues.md) |
| Gemini CLIで404 | デフォルトモデル未設定 | [tool-issues.md](tool-issues.md) |
| Codexでレート制限 | 使用量上限到達 | [tool-issues.md](tool-issues.md) |
| CIが開始されない | コンフリクト、ワークフロー変更 | [ci-cd-issues.md](ci-cd-issues.md) |
| デプロイでUTF-8エラー | 日本語コミットメッセージ | [ci-cd-issues.md](ci-cd-issues.md) |

## 最も重要な対処法

**シェルが壊れたら**: Claude Codeを再起動する。

**CIが動かなかったら**: まず `gh pr view {PR} --json mergeable,mergeStateStatus` で状態を確認する。

**フックが効かなかったら**: セッションを再起動する（フック設定はセッション開始時にキャッシュされる）。
