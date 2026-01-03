#!/usr/bin/env python3
"""Sentryスコープリークパターンを検出する。

Why:
    Cloudflare WorkersのisolateモデルでSentry.setTag()等を
    withScope()外で使用するとリクエスト間でリークするため。

What:
    - check_file(): ファイル内の禁止パターンを検出
    - main(): worker/src配下をスキャン

Remarks:
    - 正しいパターン: Sentry.withScope((scope) => { scope.setTag(...); })
    - 検出対象: setTag, setContext, setUser, setExtra

Changelog:
    - silenvx/dekita#1100: Sentryスコープリーク検出機能を追加
"""

import re
import sys
from pathlib import Path

# Patterns that indicate potential scope leaks
# These should never be used directly in worker code
BANNED_PATTERNS = [
    (r"\bSentry\.setTag\s*\(", "Sentry.setTag()"),
    (r"\bSentry\.setContext\s*\(", "Sentry.setContext()"),
    (r"\bSentry\.setUser\s*\(", "Sentry.setUser()"),
    (r"\bSentry\.setExtra\s*\(", "Sentry.setExtra()"),
]

# Directory to check
WORKER_SRC = Path("worker/src")


def is_in_comment(line: str, match_start: int) -> bool:
    """Check if the match position is inside a // comment.

    Does not handle block comments or strings containing //.
    """
    comment_pos = line.find("//")
    return comment_pos != -1 and comment_pos < match_start


def check_file(file_path: Path) -> list[tuple[int, str, str]]:
    """Check a file for banned patterns.

    Returns list of (line_number, line_content, pattern_name) tuples.
    Skips patterns that appear inside // comments.
    """
    violations = []
    try:
        content = file_path.read_text(encoding="utf-8")
        lines = content.splitlines()

        for i, line in enumerate(lines, start=1):
            for pattern, name in BANNED_PATTERNS:
                match = re.search(pattern, line)
                if match and not is_in_comment(line, match.start()):
                    violations.append((i, line.strip(), name))
    except OSError as e:
        print(f"⚠️ Warning: Could not read {file_path}: {e}", file=sys.stderr)

    return violations


def main() -> int:
    """Check all TypeScript files in worker/src for Sentry scope leaks."""
    if not WORKER_SRC.exists():
        print(f"Directory not found: {WORKER_SRC}")
        return 1

    all_violations: dict[Path, list[tuple[int, str, str]]] = {}

    for ts_file in WORKER_SRC.rglob("*.ts"):
        violations = check_file(ts_file)
        if violations:
            all_violations[ts_file] = violations

    if not all_violations:
        print("✅ No Sentry scope leak patterns detected")
        return 0

    print("❌ Sentry scope leak patterns detected!\n")
    print("The following methods should NOT be used directly in Cloudflare Workers:")
    print("  - Sentry.setTag()")
    print("  - Sentry.setContext()")
    print("  - Sentry.setUser()")
    print("  - Sentry.setExtra()")
    print("\nUse Sentry.withScope() instead:")
    print("  Sentry.withScope((scope) => {")
    print('    scope.setTag("key", "value");')
    print("    Sentry.captureException(err);")
    print("  });")
    print("\nViolations found:\n")

    for file_path, violations in sorted(all_violations.items()):
        for line_num, line_content, pattern_name in violations:
            print(f"  {file_path}:{line_num}: {pattern_name}")
            print(f"    {line_content}\n")

    return 1


if __name__ == "__main__":
    sys.exit(main())
