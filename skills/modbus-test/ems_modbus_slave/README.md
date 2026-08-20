# EMS Modbus Slave

面向 EMS 联调的 Modbus RTU 从站桌面模拟器。

## 功能概览

- Python + Qt（PySide6）桌面应用
- 从 `profiles/*.json` 加载设备协议配置
- 当前实机协议：`DM-HPWT18-U1 18一体机`
- 通过 `USB -> RS485` 提供串口 RTU 从站服务
- 支持功能码：`01`、`03`、`05`、`06`、`0F`、`10`
- 寄存器/线圈点表在线编辑
- State 状态面板，支持右键添加/移除监控点
- Message 通信报文日志
- Packet Capture 抓包：列表显示摘要，详情区显示 Request/Response 与 RX/TX 十六进制
- File 菜单支持 Profile / Preset 导入与另存为
- Preset 可保存运行时寄存器值、Capture 开关、State 字段

## 目录结构

```text
EMS Modbus Slave/
  app.py                    # 程序入口
  README.md
  requirements.txt          # 运行依赖
  build-requirements.txt    # 打包依赖（Nuitka）
  build_nuitka.ps1          # Windows 一键打包脚本
  profiles/
    dm_hpwt18_u1.json
  presets/
    dm_hpwt18_u1_ems_dhw_linkage.json
    dm_hpwt18_u1_ems_cool_linkage.json
    dm_hpwt18_u1_ems_cool_dhw_linkage.json
  schemas/
    device_profile.schema.json
  src/
    ems_modbus_slave/
      app.py                # 主窗口
      capture.py            # 抓包记录与点位匹配
      device_profile.py
      modbus_rtu.py
      paths.py              # 开发/打包路径解析
      preset_loader.py
      profile_repository.py
      protocol_messages.py
      register_model.py
      serial_slave.py
      widgets.py
  tools/
    extract_modbus_csv.py   # 从 CSV 生成 Profile
    smoke_test_*.py         # 冒烟测试
  docs/                     # 设计与联调文档
```

## 界面说明

| 面板 | 说明 |
|------|------|
| Serial | 串口、波特率、从站 ID、扫描模式、启停 |
| Device Profile | 当前协议名称与描述 |
| Log | 运行日志 |
| State | 关键寄存器/线圈实时值 |
| Message | 收发报文流 |
| Registers | 点表，支持搜索、编辑、Capture 开关 |
| Packet Capture | 上方列表为抓包摘要，下方详情区显示完整报文 |

在 Registers 表第 7 列 `Capture` 点击 `ON/OFF` 可启用/关闭对应点位的抓包。

## Profile 与 Preset

**Profile**（`profiles/`）定义设备协议：寄存器/线圈地址、名称、读写权限、枚举、单位、串口默认值、默认 State 字段等。

**Preset**（`presets/`）定义运行场景：初始寄存器/线圈值、EMS 网页侧联动数据、启用的 Capture 点位、State 面板字段等。

| 操作 | 菜单 |
|------|------|
| 导入 Profile | File → Import Register Profile JSON... |
| 导入 Preset | File → Import Simulator Preset JSON... |
| 导出 Profile | File → Save Profile As... |
| 导出 Preset | File → Save Preset As... |

Preset 中 `profile_id` 必须与当前 Profile 一致，否则拒绝加载。

Preset 可选字段示例：

```json
{
  "capture_points": [
    {"kind": "register", "address": 334},
    {"kind": "coil", "address": 6400}
  ],
  "state_fields": [
    {"label": "State", "kind": "register", "address": 4}
  ]
}
```

旧版 Preset 不含上述字段时仍可加载；Capture 默认全关，State 回退到 Profile 默认值。

## 快速开始

1. 安装 Python 3.10+
2. 安装依赖：

```powershell
pip install -r requirements.txt
```

3. 启动 GUI：

```powershell
python app.py
```

### 启动时自动导入 Preset / Profile

