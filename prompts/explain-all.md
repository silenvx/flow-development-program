# 全ファイル説明

対象リポジトリに含まれる**全ての**ファイルを網羅的に説明し、**Markdownファイルとして保存**してください。

---

## 重要ルール（必ず確認）

| ルール | 説明 |
|--------|------|
| **Mermaid必須** | flows.mdは `flowchart TD` のMermaid形式のみ。**ASCII art（`┌───`, `│`, `▼` 等）は禁止** |
| **ノード形式** | `[実装名.py<br/>説明]` 形式必須。説明だけのノード（`[Issue確認]`等）は禁止 |
| **最低要件** | 全体フロー30ノード以上、ユースケース6個以上（各15ノード以上） |
| **省略禁止** | 「主要な〜」「約N個」「〜など」「〜を参照」は全て禁止 |

---

## 重要: 出力方針

**このタスクは大量のファイル（100+）を対象とするため、以下の方針で実行すること。**

1. **ディレクトリに出力**: 結果は `generated/explain-all/{プロジェクト名}/` ディレクトリに複数ファイルとして保存する
   - FDPプロジェクトルートの `generated/explain-all/{プロジェクト名}/` ディレクトリに出力
   - プロジェクト名は対象リポジトリのディレクトリ名を使用
2. **カテゴリごとにファイル分割**: 探索結果に基づいてカテゴリを動的に決定し、カテゴリごとに別ファイルを生成
3. **生成日時を記載**: 各ファイル冒頭に `date` コマンドで取得した生成日時を記載する
4. **省略禁止**: 「主要な〜」「代表的な〜」「など」で省略しない。全ファイルを列挙する
5. **中間ファイル**: 作業中に中間ファイルが必要な場合は、`generated/explain-all/{プロジェクト名}/tmp/` ディレクトリに作成する

### 出力ファイル構成

```
generated/explain-all/{プロジェクト名}/
├── tmp/
│   └── all-files.txt       # 中間ファイル（全ファイル一覧）
├── 00-summary.md           # 必須：サマリー・目次・生成日時
├── {カテゴリ1}.md          # 探索結果から動的に決定
├── {カテゴリ2}.md          # 探索結果から動的に決定
├── ...
└── flows.md                # 必須：フロー図
```

- **00-summary.md**: 全体サマリー、各カテゴリへのリンク、ファイル数統計
- **{カテゴリ}.md**: 探索結果に基づいて論理的に分類したカテゴリごとのファイル
- **flows.md**: 全体フロー図、ユースケース別フロー図

**カテゴリの決め方**:
- 第1階層は**ディレクトリ階層**を基準にする（分類の安定性を優先）
- 第2階層で**機能/用途ラベル**を併記し、意味づけを明確にする
- 探索結果の構造と機能的なまとまりから動的に決定
- 各カテゴリファイルは独立して完結すること

---

## 探索方針（中間ファイル必須）

**最初に全ファイル一覧を中間ファイルに出力し、それを参照しながら進める。**

### Step 1: 出力ディレクトリと中間ファイルを作成

```bash
mkdir -p generated/explain-all/{プロジェクト名}/tmp
find {対象ディレクトリ} -type f | sort > generated/explain-all/{プロジェクト名}/tmp/all-files.txt
wc -l generated/explain-all/{プロジェクト名}/tmp/all-files.txt  # 件数を確認
```

この中間ファイルが「説明すべき全ファイル」の正解リストになる。

### Step 2: カテゴリ分類

中間ファイルを見ながら、ファイルを論理的なカテゴリに分類する。

### Step 3: カテゴリごとにファイル生成

`generated/explain-all/{プロジェクト名}/` ディレクトリにカテゴリごとのファイルを生成していく。

### Step 4: カテゴリ単位で検証

各カテゴリファイル生成後、そのカテゴリの件数を検証：

```bash
# カテゴリ内のファイル数（中間ファイルから抽出）
grep "該当パターン" generated/explain-all/{プロジェクト名}/tmp/all-files.txt | wc -l

# 出力ファイルのテーブル行数
grep -c "^|" generated/explain-all/{プロジェクト名}/{カテゴリ}.md
```

### Step 5: 全体検証

