# 実装ガイド

フック実装の詳細仕様とチェックリスト。

## 共通ライブラリ関数の詳細仕様

フック実装前に必ず確認すること。これを怠るとレビューで指摘される。

### `make_block_result(hook_name, reason)`

**自動的に行われる処理**（手動で行う必要なし）:

| 処理 | 詳細 |
|------|------|
| **ログ記録** | `log_hook_execution(hook_name, "block", reason)` を内部で呼び出す（Issue #2023） |
| **プレフィックス付与** | reasonに `[{hook_name}]` を自動追加 |
| **継続ヒント付与** | reasonに `CONTINUATION_HINT` を自動追加 |
| **systemMessage生成** | ユーザー表示用のsystemMessageを自動生成 |
| **stderr出力** | `❌ {hook_name}: {first_line}` をstderrに出力 |

**アンチパターン（やってはいけない）**:

```python
# ❌ 悪い例: 重複呼び出し
log_hook_execution("my-hook", "block", reason)  # 不要（make_block_resultが呼ぶ）
result = make_block_result("my-hook", reason)

# ❌ 悪い例: 重複プレフィックス
reason = f"[my-hook] {actual_reason}"  # 不要（make_block_resultが付与）
result = make_block_result("my-hook", reason)

# ✅ 良い例: reasonのみを渡す
result = make_block_result("my-hook", "操作がブロックされました")
```

### `make_approve_result(hook_name, message=None)`

**仕様**:
- `decision: "approve"` と `systemMessage` を含む辞書を返す
- messageがNoneの場合、`✅ {hook_name}: OK` がsystemMessageになる
- **`reason` フィールドは含まれない**（blockと異なる）

### その他のヘルパー関数

| 関数 | 用途 |
|------|------|
| `print_continue_and_log_skip(hook_name, reason)` | 対象外時の早期リターン（PreToolUse/PostToolUse用） |
| `print_approve_and_log_skip(hook_name, reason)` | 対象外時の早期リターン（Stop hook用） |
| `check_skip_env(hook_name, env_var)` | SKIP環境変数のチェックとログ記録 |

### `parse_hook_input()` （lib/session.py）

フック入力の標準的な読み取り方法。以下を自動で行う:

1. stdinからJSONを読み取りパース
2. `session_id` があれば自動で `set_hook_session_id()` を呼び出す
3. パースした辞書を返す（エラー時は空辞書）

**戻り値構造**（フックタイプ別）:

| フックタイプ | 主要フィールド |
|-------------|---------------|
| PreToolUse / PostToolUse | `tool_name`, `tool_input`, `session_id`, `cwd` |
| UserPromptSubmit | `user_prompt`, `session_id`, `cwd` |
| SessionStart | `session_id`, `cwd`, `source`, `transcript_path` |
| Stop | `stop_hook_active`, `session_id`, `cwd` |

**使用例**:

```python
from lib.session import parse_hook_input

def main():
    hook_input = parse_hook_input()  # session_idが自動設定される
    tool_name = hook_input.get("tool_name")
    tool_input = hook_input.get("tool_input", {})
    command = tool_input.get("command", "")
    # ... フックロジック
```

**アンチパターン**:

```python
# ❌ 悪い例: 手動でJSONパースしてsession_id設定を忘れる
data = json.loads(sys.stdin.read())
# session_idが設定されない → ログが正しくセッションに紐づかない

# ✅ 良い例: parse_hook_input()を使用
hook_input = parse_hook_input()  # session_id自動設定
```

### HookContextへの移行（Issue #2449）

`get_claude_session_id()` はグローバル状態を使用するため、テスト困難性とスレッドセーフ性の問題がある。新しいフックは `HookContext` パターンを使用すること。

**移行パターン**:

