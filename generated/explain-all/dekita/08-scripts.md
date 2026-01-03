# scripts/ シェルスクリプト

ルートレベルの開発支援スクリプト。Git hooks、worktree管理、開発環境構築。

## ファイル一覧（8個）

| ファイル名 | Why（なぜ必要か） | What（何をするか） | 使い方 |
|------------|------------------|-------------------|--------|
| `check-ad-coverage.sh` | 全ページに広告が適切に配置されているか検証し、広告収益の漏れを防ぐため。privacy/termsページには広告を配置しない規則を強制する | routes/配下のページファイルをスキャンし、AdBannerコンポーネントの存在を確認。除外ページ（privacy.tsx/terms.tsx）には広告がないこと、通常ページには広告があることを検証する | `./scripts/check-ad-coverage.sh` |
| `check-lefthook.sh` | lefthookが正しくセットアップされているか確認し、pre-push等のフックが有効になっていることを保証するため。フック未設定による品質低下を防止する | pre-pushフックファイルの存在確認と、それがlefthook管理のフックかどうかを判定する。worktree環境にも対応（git rev-parse --git-common-dir使用） | `./scripts/check-lefthook.sh` |
| `check-vite-env.sh` | Vite環境変数はビルド時に埋め込まれるため、ワークフロー（_deploy.yml）での定義漏れを事前に検出し、本番環境での不具合を防ぐため | フロントエンドコードからimport.meta.env.VITE_*変数を抽出し、_deploy.ymlのBuild frontendステップのenvセクションで定義されているか照合。未定義変数を報告する | `./scripts/check-vite-env.sh` |
| `cleanup-worktrees.sh` | マージ完了したworktreeを自動削除し、ディスク容量の節約とworktree一覧の整理を行うため。手動削除の手間を省き、開発環境を清潔に保つ | gh CLIでPR状態を確認し、MERGED/CLOSEDのworktreeを検出。worktree削除とローカルブランチ削除を実行。デフォルトはドライラン、--forceで実削除 | `./scripts/cleanup-worktrees.sh [--force]` |
| `dev.sh` | Workerのポートを自動検出し、Frontendのプロキシ設定を自動更新して開発環境を簡単に起動するため。手動でのポート設定や.env.local編集を不要にする | Workerをバックグラウンドで起動し、ログからポート番号を抽出。.env.localにVITE_API_PROXY_TARGETを設定してからFrontendを起動。両プロセスを監視し、Ctrl+Cで終了 | `./scripts/dev.sh` |
| `pre-push.sh` | CIで検知される前にローカルでLint/TypeCheckエラーを検出し、プッシュ後の手戻りを防ぐため。開発効率向上とCI負荷軽減に貢献する | pnpm lintでESLintチェック、pnpm typecheckでTypeScriptの型チェックを実行。いずれか失敗でpushをブロック。色付き出力でターミナル表示を改善 | `./scripts/setup-hooks.sh`で設定後、git push時に自動実行 |
| `prepare-lefthook.sh` | worktreeでpnpm install時にlefthookがworktreeパスをハードコードする問題を回避するため。どのworktreeからでも正しくフックをインストールする | メインリポジトリのパスを検出し、メインリポジトリのnode_modules/.bin/lefthookバイナリを使用してlefthook installを実行。pnpm postinstallから呼び出される | pnpm install時に自動実行（postinstallから呼び出し） |
| `setup-hooks.sh` | pre-push等のGit hooksを有効化し、CIで検知される前にローカルでエラーを検出するため。clone後のセットアップを簡略化し、チーム全体で品質基準を統一する | scripts/内のhookスクリプト（pre-push.sh）を.git/hooks/にシンボリックリンクとして作成。worktree対応（git rev-parse --git-path hooks使用）。既存フックはバックアップ | `./scripts/setup-hooks.sh`（clone後に1回実行） |
