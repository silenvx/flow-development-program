# フック一覧

PreToolUse/PostToolUse/Stopの全フック一覧。

## PreToolUse フック一覧

### Edit/Write ブロック

- **トリガー**: ファイル編集前
- **動作**: main/masterでの編集をブロック、worktree外を警告

### オープンIssueリマインド (`open_issue_reminder.py`)

- **目的**: 未アサインIssue表示、競合防止
- **動作**: セッション開始時（1時間間隔）に最初のBashでトリガー

### タスク開始チェックリスト (`task_start_checklist.py`)

- **目的**: タスク開始時の要件・設計確認漏れ防止
- **動作**: セッション開始時（1時間間隔）に最初のEdit/Write/Bashでトリガー
- **表示内容**: 要件確認、設計判断、影響範囲、前提条件のチェックリスト
- **ブロック**: しない（systemMessageでリマインド表示のみ）

### 依存関係チェックリマインド (`dependency_check_reminder.py`)

- **目的**: 依存関係追加時にContext7/Web検索を促す
- **動作**: `pnpm add`, `npm install`, `pip install` 等のコマンド検出時にトリガー
- **表示内容**: Context7でのドキュメント確認、Web検索での最新情報確認を促すメッセージ
- **ブロック**: しない（systemMessageでリマインド表示のみ）
- **重複防止**: 同じパッケージには1セッション1回のみ表示

### Issue自動アサイン (`issue_auto_assign.py`)

- **目的**: 複数エージェントのIssue競合防止
- **動作**: `git worktree add` でブランチ名からIssue番号を検出し自動assign
- **パターン**: `feature/issue-123-desc`, `fix/123-bug`, `#123-feature`

### PRスコープチェック (`pr_scope_check.py`)

- **目的**: 1 Issue = 1 PR ルール強制
- **動作**: `gh pr create` で複数Issue参照をブロック

### Skill使用リマインド・強制フック

worktree作成・PR作成時のSkill参照を促す2つの補完的なフック。

#### `workflow_skill_reminder.py`（リマインド型）

- **目的**: worktree/PR作成時に`development-workflow` Skill参照をリマインド
- **トリガー**: `git worktree add`, `gh pr create` 検出時
- **動作**: **警告のみ**（systemMessageでリマインド表示、ブロックしない）
- **関連Issue**: #2387

**出力例**:
```
📚 workflow-skill-reminder: worktree作成が検出されました。

【development-workflow Skill 参照リマインダー】
worktree作成時は `development-workflow` Skill を参照してください。

**確認すべき内容:**
□ worktree作成直後のチェック（main最新との差分確認）
□ `--lock` オプションの使用（他エージェントの削除防止）
...
```

#### `skill_usage_reminder.py`（強制型）

- **目的**: Skill使用なしでのworktree/PR作成を**ブロック**
- **トリガー**: `git worktree add`, `gh pr create` 検出時
- **動作**: セッション中のtranscriptを確認し、必要なSkillが使用されていなければ**ブロック**
- **関連Issue**: #2355

#### 補完関係

両フックは以下の補完関係にある:

| フック | チェック内容 | 動作 |
|--------|------------|------|
| `workflow_skill_reminder.py` | リマインド表示 | 「Skillを参照すべき」とリマインド |
| `skill_usage_reminder.py` | Skill未使用時にブロック | Skill未使用なら**ブロック** |

**フロー**:

1. `git worktree add` を実行しようとすると、同じ PreToolUse フェーズで2つのフックが起動する
2. `workflow_skill_reminder.py`: 常に `"approve"` を返しつつ、`systemMessage` で「development-workflow Skillを参照してください」とリマインドを表示
3. `skill_usage_reminder.py`: transcript を確認し、指定された Skill が未参照であれば `"block"` を返し、参照済みであれば `"approve"` を返す

両フックは同じコマンドに対して**並行して独立に実行され**、`workflow_skill_reminder.py` のリマインドと `skill_usage_reminder.py` のブロック可否判定の結果が組み合わされて Claude に渡される。

**設計意図**: リマインドで気づかせ、無視した場合はブロックで強制。2段階の防御で「手順が身についている」という誤った判断を防止。

### マージ安全性チェック (`merge_check.py`)

4つのチェック:

1. `gh pr merge --auto` をブロック
2. `requested_reviewers` にCopilot/Codexがいたらブロック
3. Issue参照なしで却下されたコメントをブロック
4. コメントなしResolveをブロック

**却下検出キーワード**: 「範囲外」「軽微」「out of scope」「defer」

### CI待機チェック (`ci_wait_check.py`)

- **目的**: CI監視を `ci_monitor_ts` に一元化
- **ブロック**: `gh pr checks --watch`, `gh pr view --json mergeStateStatus` 等

### Codex CLIレビューチェック

- **logger**: `codex review` 実行時にブランチ・コミットを記録
- **check**: `gh pr create` / `git push` 時に現在コミットがレビュー済みか確認

### Pythonコードチェック (`python_lint_check.py`)

- **目的**: CI前にPythonスタイル違反を検知
- **動作**: `git commit` でステージされた `.py` を `uvx ruff` でチェック

### フック設計チェック (`hooks_design_check.py`)

- **目的**: フック間の責務重複防止、品質チェック
- **動作**: 新規フック追加時にSRPチェックリストを警告表示、`log_hook_execution()` 未使用をブロック

### UI確認チェック (`ui_check_reminder.py`)