```python
# ❌ 旧パターン: グローバル状態を使用
from lib.session import parse_hook_input, get_claude_session_id

def main():
    hook_input = parse_hook_input()
    session_id = get_claude_session_id()  # グローバル状態から取得
    # ...

# ✅ 新パターン: HookContext（DI）を使用
from lib.session import parse_hook_input, create_hook_context

def main():
    hook_input = parse_hook_input()
    ctx = create_hook_context(hook_input)
    session_id = ctx.get_session_id()  # コンテキストから取得
    # ...
```

**メリット**:

| 観点 | 旧パターン | 新パターン |
|------|-----------|-----------|
| テスト | モック困難（グローバル状態） | コンテキスト注入で容易 |
| スレッドセーフ | 問題あり | 問題なし |
| 依存関係 | 暗黙的 | 明示的 |

**移行状況**:

- `get_claude_session_id()` はdeprecated（`SHOW_SESSION_DEPRECATION=1` で警告表示）
- 既存フックは段階的に移行中
- 新規フックは必ず `HookContext` パターンを使用すること

## フック実装チェックリスト

新しいフックを実装する際は、以下のチェックリストを確認する。

### 実装前チェック（共通ライブラリ）

- [ ] `lib/results.py` のソースを確認したか
- [ ] `lib/session.py` の `parse_hook_input()` を使用しているか
- [ ] `lib/strings`（.py/.ts）に同様の機能がないか確認したか
- [ ] `create_hook_context()` を使用しているか（`get_claude_session_id()` は非推奨）
- [ ] 使用する関数の副作用（ログ記録、プレフィックス付与）を理解したか
- [ ] 重複処理（手動ログ記録、手動プレフィックス）をしていないか

### 入力処理

- [ ] **空入力**: `tool_input` が空の場合の処理
- [ ] **不正形式**: JSON構造が期待と異なる場合
- [ ] **必須フィールド欠落**: `tool_input.command` 等が存在しない場合
- [ ] **Fail-Close設計**: 不確実な状況ではブロック側に倒す
- [ ] **hook_cwd取得**: cwdに依存するフックは `input_data.get("cwd")` を使用（Issue #1172）

**hook_cwdパターン**:
```python
input_data = parse_hook_input()
hook_cwd = input_data.get("cwd")  # Claude Codeが提供するセッションの実cwd
# hook_cwd を base_cwd パラメータとして渡す（環境変数より優先される）
cwd = get_effective_cwd(command, base_cwd=hook_cwd)
```

### エッジケースチェックリスト（Issue #3720）

フック設計時に見落としやすいエッジケース。PR #3719 で7回のfix commitが必要になった教訓から追加。

| カテゴリ | チェック項目 | 例 |
|----------|-------------|-----|
| **順序/タイミング** | ツール呼び出しの順序に依存するロジックか？ | EnterPlanMode → Write → ExitPlanMode の順序検証 |
| **セキュリティ** | 外部入力（ファイルパス、session_id等）をファイルパス構築に使用するか？ | `is_valid_session_id()` でパストラバーサル防止 |
| **ツールカバレッジ** | 対象ツール（Write, Edit, NotebookEdit等）を全て検出しているか？ | Writeのみ検出してEditを見落とす |
| **状態遷移** | 複数の状態遷移サイクル（Enter→Exit→Enter→Exit）があるか？ | 最後のサイクルのみ評価すべき |
| **クロスプラットフォーム** | Windowsパス（バックスラッシュ）の考慮が必要か？ | `filePath.replace(/\\/g, "/")` で正規化 |

**具体的なチェック**:

