/**
 * リベース/amend後にCodexレビューマーカーを自動更新する。
 *
 * Why:
 *   リベースでコミットハッシュが変わった際に
 *   Codexレビュー記録を自動更新し、手動更新を不要にするため。
 *
 * What:
 *   - sanitizeBranchNameForMarker(): ブランチ名をファイル名用にサニタイズ（マーカーファイル用）
 *   - updateMarkerFile(): マーカーファイル更新（branch:commit:diff_hash形式）
 *
 * State:
 *   - reads: .claude/logs/markers/codex-review-*.done
 *   - writes: .claude/logs/markers/codex-review-*.done
 *
 * Remarks:
 *   - lefthook post-rewriteから呼び出される
 *   - rebaseとamend両方で実行される
 *   - main/masterブランチはスキップ
 *   - detached HEAD時はスキップ
 *
 * Changelog:
 *   - silenvx/dekita#802: post-rewrite自動更新機能を追加
 *   - silenvx/dekita#811: detached HEADスキップを追加
 *   - silenvx/dekita#813: マーカーファイル仕様を定義
 *   - silenvx/dekita#1057: diff_hash保存を追加
 *   - silenvx/dekita#2875: Shell版からTypeScript版へ移行
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { getCurrentBranch, getDiffHash, getHeadCommit } from "../lib/git";

// ========== 定数 ==========
const MARKERS_DIR = ".claude/logs/markers";

// ========== ヘルパー関数 ==========

/**
 * ブランチ名をマーカーファイル用にサニタイズ
 * Note: lib/strings.tsのsanitizeBranchNameとは実装が異なる。
 * この実装はcommon.pyのsanitize_branch_name()と同じロジックで、
 * 既存のマーカーファイルとの互換性を維持するために独自実装を使用:
 * - / \ : < > " | ? * を - に置換
 * - スペースを _ に置換
 * - 連続する - を単一の - に圧縮
 * - 先頭/末尾の - を削除
 */
function sanitizeBranchNameForMarker(branch: string): string {
  let result = branch;

  // Replace / and \ with -
  result = result.replace(/[/\\]/g, "-");

  // Replace : < > " | ? * with -
  result = result.replace(/[:<>"|?*]/g, "-");

  // Replace spaces with _
  result = result.replace(/ /g, "_");

  // Remove consecutive dashes
  result = result.replace(/-+/g, "-");

  // Remove leading/trailing dashes
  result = result.replace(/^-|-$/g, "");

  return result;
}

// ========== メイン処理 ==========
async function main(): Promise<void> {
  // 現在のブランチ名を取得
  const branch = await getCurrentBranch();

  // ブランチ名が取得できない場合はスキップ
  if (!branch) {
    process.exit(0);
  }

  // main/masterブランチはスキップ（マーカー対象外）
  if (branch === "main" || branch === "master") {
    process.exit(0);
  }

  // Issue #811: detached HEAD時はスキップ
  // リベース中間コミットではHEADがdetachedになることがある
  if (branch === "HEAD") {
    process.exit(0);
  }

  // ブランチ名をサニタイズ（マーカーファイル用）
  const safeBranch = sanitizeBranchNameForMarker(branch);
  const markerFile = join(MARKERS_DIR, `codex-review-${safeBranch}.done`);

  // マーカーファイルが存在しない場合はスキップ
  // （まだCodexレビューを実行していないブランチ）
  if (!existsSync(markerFile)) {
    process.exit(0);
  }

  // 新しいHEADコミットを取得
  const newCommit = await getHeadCommit();
  if (!newCommit) {
    console.error("[post-rewrite] Warning: Could not get HEAD commit");
    process.exit(0);
  }

  // 現在のマーカー内容を読み取り
  let oldContent = "";
  try {
    oldContent = readFileSync(markerFile, "utf-8").trim();
  } catch {
    // ファイル読み取りエラーは無視
  }

  // ディレクトリが存在することを確認
  mkdirSync(MARKERS_DIR, { recursive: true });

  // diff_hashを計算（nullは空文字列として扱う）
  const diffHash = (await getDiffHash()) ?? "";

  // マーカーファイルを更新
  // Issue #813: マーカーファイル仕様
  // - ファイル名: サニタイズされたブランチ名を使用
  // - ファイル内容: 元のブランチ名を使用（正確な識別のため）
  // - フォーマット: branch:commit:diff_hash (Issue #841)
  const newContent = `${branch}:${newCommit}:${diffHash}`;
  writeFileSync(markerFile, newContent);

  // 情報出力はstderrへ（lefthookの出力を汚染しない）
  console.error("[post-rewrite] Codex review marker updated:");
  console.error(`  File: ${markerFile}`);
  console.error(`  Old:  ${oldContent}`);
  console.error(`  New:  ${newContent}`);
}

main().catch((err) => {
  console.error("update_codex_marker_on_rebase error:", err);
  process.exit(1);
});
