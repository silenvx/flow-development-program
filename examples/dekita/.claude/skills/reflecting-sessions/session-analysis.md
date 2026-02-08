# セッション分析

セッションの行動振り返りとログ調査。

## 1. このセッションの行動振り返り

今回のセッションで実行した主要アクティビティを振り返ってください：

| カテゴリ | 確認項目 |
|---------|---------|
| **実施タスク** | 何を依頼され、何を実施したか |
| **worktree操作** | 作成・削除したworktree |
| **PR操作** | 作成、レビュー対応、マージしたPR |
| **Issue操作** | 作成、更新、クローズしたIssue |
| **Skill使用** | 使用したSkill（reviewing-code, managing-development等） |
| **ブロック対応** | 遭遇したブロックとその回避方法 |

## 2. ログによる調査（必須）

**重要**: ログ確認は必須です。ログから潜在的な問題を発見できることがあります。

### 現在セッションのログ抽出

セッションIDはセッション開始時に `[CONTEXT]` メッセージで表示されます（形式: `Session: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`）。

表示されていない場合は、最新の状態ファイルから取得:

```bash
SESSION_ID=$(ls -t .claude/logs/flow/state-*.json 2>/dev/null | head -1 | sed 's/.*state-\(.*\)\.json/\1/' | sed 's/[^a-zA-Z0-9-]//g')
```

セッション固有のログを直接参照:

```bash
# セッション固有のフック実行ログ
cat ".claude/logs/execution/hook-execution-$SESSION_ID.jsonl"

# セッション固有のフロー状態
cat ".claude/logs/flow/state-$SESSION_ID.json"
```

### 利用可能なログ一覧

| ディレクトリ | ファイル | 用途 | 生成タイミング |
|-------------|----------|------|---------------|
| `execution/` | `hook-execution-{session_id}.jsonl` | セッション毎のフック実行履歴（approve/block両方） | 全フック実行時 |
| | `hook-errors.log` | 全セッションのブロック記録（集約） | block判定時 |
| | `hook-warnings.log` | 警告記録 | 警告出力時 |
| | `api-operations-{session_id}.jsonl` | セッション毎のGitHub API操作ログ | gh/git API呼び出し時 |
| | `git-operations.log` | Git操作ログ | git操作時 |
| `flow/` | `events.jsonl` | フロー状態遷移 | フェーズ変更時 |
| | `state-{session_id}.json` | セッション状態 | 状態変更時 |
| `metrics/` | `behavior-anomalies.jsonl` | 行動異常検出（偽陽性含む） | 異常検出時 |
| | `block-patterns.jsonl` | ブロックパターン分析 | セッション終了時 |
| | `session-metrics.log` | セッションメトリクス | セッション終了時 |
| | `tool-efficiency-metrics.log` | ツール効率 | ツール使用時 |
| `reports/` | `session-*.json` | セッションレポート | セッション終了時 |

### 典型的な分析クエリ

```bash
# 現在セッションのブロック理由を確認
cat ".claude/logs/execution/hook-execution-$SESSION_ID.jsonl" | jq -r 'select(.decision == "block") | .reason'

# 現在セッションの状態確認
jq '.workflows.main.current_phase' ".claude/logs/flow/state-$SESSION_ID.json"

# 特定フックのブロック回数（現在セッション）
cat ".claude/logs/execution/hook-execution-$SESSION_ID.jsonl" | jq -r 'select(.decision == "block") | .hook' | sort | uniq -c
```

### Skill使用状況の確認

```bash
# transcriptファイルのパス（SESSION_IDから特定）
TRANSCRIPT=$(ls -t ~/.claude/projects/*/sessions/"$SESSION_ID"/transcripts/*.jsonl 2>/dev/null | head -1)

# 使用されたSkill一覧
cat "$TRANSCRIPT" | jq -r 'select(.message.content[]?.name == "Skill") | .message.content[] | select(.name == "Skill") | .input.skill' | sort | uniq -c
```

**確認すべき項目**:

| 確認項目 | 質問 |
|---------|------|
| **使用タイミング** | 適切なタイミングでSkillを使用したか？ |
| **使用漏れ** | 使用すべきだったが使用しなかったSkillはないか？ |
| **効果** | Skill使用により効率化・品質向上に繋がったか？ |

**よくある使用漏れパターン**:

| 状況 | 使用すべきSkill |
|------|----------------|
| worktree作成・PR作成時 | `managing-development` |
| レビューコメント対応時 | `reviewing-code` |
| セッション終了時 | `reflecting-sessions` |
| フック実装・修正時 | `implementing-hooks` |

### ログ分析の必須チェック

**重要**: ログを「表示して終わり」にしない。以下のチェックを必ず実行してください。

| チェック項目 | 確認内容 | 具体例 |
|-------------|----------|--------|
| **タイムスタンプ分析** | 同じフックが30秒以内に2回以上ブロックしていないか | 即時再試行の疑い |
| **パターン検出** | 同じ対象への連続操作がないか | 根本原因を解消していない |
| **因果関係** | 各ブロック後、原因を解消したか、回避しただけか | NG例: 即再試行 |
| **ppidベースログ検出** | `ppid-` プレフィックスのログファイルが存在しないか | session_id伝播の問題 |

**分析コマンド例**:

```bash
# 同一セッションのブロックをタイムスタンプ順に確認
cat ".claude/logs/execution/hook-execution-$SESSION_ID.jsonl" | jq -r 'select(.decision == "block") | [.timestamp, .hook, .details.thread_id // .details.command // ""] | @tsv'
```

### セクション2の観点チェック

| 観点 | 確認内容 | チェック |
|------|----------|---------|
| **セッション事実** | ログを確認し、客観的事象を把握したか | [ ] |
| **異常パターン** | 通常と異なる動作を確認したか | [ ] |
| **Skill使用の適切性** | 適切なタイミングでSkillを使用したか | [ ] |