- [ ] **順序依存**: `lastEnterIndex > lastExitIndex` のような比較ロジックがある場合、複数サイクルでも正しく動作するか
- [ ] **パス検証**: ファイルパスを扱う場合、`../` や `\` を含むパスでテスト
- [ ] **ツール網羅**: 同等の機能を持つツール（Write/Edit/NotebookEdit）を全てカバー
- [ ] **状態リセット**: 状態遷移がリセットされるケース（Exit後のEnter）を考慮
- [ ] **パス正規化**: Windowsパスを含むテストケースを追加

**背景**: PR #3719 で以下の見落としがあった:

1. ExitPlanModeの順序検証漏れ
2. session_idのパストラバーサル対策漏れ
3. Editツールの検出漏れ
4. planモード外でのWrite検出
5. Enter→Exit→Enter→Write→Stop パターンの誤判定
6. Windowsパス区切り（`\`）の未対応

### 正規表現設計

コマンド検出の正規表現を設計する際の考慮事項:

- [ ] **環境変数プレフィックス**: `SKIP_PLAN=1 git worktree add` のようなインライン環境変数
- [ ] **パイプ連結**: `cmd1 | cmd2` パターン
- [ ] **シェル連結**: `&&`, `;`, `||` によるコマンド連結
- [ ] **引用符内のコマンド**: `echo "git commit"` のような文字列内のコマンド（誤検知防止）
  - `lib/strings.py` の `strip_quoted_strings()` を使用して引用符内を除去してから検査
- [ ] **サブシェル**: `$(cmd)` や `` `cmd` `` 内のコマンド

**参考パターン**:
```python
# 環境変数プレフィックスとシェル連結を考慮した例
# (?:^|&&|\|\||;|\s+) でコマンド開始位置を特定（\s+ で複数空白に対応）
pattern = r"(?:^|&&|\|\||;|\s+)(?:\w+=\S+\s+)*git\s+worktree\s+add"
```

### フック登録時の確認

settings.jsonにフックを登録する際のチェック項目。

- [ ] **関連フックとの実行順序**: 他のフックより先に/後に実行すべきか確認したか
  - 例: SKIP環境変数チェックは、レビューチェックより**前**に実行すべき
  - 例: ロギングフックは、ブロッキングフックより**後**に実行すべき
- [ ] **同じmatcherの他フック確認**: 同じトリガー（例: PreToolUse:Bash）の既存フックを確認したか
- [ ] **フック間の依存関係**: 他のフックの出力/副作用との依存関係を確認したか

**背景**: PR #2954でSKIP環境変数チェックフックをgemini_review_checkの後に配置したため、レビューチェックが先に実行されSKIPチェックが後になる問題が発生（Issue #2958）。

### セキュリティチェックリスト

フック実装時に確認すべきセキュリティ項目。特に外部由来のデータを使用する場合は必須。

| チェック項目 | 確認内容 | 対策例 |
|-------------|----------|--------|
| **Path Traversal** | session_id等をファイルパスに使用していないか | `is_valid_session_id()` でUUID形式を検証 |
| **ファイルパス構築** | ユーザー由来のデータをパスに含める場合 | 許可リスト方式、正規表現でフォーマット検証 |
| **コマンドインジェクション** | subprocess等でユーザー入力を使用 | `shell=False`、引数はリスト形式 |
| **秘密情報の露出** | ログや出力に秘密情報が含まれないか | API key, token, passwordをマスク |
| **パス正規化** | symlink経由でのアクセス | `Path.resolve()` で正規化 |

**Path Traversal対策の実装例**:

```python
from lib.session import is_valid_session_id

def get_state_file(session_id: str) -> Path | None:
    """Get state file path with security validation.

    Args:
        session_id: The session ID (should be UUID format).

    Returns:
        Path to state file, or None if session_id is invalid.
    """
    # Security: Validate session_id to prevent path traversal attacks
    # e.g., "../../../etc/passwd" would be rejected
    if not is_valid_session_id(session_id):
        return None
    return STATE_DIR / f"state-{session_id}.json"
```

**アンチパターン**:

```python
# ❌ 悪い例: 検証なしでファイルパスに使用
def get_state_file(session_id: str) -> Path:
    # session_id = "../../../etc/passwd" で任意ファイルアクセス可能
    return STATE_DIR / f"state-{session_id}.json"

# ✅ 良い例: 検証してから使用
def get_state_file(session_id: str) -> Path | None:
    if not is_valid_session_id(session_id):
        return None  # Invalid session_id
    return STATE_DIR / f"state-{session_id}.json"
