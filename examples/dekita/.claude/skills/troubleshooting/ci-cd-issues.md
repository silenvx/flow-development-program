# CI/CD関連のトラブルシューティング

CI/CDに関連する問題と解決策。

## Cloudflare Pages デプロイで UTF-8 エラーが発生する

**症状**: デプロイ時に以下のエラーが発生:

```text
Invalid commit message, it must be a valid UTF-8 string. [code: 8000111]
```

**原因**: `wrangler pages deploy` がgitのコミットメッセージを自動取得し、日本語や絵文字を含むメッセージをCloudflare Pages APIに送信すると拒否される。

**解決策**: `--commit-message` オプションでASCIIのみのメッセージを明示的に指定する。

```yaml
# .github/workflows/_deploy.yml
command: pages deploy frontend/dist --project-name=dekita --commit-message=${{ github.sha }}
```

**注意**: GitHub Actionsの `command` パラメータでは、`${{ github.sha }}` をダブルクォートで囲む必要はない。

## パッケージマネージャー移行時のCI失敗

パッケージマネージャー（npm → pnpm など）を移行した後にCIが失敗する場合がある。

**よくある原因と確認事項**:

1. **設定ファイル内のコマンド**
   - `playwright.config.ts`, `jest.config.ts` 等のテスト設定
   - `package.json` の scripts
   - 確認: `grep -rE "npm run|npm install|yarn" --include="*.ts" --include="*.json"`

2. **CI/CDワークフロー内の直接的なコマンド**
   - `.github/workflows/*.yml` 内の `npm` / `yarn` コマンド
   - 確認: `grep -rE "npm|yarn" .github/workflows/`

3. **外部GitHub Actionsのpnpm互換性**
   - `cloudflare/wrangler-action` 等は pnpm workspace で問題が発生する場合がある
   - **症状**: `ERR_PNPM_ADDING_TO_ROOT`（pnpm 9.13.2+でworkspace root への直接インストールが禁止）
   - **解決策**: 該当ツールをルートの `package.json` に事前インストール

**wrangler-action の場合**:

```bash
# ルート package.json に wrangler を追加
pnpm add -wD wrangler
```

これにより、wrangler-action が wrangler を自動インストールしようとしてエラーになるのを防ぐ。

**参考**: [cloudflare/wrangler-action#338](https://github.com/cloudflare/wrangler-action/issues/338)

## Status Check と skipped ジョブの動作

**現在の設計**:

- `status-check` ジョブは失敗/キャンセル時のみ実行（成功時は skipped）
- この設計は [DevOps Directive の推奨パターン](https://devopsdirective.com/posts/2025/08/github-actions-required-checks-for-conditional-jobs/) に基づく

**推奨フロー**:

```bash
# 1. CI + AIレビュー完了を待機（バックグラウンド実行推奨）
bun run .claude/scripts/ci_monitor_ts/main.ts {PR} --session-id <SESSION_ID>

# 2. 手動マージ
gh pr merge {PR} --squash
```

**注意**: `--auto`オプションは`merge_check.py`フックでブロックされます。これはAIレビュー完了前の自動マージを防ぐための意図的な設計です。

**過去の問題（解決済み）**: 以前は手動マージで"Required status check is expected"エラーが発生するケースがありましたが、現在は正常に動作しています。問題が再発した場合は、PRの状態（`gh pr view {PR} --json mergeable,mergeStateStatus`）を確認してください。

## ワークフローファイル変更時の制限

PRでワークフローファイル（`.github/workflows/*.yml`）を変更した場合、そのPR上では変更後のワークフローは自動実行されない（`action_required`状態になる）。

**理由**: GitHubのセキュリティ機能。PRからの悪意あるワークフロー変更を防ぐため。

**対応**:

1. GitHub UIでワークフローを手動承認する、または
2. PRをマージして、次のPRで動作確認する

**注意**: 手動承認しても、承認時点のイベントコンテキストが異なるため、期待通りに動作しない場合がある。

## CIが開始されない場合

PRを作成したがCIが開始されない場合、以下の順序で確認する:

```bash
# 1. まずPRの状態を確認（最重要）
gh pr view {PR番号} --json mergeable,mergeStateStatus

# 2. 結果に応じて対応
# - mergeable: CONFLICTING → コンフリクトを解決（リベース）
# - mergeable: UNKNOWN → 少し待って再確認
# - mergeable: MERGEABLE → 他の原因を調査
```

**よくある原因と対応**:

| `mergeable` | `mergeStateStatus` | 原因 | 対応 |
| ----------- | ------------------ | ---- | ---- |
| CONFLICTING | DIRTY | コンフリクト | `git fetch origin main && git rebase origin/main` |
| MERGEABLE | BLOCKED | チェック待ち | CIの開始を待つ、またはワークフロー承認を確認 |
| UNKNOWN | UNKNOWN | GitHub処理中 | 数秒待って再確認 |

**注意**: 「ワークフローファイル変更による制限」と「コンフリクト」は異なる問題だが、どちらもCIが開始されない症状を示す。先入観で判断せず、必ず `mergeable` 状態を最初に確認すること。
