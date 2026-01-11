#!/usr/bin/env python3
"""フックのlib/依存関係を分析しMermaid図を生成する。

Why:
    フック間の依存関係を可視化し、
    リファクタリングの影響範囲を把握するため。

What:
    - extract_imports(): Pythonファイルからlib.*インポートを抽出
    - analyze_dependencies(): 全フックの依存関係を分析
    - generate_mermaid(): Mermaid図を生成

Remarks:
    - --output オプションでファイル出力
    - lib/__init__.pyは除外される

Changelog:
    - silenvx/dekita#1337: フック依存関係分析機能を追加
"""

import argparse
import re
from pathlib import Path


def get_hooks_dir() -> Path:
    """Get the hooks directory path."""
    script_dir = Path(__file__).parent
    return script_dir.parent / "hooks"


def extract_imports(file_path: Path) -> set[str]:
    """Extract lib.* imports from a Python file."""
    imports = set()
    try:
        content = file_path.read_text(encoding="utf-8")
        # Match: from lib.xxx import ...
        for match in re.finditer(r"from lib\.(\w+)", content):
            imports.add(match.group(1))
        # Match: import lib.xxx
        for match in re.finditer(r"import lib\.(\w+)", content):
            imports.add(match.group(1))
    except Exception:
        pass  # Skip files that cannot be read (encoding errors, permission issues, etc.)
    return imports


def analyze_dependencies() -> dict[str, dict]:
    """Analyze all hook files and their dependencies."""
    hooks_dir = get_hooks_dir()
    lib_dir = hooks_dir / "lib"

    # Get all lib modules
    lib_modules = {}
    for py_file in lib_dir.glob("*.py"):
        if py_file.name != "__init__.py":
            module_name = py_file.stem
            try:
                content = py_file.read_text(encoding="utf-8")
                line_count = len(content.splitlines())
            except (OSError, UnicodeError):
                line_count = 0
            lib_modules[module_name] = {"line_count": line_count, "dependents": []}

    # Get all hook files
    hook_files = list(hooks_dir.glob("*.py"))
    total_hooks = len(hook_files)

    # Analyze each hook
    for hook_file in hook_files:
        imports = extract_imports(hook_file)
        for module in imports:
            if module in lib_modules:
                lib_modules[module]["dependents"].append(hook_file.stem)

    # Calculate statistics
    for _module, info in lib_modules.items():
        info["dependent_count"] = len(info["dependents"])
        info["percentage"] = (
            round(info["dependent_count"] / total_hooks * 100) if total_hooks > 0 else 0
        )

    return {
        "total_hooks": total_hooks,
        "modules": lib_modules,
    }


def generate_mermaid(data: dict) -> str:
    """Generate Mermaid diagram from dependency data."""
    modules = data["modules"]
    total = data["total_hooks"]

    # Sort by dependency count
    sorted_modules = sorted(modules.items(), key=lambda x: x[1]["dependent_count"], reverse=True)

    # Categorize modules
    core_modules = [(m, d) for m, d in sorted_modules if d["percentage"] >= 50]
    support_modules = [(m, d) for m, d in sorted_modules if 10 <= d["percentage"] < 50]
    utility_modules = [(m, d) for m, d in sorted_modules if d["percentage"] < 10]

    lines = [
        "```mermaid",
        "graph TD",
        f'    subgraph "Hooks ({total}個)"',
        "        H[hooks/*.py]",
        "    end",
        "",
    ]

    if core_modules:
        lines.append('    subgraph "Core Libraries (50%+依存)"')
        for module, info in core_modules:
            pct = info["percentage"]
            lines.append(f"        {module.upper()}[{module}.py<br/>{pct}%依存]")
        lines.append("    end")
        lines.append("")

    if support_modules:
        lines.append('    subgraph "Support Libraries (10-50%依存)"')
        for module, info in support_modules:
            pct = info["percentage"]
            lines.append(f"        {module.upper()}[{module}.py<br/>{pct}%依存]")
        lines.append("    end")
        lines.append("")

    if utility_modules:
        lines.append('    subgraph "Utility Libraries (<10%依存)"')
        for module, info in utility_modules:
            pct = info["percentage"]
            lines.append(f"        {module.upper()}[{module}.py<br/>{pct}%依存]")
        lines.append("    end")
        lines.append("")

    # Add edges
    for module, info in sorted_modules:
        if info["dependent_count"] > 0:
            lines.append(f"    H --> {module.upper()}")

    lines.append("```")
    return "\n".join(lines)


def print_statistics(data: dict) -> None:
    """Print dependency statistics."""
    modules = data["modules"]
    total = data["total_hooks"]

    print(f"\n## 依存関係統計 (全{total}個のhook)\n")
    print("| モジュール | 依存hook数 | 割合 | 行数 |")
    print("| ---------- | ---------- | ---- | ---- |")

    sorted_modules = sorted(modules.items(), key=lambda x: x[1]["dependent_count"], reverse=True)

    for module, info in sorted_modules:
        count = info["dependent_count"]
        pct = info["percentage"]
        lines = info["line_count"]
        print(f"| {module}.py | {count}/{total} | {pct}% | {lines} |")


def main():
    parser = argparse.ArgumentParser(description="Analyze hook dependencies")
    parser.add_argument("--output", "-o", help="Output file for Mermaid diagram")
    parser.add_argument("--stats-only", action="store_true", help="Print statistics only")
    args = parser.parse_args()

    data = analyze_dependencies()

    if args.stats_only:
        print_statistics(data)
        return

    print_statistics(data)
    print("\n## 依存関係図\n")

    mermaid = generate_mermaid(data)
    if args.output:
        Path(args.output).write_text(mermaid, encoding="utf-8")
        print(f"Mermaid diagram written to {args.output}")
    else:
        print(mermaid)


if __name__ == "__main__":
    main()
