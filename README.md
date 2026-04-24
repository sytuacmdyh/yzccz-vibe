# yzccz-vibe

个人 vibe coding 配置仓库，统一管理 Claude Code 和 Codex 的自定义 skills。

## 技能列表

| 技能 | 说明 |
|------|------|
| `commit` | 生成中文 Conventional Commits 格式的提交信息并执行 git commit |
| `modbus-test` | CSV 驱动的 Modbus 串口测试，支持单文件或文件夹批处理 |

## 一键安装

将当前 skills 同步到 Claude 和 Codex 全局技能目录（`yzc/` 命名空间下）：

```bash
bash scripts/install.sh
```

安装后，在 Claude 或 Codex 中可通过 `/commit`、`/modbus-test` 等命令直接调用。
