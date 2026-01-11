#!/usr/bin/env python3
"""PRライフサイクル全体を自動化する統合ワークフロー。

Why:
    Codexレビュー→プッシュ→CI待機→レビュー対応→マージ→worktree削除
    の一連のフローを自動化し、手作業を削減するため。

What:
    - run_codex_review(): Codex CLIレビューを実行
    - wait_for_ci(): CI完了を待機
    - handle_reviews(): レビューコメントを処理
    - merge_pr(): PRをマージ
    - cleanup_worktree(): worktreeを削除

Remarks:
    - --skip-codex, --skip-push, --skip-ci でステップスキップ可能
    - --auto-verify で修正確認を自動化
    - --force で未解決スレッドがあってもマージ

Changelog:
    - silenvx/dekita#715: 統合PRワークフロー機能を追加
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Add hooks directory to path for imports
SCRIPT_DIR = Path(__file__).parent
HOOKS_DIR = SCRIPT_DIR.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from common import TIMEOUT_HEAVY, TIMEOUT_LONG, TIMEOUT_MEDIUM

# Pattern for detecting "Verified:" comments (case-insensitive, at line start)
VERIFIED_PATTERN = re.compile(r"^\s*Verified:", re.IGNORECASE | re.MULTILINE)

# Pattern for file references in various languages
# Supports: Python (.py), TypeScript/JavaScript (.ts, .tsx, .js, .jsx), Go (.go), Rust (.rs)
FILE_REFERENCE_PATTERN = re.compile(r"[\w./-]+\.(py|ts|tsx|js|jsx|go|rs):\d+")


def log(msg: str) -> None:
    """Print workflow log message."""
    print(f"[workflow] {msg}")


def run_command(
    cmd: list[str] | str,
    timeout: int = TIMEOUT_HEAVY,
    check: bool = True,
    capture: bool = True,
    shell: bool = False,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess:
    """Run a command with proper error handling."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=capture,
            text=True,
            timeout=timeout,
            check=check,
            shell=shell,
            cwd=cwd,
        )
        return result
    except subprocess.CalledProcessError as e:
        if capture:
            log(f"Command failed: {e.stderr}")
        raise
    except subprocess.TimeoutExpired:
        log(f"Command timed out after {timeout}s")
        raise


def get_project_root() -> Path:
    """Get the project root directory."""
    result = run_command(["git", "rev-parse", "--show-toplevel"])
    return Path(result.stdout.strip())


def get_main_repo_dir() -> Path:
    """Get the main repository directory (not worktree)."""
    result = run_command(["git", "worktree", "list", "--porcelain"])
    lines = result.stdout.strip().split("\n")
    for line in lines:
        if line.startswith("worktree "):
            return Path(line.split(" ", 1)[1])
    return get_project_root()


def is_in_worktree() -> bool:
    """Check if we're in a git worktree (not main repo)."""
    project_root = get_project_root()
    main_repo = get_main_repo_dir()
    return project_root != main_repo


def get_current_branch() -> str:
    """Get current git branch name."""
    result = run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    return result.stdout.strip()


def get_repo_info() -> tuple[str, str]:
    """Get repository owner and name from git remote."""
    try:
        result = run_command(["git", "remote", "get-url", "origin"])
        url = result.stdout.strip()
        # Handle both SSH and HTTPS URLs
        # SSH: git@github.com:owner/repo.git
        # HTTPS: https://github.com/owner/repo.git
        if url.startswith("git@"):
            # SSH format
            path = url.split(":")[-1]
        else:
            # HTTPS format
            path = "/".join(url.split("/")[-2:])
        path = path.removesuffix(".git")
        owner, repo = path.split("/")
        return owner, repo
    except (subprocess.SubprocessError, ValueError, IndexError):
        # CUSTOMIZE: Fallback repository owner and name - Set these to your project's GitHub repo
        return "silenvx", "dekita"


def get_pr_for_branch(branch: str) -> int | None:
    """Get PR number for the current branch."""
    try:
        result = run_command(
            ["gh", "pr", "view", branch, "--json", "number", "--jq", ".number"],
            check=False,
        )
        if result.returncode == 0:
            return int(result.stdout.strip())
    except (ValueError, subprocess.SubprocessError):
        # No PR exists for this branch, or gh command failed - return None
        pass
    return None


