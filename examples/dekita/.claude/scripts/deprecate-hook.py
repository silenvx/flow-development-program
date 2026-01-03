#!/usr/bin/env python3
"""フックを非推奨化または削除する。

Why:
    フックのライフサイクル管理を標準化し、
    非推奨化・削除の履歴を追跡可能にするため。

What:
    - deprecate(): metadata.jsonに非推奨情報を記録
    - remove_from_settings(): settings.jsonから削除
    - undo(): 非推奨を取り消してアクティブに戻す

State:
    - reads/writes: .claude/hooks/metadata.json
    - reads/writes: .claude/settings.json
    - writes: .claude/hooks/removal-history.json

Remarks:
    - --remove でsettings.jsonからも削除
    - --undo で非推奨を取り消し
    - --dry-run でプレビュー

Changelog:
    - silenvx/dekita#1400: フック非推奨化機能を追加
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# Project paths
SCRIPTS_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPTS_DIR.parent.parent
HOOKS_DIR = PROJECT_DIR / ".claude" / "hooks"
CLAUDE_DIR = PROJECT_DIR / ".claude"

METADATA_PATH = HOOKS_DIR / "metadata.json"
SETTINGS_PATH = CLAUDE_DIR / "settings.json"
REMOVAL_HISTORY_PATH = HOOKS_DIR / "removal-history.json"


def load_json(path: Path) -> dict:
    """Load JSON file safely."""
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_json(path: Path, data: dict) -> None:
    """Save JSON file with proper formatting."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def find_hook_in_settings(settings: dict, hook_name: str) -> list[tuple[str, int, int]]:
    """Find all occurrences of a hook in settings.json.

    Returns list of (hook_type, matcher_idx, hook_idx) tuples.
    """
    locations = []
    hooks_config = settings.get("hooks", {})

    for hook_type in ["PreToolUse", "PostToolUse", "Stop"]:
        type_config = hooks_config.get(hook_type, [])
        for matcher_idx, matcher in enumerate(type_config):
            hooks = matcher.get("hooks", [])
            for hook_idx, hook in enumerate(hooks):
                command = hook.get("command", "")
                if hook_name in command:
                    locations.append((hook_type, matcher_idx, hook_idx))

    return locations


def deprecate_hook(hook_name: str, reason: str, remove: bool, dry_run: bool) -> bool:
    """Deprecate a hook."""
    metadata = load_json(METADATA_PATH)
    settings = load_json(SETTINGS_PATH)
    history = load_json(REMOVAL_HISTORY_PATH)

    # Check if hook exists in metadata
    hooks_meta = metadata.get("hooks", {})
    if hook_name not in hooks_meta:
        print(f"Error: Hook '{hook_name}' not found in metadata.json")
        return False

    hook_meta = hooks_meta[hook_name]
    previous_status = hook_meta.get("status", "active")  # Capture before mutation
    if previous_status == "deprecated":
        print(f"Warning: Hook '{hook_name}' is already deprecated")
        return False

    today = datetime.now().strftime("%Y-%m-%d")

    # Update metadata
    print(f"\n{'[DRY RUN] ' if dry_run else ''}Deprecating hook: {hook_name}")
    print(f"  Reason: {reason}")

    if not dry_run:
        hook_meta["status"] = "deprecated"
        hook_meta["deprecated_at"] = today
        hook_meta["deprecation_reason"] = reason
        hooks_meta[hook_name] = hook_meta
        metadata["hooks"] = hooks_meta
        save_json(METADATA_PATH, metadata)
        print("  Updated metadata.json")

    # Find and optionally remove from settings.json
    locations = find_hook_in_settings(settings, hook_name)
    if locations:
        print(f"  Found in settings.json: {len(locations)} location(s)")
        for hook_type, matcher_idx, hook_idx in locations:
            print(f"    - {hook_type}[{matcher_idx}].hooks[{hook_idx}]")

        if remove:
            if not dry_run:
                # Remove in reverse order to maintain indices
                for hook_type, matcher_idx, hook_idx in sorted(locations, reverse=True):
                    hooks_list = settings["hooks"][hook_type][matcher_idx]["hooks"]
                    hooks_list.pop(hook_idx)
                    print(f"  Removed from {hook_type}")
                save_json(SETTINGS_PATH, settings)
                print("  Updated settings.json")
        else:
            print("  (Use --remove to also remove from settings.json)")
    else:
        print("  Not found in settings.json (may already be removed)")

    # Record in history
    if not dry_run:
        if "removals" not in history:
            history["removals"] = []

        history["removals"].append(
            {
                "hook": hook_name,
                "action": "deprecated" if not remove else "removed",
                "date": today,
                "reason": reason,
                "previous_status": previous_status,
            }
        )
        save_json(REMOVAL_HISTORY_PATH, history)
        print("  Recorded in removal-history.json")

    return True