`python app.py` 支持可选参数，启动时自动导入指定 preset/profile 并完成连接设置，免去手动点菜单：

```powershell
# 传入 preset 文件：自动按其 profile_id 切换 Profile 并应用 Preset
python app.py presets\dm_hp3_rs48_v2\dm_hp3_rs48_v2_ems_single_hs5_group_ctrl.json

# 传入目录：自动扫描目录下的 preset/profile JSON 并导入
python app.py ..\csv\group_ctrl\single_hs5_group_ctrl

# 显式指定 + 自动选串口/从站 ID/扫描模式并启动
python app.py --preset <preset.json> --profile <profile.json> `
  --port COM5 --slave-id 1 --respond-range --start
```

| 参数 | 说明 |
|------|------|
| `path`（位置参数） | preset/profile JSON 文件或包含它们的目录；目录会自动扫描 `*.json` 并按内容区分 preset（含 `preset_id`）/profile（`registers` 为列表） |
| `--preset` | 显式指定 preset JSON 路径 |
| `--profile` | 显式指定 profile JSON 路径 |
| `--port` | 启动后自动选中该 COM 口 |
| `--slave-id` | 设置从站 ID（1-247） |
| `--respond-range` | 启用 Respond ID 1-40 扫描模式 |
| `--start` | 完成设置后自动点击 Start 启动从站 |

preset 的 `profile_id` 与当前 Profile 不一致时，会自动在 `profiles/` 中查找匹配 Profile 并切换。导入失败会在 Log 面板提示。

4. **无 GUI 命令行从站**（CSV 联调 / 自动化）：

```powershell
python app.py --cli --port COM5 --profile dm_hp3_rs48_v2 `
  --preset presets\dm_hp3_rs48_v2\dm_hp3_rs48_v2_ems_group_ctrl_hs1_slave.json `
  --respond-1-40
```

打包后的 exe 同样支持：

```powershell
.\dist\app.dist\EMS_ModbusSlave.exe --cli --port COM5 --profile dm_hp3_rs48_v2 --respond-1-40
```

常用参数：`--list-ports` 列出串口；`--preset` 指定场景 JSON；`--baudrate` 覆盖波特率。

## stdio 控制通道（CSV 联调 / 自动化）

`--cli --stdio-control` 在 stdin/stdout 上提供 JSON-RPC 控制通道，供自动化（如 CSV 测试脚本）
以子进程方式启停从站、读写寄存器（不受 writable 权限限制的测试注入）。每行一个 JSON，UTF-8：

```powershell
python app.py --cli --stdio-control --port COM5 --profile dm_hp3_rs48_v2 `
  --preset presets\dm_hp3_rs48_v2\dm_hp3_rs48_v2_ems_group_ctrl_hs1_slave.json
```

**消息协议**（stdout 全部为 JSON 行，立即 flush）：

| 方向 | 消息 | 说明 |
|------|------|------|
| 子进程→外部 | `{"type":"ready","pid":...,"port":...,"profile":...}` | 串口打开成功，可开始控制 |
| 子进程→外部 | `{"type":"log","level":"info\|error","message":"..."}` | 串口日志事件 |
| 外部→子进程 | `{"type":"request","id":N,"op":"...","address":...,"value":...,"slave_id":...}` | 控制请求 |
| 子进程→外部 | `{"type":"response","id":N,"ok":true,...}` 或 `{"type":"response","id":N,"ok":false,"error":"..."}` | 控制响应 |
| 子进程→外部 | `{"type":"error","message":"..."}` | 串口打开失败（退出码 1） |

支持操作：

| op | 参数 | 说明 |
|----|------|------|
| `get_register` | `address`, `slave_id?` | 读单个寄存器 |
| `get_registers` | `address`, `count`, `slave_id?` | 读连续寄存器 |
| `set_register` | `address`, `value`, `slave_id?` | 注入写入（经 `set_direct`，绕过 writable 限制） |
| `get_coil` / `set_coil` | `address`, `value`(true/false/1/0) | 线圈读写 |
| `snapshot` | `slave_id?` | 全量寄存器/线圈快照 |
| `get_profile` | — | 当前 profile 信息 |
| `shutdown` | — | 优雅退出（退出码 0） |