全カテゴリ完了後、全体の件数を検証：

```bash
# 中間ファイルの行数（説明すべき数）
wc -l generated/explain-all/{プロジェクト名}/tmp/all-files.txt

# 全出力ファイルのテーブル行数合計（説明した数）
grep -c "^|" generated/explain-all/{プロジェクト名}/*.md | tail -1
```

**数が一致しない場合は完了とせず、漏れを特定して追記する。**

---

## 禁止事項

- 「主要な〜」「代表的な〜」「主な〜」のような表現で一部だけを説明すること
- 要約やまとめ形式での説明
- 「など」「等」で省略すること
- **「約N個」「~N個」のような曖昧な数値**（サマリー含め全箇所で正確な数を記載すること）
- **「〜を参照」で済ませること**（このドキュメント内で完結させる）
- **会話に直接大量出力すること**（必ずファイルに書き込む）
- **カバレッジ100%未満で完了とすること**（全ファイルを列挙するまで終わらない）

**「約」禁止はサマリーにも適用される**: 「合計: 約300個」ではなく「合計: 280個」のように正確な数を記載すること。

**発見した全てのファイル・ユースケースを漏れなく列挙すること。**

---

## 説明すべき内容

各ファイルについて以下を**必ず**抽出：

| 項目 | 説明 | 必須 |
|------|------|------|
| **名前** | ファイル名またはディレクトリ名 | ✅ |
| **目的（Why）** | なぜ必要か、どんな問題を解決するか（2-3文で詳細に） | ✅ |
| **機能（What）** | 何をするか、具体的な処理内容（2-3文で詳細に） | ✅ |
| **使い方** | どう使うか（コマンド例、トリガー条件等） | ✅ |
| **関連** | 関連する他ファイル（任意） | |

### Why/What の詳細さ要件

**短すぎる例（NG）**:
- Why: 「継続セッションのメトリクス収集」
- What: 「前セッションからの継続判定とメトリクス記録」

**十分な詳細さ（OK）**:
- Why: 「セッション間の継続性を追跡し、中断・再開パターンを把握するため。長期タスクの中断頻度を分析し、セッション管理の改善点を特定できる」
- What: 「前セッションのIDとタイムスタンプを確認し、一定時間内の再開であれば継続セッションとしてマーク。継続回数・中断経過時間をメトリクスログに記録」

**基準**:
- Why/What それぞれ **20文字以上** を目安にする
- 「〜のため」「〜を記録」だけで終わらせず、**具体的に何が起きるか**まで書く

---

## 出力形式

### 0. ヘッダー（生成日時）

ファイル冒頭に以下を記載：

```markdown
# {プロジェクト名} ファイル説明

生成日時: {dateコマンドの出力}
```

### 1. ファイル詳細

**重要: 全てのファイルを漏れなくテーブルに列挙すること。**

カテゴリごとにテーブル形式で出力：

```markdown
## カテゴリ名

| ファイル | Why | What | 使い方 |
|----------|-----|------|--------|
| `file1.py` | なぜ必要か | 何をするか | コマンド例等 |
| `file2.py` | なぜ必要か | 何をするか | コマンド例等 |
| `file3.py` | なぜ必要か | 何をするか | コマンド例等 |
（全ファイルを列挙）
```

※ 省略せず、発見した全ファイルをテーブルに記載すること

**テストファイルの扱い**:
- テストファイル（`test_*.py`, `*.test.ts` 等）は**個別列挙しない**
- 代わりにテストディレクトリ単位でまとめる（以下を必須で記載）
  - テストディレクトリの場所
  - テストファイル総数
  - テスト実行コマンド
  - テストの種類（unit/integration/e2e）

### 2. 実行フロー図（Mermaid）- 最後に出力

**重要: フロー図はファイル詳細を全て書き終えた後、最後に出力すること。**
ファイルの全体像を把握した上で、より正確で詳細なフロー図を作成できる。

#### フロー図の必須ルール

⚠️ **flows.md は Mermaid `flowchart TD` のみ。ASCII art は一切禁止。**

