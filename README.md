# yzccz-vibe

个人 vibe coding 配置仓库，统一管理可复用的 Agent Skills。仓库可直接作为 `npx skills` 的安装源。

## 设计原则

技能说明和工作流保持平台无关，不依赖或指定某个 AI 编程助手。具体的发现、自动触发和显式调用方式由安装技能的宿主决定。

## 技能列表

| 技能 | 说明 |
|------|------|
| `yzc-commit` | 生成中文 Conventional Commits 格式的提交信息并执行 git commit |
| `yzc-keil-wsl-build` | 从 WSL 通过 Windows Keil 命令行编译，并用临时 Git 快照和 worktree 精确验证当前文件状态 |
| `yzc-modbus-test` | CSV 驱动的 Modbus 串口测试，支持目录扫描或显式文件列表，并可控制 DeviceSimulator、驱动 EMS Modbus Slave，以及通过捆绑的 EMS MQTT master 发送/校验 MQTT 消息 |

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

只全局安装 Keil WSL 编译技能到所有支持的代理：

```bash
npx skills add sytuacmdyh/yzccz-vibe -g -s yzc-keil-wsl-build -a '*' -y
```

安装后，可在支持 Agent Skills 的 AI 编程助手中通过自然语言自动匹配，或按宿主提供的方式显式调用 `yzc-commit`、`yzc-keil-wsl-build`、`yzc-modbus-test`。
