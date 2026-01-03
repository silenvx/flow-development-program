#!/usr/bin/env python3
"""ワークフロー定義の整合性をチェックする。

Why:
    settings.jsonとflow_definitions.pyの定義漏れを防ぎ、
    フック追加時の一貫性を保証するため。

What:
    - extract_hook_names_from_settings(): settings.jsonからフック名を抽出
    - check_consistency(): 定義の整合性を検証

State:
    - reads: .claude/settings.json
    - reads: .claude/hooks/flow_definitions.py

Remarks:
    - Exit 0: 整合性OK、Exit 1: 不整合検出
    - settings.jsonとEXPECTED_HOOK_BEHAVIORSの双方向チェック

Changelog:
    - silenvx/dekita#1500: ワークフロー定義整合性チェック機能を追加
"""

import json
import re
import sys
from pathlib import Path

# Add hooks directory to path for imports
script_dir = Path(__file__).parent
hooks_dir = script_dir.parent / "hooks"
sys.path.insert(0, str(hooks_dir))

from flow_definitions import (
    DEVELOPMENT_PHASES,
    EXPECTED_HOOK_BEHAVIORS,
)


def extract_hook_names_from_settings(settings_path: Path) -> set[str]:
    """Extract all hook names from settings.json.

    Args:
        settings_path: Path to settings.json

    Returns:
        Set of hook names (without .py extension for Python hooks,
        with .sh extension for shell scripts)
    """
    hook_names: set[str] = set()

    if not settings_path.exists():
        return hook_names

    try:
        with open(settings_path, encoding="utf-8") as f:
            settings = json.load(f)
    except (json.JSONDecodeError, OSError):
        return hook_names

    hooks_config = settings.get("hooks", {})

    # Track prompt hooks by their position/type for naming
    prompt_hook_index = 0

    for hook_type in ["SessionStart", "PreToolUse", "PostToolUse", "Stop"]:
        hook_list = hooks_config.get(hook_type, [])
        for hook_group in hook_list:
            for hook in hook_group.get("hooks", []):
                if hook.get("type") == "command":
                    command = hook.get("command", "")
                    # Extract hook name from command
                    # Pattern: python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/xxx.py
                    # For Python hooks, we strip the .py extension
                    match = re.search(r'/([^/]+)\.py(?:\s|$|")', command)
                    if match:
                        hook_names.add(match.group(1))
                    # Also handle shell scripts - keep the .sh extension to distinguish
                    match = re.search(r'/([^/]+\.sh)(?:\s|$|")', command)
                    if match:
                        hook_names.add(match.group(1))
                elif hook.get("type") == "prompt":
                    # For prompt-type hooks, use a descriptive name based on hook type
                    # The reflection-prompt is the main Stop prompt hook
                    if hook_type == "Stop":
                        hook_names.add("reflection-prompt")
                    else:
                        prompt_hook_index += 1
                        hook_names.add(f"{hook_type.lower()}-prompt-{prompt_hook_index}")

    return hook_names


def check_hook_coverage(settings_path: Path) -> list[str]:
    """Check if all settings.json hooks are defined in EXPECTED_HOOK_BEHAVIORS.

    Returns:
        List of error messages (empty if all OK)
    """
    errors: list[str] = []
    settings_hooks = extract_hook_names_from_settings(settings_path)
    defined_hooks = set(EXPECTED_HOOK_BEHAVIORS.keys())

    # Hooks in settings but not in definitions
    undefined = settings_hooks - defined_hooks
    if undefined:
        errors.append(
            f"Hooks in settings.json but not in EXPECTED_HOOK_BEHAVIORS: {sorted(undefined)}"
        )

    # Hooks in definitions but not in settings (warning, not error)
    # These might be planned hooks or hooks that are conditionally loaded
    orphaned = defined_hooks - settings_hooks
    if orphaned:
        # This is a warning, not an error
        print(f"⚠️  Hooks in EXPECTED_HOOK_BEHAVIORS but not in settings.json: {sorted(orphaned)}")

    return errors


