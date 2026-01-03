#!/usr/bin/env python3
"""フックブロック判断を評価・記録する。

Why:
    フックの誤検知（false positive）を特定し、
    改善フィードバックループを確立するため。

What:
    - list: 直近のブロックイベントを表示
    - evaluate: 特定ブロックを評価（valid/false_positive）
    - summary: 評価サマリーを表示

State:
    - reads: .claude/logs/session/*/hook-execution-*.jsonl
    - writes: .claude/logs/metrics/block-evaluations.log

Remarks:
    - ブロックIDはタイムスタンプ・フック名・ブランチから生成
    - 評価結果はanalyze-false-positives.pyで分析可能

Changelog:
    - silenvx/dekita#1361: ブロック評価機能を追加
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

# Project directory
PROJECT_DIR = Path(os.environ.get("CLAUDE_PROJECT_DIR", Path(__file__).parents[2]))
LOG_DIR = PROJECT_DIR / ".claude" / "logs"
EXECUTION_LOG_DIR = LOG_DIR / "execution"
METRICS_LOG_DIR = LOG_DIR / "metrics"
EVALUATION_LOG = METRICS_LOG_DIR / "block-evaluations.log"

# Add hooks directory to path for lib imports
HOOKS_DIR = PROJECT_DIR / ".claude" / "hooks"
sys.path.insert(0, str(HOOKS_DIR))
from lib.logging import read_all_session_log_entries


def get_block_id(entry: dict) -> str:
    """Generate a unique ID for a block entry."""
    timestamp = entry.get("timestamp", "")
    hook = entry.get("hook", "")
    branch = entry.get("branch", "")
    content = f"{timestamp}_{hook}_{branch}"
    return hashlib.sha256(content.encode()).hexdigest()[:12]


def load_blocks(limit: int = 50) -> list[dict]:
    """Load recent block events from all session hook execution logs."""
    blocks = []

    # Read from all session-specific log files
    entries = read_all_session_log_entries(EXECUTION_LOG_DIR, "hook-execution")

    for entry in entries:
        if entry.get("decision") == "block":
            # Skip entries missing required fields
            if not entry.get("timestamp") or not entry.get("hook"):
                continue
            entry["block_id"] = get_block_id(entry)
            blocks.append(entry)

    # Sort by timestamp and return most recent blocks
    blocks.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return blocks[:limit]


def load_evaluations() -> dict[str, dict]:
    """Load existing evaluations."""
    if not EVALUATION_LOG.exists():
        return {}

    evaluations = {}
    with open(EVALUATION_LOG, encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
                block_id = entry.get("block_id")
                if block_id:
                    evaluations[block_id] = entry
            except json.JSONDecodeError:
                continue

    return evaluations


def save_evaluation(evaluation: dict) -> None:
    """Save an evaluation to the log."""
    METRICS_LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(EVALUATION_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(evaluation, ensure_ascii=False) + "\n")


def format_block(block: dict, evaluations: dict) -> str:
    """Format a block entry for display."""
    block_id = block.get("block_id", "unknown")
    evaluated = block_id in evaluations

    status = ""
    if evaluated:
        eval_result = evaluations[block_id].get("evaluation", "unknown")
        status_map = {
            "valid": "✅ valid",
            "false_positive": "❌ false_positive",
            "unclear": "❓ unclear",
        }
        status = f" [{status_map.get(eval_result, eval_result)}]"

    # Safe timestamp handling with format validation
    timestamp_raw = block.get("timestamp", "")
    if len(timestamp_raw) >= 19 and "T" in timestamp_raw[:19]:
        timestamp = timestamp_raw[:19].replace("T", " ")
    elif timestamp_raw:
        # Non-standard format: show as-is (truncated if too long)
        timestamp = timestamp_raw[:19]
    else:
        timestamp = "unknown"
    hook = block.get("hook", "unknown")
    branch = block.get("branch", "unknown")

    # Truncate reason for display
    reason = block.get("reason", "")
    reason_first_line = reason.split("\n")[0]
    if len(reason_first_line) > 60:
        reason_first_line = reason_first_line[:60] + "..."

    return f"[{block_id}]{status} {timestamp} | {hook:25} | {branch:30} | {reason_first_line}"


def cmd_list(args: argparse.Namespace) -> None:
    """List recent blocks."""
    blocks = load_blocks(args.limit)
    evaluations = load_evaluations()

    if not blocks:
        print("No block events found.")
        return

    print(f"Recent block events (showing {len(blocks)}):\n")
    print("-" * 120)

    for block in blocks:
        print(format_block(block, evaluations))

    print("-" * 120)

    # Summary
    evaluated_count = sum(1 for b in blocks if b["block_id"] in evaluations)
    print(f"\nTotal: {len(blocks)} blocks, {evaluated_count} evaluated")

    unevaluated = [b for b in blocks if b["block_id"] not in evaluations]
    if unevaluated:
        print("\nTo evaluate a block, run:")
        print("  python3 .claude/scripts/block-evaluator.py evaluate <block_id>")


def cmd_evaluate(args: argparse.Namespace) -> None:
    """Evaluate a specific block."""
    blocks = load_blocks(1000)  # Load more for searching
    evaluations = load_evaluations()

    # Find the block
    block = None
    for b in blocks:
        if b["block_id"] == args.block_id or b["block_id"].startswith(args.block_id):
            block = b
            break

    if not block:
        print(f"Block not found: {args.block_id}")
        return

    block_id = block["block_id"]

    # Check if already evaluated
    if block_id in evaluations and not args.force:
        print(f"Block {block_id} already evaluated:")
        existing = evaluations[block_id]
        print(f"  Evaluation: {existing['evaluation']}")
        print(f"  Reason: {existing.get('evaluation_reason', 'N/A')}")
        print("\nUse --force to re-evaluate.")
        return

    # Show block details
    print(f"\n{'=' * 80}")
    print(f"Block ID: {block_id}")
    print(f"{'=' * 80}")
    print(f"Timestamp: {block['timestamp']}")
    print(f"Hook: {block['hook']}")
    print(f"Branch: {block.get('branch', 'unknown')}")
    print(f"Session: {block.get('session_id', 'unknown')}")
    print(f"\nReason:\n{block.get('reason', 'N/A')}")
    if block.get("details"):
        print(f"\nDetails:\n{json.dumps(block['details'], indent=2, ensure_ascii=False)}")
    print(f"{'=' * 80}\n")

    # Get evaluation from args or prompt
    if args.evaluation:
        evaluation = args.evaluation
    else:
        print("Evaluation options:")
        print("  valid          - Block was correct and necessary")
        print("  false_positive - Block was incorrect (should not have blocked)")
        print("  unclear        - Cannot determine if block was correct")
        print()
        evaluation = input("Enter evaluation (valid/false_positive/unclear): ").strip().lower()

    if evaluation not in ("valid", "false_positive", "unclear"):
        print(f"Invalid evaluation: {evaluation}")
        return

    # Get reason
    if args.reason:
        reason = args.reason
    else:
        reason = input("Reason for evaluation (optional): ").strip()

    # Get improvement suggestion for false positives
    improvement = ""
    if evaluation == "false_positive":
        if args.improvement:
            improvement = args.improvement
        else:
            improvement = input("Suggested improvement (optional): ").strip()

    # Save evaluation
    eval_entry = {
        "block_id": block_id,
        "timestamp": datetime.now(UTC).isoformat(),
        "hook": block["hook"],
        "branch": block.get("branch"),
        "evaluation": evaluation,
        "evaluation_reason": reason,
        "improvement_suggestion": improvement,
        "original_block": block,
    }
    save_evaluation(eval_entry)

    print(f"\n✅ Evaluation saved for block {block_id}")


def cmd_summary(args: argparse.Namespace) -> None:
    """Show evaluation summary."""
    evaluations = load_evaluations()

    if not evaluations:
        print("No evaluations found.")
        return

    # Group by hook and evaluation
    hook_stats: dict[str, dict[str, int]] = {}
    for eval_entry in evaluations.values():
        hook = eval_entry.get("hook")
        evaluation = eval_entry.get("evaluation")
        if not hook or not evaluation:
            continue

        if hook not in hook_stats:
            hook_stats[hook] = {"valid": 0, "false_positive": 0, "unclear": 0}
        # Handle unknown evaluation values gracefully
        if evaluation in hook_stats[hook]:
            hook_stats[hook][evaluation] += 1

    print("\nEvaluation Summary by Hook:\n")
    print(f"{'Hook':<30} {'Valid':>8} {'False+':>8} {'Unclear':>8} {'FP Rate':>10}")
    print("-" * 70)

    total_stats = {"valid": 0, "false_positive": 0, "unclear": 0}
    for hook, stats in sorted(hook_stats.items()):
        total = stats["valid"] + stats["false_positive"] + stats["unclear"]
        fp_rate = (stats["false_positive"] / total * 100) if total > 0 else 0

        print(
            f"{hook:<30} {stats['valid']:>8} {stats['false_positive']:>8} {stats['unclear']:>8} {fp_rate:>9.1f}%"
        )

        for key in total_stats:
            total_stats[key] += stats[key]

    print("-" * 70)
    total = sum(total_stats.values())
    fp_rate = (total_stats["false_positive"] / total * 100) if total > 0 else 0
    print(
        f"{'TOTAL':<30} {total_stats['valid']:>8} {total_stats['false_positive']:>8} {total_stats['unclear']:>8} {fp_rate:>9.1f}%"
    )

    # Show hooks with high false positive rate
    problem_hooks = []
    for hook, stats in hook_stats.items():
        total = stats["valid"] + stats["false_positive"] + stats["unclear"]
        if total > 0 and stats["false_positive"] >= 2:
            fp_rate_hook = stats["false_positive"] / total
            if fp_rate_hook > 0.3:
                problem_hooks.append((hook, stats))

    if problem_hooks:
        print("\n⚠️  Hooks with high false positive rate (>30%):")
        for hook, _stats in problem_hooks:
            print(f"  - {hook}")

        print("\nRun the following to see improvement suggestions:")
        print("  python3 .claude/scripts/analyze-false-positives.py")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate hook block decisions")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # list command
    list_parser = subparsers.add_parser("list", help="List recent blocks")
    list_parser.add_argument("--limit", type=int, default=50, help="Number of blocks to show")

    # evaluate command
    eval_parser = subparsers.add_parser("evaluate", help="Evaluate a block")
    eval_parser.add_argument("block_id", help="Block ID to evaluate")
    eval_parser.add_argument("--evaluation", "-e", choices=["valid", "false_positive", "unclear"])
    eval_parser.add_argument("--reason", "-r", help="Reason for evaluation")
    eval_parser.add_argument("--improvement", "-i", help="Improvement suggestion")
    eval_parser.add_argument("--force", "-f", action="store_true", help="Re-evaluate existing")

    # summary command
    subparsers.add_parser("summary", help="Show evaluation summary")

    args = parser.parse_args()

    if args.command == "list":
        cmd_list(args)
    elif args.command == "evaluate":
        cmd_evaluate(args)
    elif args.command == "summary":
        cmd_summary(args)


if __name__ == "__main__":
    main()
