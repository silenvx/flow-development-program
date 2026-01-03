#!/usr/bin/env python3
"""Tests for merge-check.py - dismissal without issue module.

Covers:
- check_dismissal_without_issue function
- Code block stripping in dismissal detection
"""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add parent directory to path for common module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

HOOK_PATH = Path(__file__).parent.parent / "merge-check.py"

# Import submodules for correct mock targets after modularization (Issue #1756)
import review_checker


class TestCheckDismissalWithoutIssue:
    """Tests for check_dismissal_without_issue function.

    Issue #1181: Changed from comment-level to thread-level checking.
    Now uses GraphQL to get all comments per thread and checks if ANY
    comment in a thread has an Issue reference, not just the dismissal
    comment itself.

    This function checks if review threads contain dismissal keywords
    without Issue references. It should also skip comments that contain
    action keywords indicating a fix/response (Issue #432).
    """

    def setup_method(self):
        """Load the module."""
        spec = importlib.util.spec_from_file_location("merge_check", HOOK_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def _make_graphql_response(self, threads: list[dict]) -> str:
        """Create a mock GraphQL response JSON string."""
        return json.dumps(
            {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": threads}}}}}
        )

    def _make_thread(
        self,
        comments: list[str],
        path: str = "test.py",
        line: int = 10,
        thread_id: str = "thread_1",
    ) -> dict:
        """Create a mock thread object with comments.

        Args:
            comments: List of comment body strings
            path: File path for the thread
            line: Line number for the thread
            thread_id: ID of the thread
        """
        return {
            "id": thread_id,
            "path": path,
            "line": line,
            "allComments": {"nodes": [{"body": body} for body in comments]},
        }

    def test_detect_dismissal_without_issue(self):
        """Should detect dismissal keyword without Issue reference."""
        threads = [self._make_thread(["これは誤検知です。\n\n-- Claude Code"])]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_dismissal_without_issue("123")

        assert len(result) == 1

    def test_skip_dismissal_with_issue_reference_same_comment(self):
        """Should skip dismissal when Issue reference is in the same comment."""
        threads = [
            self._make_thread(["これは誤検知です。Issue #456 を作成しました。\n\n-- Claude Code"])
        ]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_dismissal_without_issue("123")

        assert len(result) == 0

    def test_skip_dismissal_with_issue_reference_followup_comment(self):
        """Should skip dismissal when Issue reference is in a follow-up comment (Issue #1181)."""
        # This is the key new behavior: dismissal in one comment, Issue ref in another
        threads = [
            self._make_thread(
                [
                    "これは誤検知です。\n\n-- Claude Code",  # Dismissal without Issue
                    "Issue #456 を作成しました。\n\n-- Claude Code",  # Follow-up with Issue
                ]
            )
        ]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_dismissal_without_issue("123")

        assert len(result) == 0

    def test_skip_dismissal_with_issue_reference_any_position(self):
        """Should skip if Issue reference is anywhere in thread, not just after dismissal."""
        threads = [
            self._make_thread(
                [
                    "関連: Issue #456",  # Issue ref first (no signature)
                    "これはP2として対象外です。\n\n-- Claude Code",  # Dismissal after
                ]
            )
        ]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_dismissal_without_issue("123")

        assert len(result) == 0

    def test_detect_dismissal_when_issue_in_different_thread(self):
        """Should still detect dismissal when Issue ref is in a DIFFERENT thread."""
        threads = [
            self._make_thread(
                ["これは誤検知です。\n\n-- Claude Code"],  # Dismissal without Issue
                thread_id="thread_1",
            ),
            self._make_thread(
                ["Issue #456 を作成しました。\n\n-- Claude Code"],  # Issue in different thread
                thread_id="thread_2",
            ),
        ]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_dismissal_without_issue("123")

        # Thread 1 should be flagged (dismissal without Issue in same thread)
        assert len(result) == 1

    def test_skip_with_action_keyword_fix(self):
        """Should skip when '修正しました' action keyword is present (Issue #432)."""
        threads = [
            self._make_thread(
                ["誤検知リスクのため警告のみとする設計に修正しました。\n\n-- Claude Code"]
            )
        ]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_dismissal_without_issue("123")

        assert len(result) == 0

    def test_skip_with_action_keyword_addressed(self):
        """Should skip when '対応しました' action keyword is present."""
        threads = [self._make_thread(["範囲外の指摘について対応しました。\n\n-- Claude Code"])]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_dismissal_without_issue("123")

        assert len(result) == 0

    def test_skip_with_action_keyword_implemented(self):
        """Should skip when '実装しました' action keyword is present."""
        threads = [self._make_thread(["軽微な変更を実装しました。\n\n-- Claude Code"])]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_dismissal_without_issue("123")

        assert len(result) == 0

    def test_skip_with_action_keyword_changed(self):
        """Should skip when '変更しました' action keyword is present."""
        threads = [
            self._make_thread(["false positive を避けるため変更しました。\n\n-- Claude Code"])
        ]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_dismissal_without_issue("123")

        assert len(result) == 0

    def test_skip_with_action_keyword_added(self):
        """Should skip when '追加しました' action keyword is present."""
        threads = [self._make_thread(["スコープ外だった機能を追加しました。\n\n-- Claude Code"])]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_dismissal_without_issue("123")

        assert len(result) == 0

    def test_skip_with_action_keyword_deleted(self):
        """Should skip when '削除しました' action keyword is present."""
        threads = [self._make_thread(["対象外のコードを削除しました。\n\n-- Claude Code"])]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_dismissal_without_issue("123")

        assert len(result) == 0

    def test_skip_with_action_keyword_updated(self):
        """Should skip when '更新しました' action keyword is present."""
        threads = [self._make_thread(["後回しにしていた処理を更新しました。\n\n-- Claude Code"])]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_dismissal_without_issue("123")

        assert len(result) == 0

    def test_detect_dismissal_without_action_keyword(self):
        """Should detect dismissal when no action keyword is present."""
        # This comment has dismissal keyword but no action keyword
        threads = [
            self._make_thread(["これは軽微な問題なので今回は対応しません。\n\n-- Claude Code"])
        ]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_dismissal_without_issue("123")

        assert len(result) == 1

    def test_ignore_non_claude_code_comments(self):
        """Should ignore comments without Claude Code signature."""
        threads = [
            self._make_thread(["これは誤検知です。"])  # No signature
        ]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_dismissal_without_issue("123")

        assert len(result) == 0

    def test_fail_open_on_api_error(self):
        """Should return empty list on API errors (fail open)."""
        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=1, stdout="")
                result = self.module.check_dismissal_without_issue("123")

        assert result == []

    def test_empty_threads(self):
        """Should handle PRs with no threads."""
        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response([])
                )
                result = self.module.check_dismissal_without_issue("123")

        assert result == []

    def test_skip_technical_term_hangaigai_access(self):
        """Should skip comments with technical term '範囲外アクセス' (Issue #662)."""
        # This comment uses "範囲外" in technical context, not as dismissal
        threads = [
            self._make_thread(["ループ条件により範囲外アクセスは発生しません。\n\n-- Claude Code"])
        ]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_dismissal_without_issue("123")

        assert len(result) == 0

    def test_skip_technical_term_hangaigai_reference(self):
        """Should skip comments with technical term '範囲外参照'."""
        threads = [self._make_thread(["範囲外参照のリスクはありません。\n\n-- Claude Code"])]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_dismissal_without_issue("123")

        assert len(result) == 0

    def test_still_detect_hangaigai_as_dismissal(self):
        """Should still detect '範囲外' when used as dismissal (not technical term)."""
        # This comment uses "範囲外" as dismissal without technical suffix
        threads = [self._make_thread(["これは範囲外なので対応しません。\n\n-- Claude Code"])]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_dismissal_without_issue("123")

        assert len(result) == 1

    def test_fail_open_when_repo_info_unavailable(self):
        """Should return empty list when repo info is not available."""
        with patch.object(review_checker, "get_repo_owner_and_name", return_value=None):
            result = self.module.check_dismissal_without_issue("123")

        assert result == []

    def test_skip_dismissal_when_action_follows(self):
        """Should skip dismissal when action keyword comes later (Issue #1190).

        Scenario from Issue #1190:
        1. Copilot: makes a comment
        2. Claude: [対象外] で却下
        3. Claude: 実際に修正
        4. Claude: 修正済み: でResolve

        The dismissal should be ignored because it was superseded by the fix.
        """
        threads = [
            self._make_thread(
                [
                    "Type safety suggestion.\n\n-- copilot-swe",  # Copilot comment
                    "これは対象外です。\n\n-- Claude Code",  # Dismissal
                    "修正済み: 型チェックを追加しました。\n\n-- Claude Code",  # Action after dismissal
                ]
            )
        ]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_dismissal_without_issue("123")

        # Dismissal should be ignored because action came after
        assert len(result) == 0

    def test_detect_dismissal_when_action_comes_before(self):
        """Should detect dismissal when action keyword comes BEFORE it (Issue #1190).

        If the action comes before the dismissal, the dismissal should still be flagged.
        """
        threads = [
            self._make_thread(
                [
                    "確認済み: 問題ありません。\n\n-- Claude Code",  # Action first
                    "追加の指摘: これは対象外です。\n\n-- Claude Code",  # Dismissal after
                ]
            )
        ]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_dismissal_without_issue("123")

        # Dismissal should be detected because action came before
        assert len(result) == 1

    def test_detect_dismissal_after_action_after_dismissal(self):
        """Should detect NEW dismissal after action (Codex P2 review fix).

        Scenario: dismissal → action → new dismissal
        The final dismissal should be detected because it came after the action.
        """
        threads = [
            self._make_thread(
                [
                    "これは対象外です。\n\n-- Claude Code",  # First dismissal
                    "修正済み: 修正しました。\n\n-- Claude Code",  # Action
                    "別の問題: これも対象外です。\n\n-- Claude Code",  # New dismissal after action
                ]
            )
        ]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_dismissal_without_issue("123")

        # Only the new dismissal after action should be detected
        assert len(result) == 1

    def test_detect_dismissal_when_action_targets_different_reviewer(self):
        """Should detect dismissal when action targets a DIFFERENT reviewer comment (Issue #1231).

        Scenario:
        1. ReviewerA: 問題1を指摘
        2. Claude: 問題1は対象外 (dismissal targeting ReviewerA)
        3. ReviewerB: 問題2を指摘
        4. Claude: 問題2を修正済み (action targeting ReviewerB)

        The dismissal for 問題1 should still be detected because the action
        targets ReviewerB, not ReviewerA.
        """
        threads = [
            self._make_thread(
                [
                    "問題1: 型安全性の問題があります。\n\n-- copilot-swe",  # ReviewerA
                    "これは対象外です。\n\n-- Claude Code",  # Dismissal for ReviewerA
                    "問題2: パフォーマンスの問題があります。\n\n-- copilot-swe",  # ReviewerB
                    "修正済み: パフォーマンスを改善しました。\n\n-- Claude Code",  # Action for ReviewerB
                ]
            )
        ]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_dismissal_without_issue("123")

        # Dismissal should be detected because action targets a different reviewer comment
        assert len(result) == 1

    def test_skip_dismissal_when_action_targets_same_reviewer(self):
        """Should skip dismissal when action targets the SAME reviewer comment (Issue #1231).

        Scenario:
        1. ReviewerA: 問題を指摘
        2. Claude: 対象外です (dismissal)
        3. Claude: やっぱり修正しました (action)

        The dismissal should be skipped because a later action targets the same issue.
        """
        threads = [
            self._make_thread(
                [
                    "型安全性の問題があります。\n\n-- copilot-swe",  # ReviewerA
                    "これは対象外です。\n\n-- Claude Code",  # Dismissal for ReviewerA
                    "修正済み: やはり対応することにしました。\n\n-- Claude Code",  # Action for ReviewerA
                ]
            )
        ]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_dismissal_without_issue("123")

        # Dismissal should be skipped because action targets the same reviewer
        assert len(result) == 0


