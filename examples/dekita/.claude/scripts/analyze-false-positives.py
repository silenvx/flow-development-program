#!/usr/bin/env python3
"""フック誤検知の分析と改善提案を生成する。

Why:
    フックの誤検知（false positive）を減らすため、
    評価ログから改善ポイントを抽出する機能が必要。

What:
    - load_evaluations(): 評価ログを読み込み
    - analyze_false_positives(): 誤検知パターンを分析
    - generate_suggestions(): 改善提案を生成

State:
    - reads: .claude/logs/metrics/block-evaluations.log

Remarks:
    - --hook オプションで特定フックにフィルタリング可能
    - 評価ログはblock-evaluator.pyで記録される

Changelog:
    - silenvx/dekita#1361: 誤検知分析機能を追加
"""

import argparse
import json
import os
import re
from collections import defaultdict
from pathlib import Path

# Project directory
PROJECT_DIR = Path(os.environ.get("CLAUDE_PROJECT_DIR", Path(__file__).parents[2]))
LOG_DIR = PROJECT_DIR / ".claude" / "logs"
METRICS_LOG_DIR = LOG_DIR / "metrics"
EVALUATION_LOG = METRICS_LOG_DIR / "block-evaluations.log"
HOOKS_DIR = PROJECT_DIR / ".claude" / "hooks"


