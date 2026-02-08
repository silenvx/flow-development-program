/**
 * パストラバーサル防止のためのパス検証ユーティリティを提供する。
 *
 * Why:
 *   外部入力（transcript path等）のパストラバーサル攻撃を防止し、
 *   許可されたディレクトリのみへのアクセスを保証する。
 *
 * What:
 *   - isSafeTranscriptPath(): transcriptパスの安全性検証
 *
 * Remarks:
 *   - 許可ディレクトリ: ホーム、システムtemp、cwd
 *   - シンボリックリンクは解決後に検証
 *   - Claude Code内部生成パスでも防御的に検証
 *
 * Changelog:
 *   - silenvx/dekita#2868: Python版から移行
 */

import { existsSync, realpathSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { relative, resolve } from "node:path";

/**
 * Get list of allowed directories for transcript files.
 *
 * @returns Array of resolved directory paths.
 */
function getAllowedDirectories(): string[] {
  const allowed: string[] = [];

  // User's home directory
  try {
    const home = homedir();
    if (existsSync(home)) {
      allowed.push(realpathSync(home));
    }
  } catch {
    // Ignore errors
  }

  // System temp directories
  const tmpdirEnv = process.env.TMPDIR;
  if (tmpdirEnv) {
    try {
      if (existsSync(tmpdirEnv)) {
        const resolved = realpathSync(tmpdirEnv);
        if (!allowed.includes(resolved)) {
          allowed.push(resolved);
        }
      }
    } catch {
      // Ignore errors
    }
  }

  // System temp directory (cross-platform)
  try {
    const systemTmp = tmpdir();
    if (existsSync(systemTmp)) {
      const resolved = realpathSync(systemTmp);
      if (!allowed.includes(resolved)) {
        allowed.push(resolved);
      }
    }
  } catch {
    // Ignore errors
  }

  // Current working directory (for relative paths)
  try {
    const cwd = process.cwd();
    if (existsSync(cwd)) {
      const resolved = realpathSync(cwd);
      if (!allowed.includes(resolved)) {
        allowed.push(resolved);
      }
    }
  } catch {
    // CWD may not exist in edge cases
  }

  return allowed;
}

/**
 * Check if path is under directory (handles symlinks).
 *
 * Uses relative path calculation to safely determine containment,
 * avoiding startsWith() which is vulnerable to path traversal.
 *
 * @param path - The resolved path to check.
 * @param directory - The directory to check against.
 * @returns True if path is under directory, false otherwise.
 */
function isPathUnder(pathStr: string, directory: string): boolean {
  try {
    const resolvedDir = realpathSync(resolve(directory));
    const resolvedPath = realpathSync(resolve(pathStr));

    // Use relative path to check containment
    // If path is under directory, relative will not start with '..' or '/'
    const rel = relative(resolvedDir, resolvedPath);

    // Path is under directory if:
    // - relative path doesn't start with '..' (not escaping directory)
    // - relative path doesn't start with '/' (not absolute - shouldn't happen after resolve)
    // - relative path is not empty (path == directory is allowed)
    return !rel.startsWith("..") && !rel.startsWith("/");
  } catch {
    return false;
  }
}

/**
 * Validate that a transcript path is safe to read.
 *
 * Checks that the path:
 * 1. Is not empty
 * 2. Is absolute or can be safely resolved
 * 3. After resolution, is within allowed directories:
 *    - User's home directory
 *    - System temp directories
 *    - Current working directory
 *
 * @param pathStr - The path string to validate.
 * @returns True if the path is safe, false otherwise.
 *
 * Note:
 *   This function is designed for validating transcript paths provided
 *   by Claude Code's hook system. In practice, these paths are generated
 *   by Claude Code itself, but validation is good defensive practice.
 */
export function isSafeTranscriptPath(pathStr: string): boolean {
  if (!pathStr || !pathStr.trim()) {
    return false;
  }

  try {
    // Resolve to absolute path, following symlinks
    const resolved = realpathSync(resolve(pathStr));

    // Get allowed directories
    const allowedDirs = getAllowedDirectories();

    // Check if resolved path is under any allowed directory
    return allowedDirs.some((allowedDir) => isPathUnder(resolved, allowedDir));
  } catch {
    // Path resolution failed (e.g., file doesn't exist, invalid characters)
    return false;
  }
}