示例（一条请求对应一条响应）：

```text
> {"type":"request","id":1,"op":"set_register","address":604,"value":200}
< {"type":"response","id":1,"ok":true,"address":604,"value":200}
```

不带 `--stdio-control` 的 `--cli` 模式行为不变（人类可读日志、阻塞运行）。

冒烟测试：`python tools\smoke_test_stdio_control.py [--port COMx]`（无端口跑单元式；带端口追加端到端）。

## 使用说明

- 默认从站 ID：`1`
- 18一体机默认波特率：`9600`
- 设备下拉框自动扫描 `profiles/*.json`
- 线圈地址与寄存器字共享底层存储，例如 `bit 6400` 对应 `word 400` 的 bit 0
- EMS 扫描多台 18一体机时，可勾选 `Respond ID 1-40`
- 勾选 `Respond ID 1-40` 后，通过 `View Slave ID` 切换表格、State 面板和 Preset 快照所查看的从站；未勾选时该值自动跟随 `Slave ID`

## 从 CSV 重新生成 18一体机 Profile

协议源文件位于上级目录 `DM-HPWT18-U1/Modbus Protocols/*.csv`。

```powershell
python tools\extract_modbus_csv.py
```

输出：

- `profiles/dm_hpwt18_u1.json`
- `docs/18一体机寄存器提取报告.md`

## 打包 Windows EXE（Nuitka + MinGW64）

### 前置条件

- Python 3.10+
- Windows 10/11
- 可访问 GitHub（首次构建需下载 Nuitka 指定的 winlibs MinGW 工具链）

> 本机其他路径下的 MinGW（如 `D:\...\mingw64`）不会被 Nuitka 使用，脚本会自动下载并缓存正确版本。

### 一键打包

```powershell
.\build_nuitka.ps1
```

脚本会：

1. 安装 `requirements.txt` 与 `build-requirements.txt`
2. 下载/复用 MinGW 到 Nuitka 缓存目录
3. 使用 `--mingw64` 编译 standalone 版本

### 输出位置

```text
dist/app.dist/EMS_ModbusSlave.exe
```

**分发时请拷贝整个 `dist/app.dist/` 文件夹**，其中包含：

- `EMS_ModbusSlave.exe`
- `profiles/`、`presets/`、`schemas/`
- PySide6 / Qt 运行时

### 打包说明

- 打包后程序从 exe 所在目录读取 `profiles/`、`presets/` 等资源
- 目标机器仍需安装 USB 转 RS485 驱动（CH340、FTDI 等）
- 首次编译约需数分钟；MinGW 缓存后再次打包会快很多
- 若 MinGW 下载超时，按脚本报错提示手动下载 zip 到缓存路径后重试

## 冒烟测试

```powershell
python tools\smoke_test_ui_performance.py
python tools\smoke_test_capture.py
python tools\smoke_test_profile.py
python tools\smoke_test_message_scroll.py
```

## 支持的 Modbus 操作

| 功能码 | 说明 |
|--------|------|
| FC01 | Read Coils |
| FC03 | Read Holding Registers |
| FC05 | Write Single Coil |
| FC06 | Write Single Register |
| FC0F | Write Multiple Coils |
| FC10 | Write Multiple Registers |

## 相关文档

设计与联调文档见 `docs/`，上级目录 `../Test doc/` 中有 EMS 联调指南与测试报告。

JSON 配置交接：

- `docs/下一个Agent生成JSON规范.md`
- `docs/下一个Agent生成JSON最小模板.md`

## 后续计划

- 异常响应注入
- 寄存器快照导入/导出增强
- 脚本化测试执行
