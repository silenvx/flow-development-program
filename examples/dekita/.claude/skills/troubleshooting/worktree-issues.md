# Worktree関連のトラブルシューティング

Git Worktreeに関連する問題と解決策。

## Worktree削除後にシェルが操作不能になる

**症状**: Worktree内で作業中にディレクトリが削除されると、シェルセッションが壊れて全コマンドが `Exit code 1` で失敗する。

**原因**: カレントディレクトリが存在しなくなり、シェルが `getcwd()` に失敗する。

**解決策**:

- **根本対策**: Claude Codeを再起動する
- **予防策**: Worktreeを削除する前に、必ずオリジナルディレクトリからコマンドを実行する

```bash
# NG: worktree内から自分自身を削除しようとする
cd .worktrees/feature && git worktree remove .  # 失敗

# OK: オリジナルディレクトリから削除する
cd /path/to/original && git worktree remove .worktrees/feature  # 成功
```

## Bashコマンドでカレントディレクトリが変わる

**症状**: `cd dir && command` を実行後、hookが動作しなくなる。またはツールがブロックされる。

**原因**: Claude Codeのシェルセッションは永続的であり、`cd` コマンドでカレントディレクトリが変わると、以降のコマンドも変更後のディレクトリで実行される。hookが `.claude/hooks/` を相対パスで参照している場合、見つけられなくなる。

**根本対策（設定済み）**: フック設定で `$CLAUDE_PROJECT_DIR` 環境変数を使用する。これにより、cwdに関係なくフックが正しく見つかる。

```json
"command": "python3 \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/xxx.py"
```

**運用での回避策**:

- **pnpm `-C` / `--dir` オプション**: 指定ディレクトリでコマンドを実行
- **pnpm `--filter` オプション**: ワークスペースの特定パッケージを対象
- **サブシェル**: `(cd dir && command)` で囲むとcwdが変わらない
- **発生した場合**: Claude Codeの再起動が必要

```bash
# NG: カレントディレクトリが永続的に変わる
cd frontend && pnpm add xxx

# OK: -C / --dir オプションを使用
pnpm add xxx -C frontend
pnpm add xxx --dir frontend

# OK: --filter を使用（monorepo）
pnpm --filter frontend add xxx

# OK: サブシェルで実行
(cd frontend && pnpm add xxx)
```

**参考**:

