# dekita

ハンズオン・ワークショップ向けの進捗確認アプリ。参加者が「できた！」ボタンを押すと、講師がリアルタイムで全体の進捗を確認できます。

**本番環境**: <https://dekita.app>

## 機能

- セッション作成（カスタムURL / 自動生成）
- 参加者の参加・進捗報告
- 講師向けリアルタイム進捗表示
- セッションリセット機能
- 24時間で自動削除

## 技術スタック

- **Frontend**: React 19, Vite, TanStack Router, TanStack Query, Tailwind CSS v4
- **Backend**: Cloudflare Workers, Hono, KV
- **Tooling**: pnpm, Biome, TypeScript, Vitest
- **Hosting**: Cloudflare Pages (Frontend), Cloudflare Workers (API)

## セットアップ

### 必要条件

- Node.js 24+
- pnpm（corepack経由で自動インストール）
- Cloudflare アカウント（デプロイ時）

### インストール

```bash
git clone <repository-url>
cd dekita
corepack enable  # pnpmを有効化
pnpm install
```

### ローカル開発

```bash
# フロントエンドとワーカーを同時起動
pnpm dev

# 個別に起動
pnpm dev:frontend  # http://localhost:5173
pnpm dev:worker    # http://localhost:8787
```

### スクリプト

| コマンド | 説明 |
| --------- | ------ |
| `pnpm dev` | 開発サーバー起動 |
| `pnpm build` | プロダクションビルド |
| `pnpm test` | テスト実行（watch mode） |
| `pnpm test:ci` | テスト実行（CI用、single run） |
| `pnpm lint` | Lintチェック |
| `pnpm lint:fix` | Lint自動修正 |
| `pnpm format` | コードフォーマット |
| `pnpm typecheck` | 型チェック |

## Cloudflare 環境構築

新規環境をセットアップする際の手順です。

### 1. Wrangler ログイン

```bash
npx wrangler login
```

ブラウザが開くので認証を完了してください。

### 2. アカウント情報の確認

```bash
npx wrangler whoami
```

Account ID が表示されます（GitHub Secrets で使用）。

### 3. KV Namespace の作成

```bash
npx wrangler kv namespace create DEKITA_SESSIONS
```

出力例:

```text
🌀 Creating namespace with title "DEKITA_SESSIONS"
✨ Success!
Add the following to your configuration file:
{ binding = "DEKITA_SESSIONS", id = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" }
```

表示された `id` を `worker/wrangler.jsonc` の `kv_namespaces[0].id` に設定してください。

### 4. Pages プロジェクトの作成

```bash
npx wrangler pages project create dekita --production-branch main
```

### 5. カスタムドメインの設定

#### Worker (api.dekita.app)

`worker/wrangler.jsonc` の `routes` に設定済み。デプロイ時に自動でDNS設定されます。

#### Pages (dekita.app)

