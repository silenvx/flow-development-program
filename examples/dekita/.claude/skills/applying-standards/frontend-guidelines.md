# フロントエンドガイドライン

UIコンポーネント設計、デバッグログ、Sentry使用、UI変更の確認。

## UIコンポーネント設計

同じ視覚表現は共通コンポーネントを作成:

```tsx
// ❌ インラインスタイル重複
<div className="w-6 h-6 rounded-full bg-success" />  // マーカー
<div className="w-3 h-3 rounded-full bg-success" />  // 凡例（不整合リスク）

// ✅ 共通コンポーネント
<StatusIndicator status="done" size="md" />
<StatusIndicator status="done" size="sm" />
```

## デバッグログ

### 方針

| 環境 | 対応 |
|------|------|
| **開発環境** | `console.log` で詳細ログ（条件付き） |
| **本番環境** | Sentryでエラーコンテキスト強化 |

**理由**: 本番Workerログは永続保存されない（Logpushは有料）

### ログを追加すべき箇所

以下の処理には必ずログを追加:

1. **状態変更** - ステータス更新、座席変更、参加者追加/削除
2. **管理者アクション** - リセット、キック、削除
3. **エラーハンドリング** - catch句でコンテキスト付きログ
4. **外部連携** - KV操作、WebSocket接続/切断

### 実装パターン

```typescript
// 開発環境のみログ（Frontend）
if (import.meta.env.DEV) {
  console.log('[ModuleName] State changed', { from, to, context });
}

// 開発環境のみログ（Worker）
if (env.ENVIRONMENT !== 'production') {
  console.log('[RoomDO] Operation', { details });
}

// エラー時は本番でもログ（Sentryに送信される）
console.error('[ModuleName] Operation failed', {
  operation: 'operation_name',
  error: error instanceof Error ? error.message : String(error),
  context: { participantId, roomId }
});
```

### ログフォーマット

- **プリフィックス必須**: `[ModuleName]` で始める
- **構造化データ**: オブジェクトでコンテキストを渡す
- **PII除去**: ユーザー名・メールアドレスをログに含めない

## Sentry使用ガイドライン（Worker）

Cloudflare Workersではisolateモデルのため、**グローバルスコープへの状態設定は厳禁**。

### 禁止パターン

```typescript
// ❌ スコープリーク：タグがリクエスト間でリークする
Sentry.setTag("error.type", "app_error");
Sentry.setContext("request", { path: "/api" });
Sentry.setUser({ id: "123" });
Sentry.setExtra("debug", data);
```

### 正しいパターン

```typescript
// ✅ withScopeでスコープを分離
Sentry.withScope((scope) => {
  scope.setTag("error.type", "unexpected");
  scope.setContext("request", { path: c.req.path });
  Sentry.captureException(err);
});
```

### CI検査

`check_sentry_usage.ts` がCI時に自動検査。禁止パターンがあるとCIが失敗。

## UI変更の確認

UI変更は必ずChrome DevTools MCPで確認:

1. `pnpm dev:frontend` + `pnpm dev:worker`
2. `mcp__chrome-devtools__take_screenshot`
3. ライト/ダークモード両方確認

**UI変更対象**:

- CSS/スタイル変更
- コンポーネント追加・変更
- i18n翻訳ファイル変更