- [pnpm CLI Global Options](https://pnpm.io/pnpm-cli) - `-C` / `--dir` オプション
- [Claude Code Hooks Reference](https://code.claude.com/docs/en/hooks.md) - `$CLAUDE_PROJECT_DIR` 環境変数

## Worktree使用中にgh pr mergeがエラーになる

**症状**: `gh pr merge --delete-branch` を実行すると、マージ自体は成功しているのにエラーが発生する。

```text
failed to run git: fatal: 'main' is already used by worktree at '/path/to/worktree'
```

**原因**: `--delete-branch` オプションはマージ後にローカルブランチも削除しようとするが、worktreeで使用中のブランチは削除できない。ただし、**GitHub上でのマージは成功している**。

**確認方法**:

```bash
# マージが成功しているか確認
gh pr view {PR} --json state,mergedAt
```

**解決策**:

1. **`--delete-branch` を使わない**: worktreeで作業中は `gh pr merge --squash` のみを使用
2. **worktreeを先に削除**: マージ前にworktreeを削除・アンロックしてからマージ

```bash
# 方法1: --delete-branch を使わずにマージ
gh pr merge {PR} --squash

# マージ後にworktreeを手動で削除
git worktree unlock .worktrees/{name}
git worktree remove .worktrees/{name}

# 方法2: 先にworktreeを削除してからマージ
git worktree unlock .worktrees/{name}
git worktree remove .worktrees/{name}
gh pr merge {PR} --squash --delete-branch
```

**注意**: エラーメッセージが出てもパニックにならないこと。`gh pr view` で状態を確認すれば、マージが成功しているかどうかがわかる。

## Worktree内でフック変更が反映されない

**症状**: worktreeで`.claude/hooks/`のファイルを変更しても、変更後のフックが実行されない。例えば、merge_check.pyを修正しても、`gh pr merge`実行時に古い動作のままになる。

**原因**: Claude Codeは`CLAUDE_PROJECT_DIR`環境変数をメインリポジトリのパスに設定する。`settings.json`のフックパスは`$CLAUDE_PROJECT_DIR/.claude/hooks/xxx.py`で参照されるため、worktreeで変更してもメインリポジトリのフックが実行される。

**影響**: フック自体を変更するPRでは「鶏と卵」問題が発生する。変更したフックをテストするにはPRをマージする必要があるが、マージ前にテストしたい場合がある。

**解決策**:

1. **PRをマージする**: 変更をマージすれば、メインリポジトリに反映される（推奨）
2. **環境変数をオーバーライド**: 開発中にworktreeのフックをテストしたい場合

   ```bash
   # worktreeのパスを指定してClaude Codeを起動
   CLAUDE_PROJECT_DIR=/path/to/.worktrees/issue-1132 claude
   ```

3. **コマンド単位でオーバーライド**: 特定のコマンドのみworktreeのフックを使用

   ```bash
   # gh pr mergeを新しいフックでテスト
   CLAUDE_PROJECT_DIR="$PWD/.worktrees/issue-1132" gh pr merge ...
   ```

**検知**: SessionStart時に`hook-dev-warning.py`フックが自動的に警告を表示する。worktree内で`.claude/hooks/`に変更がある場合、上記の情報が案内される。

**関連Issue**: #1132

## git rev-parseの戻り値がworktreeで異なる

**症状**: `git rev-parse --git-common-dir` などのコマンドを使用するスクリプトが、worktree内で正しく動作しない。パス解決に失敗したり、予期せぬディレクトリを参照したりする。

**原因**: `git rev-parse` の戻り値は実行環境（メインリポジトリ vs worktree）によって異なる。

**主要オプションと戻り値パターン**:

| オプション | メインリポジトリ | worktree内 |
| ---------- | --------------- | ---------- |
| `--show-toplevel` | `/path/to/repo` (絶対) | `/path/to/.worktrees/name` (絶対) |
| `--git-dir` | `.git` (相対) | `/path/to/repo/.git/worktrees/name` (絶対) |
| `--git-common-dir` | `.git` (相対) | `/path/to/repo/.git` (絶対) |

**注意**: メインリポジトリでは**相対パス**が返されることがある。`Path.resolve()` を使用すると、スクリプトの実行ディレクトリ（CWD）を基準に解決されるため、意図しないパスになる可能性がある。

**ベストプラクティス**:

```python
import subprocess
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()

def get_main_project_root() -> Path:
    """メインリポジトリのルートを取得（worktree内でも正しく動作）"""
    result = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        capture_output=True, text=True, timeout=5,
        cwd=SCRIPT_DIR,  # スクリプトのディレクトリを基準に
    )
    if result.returncode == 0:
        git_common_dir = Path(result.stdout.strip())

        # 相対パスの場合は SCRIPT_DIR を基準に解決
        if not git_common_dir.is_absolute():
            git_common_dir = (SCRIPT_DIR / git_common_dir).resolve()

        # メインリポジトリ／Worktree 共通:
        # "--git-common-dir" はメインリポジトリの ".git" を指すので、その親がルート
        return git_common_dir.parent

    # フォールバック
    return SCRIPT_DIR.parent.parent.resolve()
```

**チェックリスト**（`git rev-parse` を使用する前に）:

- [ ] 戻り値が相対パス/絶対パスのどちらかを確認
- [ ] 相対パスの場合、基準ディレクトリを明示的に指定
- [ ] worktree環境でテスト実行

**関連Issue**: #2198, #2200
