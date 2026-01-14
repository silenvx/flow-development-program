#!/usr/bin/env python3
"""開発フローファイルからインデックスを自動生成し、.fdp/に出力する。

Why:
    FDPの移植性向上のため、各プロジェクトが自己完結したドキュメントを生成する。
    docstring/Markdown/YAMLから自動的にインデックス情報を抽出し、
    フロー図やカタログも同時に生成する。

What:
    1. hooks/*.py - docstringからWhy/What/Remarksを抽出
    2. scripts/*.py - docstringからWhy/What/Remarksを抽出
    3. skills/*/SKILL.md - Markdownから説明を抽出
    4. flow_definitions.py - 開発フェーズ情報を抽出
    5. .fdp/に以下を出力:
       - index.json（機械処理用）
       - README.md（機能カタログ）
       - flows.md（Mermaidフロー図）
       - prompts/（生成・参照用プロンプト）

Remarks:
    - ASTでPython docstringを抽出
    - 正規表現でMarkdownセクションを抽出
    - settings.jsonからhookトリガー情報を追加
    - flow_definitions.pyからDEVELOPMENT_PHASESを抽出

Changelog:
    - silenvx/dekita#2771: .fdp/出力、phases抽出、README/flows/prompts生成
    - silenvx/dekita#2765: hook_type, keywordsフィールド追加
    - silenvx/dekita#2762: 全開発フローファイル対応、metadata.json依存削除
"""

import argparse
import ast
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# プロジェクトルート
PROJECT_ROOT = Path(__file__).parent.parent.parent
CLAUDE_DIR = PROJECT_ROOT / ".claude"
HOOKS_DIR = CLAUDE_DIR / "hooks"
SCRIPTS_DIR = CLAUDE_DIR / "scripts"
SKILLS_DIR = CLAUDE_DIR / "skills"
FDP_DIR = PROJECT_ROOT / ".fdp"
INDEX_PATH = FDP_DIR / "index.json"
SETTINGS_PATH = CLAUDE_DIR / "settings.json"

# flow_definitions.pyをインポートするためにパスを追加
sys.path.insert(0, str(HOOKS_DIR))


def load_json(path: Path) -> dict[str, Any]:
    """JSONファイルを読み込む。"""
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_json(path: Path, data: dict[str, Any]) -> None:
    """JSONファイルを保存する。"""
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def extract_python_docstring(file_path: Path) -> str | None:
    """Pythonファイルからモジュールレベルのdocstringを抽出する。"""
    try:
        content = file_path.read_text(encoding="utf-8")
        tree = ast.parse(content)
        return ast.get_docstring(tree)
    except (SyntaxError, UnicodeDecodeError, FileNotFoundError):
        return None


def parse_docstring_sections(docstring: str) -> dict[str, Any]:
    """docstringからWhy/What/Remarks/Tagsセクションを抽出する。

    Tagsセクションは key: value 形式でパースされ、辞書として返される。
    """
    result: dict[str, Any] = {}

    if not docstring:
        return result

    lines = docstring.split("\n")

    # サマリー（最初の非空行）
    for line in lines:
        stripped = line.strip()
        if stripped:
            result["summary"] = stripped
            break

    # セクションを抽出
    current_section: str | None = None
    section_content: list[str] = []
    base_indent: int | None = None

    def store_section() -> None:
        """現在のセクションを result に格納する。"""
        if not current_section or not section_content:
            return
        content = "\n".join(section_content).strip()
        if current_section == "Tags":
            result["tags"] = _parse_tags(content)
        else:
            result[current_section.lower()] = content

    for line in lines:
        section_match = re.match(r"^(\s*)(Why|What|Remarks|Changelog|Tags)\s*:\s*$", line)
        if section_match:
            store_section()
            current_section = section_match.group(2)
            section_content = []
            base_indent = None
            continue

        if current_section:
            if base_indent is None and line.strip():
                base_indent = len(line) - len(line.lstrip())
            if not line.strip():
                section_content.append("")
            elif base_indent is not None:
                if line.startswith(" " * base_indent):
                    section_content.append(line[base_indent:])
                else:
                    section_content.append(line.strip())

    store_section()

    return result


def _parse_tags(content: str) -> dict[str, str]:
    """Tags セクションの内容を key: value 形式でパースする。"""
    tags: dict[str, str] = {}
    for line in content.split("\n"):
        line = line.strip()
        if ":" in line:
            key, value = line.split(":", 1)
            tags[key.strip().lower()] = value.strip()
    return tags


