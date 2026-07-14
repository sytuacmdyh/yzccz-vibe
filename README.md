# yzccz-vibe

个人 vibe coding 配置仓库，统一管理 Claude Code 和 Codex 的自定义 skills。仓库可直接作为 `npx skills` 的安装源。

## 技能列表

| 技能 | 说明 |
|------|------|
| `yzc-commit` | 生成中文 Conventional Commits 格式的提交信息并执行 git commit |
| `yzc-modbus-test` | CSV 驱动的 Modbus 串口测试，支持单文件或文件夹批处理 |

## 使用 npx skills 安装

全局安装全部技能到所有已支持的代理：

```bash
npx skills add sytuacmdyh/yzccz-vibe -g --all -y
```

仅安装到当前项目时，移除 `-g`：

```bash
npx skills add sytuacmdyh/yzccz-vibe --all -y
```

安装前可查看仓库提供的技能：

```bash
npx skills add sytuacmdyh/yzccz-vibe --list
```

安装后，在 Claude 或 Codex 中可通过 `/yzc-commit`、`/yzc-modbus-test` 等命令直接调用。
