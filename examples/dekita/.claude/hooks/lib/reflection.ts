/**
 * Reflection-related utilities for hooks.
 *
 * Why:
 *   Extract common reflection-checking functions from
 *   immediate-pending-check and reflection-completion-check.
 *
 * What:
 *   - checkTranscriptForReflection: Check for reflection keywords (五省, etc.)
 *   - checkSkillInvocation: Check if /reflecting-sessions skill was invoked
 *   - isValidImmediateAction: Validate [IMMEDIATE] action whitelist
 *   - extractImmediateTags: Extract [IMMEDIATE: action] tags
 *   - checkImmediateActionExecuted: Verify action execution
 *
 * Remarks:
 *   - Python版: lib/reflection.py
 *
 * Changelog:
 *   - silenvx/dekita#2694: Python版作成
 *   - silenvx/dekita#2917: TypeScript版初期実装
 */

// Whitelist of allowed [IMMEDIATE] actions
// Only these specific commands are recognized to prevent false positives
// from test examples, documentation, and code fragments in transcripts.
const ALLOWED_IMMEDIATE_ACTIONS: Set<string> = new Set(["/reflecting-sessions"]);

/**
 * Check the conversation transcript for reflection indicators.
 *
 * Returns true if reflection keywords (五省, 振り返り, etc.) are found.
 *
 * Issue #2708: Performance optimization - combine patterns with | and search once
 * instead of looping through each pattern.
 */
export function checkTranscriptForReflection(transcriptContent: string): boolean {
  // Combine all patterns with | for single regex search (performance optimization)
  // ReDoS mitigation: replace .* with negated character class and length limit
  const reflectionPattern = new RegExp(
    [
      "五省",
      "振り返り",
      "反省点",
      "改善点",
      "教訓",
      // Issue #2708: ReDoS mitigation - use [^X]{0,512} instead of .*
      "要件理解[^悖]{0,512}悖",
      "実装[^恥]{0,512}恥",
      "検証[^欠]{0,512}欠",
      "対応[^憾]{0,512}憾",
      "効率[^欠]{0,512}欠",
    ].join("|"),
  );

  return reflectionPattern.test(transcriptContent);
}

/**
 * Check if /reflecting-sessions skill was invoked in the session.
 *
 * Issue #2140: Detect when the reflect skill is invoked so that
 * reflection completion can be enforced even without PR merge.
 *
 * Issue #2489: Exclude [IMMEDIATE: /reflecting-sessions] tags from detection.
 * The IMMEDIATE tag is issued by the hook system, not by actual skill invocation.
 *
 * Returns true if skill invocation patterns are found.
 */
export function checkSkillInvocation(transcriptContent: string): boolean {
  // Issue #2489: Remove [IMMEDIATE: ...] tags before checking
  // These are hook-issued tags, not actual skill invocations
  // ReDoS mitigation: limit content length to 256 characters
  const cleanedContent = transcriptContent.replace(/\[IMMEDIATE:\s*[^\]]{1,256}\]/gi, "");

  // Performance: combine patterns with | and search once instead of looping
  const skillPattern = new RegExp(
    [
      "Skill: reflecting-sessions", // Skill tool invocation
      "@\\.claude/skills/reflecting-sessions/SKILL\\.md", // Direct skill reference
      "/reflecting-sessions\\b", // Slash command
      // Issue #2707: ReDoS mitigation - use [^)]* instead of .* to prevent
      // catastrophic backtracking
      "Skill\\([^)]*reflecting-sessions[^)]*\\)", // Skill tool call syntax
    ].join("|"),
    "i",
  );

  return skillPattern.test(cleanedContent);
}

/**
 * Validate that an extracted action is an allowed command.
 *
 * Issue #2193: The regex pattern can match code examples in the transcript,
 * such as pattern definitions or test strings. This function filters out
 * such false positives.
 *
 * Issue #2201: Restricted to slash commands only.
 *
 * Issue #2209: Further restricted to explicit whitelist to prevent false
 * positives from test examples like [IMMEDIATE: /test] or [IMMEDIATE: /commit].
 *
 * Valid actions:
 * - Only commands in ALLOWED_IMMEDIATE_ACTIONS whitelist
 * - Currently only /reflecting-sessions is allowed
 *
 * @param action - The extracted action string
 * @returns True if the action is in the allowed whitelist
 */
export function isValidImmediateAction(action: string): boolean {
  const normalizedAction = action.trim().toLowerCase();
  return ALLOWED_IMMEDIATE_ACTIONS.has(normalizedAction);
}

/**
 * Extract [IMMEDIATE: action] tags from transcript.
 *
 * Issue #2186: Detect [IMMEDIATE: /reflecting-sessions] or similar tags that require
 * immediate execution without user confirmation.
 *
 * Issue #2193: Validates extracted actions to filter out code fragments
 * that accidentally match the pattern.
 *
 * Issue #2209: Normalizes actions to lowercase for consistent deduplication.
 *
 * @returns List of actions that were requested (e.g., ["/reflecting-sessions"])
 */
export function extractImmediateTags(transcriptContent: string): string[] {
  // Pattern: [IMMEDIATE: action] where action can be a slash command or text
  // Issue #2704: ReDoS mitigation - limit content length to 256 characters
  const pattern = /\[IMMEDIATE:\s*([^\]]{1,256})\]/gi;
  const matches: string[] = [];

  for (
    let match = pattern.exec(transcriptContent);
    match !== null;
    match = pattern.exec(transcriptContent)
  ) {
    const action = match[1].trim().toLowerCase();
    if (action && isValidImmediateAction(action)) {
      matches.push(action);
    }
  }

  // Deduplicate while preserving order
  return [...new Set(matches)];
}

/**
 * Check if an [IMMEDIATE] action was executed.
 *
 * Issue #2186: Verify that the specified action was performed.
 *
 * For the special case of "/reflecting-sessions", this verifies BOTH:
 * 1. Skill invocation (via `checkSkillInvocation`) - actual /reflecting-sessions skill was called
 * 2. Reflection content (via `checkTranscriptForReflection`) - 五省 keywords present
 *
 * Issue #2489: Manual 五省 summaries without skill invocation are not sufficient.
 *
 * For other (generic) actions, this function currently returns false
 * because reliable verification requires action-specific logic (e.g.,
 * checking command execution logs, test results, etc.) which is not
 * yet implemented.
 *
 * @param action - The action string (e.g., "/reflecting-sessions", "run tests")
 * @param transcriptContent - Full transcript to search
 * @returns True if the action appears to have been executed.
 *          Currently only /reflecting-sessions is verifiable; other actions return false.
 */
export function checkImmediateActionExecuted(action: string, transcriptContent: string): boolean {
  const actionLower = action.toLowerCase().trim();

  // Handle /reflecting-sessions action - verify BOTH skill invocation AND reflection content
  // Issue #2489: Keyword-only detection allowed manual summaries to bypass enforcement
  if (actionLower.includes("/reflecting-sessions")) {
    // Must verify skill was actually invoked (not just keywords in transcript)
    const skillInvoked = checkSkillInvocation(transcriptContent);
    const hasReflectionContent = checkTranscriptForReflection(transcriptContent);
    return skillInvoked && hasReflectionContent;
  }

  // Future enhancement: For actions other than /reflecting-sessions, implement
  // action-specific verification logic (e.g., check command execution
  // logs, test results, etc.).
  //
  // Currently, we cannot reliably verify generic actions, so we return
  // false to indicate "not verified" (which will trigger a block if
  // called from the main verification flow).
  return false;
}