**よくある間違い（絶対に使わないこと）**:
```
❌ ASCII art（禁止）:
┌─────────────────┐
│  Issue選択      │
└────────┬────────┘
         ▼
┌─────────────────┐
│  実装開始       │
└─────────────────┘

✅ Mermaid flowchart TD（必須）:
flowchart TD
    A[gh issue view<br/>Issue詳細確認] --> B[Edit tool<br/>コード変更]
```

**使用する図の種類**:
- ✅ `flowchart TD` のみ使用すること
- ❌ `flowchart TB` は禁止（`TD` を使う）
- ❌ `flowchart LR` は禁止（`TD` を使う）
- ❌ `sequenceDiagram` は禁止
- ❌ `stateDiagram` は禁止
- ❌ `graph TD` は禁止（`flowchart TD` を使う）
- ❌ **ASCII art（`┌───`, `│`, `▼`, `└───`, `├──`, `→` 等の罫線・矢印）は禁止**

**ノードの記載形式（最重要）**:

⚠️ **フロー図の価値は「どの実装ファイルが関わるか」を示すことにある。説明だけのノードは価値がない。**

- **全てのノードに実装名（ファイル名 or コマンド）を含めること**
- 形式: `[実装名.py<br/>説明]` または `[コマンド<br/>説明]`
- **`.py` や `.sh` の拡張子を省略しない**
- **説明（`<br/>`の後）を省略しない**

**NG例（説明だけのノードは禁止）**:
- ❌ `SS1[claude-code起動]` → 実装名なし
- ❌ `I1[Issue確認]` → 実装名なし
- ❌ `IM1[コード変更]` → 実装名なし
- ❌ `G1[至誠に悖るなかりしか]` → 実装名なし

**OK例（必ず実装名を含める）**:
- ✅ `SS1[environment-integrity-check.py<br/>フック環境確認]`
- ✅ `I1[gh issue view<br/>Issue詳細確認]`
- ✅ `IM1[Edit tool<br/>コード変更]`
- ✅ `G1[reflection/execute.md<br/>五省実行]`

**例外（実装なしノード）は最小限に**:
- 開始/終了/待機などの状態ノードのみ `[状態名]` で可（例: `[セッション終了]`）
- 分岐判定ノードは `{条件?}` で可（例: `{テスト成功?}`）
- **それ以外は必ず実装名を含めること**

#### 全体フロー

処理がどのように進むかをMermaidで可視化。

**詳細さの要件**:
- 各ノードは `[実装名.py<br/>説明]` 形式で記載（実装名と説明の両方を含める）
- **複数のsubgraph**でフェーズを分割する（セッション開始、タスク実行、セッション終了など）
- **スクリプト内部の処理ステップも展開**する（コマンドレベルまで記載）
- 分岐条件（成功/失敗、条件分岐）を詳細に記載
- エラーケース・例外フローも含める
- **最低30ノード以上**、subgraph 5個以上を目安に