```

**関連Issue**: #2696, PR #2693

### テスト

- [ ] **正常系**: 期待するコマンドを正しく検出
- [ ] **異常系**: 不正入力でクラッシュしない
- [ ] **エッジケース**: 環境変数プレフィックス、パイプ連結等
- [ ] **誤検知防止**: 類似コマンドや引用符内を誤検出しない
- [ ] **テスト手法の確認**: `run_hook`（subprocess）かdirect callか事前確認

**テスト手法の選択**:

| 方式 | モック可否 | 用途 |
|------|-----------|------|
| `run_hook()`（subprocess） | ❌ 効かない | E2Eテスト、実際の動作確認 |
| `hook_module.main()` 直接呼び出し | ✅ 効く | ユニットテスト、例外ハンドリング確認 |

```python
# モックが必要なテストは直接呼び出しを使用
from unittest.mock import patch

def mock_func(*args, **kwargs):
    raise FileNotFoundError("simulated error")

with patch.object(hook_module, "get_effective_cwd", side_effect=mock_func):
    hook_module.main()  # 例外発生時の動作をテスト
```

### 出力フォーマット設計

systemMessage出力を含むフックを実装する場合のチェックリスト:

- [ ] **出力目的の明確化**: 何を伝えたいか1文で説明できるか
- [ ] **出力構造の設計**: 絵文字タイトル + 箇条書き/チェックリスト形式
- [ ] **出力長の確認**: 5-15行を目安（長すぎると読まれない）
- [ ] **アクション提示**: 次に何をすべきか具体的に示す
- [ ] **hooks-referenceへの追記**: 出力例をドキュメントに追加

**出力テンプレート**:
```python
def get_message() -> str:
    """Generate the systemMessage content."""
    lines = [
        "📋 **[タイトル]**",
        "",
        "[説明文]",
        "",
        "**[セクション1]**:",
        "  - [項目1]",
        "  - [項目2]",
        "",
        "💡 [アクション/ヒント]",
    ]
    return "\n".join(lines)
```

**精度向上のポイント**:
- 研究によると、出力例を含めると精度が向上する場合がある
- 曖昧な指示より具体的なフォーマット指定が効果的
- 箇条書き/チェックリスト形式は解釈のばらつきを減らす

## Skill整合性チェック（Issue #1196）

フック設計時に関連Skillのルールとの整合性を確認する。これを怠ると、Skillに記載されたルールがフックで強制されず、ルール違反が発生する。

**必須チェック項目**:

- [ ] **関連Skill特定**: このフックが関連するSkillを特定したか？
  - 例: `bug_issue_creation_guard.py` → `code-review` Skill
  - 例: `merge_check.py` → `code-review` Skill
  - 例: `worktree_removal_check.py` → `development-workflow` Skill
- [ ] **ルール網羅**: 関連Skillに記載されたルールを全てカバーしているか？
  - 例: `code-review` Skillに「テスト不足は同じPRで対応」とあれば、「テスト」パターンも検出必須
- [ ] **パターン確認**: フックの検出パターンがSkillの記述と一致するか？
  - 例: Skillに「バグ、テスト不足、エッジケース」とあれば、全てパターンに含める

**確認手順**:

1. フックの目的を明確化（何を防止/検出するか）
2. 関連するSkillを `.claude/skills/` から特定
3. Skillに記載されたルール/条件を抽出
4. フックのパターン/ロジックが全ルールをカバーしているか確認
5. 不足があればフックを拡張

**docstringテンプレート**:

```python
"""
Hook to [目的].

Related Skills:
- code-review: [関連ルール]
- development-workflow: [関連ルール]

Detection patterns based on Skill rules:
- Pattern A: [Skillルール1]
- Pattern B: [Skillルール2]
"""
```

## SKIP環境変数

全ブロッキングフックは `SKIP_*` 環境変数でバイパス可能にする。

**命名規則**:

| フック | 環境変数 |
|--------|----------|
| worktree-removal-check | `SKIP_WORKTREE_CHECK` |
| issue-incomplete-close-check | `SKIP_INCOMPLETE_CHECK` |
| issue-review-response-check | `SKIP_REVIEW_RESPONSE` |
| planning-enforcement | `SKIP_PLAN` |
| codex-review-check | `SKIP_CODEX_REVIEW` |

**実装パターン**:
```python
from common import extract_inline_skip_env, is_skip_env_enabled

