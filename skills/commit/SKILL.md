---
name: yzc-commit
description: 生成符合规范的提交信息并创建 git commit。输出中文 Conventional Commits 格式，包含标题和正文要点。
metadata:
  short-description: 生成规范提交信息并提交
---

# Commit Skill

生成中文 Conventional Commits 格式的提交信息，并执行 `git commit`。

## 执行步骤（严格按顺序）

1. 运行 `git diff --staged` 获取暂存区变更；如果已有变更，仅提交当前已暂存的内容，不追加暂存其他改动（包括同一文件中未暂存的部分）
2. 如果暂存区为空，自动运行 `git add -A` 暂存整个仓库的全部修改（包括新增、修改和删除的文件，遵循 Git 忽略规则），然后再次运行 `git diff --staged` 获取变更；无需提示用户手动暂存或再次确认。如果暂存后仍无变更，告知用户没有可提交的修改并终止；如果暂存失败，报告错误并终止，不继续提交
3. 仅根据最终暂存区的变更内容生成提交信息（见格式要求）
4. 运行 `git commit -m "<title>" -m "<body>"` 提交

## 提交信息格式要求（硬性）

- **语言**：中文
- **标题**：严格使用 Conventional Commits 格式：`<type>(<scope>): <subject>`
  - subject 极简，不超过 20 字
  - type 可选：feat / fix / refactor / chore / docs / test / perf / style
- **正文**：用项目符号（`- `）列出 3~5 条关键修改点，每条简洁
- **输出结构**：第一行标题，空一行，然后正文要点
- **禁止**：不加解释、不加前后缀、不加代码块标记（```）

## git commit 命令格式

使用 `$'...'` ANSI-C 引用语法，让 shell 将 `\n` 解释为真实换行：

```
git commit -m "<type>(<scope>): <subject>" -m $'- 要点1\n- 要点2\n- 要点3'
```

**禁止**使用普通双引号 `"- 要点1\n- 要点2"`——双引号内 `\n` 不会被 shell 转义为换行符。

## 行为约束（硬性）

- 不修改工作区文件内容
- 只允许运行以下命令：`git diff --staged`、`git diff`、`git add -A`、`git commit -m ...`
- 仅在暂存区为空时自动运行 `git add -A`；已有暂存变更时保持当前暂存范围
- 不运行 `git push` 或其他任何命令