```mermaid
flowchart TD
    subgraph "セッション開始"
        A[Claude Code起動] --> B[SessionStart hooks]
        B --> B1[environment-check.py<br/>環境確認]
        B1 --> B2[git-config-check.py<br/>Git設定確認]
        B2 --> B3[session-handoff-reader.py<br/>前セッション読込]
        B3 --> B4[session-worktree-status.py<br/>worktree状態表示]
        B4 --> B5[open-pr-warning.py<br/>オープンPR警告]
        B5 --> B6[branch-check.py<br/>ブランチ確認]
        B6 --> C[ユーザー入力待ち]
    end

    subgraph "worktree作成"
        C --> W1[gh issue view<br/>Issue選択]
        W1 --> W2[git worktree add<br/>ブランチ作成]
        W2 --> W3[cd worktrees/issue-N<br/>ディレクトリ移動]
        W3 --> W4[pnpm install<br/>依存関係インストール]
        W4 --> W5[cp .env.example .env<br/>環境設定]
        W5 --> W6[worktree-creation-marker.py<br/>作成記録]
        W6 --> D[実装開始]
    end

    subgraph "タスク実行"
        D --> E{ツール使用}
        E -->|Edit/Write| F[PreToolUse hooks]
        F --> F1[worktree-warning.py<br/>mainブロック]
        F1 --> F2[task-start-checklist.py<br/>チェックリスト]
        F2 --> F3{ブロック判定}
        F3 -->|Yes| C
        F3 -->|No| G[ツール実行]

        E -->|Bash| H[PreToolUse hooks]
        H --> H1[merge-check.py<br/>マージ前チェック]
        H1 --> H2[force-push-guard.py<br/>強制プッシュガード]
        H2 --> H3{ブロック判定}
        H3 -->|Yes| C
        H3 -->|No| G

        G --> I[PostToolUse hooks]
        I --> I1[bash-failure-tracker.py<br/>失敗追跡]
        I1 --> I2[tool-efficiency-tracker.py<br/>効率追跡]
        I2 --> J{継続判定}
        J -->|Yes| D
        J -->|No| K[Stop hooks]
    end

    subgraph "コミット・プッシュ"
        D --> L[git add<br/>変更をステージ]
        L --> M[git commit<br/>コミット作成]
        M --> N[commit-message-check.py<br/>メッセージ確認]
        N --> O[git push<br/>リモートにプッシュ]
        O --> P[gh pr create<br/>PR作成]
        P --> Q[pr-body-quality-check.py<br/>PR本文品質確認]
    end

    subgraph "セッション終了"
        K --> K1[flow-verifier.py<br/>フロー検証]
        K1 --> K2[session-outcome-collector.py<br/>結果収集]
        K2 --> K3[session-handoff-writer.py<br/>引継ぎ情報作成]
        K3 --> K4[state/handoff.json<br/>JSONファイル保存]
        K4 --> K5[reflection-quality-check.py<br/>振り返り品質確認]
        K5 --> K6{ブロック判定}
        K6 -->|Yes| D
        K6 -->|No| Z[セッション終了]
    end
```

#### ユースケース別フロー

発見した全てのユースケースについて、それぞれMermaidで詳細フローを図示。

**ユースケースの抽出方法**:
- ファイルの機能から推測できるユースケースを**全て**列挙する
- **最低6つ以上**のユースケースを図示すること（5つ以下は不可）
- 発見した機能に応じて網羅的に図示する
 - **各ユースケースごとに根拠ファイルを明記する**（推測だけのフローを防ぐ）

**必須ユースケース（存在する場合）**:
1. **全体フロー**: セッション開始〜終了までの全体像
2. **Issue実装フロー**: Issue選択→worktree作成→実装→PR→マージ
3. **CI監視フロー**: ci-monitor.py の詳細動作
4. **振り返りフロー**: /reflect の詳細動作
5. **レビュー対応フロー**: レビューコメント対応→resolve
6. **マージ条件チェックフロー**: merge-check.py の詳細動作

**追加ユースケース例**:
- Dependabot対応、fork-sessionコラボレーション、E2Eテスト、フック開発、セッション引き継ぎ など

**各ユースケースの詳細さ**:
- 各ノードは `[実装名.py<br/>説明]` 形式で記載
  - 実装名: 実際のファイル名（フック/スクリプト/コマンド）
  - 説明: 何をするか
  - 例: `[environment-integrity-check.py<br/>フック環境確認]`
- **スクリプト内部の処理ステップも展開**する（コマンドレベルまで記載）
- **重要な処理はsubgraphで詳細化**する
- 分岐・ループ・エラー処理を含める
- 1ユースケースにつき**最低15ノード以上**