def load_evaluations() -> list[dict]:
    """Load all evaluations."""
    if not EVALUATION_LOG.exists():
        return []

    evaluations = []
    with open(EVALUATION_LOG, encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
                evaluations.append(entry)
            except json.JSONDecodeError:
                continue

    return evaluations


def analyze_false_positives(evaluations: list[dict], target_hook: str | None = None) -> dict:
    """Analyze false positives and group by patterns."""
    false_positives = [
        e
        for e in evaluations
        if e.get("evaluation") == "false_positive"
        and (target_hook is None or e.get("hook") == target_hook)
    ]

    if not false_positives:
        return {}

    # Group by hook
    by_hook: dict[str, list[dict]] = defaultdict(list)
    for fp in false_positives:
        hook = fp.get("hook")
        if hook:
            by_hook[hook].append(fp)

    analysis = {}
    for hook, fps in by_hook.items():
        # Extract patterns from the original blocks
        patterns: dict[str, list[dict]] = defaultdict(list)

        for fp in fps:
            original = fp.get("original_block", {})
            details = original.get("details", {})

            # Try to identify patterns
            if "command" in details:
                # Group by command pattern
                cmd = details["command"]
                # Normalize command pattern (remove specific values)
                pattern_key = extract_command_pattern(cmd)
                patterns[f"command:{pattern_key}"].append(fp)
            elif "file_path" in details:
                # Group by file type
                file_path = details["file_path"]
                pattern_key = extract_file_pattern(file_path)
                patterns[f"file:{pattern_key}"].append(fp)
            elif "type" in details:
                patterns[f"type:{details['type']}"].append(fp)
            else:
                patterns["unknown"].append(fp)

        analysis[hook] = {
            "total_fps": len(fps),
            "patterns": dict(patterns),
            "improvement_suggestions": [
                fp.get("improvement_suggestion") for fp in fps if fp.get("improvement_suggestion")
            ],
        }

    return analysis


def extract_command_pattern(cmd: str) -> str:
    """Extract a pattern from a command for grouping."""
    # Normalize PR/Issue numbers in specific contexts
    # More targeted than replacing all digits to avoid false matches
    # - gh pr view 123, gh pr merge 123, gh issue view 123
    cmd = re.sub(r"(gh\s+(?:pr|issue)\s+\w+\s+)\d+", r"\1<NUM>", cmd)
    # - #123 at word boundary (not in file paths like /path#123)
    cmd = re.sub(r"(?<![/\\])#\d+", "#<NUM>", cmd)
    # - pulls/123, issues/123 (GitHub URL patterns)
    cmd = re.sub(r"(pulls|issues)/\d+", r"\1/<NUM>", cmd)
    # Remove branch names (common patterns)
    cmd = re.sub(r"(feat|fix|issue|hotfix)/[^\s]+", "<BRANCH>", cmd)
    # Remove file paths
    cmd = re.sub(r"/[^\s]+\.(py|ts|tsx|js|json|md)", "<FILE>", cmd)
    # Truncate for grouping
    return cmd[:80]


def extract_file_pattern(file_path: str) -> str:
    """Extract a pattern from a file path for grouping."""
    # Handle empty or invalid file paths
    if not file_path or not file_path.strip():
        return "root/*.unknown"

    # Extract directory structure and file type
    # Filter out empty parts (from trailing slashes or double slashes)
    parts = [p for p in file_path.split("/") if p]

    # Get extension from the last non-empty part
    last_part = parts[-1] if parts else ""
    if last_part and "." in last_part:
        ext = last_part.split(".")[-1]
    else:
        ext = "unknown"

    # Get key directories
    key_dirs = []
    for part in parts:
        if part in ("frontend", "worker", "shared", ".claude", "hooks", "scripts"):
            key_dirs.append(part)

    return f"{'/'.join(key_dirs[-2:]) or 'root'}/*.{ext}"


def generate_improvement_report(analysis: dict) -> str:
    """Generate a detailed improvement report."""
    lines = []
    lines.append("=" * 80)
    lines.append("FALSE POSITIVE ANALYSIS REPORT")
    lines.append("=" * 80)
    lines.append("")

    for hook, data in sorted(analysis.items(), key=lambda x: -x[1]["total_fps"]):
        lines.append(f"## {hook}")
        lines.append(f"   False Positives: {data['total_fps']}")
        lines.append("")

        # Show patterns
        if data["patterns"]:
            lines.append("   Patterns detected:")
            for pattern, fps in sorted(data["patterns"].items(), key=lambda x: -len(x[1])):
                lines.append(f"     - {pattern}: {len(fps)} occurrences")

                # Show sample reasons
                for fp in fps[:2]:
                    reason = fp.get("evaluation_reason", "")
                    if reason:
                        truncated = reason[:60] + "..." if len(reason) > 60 else reason
                        lines.append(f"       Reason: {truncated}")
        lines.append("")

        # Show user-suggested improvements
        if data["improvement_suggestions"]:
            lines.append("   User-suggested improvements:")
            for suggestion in data["improvement_suggestions"]:
                lines.append(f"     • {suggestion}")
        lines.append("")

        # Generate automated recommendations
        recommendations = generate_recommendations(hook, data)
        if recommendations:
            lines.append("   Recommended actions:")
            for rec in recommendations:
                lines.append(f"     → {rec}")
        lines.append("")
        lines.append("-" * 80)
        lines.append("")

    return "\n".join(lines)


def generate_recommendations(hook: str, data: dict) -> list[str]:
    """Generate automated recommendations based on patterns."""
    recommendations = []

    patterns = data.get("patterns", {})

    # Check for command patterns
    cmd_patterns = [p for p in patterns if p.startswith("command:")]
    if cmd_patterns:
        recommendations.append(
            "Review command detection logic - consider adding exceptions for detected patterns"
        )

    # Check for file patterns
    file_patterns = [p for p in patterns if p.startswith("file:")]
    if file_patterns:
        for pattern in file_patterns:
            if "test" in pattern.lower():
                recommendations.append(f"Consider excluding test files from {hook}")
            elif ".claude/" in pattern:
                recommendations.append("Consider special handling for .claude/ directory files")

    # High volume recommendations
    if data["total_fps"] >= 5:
        recommendations.append(
            f"High false positive rate - consider refactoring {hook} detection logic"
        )

    # Check hook file for potential issues
    hook_file = HOOKS_DIR / f"{hook}.py"
    if hook_file.exists():
        try:
            content = hook_file.read_text(encoding="utf-8")
            if "strip_quoted_strings" not in content and "command" in str(patterns):
                recommendations.append(
                    "Consider using strip_quoted_strings() to avoid matching commands in strings"
                )
        except (OSError, UnicodeDecodeError):
            # Skip hook file analysis if file cannot be read
            pass

    return recommendations


def cmd_analyze(args: argparse.Namespace) -> None:
    """Run the analysis."""
    evaluations = load_evaluations()

    if not evaluations:
        print("No evaluations found. Run block-evaluator.py to evaluate blocks first.")
        return

    analysis = analyze_false_positives(evaluations, args.hook)

    if not analysis:
        if args.hook:
            print(f"No false positives found for hook: {args.hook}")
        else:
            print("No false positives found in evaluations.")
        return

    report = generate_improvement_report(analysis)
    print(report)

    # Offer to create an issue
    print("\nTo create a GitHub Issue for hook improvement:")
    print("  gh issue create --title 'Hook improvement: <hook_name>' --body '<description>'")


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze false positives and suggest improvements")
    parser.add_argument("--hook", help="Analyze specific hook only")

    args = parser.parse_args()
    cmd_analyze(args)


if __name__ == "__main__":
    main()
