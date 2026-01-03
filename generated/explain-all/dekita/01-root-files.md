# ルートファイル（設定・ドキュメント）

## ファイル一覧（19個）

| ファイル名 | Why（なぜ必要か） | What（何をするか） | 使い方 |
|------------|------------------|-------------------|--------|
| `.editorconfig` | 異なるエディタ間でコーディングスタイルを統一し、チーム開発での一貫性を保つために必要 | インデント（スペース2）、改行コード（LF）、文字コード（UTF-8）、末尾空白削除のエディタ設定を定義する | エディタがEditorConfigプラグインを認識すると自動適用される。Makefileはタブ、Markdownは末尾空白保持の例外設定あり |
| `.gitignore` | ビルド成果物、依存関係、IDE設定、機密情報をGit管理から除外し、リポジトリをクリーンに保つために必要 | node_modules、dist、.env、IDE設定、Playwrightレポート、Pythonキャッシュを無視する | ファイル配置時に自動適用。`.claude/`配下の特定ディレクトリ（hooks, scripts）は追跡対象として例外指定 |
| `.markdownlint.json` | Markdownファイルの品質を統一し、読みやすいドキュメントを維持するために必要 | MD013（行長制限）とMD041（先頭見出し必須）ルールを無効化するmarkdownlint設定 | `pnpm lint`実行時に自動適用。長い行やメタ情報先頭のMarkdownを許容 |
| `.markdownlintignore` | Pythonやコードファイルがmarkdownlintで誤処理されるのを防ぐために必要 | .py、.ts、.json等のコードファイルとビルド成果物をmarkdownlint対象から除外する | markdownlint実行時に自動参照。Issue #1270で導入された |
| `.mcp.json` | Claude Code向けにMCPサーバー（外部ツール連携）を設定するために必要 | chrome-devtools-mcpサーバーを定義し、ブラウザDevTools連携を有効にする | Claude Codeセッション開始時に自動読み込み。`--isolated`オプションで独立モード起動 |
| `.nvmrc` | Node.jsバージョンをチーム全体で統一し、環境差異による問題を防ぐために必要 | プロジェクトで使用するNode.jsバージョン（24）を指定する | `nvm use`コマンドで自動切り替え。`engines`フィールドと連携してCI/ローカルで一貫性確保 |
| `AGENTS.md` | AIコーディングエージェント（Claude等）向けにプロジェクトルールと指示を提供するために必要 | 基本原則、Issue管理、PR作成、フック使用、レビューガイドライン等のAI向け詳細指示を記載 | Claude CodeがCLAUDE.md経由で自動参照。Skills、ワークフロー、コマンド体系を網羅 |
| `CLAUDE.md` | Claude Codeがプロジェクト固有の指示を認識できるよう標準的なエントリポイントを提供するために必要 | `@AGENTS.md`を参照し、AGENTS.mdの内容をClaude Codeに読み込ませる | Claude Codeセッション開始時に自動読み込み。シンプルな参照構造でメンテナンス容易 |
| `README.md` | 新規開発者がプロジェクト概要、セットアップ手順、使い方を把握できるようにするために必要 | dekita!の機能説明、技術スタック、セットアップ手順、Cloudflare環境構築、API仕様を文書化 | `git clone`後に最初に参照。`pnpm install`、`pnpm dev`でローカル開発開始 |
| `TROUBLESHOOTING.md` | 開発中に発生する問題の解決策を集約し、同じ問題での時間浪費を防ぐために必要 | Worktree、Claude Code、Agent CLI、CI/CD関連の問題と解決策をカテゴリ別に文書化 | 問題発生時に目次から該当セクションを参照。Issue番号付きで背景も理解可能 |
| `Makefile` | 開発環境セットアップを簡単なコマンドで実行できるようにするために必要 | `make setup`でlefthookのインストールとGitフック有効化を自動実行する | クローン後に`make setup`を実行。brew/go経由でlefthookをインストール |
| `biome.json` | TypeScript/JavaScriptのLint・フォーマットルールを統一し、コード品質を確保するために必要 | import整理、推奨Lintルール、スペース2・行幅100のフォーマット設定を定義する | `pnpm lint`、`pnpm format`実行時に適用。Git VCS連携で.gitignore尊重 |
| `lefthook.yml` | Git操作時の自動チェックを設定し、問題のあるコミット・プッシュを防ぐために必要 | pre-commit（shebang検証、ruff、gitleaks）、pre-push（typecheck、lint、test）等のGitフックを定義 | `make setup`でインストール。コミット/プッシュ時に自動実行。`--skip-hooks-only`条件で効率化 |
| `package.json` | プロジェクトのメタ情報、依存関係、スクリプトを管理するNode.jsパッケージ設定として必要 | pnpmワークスペースのルートパッケージ。dev/build/test/lint/typecheckスクリプトと共通devDependenciesを定義 | `pnpm install`で依存解決。`pnpm dev`で開発開始、`pnpm test:ci`でCI用テスト実行 |
| `playwright.config.ts` | E2Eテストの実行環境、対象ブラウザ、サーバー設定を統一するために必要 | Chromium/Firefox/WebKit+モバイルビューポートのテスト設定、CI/ローカル別のwebServer設定を定義 | `pnpm test:e2e`でE2Eテスト実行。`--project=chromium`で特定ブラウザのみ実行可能 |
| `pnpm-lock.yaml` | 依存パッケージのバージョンを固定し、再現可能なインストールを保証するために必要 | 全依存パッケージの正確なバージョン、ハッシュ、ワークスペース間の依存関係を記録 | `pnpm install`で自動参照。直接編集不要、依存変更時に自動更新 |
| `pnpm-workspace.yaml` | pnpmモノレポのワークスペース構成を定義し、パッケージ間参照を可能にするために必要 | frontend、worker、sharedの3パッケージをワークスペースとして登録する | `pnpm --filter @dekita/frontend`等でワークスペース指定コマンド実行可能 |
| `pyproject.toml` | Claude Codeフック用Pythonツール（ruff、pytest）の設定を統一するために必要 | ruff（Lint/Format）とpytestの設定。.claude/hooks/とscripts/を対象にルール適用 | `uvx ruff format`、`uvx ruff check`で実行。pre-commit/pre-pushで自動適用 |