```mermaid
flowchart TD
    subgraph "Issue実装フロー"
        I1[Issue選択] --> I2[gh issue view 番号<br/>Issue詳細確認]
        I2 --> I3[issue-comments-check.py<br/>コメント確認]

        subgraph "worktree作成"
            I3 --> W1[git worktree add<br/>ブランチ作成]
            W1 --> W2[cd worktrees/issue-N<br/>ディレクトリ移動]
            W2 --> W3[pnpm install<br/>依存関係インストール]
            W3 --> W4[cp .env.example .env<br/>環境設定]
            W4 --> W5[worktree-creation-marker.py<br/>作成記録]
        end

        W5 --> I4[task-start-checklist.py<br/>チェックリスト表示]
        I4 --> I5[実装開始]
        I5 --> I6[pnpm test<br/>テスト実行]
        I6 --> I7{テスト結果}
        I7 -->|失敗| I8[テストログ解析<br/>エラー確認]
        I8 --> I9[修正]
        I9 --> I6
        I7 -->|成功| I10[pnpm lint<br/>Lint実行]
        I10 --> I11[git add<br/>変更をステージ]
        I11 --> I12[git commit<br/>コミット作成]
        I12 --> I13[commit-message-check.py<br/>メッセージ確認]

        subgraph "PR作成"
            I13 --> P1[git push -u origin<br/>リモートにプッシュ]
            P1 --> P2[gh pr create<br/>PR作成]
            P2 --> P3[pr-body-quality-check.py<br/>本文品質確認]
            P3 --> P4[closes-keyword-check.py<br/>Closesキーワード確認]
        end

        P4 --> I14[codex review<br/>Codexレビュー依頼]
        I14 --> I15[codex-review-logger.py<br/>レビューログ]
        I15 --> I16[ci-monitor.py<br/>CI監視開始]
        I16 --> I17{CI結果}
        I17 -->|失敗| I8
        I17 -->|成功| I18[merge-check.py<br/>AIレビュー確認]
        I18 --> I19[gh pr merge<br/>マージ実行]
        I19 --> I20[git worktree remove<br/>worktree削除]
        I20 --> I21[post-merge-reflection-enforcer.py<br/>振り返り強制]
        I21 --> I22[reflection SKILL<br/>五省実行]
    end
```

```mermaid
flowchart TD
    subgraph "CI監視フロー"
        C1[ci-monitor.py<br/>CI監視開始] --> C2[gh pr view 番号<br/>PR状態取得]
        C2 --> C3[gh pr checks<br/>チェック状態取得]
        C3 --> C4{チェック状態}

        C4 -->|実行中| C5[30秒間隔でポーリング<br/>待機]
        C5 --> C3

        C4 -->|成功| C6[gh pr view --json reviews<br/>レビュー状態確認]
        C6 --> C7{AIレビュー状態}
        C7 -->|未完了| C8[codex review<br/>Codexレビュー依頼]
        C8 --> C5
        C7 -->|完了| C9[git status<br/>BEHIND確認]
        C9 --> C10{ブランチ状態}
        C10 -->|BEHIND| C11[git rebase origin/main<br/>自動リベース]
        C11 --> C12[git push --force-with-lease<br/>強制プッシュ]
        C12 --> C3
        C10 -->|最新| C13[マージ準備完了通知]

        C4 -->|失敗| C14[gh run view<br/>失敗ログ取得]
        C14 --> C15[失敗種別判定]
        C15 --> C16{失敗種別}
        C16 -->|テスト失敗| C17[pnpm test<br/>ローカルで再現]
        C16 -->|Lint失敗| C18[pnpm lint --fix<br/>Lint修正]
        C16 -->|型エラー| C19[pnpm typecheck<br/>型修正]
        C17 --> C20[修正・再コミット]
        C18 --> C20
        C19 --> C20
        C20 --> C21[git push<br/>プッシュ]
        C21 --> C3
    end
```

```mermaid
flowchart TD
    subgraph "振り返りフロー"
        R1[reflect SKILL<br/>振り返り開始] --> R2[transcript解析<br/>セッションログ収集]
        R2 --> R3[五省チェック開始]

        subgraph "五省"
            R3 --> Q1{要件理解は正確だったか}
            Q1 -->|問題あり| Q1a[原因分析]
            Q1 -->|OK| Q2{品質は十分か}
            Q2 -->|問題あり| Q2a[原因分析]
            Q2 -->|OK| Q3{検証は十分か}
            Q3 -->|問題あり| Q3a[原因分析]
            Q3 -->|OK| Q4{フィードバック対応は適切か}
            Q4 -->|問題あり| Q4a[原因分析]
            Q4 -->|OK| Q5{効率的に作業できたか}
            Q5 -->|問題あり| Q5a[原因分析]
        end

        Q1a --> R4[5回のなぜ<br/>なぜなぜ分析]
        Q2a --> R4
        Q3a --> R4
        Q4a --> R4
        Q5a --> R4
        Q5 -->|OK| R5[問題なし]

        R4 --> R6[根本原因特定]
        R6 --> R7{仕組み化必要}
        R7 -->|Yes| R8[gh issue create<br/>Issue作成]
        R8 --> R9[gh issue edit --add-label<br/>ラベル付与]
        R9 --> R10{フック実装が適切か}
        R10 -->|Yes| R11[hooks-reference SKILL<br/>フック設計]
        R11 --> R12[フック実装]
        R12 --> R13[test_hook.py<br/>テスト作成]
        R10 -->|No| R14[ドキュメント更新]

        R7 -->|No| R15[lessons-learned.md<br/>教訓を記録]
        R5 --> R16[reflection-completion-check.py<br/>振り返り完了]
        R13 --> R16
        R14 --> R16
        R15 --> R16
    end
```