def check_codex_review_done(branch: str) -> bool:
    """Check if Codex review has been done for current HEAD."""
    marker_dir = Path(os.environ.get("TMPDIR", "/tmp")) / "claude-hooks"
    marker_file = marker_dir / f"codex-review-{branch}.marker"

    if not marker_file.exists():
        return False

    try:
        result = run_command(["git", "rev-parse", "HEAD"])
        current_head = result.stdout.strip()
        stored_head = marker_file.read_text().strip()
        return current_head == stored_head
    except (subprocess.SubprocessError, OSError):
        return False


def run_codex_review() -> bool:
    """Run Codex CLI review."""
    log("Running Codex review...")
    try:
        result = run_command(
            ["codex", "review", "--base", "main"],
            timeout=TIMEOUT_LONG,
            capture=False,
            check=False,
        )
        return result.returncode == 0
    except subprocess.SubprocessError:
        return False


def push_changes() -> bool:
    """Push changes to remote."""
    log("Pushing changes...")
    try:
        result = run_command(
            ["git", "push"],
            timeout=TIMEOUT_MEDIUM,
            capture=False,
            check=False,
        )
        return result.returncode == 0
    except subprocess.SubprocessError:
        return False


def wait_for_ci_and_reviews(pr_number: int) -> dict:
    """Wait for CI and AI reviews using ci_monitor.py."""
    log(f"Waiting for CI and reviews on PR #{pr_number}...")

    ci_monitor = SCRIPT_DIR / "ci_monitor.py"
    try:
        result = run_command(
            ["python3", str(ci_monitor), str(pr_number), "--timeout", "30"],
            timeout=1800,
            capture=True,
            check=False,
        )
        print(result.stdout)
        return {
            "success": "SUCCESS" in result.stdout or "CI passed" in result.stdout,
            "output": result.stdout,
        }
    except subprocess.SubprocessError as e:
        return {"success": False, "error": str(e)}


