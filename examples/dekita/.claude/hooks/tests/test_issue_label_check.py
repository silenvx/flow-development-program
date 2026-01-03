"""issue-label-check.py のテスト"""

import json
import os
import subprocess
import sys
from pathlib import Path


def run_hook(command: str) -> tuple[int, str, str]:
    """フックを実行して結果を返す"""
    hook_path = Path(__file__).parent.parent / "issue-label-check.py"
    stdin_data = json.dumps({"tool_input": {"command": command}})
    env = os.environ.copy()
    result = subprocess.run(
        [sys.executable, str(hook_path)],
        input=stdin_data,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return result.returncode, result.stdout, result.stderr


class TestIssueLabelCheck:
    """Issue作成時のラベル確認フックのテスト"""

    def test_blocks_issue_create_without_label(self):
        """ラベルなしの gh issue create をブロックする"""
        _, stdout, _ = run_hook('gh issue create --title "test" --body "test body"')
        output = json.loads(stdout)
        assert output["decision"] == "block"
        assert "--label" in output["reason"]

    def test_allows_issue_create_with_label(self):
        """ラベル付きの gh issue create を許可する"""
        _, stdout, _ = run_hook('gh issue create --title "test" --body "test" --label "bug"')
        # 出力なし = 許可
        assert stdout == ""

    def test_allows_issue_create_with_short_label_flag(self):
        """短縮形 -l でラベル指定した場合も許可する"""
        _, stdout, _ = run_hook('gh issue create --title "test" --body "test" -l "bug"')
        assert stdout == ""

    def test_ignores_other_commands(self):
        """他のコマンドは無視する"""
        _, stdout, _ = run_hook("gh pr create --title test")
        assert stdout == ""

    def test_ignores_issue_list(self):
        """gh issue list は無視する"""
        _, stdout, _ = run_hook("gh issue list")
        assert stdout == ""

    def test_blocks_when_label_in_title_only(self):
        """タイトルに --label が含まれていてもブロックする"""
        _, stdout, _ = run_hook('gh issue create --title "add --label option" --body "test"')
        output = json.loads(stdout)
        assert output["decision"] == "block"

    def test_blocks_when_label_in_body_only(self):
        """ボディに -l が含まれていてもブロックする"""
        _, stdout, _ = run_hook('gh issue create --title "test" --body "use -l flag for labels"')
        output = json.loads(stdout)
        assert output["decision"] == "block"

    def test_ignores_gh_issue_create_in_commit_message(self):
        """コミットメッセージに "gh issue create" が含まれていても無視する"""
        # git commit -m "fix: gh issue create ..." は gh issue create コマンドではない
        _, stdout, _ = run_hook('git commit -m "feat: gh issue create command support"')
        assert stdout == ""

    def test_ignores_gh_issue_create_in_arguments(self):
        """引数に "gh issue create" が含まれていても無視する"""
        # 他のコマンドの引数に含まれている場合は無視
        _, stdout, _ = run_hook('echo "use gh issue create to make issues"')
        assert stdout == ""

    def test_detects_actual_gh_issue_create_command(self):
        """実際の gh issue create コマンドを検出する"""
        # 先頭が gh issue create の場合のみブロック対象
        _, stdout, _ = run_hook('gh issue create --title "test"')
        output = json.loads(stdout)
        assert output["decision"] == "block"

    def test_detects_env_prefixed_gh_issue_create(self):
        """環境変数プレフィックス付きの gh issue create を検出する"""
        # GH_TOKEN=xxx gh issue create も検出対象
        _, stdout, _ = run_hook('GH_TOKEN=xxx gh issue create --title "test"')
        output = json.loads(stdout)
        assert output["decision"] == "block"

    def test_allows_env_prefixed_with_label(self):
        """環境変数プレフィックス付きでラベルありなら許可"""
        _, stdout, _ = run_hook('GH_HOST=github.com gh issue create --title "test" --label "bug"')
        assert stdout == ""

    def test_detects_full_path_gh_command(self):
        """フルパス指定の gh コマンドを検出する"""
        _, stdout, _ = run_hook('/usr/local/bin/gh issue create --title "test"')
        output = json.loads(stdout)
        assert output["decision"] == "block"

    def test_allows_full_path_gh_with_label(self):
        """フルパス指定でラベルありなら許可"""
        _, stdout, _ = run_hook('/opt/homebrew/bin/gh issue create --title "test" --label "bug"')
        assert stdout == ""

    def test_detects_env_prefix_with_full_path_gh(self):
        """環境変数プレフィックス + フルパス gh を検出する"""
        _, stdout, _ = run_hook('GH_TOKEN=xxx /usr/local/bin/gh issue create --title "test"')
        output = json.loads(stdout)
        assert output["decision"] == "block"

    def test_allows_env_prefix_with_full_path_gh_and_label(self):
        """環境変数プレフィックス + フルパス + ラベルなら許可"""
        _, stdout, _ = run_hook(
            'GH_HOST=example.com /opt/homebrew/bin/gh issue create --title "test" --label "enhancement"'
        )
        assert stdout == ""

    def test_ignores_gh_suffix_in_path(self):
        """パス途中に /gh が含まれる別コマンドは無視する"""
        # /path/to/gh/something は gh issue create ではない
        _, stdout, _ = run_hook('/path/to/gh/other-command create --title "test"')
        assert stdout == ""

    def test_ignores_executable_ending_with_gh(self):
        """gh で終わる別の実行ファイルは無視する"""
        # /usr/bin/fakegh は gh コマンドではない
        _, stdout, _ = run_hook('/usr/bin/fakegh issue create --title "test"')
        assert stdout == ""

    def test_ignores_testgh_command(self):
        """testgh のような名前の実行ファイルは無視する"""
        _, stdout, _ = run_hook('/usr/local/bin/testgh issue create --title "test"')
        assert stdout == ""