#### Mermaid記法の注意事項

以下の文字はMermaidでエラーを引き起こすため、使用を避けること：

| 禁止文字 | 理由 | 代替表現 |
|----------|------|----------|
| `#` | コメントとして解釈される | `番号` や `No.` |
| `:` | ノード定義と誤認される | `-` や `で` に置換 |
| `/` (先頭) | パスとして解釈される | 先頭の `/` を削除 |
| `.` (先頭) | 隠しファイルパスと誤認 | 先頭の `.` を削除 |
| `[/` | 台形ノード記法と誤認 | `[` の直後に `/` を置かない |

**例**:
- ❌ `gh issue view #123` → ✅ `gh issue view 番号123`
- ❌ `失敗: 重複` → ✅ `重複で失敗`
- ❌ `/reflect実行` → ✅ `reflect実行`
- ❌ `.worktrees/` → ✅ `worktrees/`
- ❌ `[/reflect コマンド]` → ✅ `[reflect コマンド]`（`[` の直後に `/` は禁止）

---

## 実行手順

1. **出力ディレクトリ・中間ファイル生成（必須）**:
   ```bash
   mkdir -p generated/explain-all/{プロジェクト名}/tmp
   find {対象ディレクトリ} -type f | sort > generated/explain-all/{プロジェクト名}/tmp/all-files.txt
   wc -l generated/explain-all/{プロジェクト名}/tmp/all-files.txt
   ```
2. **ファイル数報告**: 中間ファイルの行数を報告（例: 「272個のファイルを検出」）
3. **中間ファイル確認**: `generated/explain-all/{プロジェクト名}/tmp/all-files.txt` を `Read` して内容を確認
4. **カテゴリ分類**: 中間ファイルを見ながら、ファイルを論理的なカテゴリに分類
5. **00-summary.md作成**: サマリーファイルを作成（生成日時、カテゴリ一覧、ファイル数統計）
6. **カテゴリ別ファイル生成**: 各カテゴリについて以下を繰り返す
   - 中間ファイルから該当カテゴリのファイルパスを確認
   - 各ファイルを `Read` して内容を把握
   - `{カテゴリ}.md` を作成
   - **カテゴリ単位で件数検証**（中間ファイルの該当件数と一致するか確認）
   - 進捗を報告（例: 「カテゴリA 35/35完了」）
7. **flows.md作成（最後）**: 全カテゴリ完了後、フロー図ファイルを作成
8. **全体検証（必須）**: 中間ファイルと全出力ファイルの合計を比較
   ```bash
   wc -l generated/explain-all/{プロジェクト名}/tmp/all-files.txt  # 説明すべき数
   grep -h "^|" generated/explain-all/{プロジェクト名}/*.md | grep -v "^|--" | wc -l
   ```
   **数が一致しない場合は漏れを特定して追記する。完了とするのは一致確認後のみ。**

---

## 段階的出力の例

