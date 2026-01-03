# .github/ ディレクトリ

GitHub Actions CI/CD、Issue/PRテンプレート、Dependabot設定。

## ファイル一覧（13個）

### workflows/ - 6ファイル

| ファイル名 | Why（なぜ必要か） | What（何をするか） | 使い方 |
|------------|------------------|-------------------|--------|
| `ci.yml` | mainブランチへのpush/PRで自動的に品質チェックとデプロイを実行するため。手動トリガーや強制デプロイオプションも提供し、柔軟なCI/CD運用を可能にする | 変更検出、CI、Preview E2E、E2E、デプロイの5段階パイプラインをorchestrateする。各ジョブは再利用可能ワークフローを呼び出し、Status Checkジョブで全体の成否を判定する | mainブランチへのpush/PRで自動実行。手動実行は Actions タブから `workflow_dispatch` で `force_deploy` オプション指定可能 |
| `merge-check.yml` | PRマージ前に安全性を確認し、問題のあるPRがマージされるのを防ぐため。ドラフトPR以外の全PRで自動実行される | `.claude/hooks/merge-check.py` スクリプトを dry-run モードで実行し、マージ可能かどうかを検証する。Exit code 0=ready、1=issues found、2=error で結果を判定 | mainブランチへのPR作成・更新時に自動実行。ドラフトPRではスキップされる |
| `_ci.yml` | 再利用可能ワークフローとしてCI処理を一箇所に集約し、メンテナンス性を向上させるため。TypeScript/Python/Shell全てのLint、テスト、ビルドを包括的に実行 | pnpm install、security audit、各種Lint（TypeScript、Python ruff、shell shellcheck）、i18nチェック、Sentryチェック、アイコン整合性チェック、wrangler types確認、typecheck、テスト（カバレッジ付き）、ビルドを実行 | `ci.yml` から `uses: ./.github/workflows/_ci.yml` で呼び出し。`webapp_changed` 入力でWebアプリ変更時のみ一部ステップを実行 |
| `_deploy.yml` | 本番環境へのデプロイを再利用可能ワークフローとして提供し、Worker→Frontend の順序でデプロイを確実に実行するため | Cloudflare Workers にワーカーをデプロイ後、Vite環境変数（Sentry、PostHog、広告設定）を設定してフロントエンドをビルドし、Cloudflare Pages にデプロイする | `ci.yml` から `uses: ./.github/workflows/_deploy.yml` で呼び出し。CLOUDFLARE認証情報とVITE環境変数をsecrets/inputsで渡す |
| `_e2e.yml` | ローカル環境でのE2Eテストを再利用可能ワークフローとして提供し、本番デプロイ前に動作確認を行うため | Playwright Chromiumをインストール、動的ポートでWorkerを起動、フロントエンドをビルドしてE2Eテストを実行。レポートはアーティファクトとして30日間保存 | `ci.yml` から `uses: ./.github/workflows/_e2e.yml` で呼び出し。mainブランチへのpush時かつWebアプリ変更時に実行される |
| `_preview-e2e.yml` | PRのプレビュー環境でE2Eテストを実行し、本番相当の環境で動作確認を行うため。`run-e2e` ラベル付きPRで実行される | Worker Preview環境にデプロイ、そのURLを使ってフロントエンドをビルド・Cloudflare Pagesにプレビューデプロイし、デプロイURLに対してPlaywright E2Eテストを実行 | PRに `run-e2e` ラベルを付けると自動実行。または `workflow_dispatch` で手動実行（main以外のブランチ） |

### ISSUE_TEMPLATE/ - 4ファイル

| ファイル名 | Why（なぜ必要か） | What（何をするか） | 使い方 |
|------------|------------------|-------------------|--------|
| `bug_report.yml` | バグ報告を構造化されたフォーマットで収集し、再現手順や環境情報を漏れなく取得するため。トリアージの効率化にも貢献する | 優先度（P0-P3）、バグ説明、再現手順、期待動作、環境情報、追加情報の各フィールドをYAML形式のフォームで定義。`bug` と `needs-triage` ラベルが自動付与される | GitHubの Issues → New Issue から「Bug Report」テンプレートを選択して入力 |
| `config.yml` | Issueテンプレートの動作設定を制御し、空のIssue作成を禁止して必ずテンプレートを使用させるため | `blank_issues_enabled: false` で空Issue作成を無効化。`contact_links` は空配列で外部リンクなし | 設定ファイルとして配置するのみ。ユーザーは直接操作しない |
| `feature_request.yml` | 新機能・改善提案を構造化されたフォーマットで収集し、課題と解決策を明確にするため。優先度付けで実装順序の判断材料を提供 | 優先度（P0-P3）、解決したい課題、提案する解決策、代替案、追加情報の各フィールドをYAML形式で定義。`enhancement` と `needs-triage` ラベルが自動付与される | GitHubの Issues → New Issue から「Feature Request」テンプレートを選択して入力 |
| `other.yml` | バグでも機能要望でもない課題（ドキュメント、リファクタリング、CI/CD、セキュリティ、パフォーマンス）を報告するため | 優先度（P0-P3）、種類（ドキュメント/リファクタリング/テスト/CI・CD/セキュリティ/パフォーマンス/その他）、説明、追加情報の各フィールドを定義。`needs-triage` ラベルが自動付与 | GitHubの Issues → New Issue から「その他」テンプレートを選択して入力 |

### その他のファイル - 3ファイル

| ファイル名 | Why（なぜ必要か） | What（何をするか） | 使い方 |
|------------|------------------|-------------------|--------|
| `copilot-instructions.md` | GitHub Copilot（コード補完・レビュー）がこのリポジトリで生成するテキストを日本語に統一するため | 「Write all generated comments, explanations, responses, and reviews in Japanese.」という1行の指示を記載 | ファイルを配置するのみ。Copilotが自動的に読み込んで日本語で応答するようになる |
| `dependabot.yml` | 依存関係の更新を自動化し、セキュリティパッチやバグ修正を定期的に取り込むため。PRノイズを抑えつつメジャーアップデートは手動レビューに | npm（pnpm）とGitHub Actionsの2エコシステムを監視。毎週月曜9:00 JSTに更新チェック。dev-dependenciesとproduction-patchをグループ化し、メジャーアップデートは無視。PR上限5件 | ファイルを配置するとDependabotが自動でPRを作成。`dependencies` ラベル付きで `chore(deps):` プレフィックスのコミットメッセージ |
| `pull_request_template.md` | PR作成時に必要な情報（変更概要、テスト計画、関連Issue）を漏れなく記載させるため。レビュアーの理解を助け、テスト漏れを防ぐ | Summary（変更内容の説明）、Test plan（テスト方法のチェックリスト）、Related Issues（関連Issue番号）の3セクションをマークダウンテンプレートで定義 | PR作成時に自動的にテンプレートが適用される。各セクションのプレースホルダーを埋めて使用 |
