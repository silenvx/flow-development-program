import { describe, expect, test } from "bun:test";
import { extractVerificationItems, hasPlaceholder } from "./main";

describe("extractVerificationItems", () => {
  test("returns empty array for empty body", () => {
    expect(extractVerificationItems("")).toEqual([]);
  });

  test("returns empty array when no verification section exists", () => {
    const body = "## Summary\nSome text\n## Details\nMore text";
    expect(extractVerificationItems(body)).toEqual([]);
  });

  test("extracts Claude command items from 確認コマンド section", () => {
    const body = `## 確認コマンド
- [ ] [Claude] \`pnpm build\` - ビルドが成功する
- [ ] [Claude] \`pnpm test:ci\` - テストがパスする`;
    const items = extractVerificationItems(body);
    expect(items).toHaveLength(2);
    expect(items[0]).toEqual({
      command: "pnpm build",
      description: "ビルドが成功する",
      isHumanOnly: false,
      isChecked: false,
    });
    expect(items[1]).toEqual({
      command: "pnpm test:ci",
      description: "テストがパスする",
      isHumanOnly: false,
      isChecked: false,
    });
  });

  test("extracts human-only items", () => {
    const body = `## 確認コマンド
- [ ] [人間] ブラウザで動作確認`;
    const items = extractVerificationItems(body);
    expect(items).toHaveLength(1);
    expect(items[0]).toEqual({
      command: "",
      description: "ブラウザで動作確認",
      isHumanOnly: true,
      isChecked: false,
    });
  });

  test("handles [human] tag (English)", () => {
    const body = `## 確認コマンド
- [ ] [human] Check in browser`;
    const items = extractVerificationItems(body);
    expect(items).toHaveLength(1);
    expect(items[0].isHumanOnly).toBe(true);
  });

  test("detects checked items with lowercase x", () => {
    const body = `## 確認コマンド
- [x] [Claude] \`pnpm build\` - ビルドが成功する`;
    const items = extractVerificationItems(body);
    expect(items).toHaveLength(1);
    expect(items[0].isChecked).toBe(true);
  });

  test("detects checked items with uppercase X", () => {
    const body = `## 確認コマンド
- [X] [Claude] \`pnpm build\` - ビルドが成功する`;
    const items = extractVerificationItems(body);
    expect(items).toHaveLength(1);
    expect(items[0].isChecked).toBe(true);
  });

  test("detects checked human items", () => {
    const body = `## 確認コマンド
- [x] [人間] ブラウザで確認済み`;
    const items = extractVerificationItems(body);
    expect(items).toHaveLength(1);
    expect(items[0].isChecked).toBe(true);
    expect(items[0].isHumanOnly).toBe(true);
  });

  test("defaults to Claude tag when no tag specified", () => {
    const body = `## 確認コマンド
- [ ] \`pnpm typecheck\` - 型チェックが通る`;
    const items = extractVerificationItems(body);
    expect(items).toHaveLength(1);
    expect(items[0].isHumanOnly).toBe(false);
    expect(items[0].command).toBe("pnpm typecheck");
  });

  test("handles 確認手順 section name", () => {
    const body = `## 確認手順
- [ ] [Claude] \`pnpm lint\` - Lintが通る`;
    const items = extractVerificationItems(body);
    expect(items).toHaveLength(1);
  });

  test("handles 検証 section name", () => {
    const body = `## 検証
- [ ] [Claude] \`pnpm build\` - ビルド成功`;
    const items = extractVerificationItems(body);
    expect(items).toHaveLength(1);
  });

  test("stops at next section boundary", () => {
    const body = `## 確認コマンド
- [ ] [Claude] \`pnpm build\` - ビルド

## その他
Some other content`;
    const items = extractVerificationItems(body);
    expect(items).toHaveLength(1);
  });

  test("handles whitespace in tag brackets", () => {
    const body = `## 確認コマンド
- [ ] [ Human ] \`echo test\` - テスト`;
    const items = extractVerificationItems(body);
    expect(items).toHaveLength(1);
    expect(items[0].isHumanOnly).toBe(true);
  });

  test("handles mixed Claude and human items", () => {
    const body = `## 確認コマンド
- [ ] [Claude] \`pnpm build\` - ビルド
- [ ] [人間] ブラウザ確認
- [x] [Claude] \`pnpm test:ci\` - テスト`;
    const items = extractVerificationItems(body);
    expect(items).toHaveLength(3);
    expect(items[0].isHumanOnly).toBe(false);
    expect(items[0].isChecked).toBe(false);
    expect(items[1].isHumanOnly).toBe(true);
    expect(items[2].isHumanOnly).toBe(false);
    expect(items[2].isChecked).toBe(true);
  });
});

describe("hasPlaceholder", () => {
  test("returns true for command with Japanese placeholder", () => {
    expect(hasPlaceholder("grep <フック名> file.txt")).toBe(true);
  });

  test("returns true for command with English placeholder", () => {
    expect(hasPlaceholder("bun run .claude/scripts/<script_name>/main.ts")).toBe(true);
  });

  test("returns true for command with multiple placeholders", () => {
    expect(hasPlaceholder("cat <file> | grep <pattern>")).toBe(true);
  });

  test("returns true for Issue #4020 example commands", () => {
    // From Issue #4020
    expect(
      hasPlaceholder(
        "cat .claude/logs/execution/hook-execution-*.jsonl | grep <フック名> | tail -5",
      ),
    ).toBe(true);
    expect(hasPlaceholder("bun run .claude/scripts/<スクリプト名>/main.ts --help")).toBe(true);
  });

  test("returns false for command without placeholders", () => {
    expect(hasPlaceholder("pnpm build")).toBe(false);
    expect(hasPlaceholder("git status")).toBe(false);
    expect(hasPlaceholder("echo 'hello world'")).toBe(false);
  });

  test("returns false for commands with shell redirects (not placeholders)", () => {
    // These use < for shell redirect, not as placeholder markers
    // The pattern <[^>\s]+> disallows whitespace to avoid matching redirection patterns
    expect(hasPlaceholder("cat < input.txt")).toBe(false);
    expect(hasPlaceholder("cat < input.txt > output.txt")).toBe(false);
    expect(hasPlaceholder("sort < data.txt > sorted.txt")).toBe(false);
  });

  test("returns false for empty command", () => {
    expect(hasPlaceholder("")).toBe(false);
  });

  test("returns false for comparison operators in commands", () => {
    // < and > used as comparison, not as placeholder delimiters
    expect(hasPlaceholder("test $a -lt $b")).toBe(false);
    expect(hasPlaceholder('echo "1 < 2"')).toBe(false);
  });
});