```
ユーザー: explain-all.md を読んで実行して

AI: 出力ディレクトリと中間ファイルを作成します...
    $ mkdir -p generated/explain-all/my-project/tmp
    $ find {対象ディレクトリ} -type f | sort > generated/explain-all/my-project/tmp/all-files.txt
    $ wc -l generated/explain-all/my-project/tmp/all-files.txt
    272 generated/explain-all/my-project/tmp/all-files.txt

    272個のファイルを検出しました。中間ファイルを確認します...
    （generated/explain-all/my-project/tmp/all-files.txt を Read）

    カテゴリ分類（探索結果に基づいて動的に決定）:
    - カテゴリA: 186個
    - カテゴリB: 50個
    - カテゴリC: 8個
    - その他: 28個

    [1/5] 00-summary.md を作成...
    [2/5] category-a.md を作成... 186/186完了 ✓
    [3/5] category-b.md を作成... 50/50完了 ✓
    [4/5] category-c.md を作成... 8/8完了 ✓
    [5/5] flows.md を作成...
    - 全体フロー（30ノード）
    - ユースケース別フロー（6個、各15ノード以上）

    全体検証を実施します...
    $ wc -l generated/explain-all/my-project/tmp/all-files.txt
    272 generated/explain-all/my-project/tmp/all-files.txt
    $ grep -h "^|" generated/explain-all/my-project/*.md | grep -v "^|--" | wc -l
    272

    中間ファイル: 272個、出力テーブル: 272個 → 一致確認OK

    完了しました。
    generated/explain-all/my-project/
    ├── tmp/
    │   └── all-files.txt
    ├── 00-summary.md
    ├── category-a.md (186件)
    ├── category-b.md (50件)
    ├── category-c.md (8件 + その他28件)
    └── flows.md
```

---

## 網羅性チェック（必須）

出力完了後、以下を確認：

### ファイル詳細
- [ ] **中間ファイルとの一致**: `generated/explain-all/{プロジェクト名}/tmp/all-files.txt` の行数と出力テーブルの行数が一致するか
- [ ] 各ファイルの Why / What / 使い方 を記載したか
- [ ] テストコードがある場合、言及したか
- [ ] **数値の整合性**: セクションで「N個」と記載した数と、実際のテーブル行数が一致するか
- [ ] **カバレッジ100%**: 全カテゴリで100%カバーしているか（45%等の中途半端な状態で終わらない）

**漏れがある場合の対処**:
```bash
# 中間ファイルにあるが出力にないファイルを特定
diff <(cat generated/explain-all/{プロジェクト名}/tmp/all-files.txt | xargs -I{} basename {}) \
     <(grep -h "^|" generated/explain-all/{プロジェクト名}/*.md | grep -oE "[a-zA-Z0-9_-]+\.[a-z]+")
```

### フロー図
- [ ] **図の種類**: 全て `flowchart TD` を使用しているか（TB/LR/sequenceDiagram/stateDiagram は禁止）
- [ ] **ASCII art禁止**: `┌`, `│`, `▼`, `└`, `├`, `→` 等の罫線文字がないか
- [ ] **ノード形式**: 説明だけのノード（`[Issue確認]`等）がないか確認。全ノードに実装名を含めること
- [ ] **実装名の確認**: 各ノードが `[実装名.py<br/>説明]` または `[コマンド<br/>説明]` 形式か
- [ ] **ユースケース数**: 最低6つ以上のフロー図があるか（5つ以下は不可）
- [ ] **詳細さ**: 各ユースケースが15ノード以上、全体フローは30ノード以上あるか

**フロー図検証コマンド**:
```bash
# Mermaid形式確認（flowchart TDが必須）
grep -c "flowchart TD" generated/explain-all/{プロジェクト名}/flows.md

# ASCII art検出（これらがあれば再生成が必要）
grep -E "[┌│▼└├→─]" generated/explain-all/{プロジェクト名}/flows.md && echo "ERROR: ASCII art detected"

# ノード数カウント
grep -oE "\[[^\]]+\]" generated/explain-all/{プロジェクト名}/flows.md | wc -l
```

### 禁止表現チェック
- [ ] **「約」「~」禁止**: 「約50個」「~100%」のような曖昧表現がないか
- [ ] **「主要な」禁止**: 「主要なフック」のような省略表現がないか
- [ ] **「など」禁止**: 「〜など」で終わる列挙がないか
- [ ] **「参照」禁止**: 「詳細は〜を参照」で逃げていないか

**重要: 網羅性チェックで不足が判明した場合、そのまま完了にせず追記して100%にすること。**
**「〜を参照」「詳細は省略」等で逃げずに、全ファイルをテーブルに列挙すること。**