def extract_markdown_description(file_path: Path) -> dict[str, str]:
    """Markdownファイルから説明を抽出する。"""
    result: dict[str, str] = {}

    try:
        content = file_path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, FileNotFoundError):
        return result

    lines = content.split("\n")

    # 最初のH1見出しをサマリーとして取得
    for line in lines:
        if line.startswith("# "):
            result["summary"] = line[2:].strip()
            break

    # 最初の段落を説明として取得
    in_paragraph = False
    paragraph_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            if in_paragraph:
                break
            continue
        if stripped:
            in_paragraph = True
            paragraph_lines.append(stripped)
        elif in_paragraph:
            break

    if paragraph_lines:
        result["description"] = " ".join(paragraph_lines)

    return result


def extract_hook_type(tags: dict[str, str] | None = None) -> str:
    """Tags セクションからフックのタイプを抽出する。

    フックは以下のタイプに分類される:
    - blocking: ユーザーアクションをブロックする
    - warning: 警告を表示するが続行可能
    - info: 情報提供のみ
    - logging: ログ記録のみ
    - utility: ユーティリティモジュール（フックではない）
    """
    if not tags or "type" not in tags:
        return "unknown"

    # _parse_tags() で既に小文字化済み
    hook_type = tags["type"]
    valid_types = {"blocking", "warning", "info", "logging", "utility"}
    return hook_type if hook_type in valid_types else "unknown"


def extract_keywords(name: str, summary: str, why: str = "") -> list[str]:
    """フック名とサマリーからキーワードを抽出する。

    キーワードは検索性向上のため使用される。
    """
    keywords: set[str] = set()

    # 名前をスネークケースから分割
    name_parts = name.split("_")
    for part in name_parts:
        if len(part) >= 3:  # 短すぎる単語は除外
            keywords.add(part.lower())

    # サマリーから主要な単語を抽出（日本語対応は限定的）
    # 英単語のみ抽出
    english_words = re.findall(r"[a-zA-Z]{3,}", summary + " " + why)
    for word in english_words:
        keywords.add(word.lower())

    # 除外する一般的な単語
    stopwords = {
        "the",
        "and",
        "for",
        "with",
        "this",
        "that",
        "from",
        "are",
        "was",
        "will",
        "can",
        "not",
        "has",
        "have",
        "been",
        "being",
        "check",
    }
    keywords -= stopwords

    return sorted(keywords)


def get_python_files(directory: Path, exclude_dirs: set[str] | None = None) -> list[Path]:
    """指定ディレクトリのPythonファイル一覧を取得する。"""
    if exclude_dirs is None:
        exclude_dirs = {"tests", "__pycache__", "lib"}

    files = []
    for py_file in directory.glob("*.py"):
        if py_file.parent.name in exclude_dirs:
            continue
        # ユーティリティファイルを除外
        if py_file.stem in (
            "common",
            "check_utils",
            "command_parser",
            "guard_rules",
            "__init__",
        ):
            continue
        files.append(py_file)
    return sorted(files)


def get_hook_trigger(hook_name: str, settings: dict[str, Any]) -> dict[str, Any]:
    """settings.jsonからフックのトリガー情報を取得する。

    複数のイベントタイプに登録されているフックの場合、全てのトリガーを収集する。
    """
    triggers: list[str] = []
    matchers: list[Any] = []

    hooks_config = settings.get("hooks", {})
    for event_type, event_hooks in hooks_config.items():
        if not isinstance(event_hooks, list):
            continue
        for hook_group in event_hooks:
            if not isinstance(hook_group, dict):
                continue
            matcher = hook_group.get("matcher")
            hooks_list = hook_group.get("hooks", [])
            for hook in hooks_list:
                if isinstance(hook, dict):
                    command = hook.get("command", "")
                    if f"/{hook_name}.py" in command:
                        if event_type not in triggers:
                            triggers.append(event_type)
                            matchers.append(matcher)

    # 後方互換性のため、単一トリガーの場合は文字列を返す
    if len(triggers) == 0:
        return {"trigger": None, "matcher": None}
    elif len(triggers) == 1:
        return {"trigger": triggers[0], "matcher": matchers[0]}
    else:
        return {"trigger": triggers, "matcher": matchers}