- **目的**: UI変更後の目視確認漏れ防止
- **対象**: `locales/*.json`, `components/**/*.tsx`, `routes/**/*.tsx`, `index.css`
- **確認記録**: `bun run .claude/scripts/confirm_ui_check.ts`

### Markdownサイズチェック (`markdown_size_check.py`)

- **目的**: Markdownファイル肥大化防止
- **上限**: 40KB（Claude Codeパフォーマンス影響閾値）

### Worktree削除前チェック (`worktree_removal_check.py`)

- **目的**: worktree削除前にアクティブ作業を検出し、セッション競合・破壊を防止
- **トリガー**: `git worktree remove` コマンド検出時

**2段階チェック**:

| チェック | 対象 | `--force` でバイパス |
|----------|------|----------------------|
| **cwdチェック** | 現在の作業ディレクトリがworktree内にあるか | **不可**（常にブロック） |
| **アクティブ作業チェック** | 最新コミット・未コミット変更・stash | 可能 |

**cwdチェックの重要性**:

cwdがworktree内にある状態で削除すると、以降の全Bashコマンドが `ENOENT` エラーで失敗する。
セッション全体が壊れるため、`--force` でも絶対にバイパスできない。

**Fail-Close設計**:

```python
# check_cwd_inside_worktree() の例
try:
    cwd = Path.cwd().resolve()
    # ... パス比較 ...
except OSError:
    # cwdが取得できない場合は安全側に倒す
    return True  # ブロック
```

不確実な状況（OSError等）では常に「ブロック」を選択。誤ブロックは回復可能だが、誤許可によるセッション破壊は回復不可能なため。

## PostToolUse フック一覧

### Worktree自動セットアップ (`worktree_auto_setup.py`)

- **目的**: worktree作成後の依存関係自動インストール
- **トリガー**: `git worktree add` 成功後
- **動作**: `setup_worktree.sh` を自動実行（pnpm install等）
- **ブロック**: しない（PostToolUseで非ブロッキング実行）

### ブロック改善リマインダー (`block_improvement_reminder.py`)

- **目的**: 同一フックが連続ブロックした際にフック改善を促す
- **トリガー**: Bashツール実行後、セッションのブロック履歴を確認
- **動作**: 同一フックが3回連続でブロックしていたら、改善策をsystemMessageで表示
- **ブロック**: しない（systemMessageでリマインド表示のみ）
- **重複防止**: 同じフックには1セッション1回のみ表示
- **関連Issue**: #2432

**検討すべき改善策の例**:
1. SKIP環境変数のサポート追加
2. 拒否メッセージの改善（具体的な解決策を提示）
3. 誤検知パターンの修正

**出力例**:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 フック改善リマインダー: merge-check
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

このセッションで `merge-check` が3回連続でブロックしています。

**検討すべき改善策:**

1. **SKIP環境変数のサポート追加**
   - `SKIP_MERGE_CHECK=1` でバイパス可能に

2. **拒否メッセージの改善**
   - 具体的な解決策を提示
   - 何をすべきか明確に説明

3. **誤検知パターンの修正**
   - 正当なケースをブロックしていないか確認
   - 検出ロジックの精度を改善

詳細は `hooks-reference` Skill を参照してください。
```

## Stop フック

### cwd-check

- **目的**: カレントディレクトリ消失検知
- **動作**: セッション終了時にcwd存在確認

### git-status-check

- **目的**: 未コミット変更検知
- **動作**: mainに未コミット変更があれば警告

### reflection-prompt

五省ベースの自己評価（`prompt`型）:

- **評価基準**: 要件理解、実装品質、検証、対応、効率 + 仕組み化
- **動作**:
  - 重大な未完了タスク → `block`
  - 教訓が見つかったが仕組み化されていない → `block`
  - タスク完了 → `approve`

## systemMessage出力例

各フックのsystemMessage出力例。新規フック開発時の参考に。

**task_start_checklist.py**:
```
📋 **タスク開始前の確認チェックリスト**

以下の点を確認してからタスクを開始してください:

**要件確認**:
  [ ] 要件は明確か？曖昧な点があれば質問する
  [ ] ユーザーの意図を正しく理解しているか？

**設計判断**:
  [ ] 設計上の選択肢がある場合、ユーザーに確認する
  [ ] 既存のコードパターン・規約を把握しているか？

💡 不明点があれば、実装前に必ず質問してください。
```

**dependency_check_reminder.py**:
```
📦 **依存関係追加を検出**

パッケージ `react-query` を追加しようとしています。

**最新情報を確認してください:**

1. **Context7**: `react-query` のドキュメントを参照
   - `mcp__context7__resolve-library-id` でライブラリIDを取得
   - `mcp__context7__get-library-docs` でドキュメントを取得

2. **Web検索**: 最新バージョン・変更履歴を確認
   - 「react-query latest version」で検索

💡 古いAPIや非推奨メソッドの使用を防ぐため、最新情報の確認を推奨します。
```

**open_issue_reminder.py**:
```
🚨 **高優先度Issue（優先対応必須）**:
  → #123: 本番環境でログイン失敗 [P1, bug]

📋 **未アサインのオープンIssue** (対応検討してください):
  - #456: ダークモード対応 [enhancement]
  - #789: パフォーマンス改善 [P2]

詳細: `gh issue list --state open`
```

**出力設計ガイドライン**:

| 項目 | 推奨 |
|------|------|
| **タイトル** | 絵文字 + 太字で目立たせる |
| **構造** | 箇条書き/チェックリスト形式 |
| **長さ** | 5-15行（長すぎると読まれない） |
| **アクション** | 具体的な次のステップを提示 |
