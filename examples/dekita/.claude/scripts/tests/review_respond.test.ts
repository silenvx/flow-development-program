/**
 * Tests for review_respond.ts
 *
 * Issue #3625: Add tests for TypeScript review_respond and record_review_response
 */

import { describe, expect, test } from "bun:test";
import { formatVerifiedMessage } from "../review_respond";

describe("formatVerifiedMessage", () => {
  describe("fix message prefix handling", () => {
    test("adds prefix when fix message has no prefix", () => {
      const result = formatVerifiedMessage("処理順序修正", "file.py:10-20");
      expect(result).toContain("修正済み: 処理順序修正");
    });

    test("preserves prefix when fix message already has prefix", () => {
      const result = formatVerifiedMessage("修正済み: 処理順序修正", "file.py:10-20");
      expect(result).toContain("修正済み: 処理順序修正");
      // Should not have double prefix
      expect(result).not.toContain("修正済み: 修正済み:");
    });

    test("normalizes prefix when fix message has prefix without space", () => {
      const result = formatVerifiedMessage("修正済み:処理順序修正", "file.py:10-20");
      expect(result).toContain("修正済み: 処理順序修正");
      // Should not have double prefix
      expect(result).not.toContain("修正済み: 修正済み:");
    });
  });

  describe("verify message prefix handling", () => {
    test("adds prefix when verify message has no prefix", () => {
      const result = formatVerifiedMessage("処理順序修正", "file.py:10-20");
      expect(result).toContain("Verified: file.py:10-20");
    });

    test("preserves prefix when verify message already has prefix", () => {
      const result = formatVerifiedMessage("処理順序修正", "Verified: file.py:10-20");
      expect(result).toContain("Verified: file.py:10-20");
      // Should not have double prefix
      expect(result).not.toContain("Verified: Verified:");
    });

    test("normalizes prefix when verify message has prefix without space", () => {
      const result = formatVerifiedMessage("処理順序修正", "Verified:file.py:10-20");
      expect(result).toContain("Verified: file.py:10-20");
      // Should not have double prefix
      expect(result).not.toContain("Verified: Verified:");
    });

    test("handles case-insensitive Verified prefix", () => {
      const result = formatVerifiedMessage("fix", "verified: file.py:10-20");
      expect(result).toContain("Verified: file.py:10-20");
      expect(result).not.toContain("Verified: verified:");
    });
  });

  describe("output format", () => {
    test("separates fix and verify messages with double newline", () => {
      const result = formatVerifiedMessage("fix content", "verify content");
      expect(result).toBe("修正済み: fix content\n\nVerified: verify content");
    });

    test("handles empty fix message", () => {
      const result = formatVerifiedMessage("", "verify content");
      expect(result).toBe("修正済み: \n\nVerified: verify content");
    });

    test("handles empty verify message", () => {
      const result = formatVerifiedMessage("fix content", "");
      expect(result).toBe("修正済み: fix content\n\nVerified: ");
    });

    test("handles both empty messages", () => {
      const result = formatVerifiedMessage("", "");
      expect(result).toBe("修正済み: \n\nVerified: ");
    });
  });

  describe("multiline content", () => {
    test("handles multiline fix message", () => {
      const result = formatVerifiedMessage("line1\nline2", "verify");
      expect(result).toContain("修正済み: line1\nline2");
    });

    test("handles multiline verify message", () => {
      const result = formatVerifiedMessage("fix", "line1\nline2");
      expect(result).toContain("Verified: line1\nline2");
    });
  });

  describe("special characters", () => {
    test("handles Japanese characters", () => {
      const result = formatVerifiedMessage("変更を適用しました", "テストファイル");
      expect(result).toContain("修正済み: 変更を適用しました");
      expect(result).toContain("Verified: テストファイル");
    });

    test("handles file paths with colons", () => {
      const result = formatVerifiedMessage("fix", "src/file.ts:123");
      expect(result).toContain("Verified: src/file.ts:123");
    });

    test("handles line ranges", () => {
      const result = formatVerifiedMessage("fix", "file.py:10-20");
      expect(result).toContain("Verified: file.py:10-20");
    });
  });
});