def check_phase_hooks() -> list[str]:
    """Check if all phase expected_hooks are defined in EXPECTED_HOOK_BEHAVIORS.

    Returns:
        List of error messages (empty if all OK)
    """
    errors: list[str] = []
    defined_hooks = set(EXPECTED_HOOK_BEHAVIORS.keys())

    for phase in DEVELOPMENT_PHASES:
        for hook_name in phase.expected_hooks:
            if hook_name not in defined_hooks:
                errors.append(f"Phase '{phase.id}' references undefined hook: {hook_name}")

    return errors


def check_hook_phase_consistency() -> list[str]:
    """Check if EXPECTED_HOOK_BEHAVIORS.phase_id matches DEVELOPMENT_PHASES.expected_hooks.

    For each hook with a phase_id, verify that the phase's expected_hooks includes it.

    Returns:
        List of warning messages (not errors, just inconsistencies)
    """
    warnings: list[str] = []

    # Build a map of phase_id -> expected_hooks
    phase_hooks: dict[str, set[str]] = {}
    for phase in DEVELOPMENT_PHASES:
        phase_hooks[phase.id] = set(phase.expected_hooks)

    for hook_name, behavior in EXPECTED_HOOK_BEHAVIORS.items():
        phase_id = behavior.phase_id
        if phase_id not in phase_hooks:
            warnings.append(f"Hook '{hook_name}' has unknown phase_id: {phase_id}")
        elif hook_name not in phase_hooks[phase_id]:
            warnings.append(
                f"Hook '{hook_name}' has phase_id='{phase_id}' but is not in that phase's expected_hooks"
            )

    return warnings


def main() -> int:
    # Find project directory
    project_dir = script_dir.parent.parent
    settings_path = project_dir / ".claude" / "settings.json"

    print("🔍 Checking workflow definition integrity...")
    print("")

    all_errors: list[str] = []
    all_warnings: list[str] = []

    # Check 1: settings.json <-> EXPECTED_HOOK_BEHAVIORS coverage
    print("1. Checking hook coverage (settings.json ↔ EXPECTED_HOOK_BEHAVIORS)...")
    coverage_errors = check_hook_coverage(settings_path)
    all_errors.extend(coverage_errors)
    if not coverage_errors:
        settings_hooks = extract_hook_names_from_settings(settings_path)
        print(f"   ✅ {len(settings_hooks)} hooks in settings.json are all defined")

    # Check 2: Phase expected_hooks -> EXPECTED_HOOK_BEHAVIORS
    print("2. Checking phase hook references...")
    phase_errors = check_phase_hooks()
    all_errors.extend(phase_errors)
    if not phase_errors:
        total_phase_hooks = sum(len(p.expected_hooks) for p in DEVELOPMENT_PHASES)
        print(f"   ✅ {total_phase_hooks} phase hook references are all valid")

    # Check 3: Hook phase_id consistency
    print("3. Checking hook-phase consistency...")
    consistency_warnings = check_hook_phase_consistency()
    all_warnings.extend(consistency_warnings)
    if not consistency_warnings:
        print(f"   ✅ All {len(EXPECTED_HOOK_BEHAVIORS)} hook phase_ids are consistent")

    print("")

    # Report errors
    if all_errors:
        print("❌ Errors detected:")
        for error in all_errors:
            print(f"   - {error}")
        print("")

    # Report warnings
    if all_warnings:
        print("⚠️  Warnings:")
        for warning in all_warnings:
            print(f"   - {warning}")
        print("")

    # Summary
    if all_errors:
        print("❌ Workflow definition check FAILED")
        print("")
        print("To fix:")
        print("  1. Add missing hooks to EXPECTED_HOOK_BEHAVIORS in flow_definitions.py")
        print("  2. Or remove unused hooks from settings.json")
        print("  3. Or update phase expected_hooks to match defined hooks")
        return 1

    print("✅ Workflow definition check PASSED")
    print(f"   - {len(EXPECTED_HOOK_BEHAVIORS)} hooks defined")
    print(f"   - {len(DEVELOPMENT_PHASES)} phases defined")
    return 0


if __name__ == "__main__":
    sys.exit(main())
