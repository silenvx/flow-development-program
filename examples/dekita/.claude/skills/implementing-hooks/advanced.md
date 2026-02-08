# 高度なトピック

Session ID取得、プロセス間状態共有、Block評価、フックコード更新タイミング。

## Session ID取得の仕組み

フックはセッションIDを使用してセッション固有の状態を管理する。

### 取得優先順位

Issue #777でClaude Codeが直接session_idを提供するようになったため、シンプルな2段階:

| 優先度 | ソース | 説明 |
|--------|--------|------|
| 1 | hook/statusline input JSON `.session_id` | Claude Codeが提供（最も信頼性が高い） |
| 2 | fallback | Python: `ppid-{PPID}` / Bash: 空文字列 |

**廃止済み**: 環境変数（`CLAUDE_SESSION_ID`, `CLAUDE_CONVERSATION_ID`）、marker file

### 実装

**Python（正式実装）**: `common.py` の `get_claude_session_id()`

```python
from common import get_claude_session_id

session_id = get_claude_session_id()
```

**Bash**: `statusline.sh` の `get_session_id()`
（パフォーマンス上、Python実装と同様の方針でbashに実装。ただしfallback時の挙動は異なり、Pythonは `ppid-{PPID}`、Bashは空文字列を返す）

### DEBUGログ

`CLAUDE_DEBUG=1` 環境変数を設定すると、取得元がstderrに出力される:

```
[session_id] source=hook_input, value=abc123...
```

### 関連Issue

- Issue #734: セッションごとのstate file分離
- Issue #756: hook inputからsession_id取得
- Issue #777: Claude Codeによるsession_id提供
- Issue #779: 取得ロジックの統一・ドキュメント化

## プロセス間状態共有

### 重要な制約

フックは**別プロセス**で実行されるため、以下の制約がある:

| 方式 | 動作 | 使用可否 |
|-----|-----|---------|
| グローバル変数 | プロセス終了で消失 | ❌ |
| モジュールレベル辞書 | プロセス間で共有不可 | ❌ |
| ファイルベース | 永続化・共有可能 | ✅ |
| 環境変数 | 読み取り専用 | △ (読み取りのみ) |

**よくある間違い**（Issue #1617）:

```python
# ❌ 動作しない: プロセス終了で消失
_cache = {}

def get_cached_value(key):
    if key not in _cache:
        _cache[key] = expensive_operation()
    return _cache[key]
```

### 実装前チェックリスト

フック/スクリプト実装前に確認:

- [ ] プロセス間で状態共有が必要か？
- [ ] 必要ならファイルベース永続化を使用
- [ ] 複数プロセスからの同時書き込みは？ → ファイルロック検討

### ファイルベース永続化パターン

**推奨**: JSON Lines形式（`.jsonl`）で追記

```python
from __future__ import annotations

import json
from pathlib import Path
from common import METRICS_LOG_DIR

# ディレクトリを事前に作成
METRICS_LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = METRICS_LOG_DIR / "my-data.jsonl"

def append_event(event: dict) -> None:
    """イベントを追記（必要に応じてファイルロックを併用）"""
    # 注意:
    #   - 小規模なログの追記であれば、通常はファイルシステムの書き込みアトミック性で
    #     十分なことが多く、必ずしもロックを導入する必要はありません。
    #   - 複数プロセスから高頻度に同一ファイルへ書き込む場合は、fcntl や filelock などに
    #     よるファイルロック、あるいは専用のログ集約プロセスや外部ストレージの利用を
    #     検討してください。
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(event) + "\n")

def read_events() -> list[dict]:
    """全イベントを読み込み"""
    if not LOG_FILE.exists():
        return []
    events = []
    # 大きなログファイルでもメモリ効率よく読み込むため、行単位で処理する
    with open(LOG_FILE, "r") as f:
        for line in f:
            if line.strip():
                events.append(json.loads(line))
    return events
```

**既存の実装例**:
- `.claude/logs/metrics/block-patterns.jsonl` - ブロック→成功パターン
- `.claude/logs/execution/hook-execution.log` - フック実行ログ

### 関連Issue

- Issue #1617: インメモリ状態管理の問題
- Issue #1634: プロセス間状態共有の教訓

## Block評価・改善サイクル

フックによるブロックが妥当だったか評価し、誤検知の場合はフックを改善するサイクル。

### ワークフロー

```
[Block発生] → [ログ記録] → [評価] → [分析] → [改善]
```

### 1. Blockログの確認

```bash
# 最近のブロック一覧
python3 .claude/scripts/block_evaluator.py list

# 特定のblockを詳細表示
python3 .claude/scripts/block_evaluator.py evaluate <block_id>
```

### 2. Block妥当性の評価

評価オプション:
- `valid` - ブロックは正しかった（本来止めるべき操作）
- `false_positive` - 誤検知（止めるべきではなかった）
- `unclear` - 判断できない

```bash
# 対話的に評価
python3 .claude/scripts/block_evaluator.py evaluate <block_id>

# ワンライナーで評価
python3 .claude/scripts/block_evaluator.py evaluate <block_id> \
  -e false_positive \
  -r "テストファイルなのにブロックされた" \
  -i "テストファイルを除外すべき"
```

### 3. 評価サマリーの確認

```bash
python3 .claude/scripts/block_evaluator.py summary
```

出力例:
```
Hook                           Valid   False+  Unclear   FP Rate
----------------------------------------------------------------------
ci-wait-check                      5        3        0     37.5%
codex-review-check                10        1        0      9.1%
```

### 4. 誤検知パターンの分析

```bash
python3 .claude/scripts/analyze_false_positives.py
# 特定のフックのみ分析
python3 .claude/scripts/analyze_false_positives.py --hook ci-wait-check
```

### 5. フック改善

分析結果に基づいてフックを改善:

1. 改善用worktree作成
2. フックコード修正
3. テスト追加
4. PR作成・マージ

### ログファイル

| ファイル | 内容 |
|---------|------|
| `.claude/logs/hook-execution.log` | 全フック実行ログ |
| `.claude/logs/block-evaluations.log` | Block評価記録 |

### 評価タイミング

- **推奨**: セッション終了時に未評価ブロックを確認
- **必須**: 「誤検知では？」と感じた時に即評価

## フックコード更新タイミング

フックはセッション開始時にロードされ、セッション中の修正は反映されない。

| タイミング | 動作 |
|----------|------|
| **セッション開始時** | フックコードがロードされる |
| **セッション中** | フックを修正・マージしても**現セッションには反映されない** |
| **次セッション** | 修正済みコードが適用される |

### 影響

- フックの修正後も、現セッションでは旧コードが動作し続ける
- 修正を検証する場合、新しいセッションを開始する必要がある
- 誤検知が発生しても、現セッション内で「なぜまだ動くのか」と混乱しやすい

### 対処法

- **修正検証**: 新セッションで動作確認
- **現セッション**: 誤検知は無視して作業続行（修正済みなら問題なし）

### 関連Issue

- Issue #2120: lesson-issue-checkの誤検知修正（この問題を発見したきっかけ）
- Issue #2124: 本ドキュメント追加

## CWD問題の対処

`cd` でフックが見つからなくなる問題:

1. **フック設定**: `$CLAUDE_PROJECT_DIR` を使用（設定済み）
2. **`cd` を避ける**: `-C` / `--dir` オプションを使用
   - ✅ `pnpm add xxx -C frontend`
   - ❌ `cd frontend && pnpm add xxx`
3. **問題発生時**: セッション再起動が必要