1. [Cloudflare Dashboard](https://dash.cloudflare.com) にアクセス
2. Workers & Pages → dekita プロジェクト → Custom domains
3. `dekita.app` を追加

### 6. API トークンの作成

1. [Cloudflare API Tokens](https://dash.cloudflare.com/profile/api-tokens) にアクセス
2. **Create Token** → **Edit Cloudflare Workers** テンプレートを使用
3. トークンをコピー（一度しか表示されません）

### 7. GitHub Secrets/Variables の設定

```bash
# Account ID を設定
gh secret set CLOUDFLARE_ACCOUNT_ID

# API Token を設定（対話的に入力）
gh secret set CLOUDFLARE_API_TOKEN

# API URL を設定（フロントエンドビルド用）
gh variable set VITE_API_URL --body "https://api.dekita.app"
```

### 8. 監視・アナリティクスの設定（任意）

#### Sentry（エラー監視）

1. [Sentry](https://sentry.io/) でアカウント作成
2. React プロジェクトを作成 → DSN をコピー
3. Cloudflare Workers プロジェクトを作成 → DSN をコピー

```bash
# フロントエンド用 DSN（GitHub Secrets 経由でビルド時に渡す）
gh secret set VITE_SENTRY_DSN -b "https://xxx@xxx.ingest.sentry.io/xxx"

# Worker 用 DSN（wrangler secret）
cd worker
echo "https://xxx@xxx.ingest.sentry.io/xxx" | npx wrangler secret put SENTRY_DSN --env production
```

#### PostHog（アナリティクス）

1. [PostHog](https://posthog.com/) でアカウント作成
2. プロジェクトを作成 → Project API Key をコピー

```bash
gh secret set VITE_POSTHOG_KEY -b "phc_xxx"
gh secret set VITE_POSTHOG_HOST -b "https://us.i.posthog.com"
```

#### 環境変数一覧（監視・アナリティクス）

| 変数名 | 用途 | 設定場所 | 必須 |
| -------- | ------ | ---------- | ------ |
| `SENTRY_DSN` | Worker エラー監視 | wrangler secret | No |
| `VITE_SENTRY_DSN` | フロントエンド エラー監視 | GitHub Secrets | No |
| `VITE_POSTHOG_KEY` | アナリティクス | GitHub Secrets | No |
| `VITE_POSTHOG_HOST` | PostHog ホスト | GitHub Secrets | No |

### 9. デプロイ

`main` ブランチにプッシュすると自動デプロイされます。

手動でデプロイする場合:

```bash
# Worker
cd worker && pnpm exec wrangler deploy

# Pages
pnpm build:frontend
pnpm exec wrangler pages deploy frontend/dist --project-name=dekita
```

## GitHub Actions Self-Hosted Runner

コスト削減のため、GitHub Actionsはデフォルトでself-hosted runnerを使用します。

### Runnerセットアップ

1. **Runnerの追加**: Settings → Actions → Runners → New self-hosted runner から表示されるコマンドを実行（Apple Silicon Macはarm64版を選択）

```bash
mkdir ~/actions-runner && cd ~/actions-runner
curl -o actions-runner-osx-arm64-2.321.0.tar.gz -L https://github.com/actions/runner/releases/download/v2.321.0/actions-runner-osx-arm64-2.321.0.tar.gz
tar xzf ./actions-runner-osx-arm64-2.321.0.tar.gz
./config.sh --url https://github.com/YOUR_USER/dekita --token YOUR_TOKEN
./run.sh
```

1. **必要なツール**: Node.js 24+, pnpm（corepack経由）, Python 3, Git, uvx

### Runner切り替え

`RUNNER_TYPE` repository variableで制御:

| 値 | 動作 |
| --- | --- |
| `self-hosted`（デフォルト） | ローカルrunnerを使用 |
| `ubuntu-latest` | GitHub hosted runnerを使用 |

```bash
# GitHub hostedに切り替え
gh variable set RUNNER_TYPE --body "ubuntu-latest"

# self-hostedに戻す
gh variable set RUNNER_TYPE --body "self-hosted"
```

## プロジェクト構成

```text
dekita/
├── .github/workflows/ # CI/CD設定
│   ├── ci.yml        # lint, test, build
│   └── deploy.yml    # Cloudflare デプロイ
├── frontend/          # React フロントエンド
│   └── src/
│       ├── routes/    # ページコンポーネント
│       └── lib/       # ユーティリティ
├── worker/            # Cloudflare Worker API
│   └── src/
│       ├── routes/    # APIエンドポイント
│       ├── services/  # ビジネスロジック
│       └── repositories/ # データアクセス
├── shared/            # 共有型定義
├── biome.json           # Linter/Formatter設定
├── package.json         # プロジェクト設定
└── pnpm-workspace.yaml  # pnpm workspaces設定
```

## API エンドポイント

| Method | Path | 説明 |
| -------- | ------ | ------ |
| GET | `/api/health` | ヘルスチェック |
| POST | `/api/sessions` | セッション作成 |
| GET | `/api/sessions/:urlId` | セッション取得 |
| POST | `/api/sessions/:urlId/join` | セッション参加 |
| PATCH | `/api/sessions/:urlId/done` | 完了状態更新 |
| POST | `/api/sessions/:urlId/reset` | セッションリセット |
| POST | `/api/sessions/:urlId/verify-admin` | 管理者トークン検証 |

## ライセンス

Proprietary - All Rights Reserved