class TestCheckDismissalWithoutIssueCodeBlocks:
    """Tests for code block stripping in check_dismissal_without_issue (Issue #797).

    This tests that keywords in code blocks are not detected as dismissals.
    Issue #1181: Updated to use GraphQL response format.
    """

    def setup_method(self):
        """Load the module."""
        spec = importlib.util.spec_from_file_location("merge_check", HOOK_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def _make_graphql_response(self, threads: list[dict]) -> str:
        """Create a mock GraphQL response JSON string."""
        return json.dumps(
            {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": threads}}}}}
        )

    def _make_thread(self, comments: list[str], path: str = "test.py", line: int = 10) -> dict:
        """Create a mock thread object with comments."""
        return {
            "id": "thread_1",
            "path": path,
            "line": line,
            "allComments": {"nodes": [{"body": body} for body in comments]},
        }

    def test_ignore_signature_in_code_block(self):
        """Should ignore '-- Claude Code' signature when it's in a code block (Issue #797)."""
        # This is the exact case from Issue #797: Copilot suggestion containing code example
        comment_body = """**Suggestion:**
```python
# Example response format
body = "Fixed: Updated the code.\\n\\n-- Claude Code"
```

Consider using this format for responses."""
        threads = [self._make_thread([comment_body])]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_dismissal_without_issue("123")

        # Should NOT detect because the signature is in a code block
        assert len(result) == 0

    def test_ignore_dismissal_keyword_in_code_block(self):
        """Should ignore dismissal keywords in code blocks."""
        # Comment containing dismissal keyword in code example
        comment_body = """Example of how to write a dismissal:
```bash
echo "False positive: This is an example"
```

No actual dismissal here."""
        threads = [self._make_thread([comment_body])]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_dismissal_without_issue("123")

        # Should NOT detect because it's not a Claude Code comment
        # (no signature outside code block)
        assert len(result) == 0

    def test_ignore_signature_in_inline_code(self):
        """Should ignore '-- Claude Code' in inline code."""
        comment_body = "The signature `-- Claude Code` should be added at the end."
        threads = [self._make_thread([comment_body])]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_dismissal_without_issue("123")

        # Should NOT detect because the signature is in inline code
        assert len(result) == 0

    def test_detect_real_dismissal_with_code_example(self):
        """Should detect real dismissal even when code examples are present."""
        # Real dismissal with a code example
        comment_body = """これは誤検知です。

Example:
```python
# This is just for reference
```

-- Claude Code"""
        threads = [self._make_thread([comment_body])]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_dismissal_without_issue("123")

        # Should detect because the dismissal keyword and signature are OUTSIDE code blocks
        assert len(result) == 1

    def test_copilot_suggestion_with_signature_example(self):
        """Should NOT detect Copilot suggestion containing '-- Claude Code' example."""
        # Realistic Copilot suggestion (the actual case from Issue #797)
        comment_body = """Consider escaping the string properly.

**Suggested change:**
```python
body = f'''修正しました:

- Changed X to Y
- Added validation

-- Claude Code'''
```
"""
        threads = [self._make_thread([comment_body])]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_dismissal_without_issue("123")

        # Should NOT detect because the signature is in a code block
        assert len(result) == 0

    def test_ignore_issue_reference_in_code_block(self):
        """Should NOT treat Issue reference in code block as valid (PR #1194).

        If a dismissal comment contains Issue reference only in a code block,
        it should NOT satisfy the requirement - the Issue reference must be
        outside code blocks.
        """
        # Dismissal with Issue reference only in code block
        comment_body = """これは誤検知です。

```bash
# See Issue #123 for details
echo "test"
```

-- Claude Code"""
        threads = [self._make_thread([comment_body])]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_dismissal_without_issue("123")

        # Should detect because Issue reference is in code block (doesn't count)
        assert len(result) == 1
        assert result[0]["path"] == "test.py"

    def test_accept_issue_reference_outside_code_block(self):
        """Should accept Issue reference outside code block even with code blocks present."""
        # Dismissal with Issue reference outside code block
        comment_body = """これは誤検知です。Issue #456 を参照してください。

```bash
# Example code
echo "test"
```

-- Claude Code"""
        threads = [self._make_thread([comment_body])]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_dismissal_without_issue("123")

        # Should NOT detect because Issue reference is outside code block
        assert len(result) == 0