def undo_deprecation(hook_name: str, dry_run: bool) -> bool:
    """Restore a deprecated hook to active status."""
    metadata = load_json(METADATA_PATH)
    history = load_json(REMOVAL_HISTORY_PATH)

    hooks_meta = metadata.get("hooks", {})
    if hook_name not in hooks_meta:
        print(f"Error: Hook '{hook_name}' not found in metadata.json")
        return False

    hook_meta = hooks_meta[hook_name]
    if hook_meta.get("status") != "deprecated":
        print(f"Warning: Hook '{hook_name}' is not deprecated (status: {hook_meta.get('status')})")
        return False

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Restoring hook: {hook_name}")

    if not dry_run:
        hook_meta["status"] = "active"
        hook_meta.pop("deprecated_at", None)
        hook_meta.pop("deprecation_reason", None)
        hooks_meta[hook_name] = hook_meta
        metadata["hooks"] = hooks_meta
        save_json(METADATA_PATH, metadata)
        print("  Updated metadata.json")

        # Record in history
        if "removals" not in history:
            history["removals"] = []
        history["removals"].append(
            {
                "hook": hook_name,
                "action": "restored",
                "date": datetime.now().strftime("%Y-%m-%d"),
                "reason": "Undo deprecation",
            }
        )
        save_json(REMOVAL_HISTORY_PATH, history)
        print("  Recorded in removal-history.json")

    print("\nNote: If the hook was removed from settings.json, you need to manually re-add it.")
    return True


def list_deprecated(metadata: dict) -> None:
    """List all deprecated hooks."""
    hooks_meta = metadata.get("hooks", {})
    deprecated = [
        (name, meta) for name, meta in hooks_meta.items() if meta.get("status") == "deprecated"
    ]

    if not deprecated:
        print("No deprecated hooks found.")
        return

    print(f"\nDeprecated hooks ({len(deprecated)}):")
    for name, meta in deprecated:
        print(f"  - {name}")
        print(f"    Deprecated: {meta.get('deprecated_at', 'unknown')}")
        print(f"    Reason: {meta.get('deprecation_reason', 'not specified')}")


def main():
    parser = argparse.ArgumentParser(
        description="Deprecate or restore hooks",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("hook_name", nargs="?", help="Name of the hook to deprecate")
    parser.add_argument("--reason", help="Reason for deprecation")
    parser.add_argument("--remove", action="store_true", help="Also remove from settings.json")
    parser.add_argument("--undo", action="store_true", help="Restore deprecated hook")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done")
    parser.add_argument("--list", action="store_true", help="List deprecated hooks")
    args = parser.parse_args()

    if args.list:
        metadata = load_json(METADATA_PATH)
        list_deprecated(metadata)
        return

    if not args.hook_name:
        parser.print_help()
        sys.exit(1)

    if args.undo:
        success = undo_deprecation(args.hook_name, args.dry_run)
    else:
        if not args.reason:
            print("Error: --reason is required for deprecation")
            sys.exit(1)
        success = deprecate_hook(args.hook_name, args.reason, args.remove, args.dry_run)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
