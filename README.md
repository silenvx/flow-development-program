# Flow Development Program (FDP)

AIエージェントのFlowに着目し、「お願い」だけでなく「仕組み」でも制御するアプローチ。

FDPは、実際のプロジェクト内で動かされ続けていないと形骸化します。  
FDPは「実行して、破綻に気づき、修正する」という flow そのものなので、動作させない状態では修正が発生せず、どこかで崩れていきます。

[examples/](examples/) は実プロジェクトから切り出したスナップショットです。  
FDP関連ファイル（`.claude/`、`AGENTS.md`等）のみを含み、アプリ本体のコードは含まれていません。  
完成形や理想形ではなく、実際に動かしながら調整されているFDPの断面を共有するためのものです。  
理想は、動いているプロジェクト間で互いのFDPを参照し、必要な flow を再実装しながら育てていく形ですが、Private Repositoryで運用しているためこのような形での共有となっています。

## このプログラムの目的

| 目的 | 説明 | 例 |
|------|------|-----|
| **防ぐ** | 悪い行動をBlockする仕組み | mainで編集 → Block |
| **導く** | 正しい行動に誘導する仕組み | Block時に代替手順を提示 |
| **学習させる** | 失敗から改善する仕組み | マージ後に振り返りを強制 |

## リポジトリ構造

```text
flow-development-program/
├── README.md
├── prompts/                  # Claudeに実行させるプロンプト
│   ├── explain-all.md        # 全ファイル説明
│   ├── export-to-fdp.md      # FDPへの同期（公開用）
│   └── import-from-fdp.md    # 参照リポジトリからのインポート
├── examples/
│   └── <project>/            # 実装例（公開対象）
└── generated/                # 生成された説明ファイル
```

## 使い方

[prompts/](prompts/) ディレクトリのファイルはClaude Codeに読ませて実行するプロンプトです。

### 参考にして取り込む

[examples/](examples/) のコードは**コピーではなく参考**として使用し、必要なパターンを再実装してください。

**理由**:

- **セキュリティ**: 外部コードをそのまま実行するのは危険
- **適応**: プロジェクト固有の要件に最適化

参照リポジトリのパターンを再実装するには：

```text
prompts/import-from-fdp.md を読んで、worktree保護のパターンを再実装してください
```

### ファイル説明の生成

ファイルの説明を生成するには：

```text
prompts/explain-all.md を読んで、examples/<project>/ の全ファイルを説明してください
```

[generated/explain-all/](generated/explain-all/) に説明ファイルが生成されます。

### FDPへの同期

公開対象をFDPに同期するには：

```text
prompts/export-to-fdp.md を読んで、<project>をFDPに同期してください
```