def process_hooks(settings: dict[str, Any]) -> list[dict[str, Any]]:
    """フックファイルを処理してインデックスエントリを生成する。"""
    entries = []
    hook_files = get_python_files(HOOKS_DIR)

    for file_path in hook_files:
        hook_name = file_path.stem
        rel_path = str(file_path.relative_to(PROJECT_ROOT))

        docstring = extract_python_docstring(file_path)
        sections = parse_docstring_sections(docstring) if docstring else {}
        trigger_info = get_hook_trigger(hook_name, settings)

        remarks = sections.get("remarks", "")
        tags = sections.get("tags", {})
        entry = {
            "name": hook_name,
            "path": rel_path,
            "type": "hook",
            "hook_type": extract_hook_type(tags),
            "summary": sections.get("summary", ""),
            "keywords": extract_keywords(
                hook_name,
                sections.get("summary", ""),
                sections.get("why", ""),
            ),
            "why": sections.get("why", ""),
            "what": sections.get("what", ""),
            "remarks": remarks,
            "trigger": trigger_info["trigger"],
            "matcher": trigger_info["matcher"],
        }
        entries.append(entry)

    return entries


def process_scripts() -> list[dict[str, Any]]:
    """スクリプトファイルを処理してインデックスエントリを生成する。"""
    entries = []

    # Python scripts
    for py_file in get_python_files(SCRIPTS_DIR):
        rel_path = str(py_file.relative_to(PROJECT_ROOT))
        docstring = extract_python_docstring(py_file)
        sections = parse_docstring_sections(docstring) if docstring else {}

        entry = {
            "name": py_file.stem,
            "path": rel_path,
            "type": "script",
            "language": "python",
            "summary": sections.get("summary", ""),
            "why": sections.get("why", ""),
            "what": sections.get("what", ""),
            "remarks": sections.get("remarks", ""),
        }
        entries.append(entry)

    # Shell scripts
    for sh_file in sorted(SCRIPTS_DIR.glob("*.sh")):
        rel_path = str(sh_file.relative_to(PROJECT_ROOT))
        # Shell scriptは最初のコメントブロックから説明を抽出
        try:
            content = sh_file.read_text(encoding="utf-8")
            lines = content.split("\n")
            description_lines = []
            for line in lines[1:]:  # shebang をスキップ
                if line.startswith("#"):
                    description_lines.append(line[1:].strip())
                elif line.strip():
                    break

            entry = {
                "name": sh_file.stem,
                "path": rel_path,
                "type": "script",
                "language": "shell",
                "summary": description_lines[0] if description_lines else "",
                "description": " ".join(description_lines) if description_lines else "",
            }
            entries.append(entry)
        except (UnicodeDecodeError, FileNotFoundError):
            continue

    return entries


def process_skills() -> list[dict[str, Any]]:
    """スキルファイルを処理してインデックスエントリを生成する。"""
    entries = []

    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue

        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            continue

        rel_path = str(skill_file.relative_to(PROJECT_ROOT))
        md_info = extract_markdown_description(skill_file)

        entry = {
            "name": skill_dir.name,
            "path": rel_path,
            "type": "skill",
            "summary": md_info.get("summary", skill_dir.name),
            "description": md_info.get("description", ""),
        }
        entries.append(entry)

    return entries


def process_docs() -> list[dict[str, Any]]:
    """ドキュメントファイルを処理してインデックスエントリを生成する。"""
    entries = []

    doc_files = [
        PROJECT_ROOT / "AGENTS.md",
        PROJECT_ROOT / "lefthook.yml",
    ]

    for doc_file in doc_files:
        if not doc_file.exists():
            continue

        rel_path = str(doc_file.relative_to(PROJECT_ROOT))

        if doc_file.suffix == ".md":
            md_info = extract_markdown_description(doc_file)
            entry = {
                "name": doc_file.stem,
                "path": rel_path,
                "type": "doc",
                "format": "markdown",
                "summary": md_info.get("summary", doc_file.stem),
                "description": md_info.get("description", ""),
            }
        elif doc_file.suffix == ".yml":
            entry = {
                "name": doc_file.stem,
                "path": rel_path,
                "type": "config",
                "format": "yaml",
                "summary": "Git hooks configuration (lefthook)",
            }
        else:
            continue

        entries.append(entry)

    return entries


def process_phases() -> list[dict[str, Any]]:
    """flow_definitions.pyからDEVELOPMENT_PHASESを抽出する。"""
    try:
        from flow_definitions import DEVELOPMENT_PHASES
    except ImportError:
        return []

    phases = []
    for phase in DEVELOPMENT_PHASES:
        phases.append(
            {
                "id": phase.id,
                "name": phase.name,
                "description": phase.description,
                "order": phase.order,
                "expected_hooks": phase.expected_hooks,
                "trigger_step": phase.trigger_step,
                "completion_step": phase.completion_step,
            }
        )

    return phases