SKIP_HOOK_NAME_ENV = "SKIP_HOOK_NAME"

# main()関数内でチェック（エクスポートされた環境変数とインライン両方をサポート）
# 1. エクスポートされた環境変数をチェック
if is_skip_env_enabled(os.environ.get(SKIP_HOOK_NAME_ENV)):
    log_hook_execution("hook-name", "skip", "SKIP_HOOK_NAME=1: チェックをスキップ")
    print(json.dumps({"decision": "approve"}))
    return

# 2. インライン環境変数をチェック（例: SKIP_HOOK_NAME=1 gh issue close）
inline_value = extract_inline_skip_env(command, SKIP_HOOK_NAME_ENV)
if is_skip_env_enabled(inline_value):
    log_hook_execution("hook-name", "skip", "SKIP_HOOK_NAME=1: チェックをスキップ（インライン）")
    print(json.dumps({"decision": "approve"}))
    return
```

## コマンド実行パターン

フック内で外部コマンドを実行する際の標準パターン。

### shell=True vs shell=False の使い分け

| 状況 | shell | 理由 |
|------|-------|------|
| リダイレクト（`2>&1`）を含む | `True` | シェルがリダイレクトを解釈 |
| パイプ（`\|`）を含む | `True` | シェルがパイプを解釈 |
| 環境変数展開（`$VAR`）が必要 | `True` | シェルが展開 |
| 上記以外 | `False` | セキュリティと明確さのため |

### 推奨パターン

**✅ shell=False（デフォルト）**:
```python
# リストでコマンドを渡す（推奨）
result = subprocess.run(
    ["gh", "pr", "list", "--json", "number,title"],
    capture_output=True,
    text=True,
    timeout=30
)
```

**✅ shell=True（リダイレクト・パイプが必要な場合）**:
```python
# 文字列でコマンドを渡す
result = subprocess.run(
    "gh pr list --search 'author:@me' 2>&1",
    shell=True,
    capture_output=True,
    text=True,
    timeout=30
)
```

### アンチパターン

**❌ shell=Falseでリダイレクトを含む**:
```python
# 悪い例: 2>&1がghの引数として渡される
result = subprocess.run(
    "gh pr list 2>&1".split(),  # split()でリストに変換
    capture_output=True,
    text=True
)
# エラー: "2>&1" が gh のコマンド引数として解釈される
```

**❌ shell=Falseで文字列を渡す**:
```python
# 悪い例: shell=Falseで文字列は非推奨
result = subprocess.run(
    "gh pr list",  # 文字列
    shell=False,   # shell=False
    ...
)
# エラー: FileNotFoundError: [Errno 2] No such file or directory: 'gh pr list'
```

### common.pyのヘルパー関数

フック共通モジュールにはコマンド実行のヘルパーがあります:

```python
from common import run_command

# 基本的な使用法（shell=False、リストで渡す）
result = run_command(["gh", "pr", "list", "--json", "number"])

# シェル機能が必要な場合
result = run_command("gh pr list 2>&1 | grep error", shell=True)
```

## 関連Issue

- Issue #1085: 正規表現で環境変数プレフィックスを考慮していなかった事例
- Issue #1106: locked-worktree-guardがシェルリダイレクトを誤認識
- Issue #1172: hook_cwdを使用していなかったためcwd検出が失敗
- Issue #1196: フック設計時のSkillルール整合性チェック