def get_unresolved_threads(pr_number: int) -> list[dict]:
    """Get unresolved review threads."""
    owner, repo = get_repo_info()
    query = f"""query {{
      repository(owner: "{owner}", name: "{repo}") {{
        pullRequest(number: {pr_number}) {{
          reviewThreads(first: 50) {{
            nodes {{
              id
              isResolved
              comments(first: 10) {{
                nodes {{ id databaseId body author {{ login }} }}
              }}
            }}
          }}
        }}
      }}
    }}"""

    try:
        result = run_command(
            ["gh", "api", "graphql", "-f", f"query={query}"],
            timeout=TIMEOUT_MEDIUM,
        )
        data = json.loads(result.stdout)
        threads = data["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]

        unresolved = []
        for thread in threads:
            if not thread["isResolved"]:
                comments = thread["comments"]["nodes"]
                if comments:
                    first_comment = comments[0]
                    unresolved.append(
                        {
                            "thread_id": thread["id"],
                            "comment_id": first_comment["databaseId"],
                            "body": first_comment["body"],
                            "author": first_comment["author"]["login"],
                            "has_fix_claim": any("修正済み" in c["body"] for c in comments),
                            "has_verified": any(
                                VERIFIED_PATTERN.search(c["body"]) for c in comments
                            ),
                        }
                    )
        return unresolved
    except (subprocess.SubprocessError, json.JSONDecodeError, KeyError):
        return []


def add_reply_comment(pr_number: int, comment_id: int, body: str) -> bool:
    """Add a reply comment to a review thread."""
    try:
        # Note: {owner} and {repo} are automatically resolved by gh api
        run_command(
            [
                "gh",
                "api",
                f"/repos/{{owner}}/{{repo}}/pulls/{pr_number}/comments",
                "-X",
                "POST",
                "-f",
                f"body={body}",
                "-F",
                f"in_reply_to={comment_id}",
            ]
        )
        return True
    except subprocess.SubprocessError:
        return False


def resolve_thread(thread_id: str) -> bool:
    """Resolve a review thread."""
    mutation = f'mutation {{ resolveReviewThread(input: {{threadId: "{thread_id}"}}) {{ thread {{ isResolved }} }} }}'
    try:
        run_command(["gh", "api", "graphql", "-f", f"query={mutation}"])
        return True
    except subprocess.SubprocessError:
        return False


def auto_verify_threads(pr_number: int, threads: list[dict]) -> int:
    """Auto-verify threads that claim fixes and add Verified comments.

    WARNING: This is a best-effort convenience feature. It only checks
    if the referenced file exists, not whether the actual fix was applied.
    Use with caution and review the auto-verified threads manually.
    """
    verified_count = 0

    for thread in threads:
        if thread["has_fix_claim"] and not thread["has_verified"]:
            # Extract file path and check pattern from the comment
            body = thread["body"]

            # Look for file references in the fix claim
            file_match = FILE_REFERENCE_PATTERN.search(body)
            if file_match:
                ref = file_match.group()
                file_path, line = ref.rsplit(":", 1)

                # Simple verification: check file exists
                # NOTE: This only verifies file existence, not the actual fix content
                full_path = get_project_root() / file_path
                if full_path.exists():
                    verify_body = (
                        f"Auto-verified: ファイル存在確認\n"
                        f"- ファイル: {ref}\n"
                        f"- 確認内容: ファイルが存在することを確認（内容は未検証）\n\n"
                        f"⚠️ 自動検証のため、手動での確認を推奨します\n\n"
                        f"-- Claude Code (auto-verify)"
                    )
                    if add_reply_comment(pr_number, thread["comment_id"], verify_body):
                        if resolve_thread(thread["thread_id"]):
                            verified_count += 1
                            log(f"Auto-verified thread: {body[:50]}...")

    return verified_count


def check_closes_issues(pr_number: int) -> list[dict]:
    """Check if PR closes any issues with incomplete checkboxes."""
    try:
        result = run_command(
            [
                "gh",
                "pr",
                "view",
                str(pr_number),
                "--json",
                "body,closingIssuesReferences",
            ]
        )
        data = json.loads(result.stdout)

        issues = []
        for issue_ref in data.get("closingIssuesReferences", []):
            issue_num = issue_ref.get("number")
            if issue_num:
                issue_result = run_command(
                    ["gh", "issue", "view", str(issue_num), "--json", "body,title"]
                )
                issue_data = json.loads(issue_result.stdout)
                body = issue_data.get("body", "")

                # Count checkboxes
                unchecked = len(re.findall(r"^\s*[-*]\s+\[ \]", body, re.MULTILINE))
                checked = len(re.findall(r"^\s*[-*]\s+\[[xX]\]", body, re.MULTILINE))

                if unchecked > 0:
                    issues.append(
                        {
                            "number": issue_num,
                            "title": issue_data.get("title", ""),
                            "unchecked": unchecked,
                            "checked": checked,
                        }
                    )
        return issues
    except (subprocess.SubprocessError, json.JSONDecodeError):
        return []


def merge_pr(pr_number: int, main_repo_dir: Path) -> bool:
    """Merge PR from main repo directory."""
    log(f"Merging PR #{pr_number} from {main_repo_dir}...")
    try:
        result = run_command(
            ["gh", "pr", "merge", str(pr_number), "--squash"],
            cwd=main_repo_dir,
            timeout=TIMEOUT_MEDIUM,
            check=False,
        )
        if result.returncode == 0:
            log(f"PR #{pr_number} merged successfully!")
            return True
        else:
            log(f"Merge failed: {result.stderr}")
            return False
    except subprocess.SubprocessError as e:
        log(f"Merge error: {e}")
        return False


def cleanup_worktree(worktree_path: Path, main_repo_dir: Path) -> bool:
    """Clean up worktree after merge."""
    log(f"Cleaning up worktree: {worktree_path}")
    try:
        run_command(
            ["git", "worktree", "remove", str(worktree_path)],
            cwd=main_repo_dir,
            timeout=TIMEOUT_MEDIUM,
            check=True,
        )
        return True
    except subprocess.SubprocessError as e:
        log(f"Worktree cleanup failed: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Unified PR merge workflow")
    parser.add_argument("pr_number", nargs="?", type=int, help="PR number to merge")
    parser.add_argument("--skip-codex", action="store_true", help="Skip Codex review")
    parser.add_argument("--skip-push", action="store_true", help="Skip push (already pushed)")
    parser.add_argument("--skip-ci", action="store_true", help="Skip CI wait")
    parser.add_argument("--auto-verify", action="store_true", help="Auto-verify fix claims")
    parser.add_argument(
        "--force", action="store_true", help="Force merge even with unresolved threads"
    )
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done")
    args = parser.parse_args()

    # Get current context
    branch = get_current_branch()
    in_worktree = is_in_worktree()
    main_repo = get_main_repo_dir()
    current_dir = get_project_root()

    log(f"Branch: {branch}")
    log(f"In worktree: {in_worktree}")
    log(f"Main repo: {main_repo}")

    # Get or determine PR number
    pr_number = args.pr_number
    if not pr_number:
        pr_number = get_pr_for_branch(branch)
        if not pr_number:
            log("No PR found for current branch. Create PR first.")
            sys.exit(1)

    log(f"PR: #{pr_number}")

    if args.dry_run:
        log("Dry run - would perform the following steps:")
        print("  1. Run Codex review (if needed)")
        print("  2. Push changes")
        print("  3. Wait for CI and reviews")
        print("  4. Handle review comments")
        print("  5. Merge PR")
        print("  6. Clean up worktree")
        sys.exit(0)

    # Step 1: Codex review (independent of push - can review even if already pushed)
    if not args.skip_codex:
        if not check_codex_review_done(branch):
            if not run_codex_review():
                log("Codex review failed, continuing anyway...")
        else:
            log("Codex review already done")

    # Step 2: Push
    if not args.skip_push:
        if not push_changes():
            log("Push failed, may need manual intervention")
            # Continue to check if already pushed

    # Step 3: Wait for CI and reviews
    if not args.skip_ci:
        ci_result = wait_for_ci_and_reviews(pr_number)
        if not ci_result["success"]:
            log("CI or review wait reported issues, continuing to check state...")

    # Step 4: Handle unresolved threads
    threads = get_unresolved_threads(pr_number)
    if threads:
        log(f"{len(threads)} unresolved thread(s)")

        # Check for threads needing verification
        needs_verification = [t for t in threads if t["has_fix_claim"] and not t["has_verified"]]
        if needs_verification:
            log(f"{len(needs_verification)} thread(s) need verification")
            if args.auto_verify:
                verified = auto_verify_threads(pr_number, needs_verification)
                log(f"Auto-verified {verified} thread(s)")
            else:
                for t in needs_verification:
                    print(f"  - {t['body'][:60]}...")
                log("Use --auto-verify to auto-verify, or verify manually")

        # Refresh threads after potential auto-verify
        threads = get_unresolved_threads(pr_number)
        if threads:
            log(f"Still {len(threads)} unresolved thread(s)")
            for t in threads:
                print(f"  - [{t['author']}] {t['body'][:60]}...")
            if not args.force:
                log("Merge blocked: unresolved review threads exist")
                log("Resolve threads manually, or use --force to override")
                sys.exit(1)
            else:
                log("WARNING: --force specified, proceeding despite unresolved threads")

    # Step 5: Check for incomplete issues
    incomplete_issues = check_closes_issues(pr_number)
    if incomplete_issues:
        log("PR closes issues with incomplete checkboxes:")
        for issue in incomplete_issues:
            print(f"  - #{issue['number']}: {issue['unchecked']} unchecked")
        log("Consider editing PR body to use 'Related to' instead of 'Closes'")

    # Step 6: Merge
    if in_worktree:
        log("In worktree, merging from main repo...")
        success = merge_pr(pr_number, main_repo)
    else:
        success = merge_pr(pr_number, current_dir)

    if not success:
        log("Merge failed - check output above for details")
        sys.exit(1)

    # Step 7: Cleanup
    if in_worktree:
        cleanup_worktree(current_dir, main_repo)

    log("Complete!")


if __name__ == "__main__":
    main()