def generate_readme(index: dict[str, Any]) -> str:
    """index.jsonから機能カタログ（README.md）を生成する。"""
    generated_at = index.get("generated_at", "")[:10]  # YYYY-MM-DD
    project = index.get("project", "unknown")

    lines = [
        f"# {project} 機能カタログ",
        "",
        f"生成日時: {generated_at}",
        "",
        "## 概要",
        "",
        "このプロジェクトの開発フロー構成要素。",
        "",
        "## 統計",
        "",
        "| カテゴリ | 数 |",
        "|---------|---|",
        f"| フック | {len(index.get('hooks', []))} |",
        f"| スクリプト | {len(index.get('scripts', []))} |",
        f"| スキル | {len(index.get('skills', []))} |",
        f"| フェーズ | {len(index.get('phases', []))} |",
        "",
        "---",
        "",
        f"## フック一覧（全{len(index.get('hooks', []))}件）",
        "",
        "| フック | 説明 |",
        "|-------|------|",
    ]

    for hook in index.get("hooks", []):
        name = hook.get("name", "")
        summary = hook.get("summary", "").replace("\n", " ")
        lines.append(f"| `{name}` | {summary or '（説明なし）'} |")

    lines.extend(
        [
            "",
            "---",
            "",
            f"## スクリプト一覧（全{len(index.get('scripts', []))}件）",
            "",
            "| スクリプト | 説明 |",
            "|----------|------|",
        ]
    )

    for script in index.get("scripts", []):
        name = script.get("name", "")
        summary = script.get("summary", "").replace("\n", " ")
        lines.append(f"| `{name}` | {summary or '（説明なし）'} |")

    lines.extend(
        [
            "",
            "---",
            "",
            f"## スキル一覧（全{len(index.get('skills', []))}件）",
            "",
            "| スキル | 説明 |",
            "|-------|------|",
        ]
    )

    for skill in index.get("skills", []):
        name = skill.get("name", "")
        summary = skill.get("summary", "").replace("\n", " ")
        lines.append(f"| `{name}` | {summary or '（説明なし）'} |")

    lines.extend(
        [
            "",
            "---",
            "",
            "## 詳細情報",
            "",
            "各フックの詳細（Why/What/keywords）は `index.json` を参照:",
            "",
            "```bash",
            "jq '.hooks[] | select(.name == \"merge_check\")' .fdp/index.json",
            "```",
        ]
    )

    return "\n".join(lines) + "\n"


def generate_flows(index: dict[str, Any]) -> str:
    """index.jsonのphasesから開発フロー図（flows.md）を生成する。"""
    project = index.get("project", "unknown")
    phases = index.get("phases", [])

    lines = [
        f"# {project} 開発フロー図",
        "",
        "## 開発ワークフロー全体像",
        "",
        "```mermaid",
        "flowchart TD",
    ]

    # フェーズノードを生成
    for i, phase in enumerate(phases):
        phase_id = phase.get("id", f"phase_{i}")
        phase_name = phase.get("name", phase_id)
        hook_count = len(phase.get("expected_hooks", []))

        # ノード形状を決定
        if i == 0:
            lines.append(f"    {phase_id}([{phase_name}])")
        elif i == len(phases) - 1:
            lines.append(f"    {phase_id}([{phase_name}])")
        else:
            lines.append(f"    {phase_id}[{phase_name}<br/>{hook_count}フック]")

    lines.append("")

    # 接続を生成
    for i in range(len(phases) - 1):
        current = phases[i].get("id", f"phase_{i}")
        next_phase = phases[i + 1].get("id", f"phase_{i + 1}")
        lines.append(f"    {current} --> {next_phase}")

    lines.extend(
        [
            "```",
            "",
            "---",
            "",
            "## フェーズ詳細",
            "",
        ]
    )

    # 各フェーズの詳細
    for phase in phases:
        phase_id = phase.get("id", "")
        phase_name = phase.get("name", phase_id)
        description = phase.get("description", "")
        expected_hooks = phase.get("expected_hooks", [])

        lines.extend(
            [
                f"### {phase_name}",
                "",
                f"{description}",
                "",
                "**期待されるフック:**",
                "",
            ]
        )

        for hook in expected_hooks:
            lines.append(f"- `{hook}`")

        lines.append("")

    return "\n".join(lines) + "\n"


