#!/usr/bin/env bun
/**
 * セッション終了時に未完了TODOを検出して警告。
 *
 * Why:
 *   未完了のTODOがIssue化されないままセッションが終了すると、
 *   タスクが忘れられる。セッション終了時に警告することで対応漏れを防ぐ。
 *
 * What:
 *   - セッション終了時（Stop）に発火
 *   - transcriptからTodoWriteツール呼び出しを解析
 *   - 未完了かつIssue参照（#xxx）のないTODOを抽出
 *   - 該当TODOがあれば警告メッセージを表示
 *
 * Remarks:
 *   - 非ブロック型（警告のみ、セッション終了は許可）
 *   - Issue参照があるTODOはスキップ（Issue化済みと判断）
 *   - 5件まで表示し、残りは件数のみ表示
 *   - Python版: session_todo_check.py
 *
 * Changelog:
 *   - silenvx/dekita#1909: フック追加
 *   - silenvx/dekita#1914: パストラバーサル攻撃対策追加
 *   - silenvx/dekita#2986: TypeScript版に移植
 */

import { readFileSync } from "node:fs";
import { logHookExecution } from "../lib/logging";
import { isSafeTranscriptPath } from "../lib/path_validation";
import { parseHookInput } from "../lib/session";

const HOOK_NAME = "session-todo-check";

// Issue参照パターン
export const ISSUE_REFERENCE_PATTERN = /#(\d+)/;

export interface Todo {
  status?: string;
  content?: string;
}

interface ContentBlock {
  type?: string;
  name?: string;
  input?: {
    todos?: Todo[];
  };
}

interface TranscriptEntry {
  role?: string;
  content?: ContentBlock[];
}

/**
 * transcriptから最新のTodoWriteツール呼び出しを抽出.
 */
function extractLatestTodos(transcriptPath: string): Todo[] | null {
  let content: string;
  try {
    content = readFileSync(transcriptPath, "utf-8");
  } catch {
    return null;
  }

  let latestTodos: Todo[] | null = null;

  // JSONLフォーマット（1行1JSON）
  for (const line of content.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;

    try {
      const obj = JSON.parse(trimmed) as TranscriptEntry;
      if (obj && typeof obj === "object" && !Array.isArray(obj) && obj.role === "assistant") {
        const contentBlocks = obj.content;
        if (Array.isArray(contentBlocks)) {
          for (const block of contentBlocks) {
            if (block?.type === "tool_use" && block?.name === "TodoWrite") {
              const todos = block.input?.todos;
              if (Array.isArray(todos) && todos.length > 0) {
                latestTodos = todos;
              }
            }
          }
        }
      }
    } catch {
      // JSON parse error, skip line
    }
  }

  // JSON配列フォーマットの場合
  if (latestTodos === null) {
    try {
      const data = JSON.parse(content) as TranscriptEntry[];
      if (Array.isArray(data)) {
        for (const item of data) {
          if (item?.role === "assistant") {
            const contentBlocks = item.content;
            if (Array.isArray(contentBlocks)) {
              for (const block of contentBlocks) {
                if (block?.type === "tool_use" && block?.name === "TodoWrite") {
                  const todos = block.input?.todos;
                  if (Array.isArray(todos) && todos.length > 0) {
                    latestTodos = todos;
                  }
                }
              }
            }
          }
        }
      }
    } catch {
      // Not JSON array format, ignore
    }
  }

  return latestTodos;
}

/**
 * 未完了かつIssue参照のないTODOを抽出.
 */
export function findIncompleteTodosWithoutIssue(todos: Todo[]): Todo[] {
  const incomplete: Todo[] = [];

  for (const todo of todos) {
    const status = todo.status ?? "";
    const content = todo.content ?? "";

    // completed以外は未完了扱い
    if (status === "completed") {
      continue;
    }

    // Issue参照があるかチェック
    if (ISSUE_REFERENCE_PATTERN.test(content)) {
      continue;
    }

    incomplete.push(todo);
  }

  return incomplete;
}

async function main(): Promise<void> {
  let sessionId: string | undefined;
  const hookInput = await parseHookInput();
  sessionId = hookInput.session_id;

  // Stop hookはtranscript_pathをトップレベルで受け取る
  const transcriptPath = (hookInput.transcript_path as string) ?? "";

  if (!transcriptPath) {
    await logHookExecution(HOOK_NAME, "approve", "No transcript path", undefined, { sessionId });
    console.log(JSON.stringify({ continue: true }));
    return;
  }

  // セキュリティ: パストラバーサル攻撃を防止 (Issue #1914)
  if (!isSafeTranscriptPath(transcriptPath)) {
    await logHookExecution(
      HOOK_NAME,
      "approve",
      `Invalid transcript path: ${transcriptPath}`,
      undefined,
      { sessionId },
    );
    console.log(JSON.stringify({ continue: true }));
    return;
  }

  // TODOを抽出
  const todos = extractLatestTodos(transcriptPath);

  if (!todos) {
    await logHookExecution(HOOK_NAME, "approve", "No todos found", undefined, { sessionId });
    console.log(JSON.stringify({ continue: true }));
    return;
  }

  // 未完了かつIssue参照のないTODOを抽出
  const incomplete = findIncompleteTodosWithoutIssue(todos);

  if (incomplete.length === 0) {
    await logHookExecution(
      HOOK_NAME,
      "approve",
      "All todos completed or have issue refs",
      undefined,
      { sessionId },
    );
    console.log(JSON.stringify({ continue: true }));
    return;
  }

  // 警告メッセージを生成
  const todoListItems = incomplete.slice(0, 5).map((todo) => {
    const status = todo.status ?? "unknown";
    const content = todo.content ?? "(no content)";
    return `  - [${status}] ${content}`;
  });

  let todoList = todoListItems.join("\n");
  if (incomplete.length > 5) {
    todoList += `\n  ... 他 ${incomplete.length - 5} 件`;
  }

  const warningLines = [
    "⚠️ 未完了のTODOがあります:",
    "",
    todoList,
    "",
    "未完了タスクがある場合:",
    "  1. 対応するIssueを作成 (`gh issue create`)",
    "  2. または、TODOの内容にIssue番号を含める (例: `#1234の実装`)",
    "",
    "Issue化しておかないと、セッション終了後に忘れられる可能性があります。",
  ];
  const warningMsg = warningLines.join("\n");

  await logHookExecution(
    HOOK_NAME,
    "warn",
    `Incomplete todos without issue ref: ${incomplete.length}`,
    undefined,
    { sessionId },
  );
  console.log(
    JSON.stringify({
      continue: true,
      message: warningMsg,
    }),
  );
}

if (import.meta.main) {
  main();
}
