---
name: yzc-commit
description: 生成符合规范的提交信息并创建 git commit。输出中文 Conventional Commits 格式，包含标题和正文要点。
metadata:
  short-description: 生成规范提交信息并提交
---

# Commit Skill

生成中文 Conventional Commits 格式的提交信息，并执行 `git commit`。

## 执行步骤（严格按顺序）

1. 运行 `git diff --staged` 获取暂存区变更
2. 如果暂存区为空，运行 `git diff` 查看工作区变更，并提示用户先执行 `git add`，然后终止
3. 根据变更内容生成提交信息（见格式要求）
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

- 不修改任何文件
- 只允许运行以下命令：`git diff --staged`、`git diff`、`git commit -m ...`
- 不运行 `git add`、`git push` 或其他任何命令