def generate_prompts() -> dict[str, str]:
    """プロンプトファイルの内容を生成する。"""
    prompts = {}

    # generate-index.md
    prompts["generate-index.md"] = """# インデックス再生成

.fdp/のインデックスとドキュメントを再生成する。

---

## 使い方

```bash
python3 .claude/scripts/generate_index.py
```

---

## 出力

| ファイル | 内容 |
|---------|------|
| `.fdp/index.json` | 機械処理用インデックス |
| `.fdp/README.md` | 機能カタログ |
| `.fdp/flows.md` | Mermaidフロー図 |

---

## オプション

```bash
# 詳細出力
python3 .claude/scripts/generate_index.py --verbose

# ドライラン（ファイル出力なし）
python3 .claude/scripts/generate_index.py --dry-run
```
"""

    # import-pattern.md
    prompts["import-pattern.md"] = """# パターン参照・移植ガイド

このプロジェクトから開発フローパターンを参照・移植する方法。

---

## 1. パターン検索

```bash
# キーワードで検索
jq '.hooks[] | select(.keywords[] | contains("worktree"))' .fdp/index.json

# フックタイプで検索
jq '.hooks[] | select(.hook_type == "blocking")' .fdp/index.json

# サマリーで検索
jq '.hooks[] | select(.summary | contains("PR"))' .fdp/index.json
```

---

## 2. パターン理解

```bash
# 詳細を確認
jq '.hooks[] | select(.name == "merge_check")' .fdp/index.json
```

**確認ポイント:**
- `why`: なぜこのフックが必要か
- `what`: 何をするか
- `trigger`: いつ発火するか
- `hook_type`: blocking/warning/info/logging

---

## 3. ソースコード参照

```bash
# pathフィールドからソースコードを確認
cat $(jq -r '.hooks[] | select(.name == "merge_check") | .path' .fdp/index.json)
```

---

## 4. 移植

1. ソースコードをコピー
2. プロジェクト固有の設定を調整（パス、コマンド等）
3. settings.jsonにフックを登録
4. テスト実行
"""

    return prompts


def generate_index(verbose: bool = False) -> dict[str, Any]:
    """全開発フローファイルのインデックスを生成する。"""
    settings = load_json(SETTINGS_PATH)

    index = {
        "version": "3.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "project": "dekita",
        "hooks": [],
        "scripts": [],
        "skills": [],
        "phases": [],
        "docs": [],
    }

    # Hooks
    if verbose:
        print("Processing hooks...")
    index["hooks"] = process_hooks(settings)
    if verbose:
        print(f"  Found {len(index['hooks'])} hooks")

    # Scripts
    if verbose:
        print("Processing scripts...")
    index["scripts"] = process_scripts()
    if verbose:
        print(f"  Found {len(index['scripts'])} scripts")

    # Skills
    if verbose:
        print("Processing skills...")
    index["skills"] = process_skills()
    if verbose:
        print(f"  Found {len(index['skills'])} skills")

    # Phases
    if verbose:
        print("Processing phases...")
    index["phases"] = process_phases()
    if verbose:
        print(f"  Found {len(index['phases'])} phases")

    # Docs
    if verbose:
        print("Processing docs...")
    index["docs"] = process_docs()
    if verbose:
        print(f"  Found {len(index['docs'])} docs")

    return index


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate development flow index and documentation to .fdp/"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print output without writing files",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print detailed progress",
    )
    args = parser.parse_args()

    index = generate_index(verbose=args.verbose)

    # サマリー出力
    total = (
        len(index["hooks"])
        + len(index["scripts"])
        + len(index["skills"])
        + len(index["phases"])
        + len(index["docs"])
    )
    print(f"\nTotal entries: {total}")
    print(f"  Hooks: {len(index['hooks'])}")
    print(f"  Scripts: {len(index['scripts'])}")
    print(f"  Skills: {len(index['skills'])}")
    print(f"  Phases: {len(index['phases'])}")
    print(f"  Docs: {len(index['docs'])}")

    if args.dry_run:
        print("\n(dry-run mode, files not saved)")
        if args.verbose:
            print("\nGenerated index:")
            print(json.dumps(index, indent=2, ensure_ascii=False))
        return

    # .fdp/ディレクトリを作成
    FDP_DIR.mkdir(parents=True, exist_ok=True)
    prompts_dir = FDP_DIR / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)

    # index.jsonを保存
    save_json(INDEX_PATH, index)
    print(f"\nSaved: {INDEX_PATH}")

    # README.mdを生成・保存
    readme_content = generate_readme(index)
    readme_path = FDP_DIR / "README.md"
    readme_path.write_text(readme_content, encoding="utf-8")
    print(f"Saved: {readme_path}")

    # flows.mdを生成・保存
    flows_content = generate_flows(index)
    flows_path = FDP_DIR / "flows.md"
    flows_path.write_text(flows_content, encoding="utf-8")
    print(f"Saved: {flows_path}")

    # プロンプトを生成・保存
    prompts = generate_prompts()
    for name, content in prompts.items():
        prompt_path = prompts_dir / name
        prompt_path.write_text(content, encoding="utf-8")
        print(f"Saved: {prompt_path}")


if __name__ == "__main__":
    main()
