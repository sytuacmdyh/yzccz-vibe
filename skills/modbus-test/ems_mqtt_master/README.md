# EMS Workflow MQTT 下发工具 (GUI)

通过 MQTT over WebSocket 将 workflow JSON 下发到 ESP32（`sync_workflow_config`），
实时显示设备上行消息与 ack 结果。界面风格与 EMS Modbus Slave 一致（PySide6）。

## 运行

```powershell
pip install -r tools\mqtt_workflow_gui\requirements.txt
python tools\mqtt_workflow_gui\app.py          # GUI
python tools\mqtt_workflow_gui\app_cli.py -h  # 无 GUI 命令行（脚本/联调调用）
```

## CLI（app_cli.py）

连接参数默认读 `config.json`，命令行参数优先覆盖（host 可含 `wss://` 等 scheme）。

```powershell
# 下发完整信封并等待 ack（默认期望 code=0）
python app_cli.py send --json '{"id":1,"method":"sync_workflow_config","params":{...}}'
python app_cli.py send --file env.json --expect 0

# 用 --id/--method/--params 组装信封
python app_cli.py send --method sync_workflow_config --id 1001 --params '{"flow_id":1,"revision":2}'
python app_cli.py send --method execute_workflow --id 20001 --params '{"flow_id":1,"revision":1,"run_id":"run-1","triggered_by":"mqtt-gui"}'

# 监听上行消息（含调试流 up/{pid}/{did}/log），Ctrl+C 提前退出
python app_cli.py watch --seconds 60

# 打印 code 对照表（脚本里可用 "code=$(cut -f1 ...)" 方式解析）
python app_cli.py codes
```

**退出码**：`0`=收到 ack 且 code 等于期望值；`1`=ack code≠期望；`2`=超时未收到 ack；
`3`=连接失败/断开；`4`=参数或配置错误；`5`=publish 失败。
ack 按 (id, method) 双向匹配（`--no-match-method` 可只按 id），不匹配的 ack 会打印提示并继续等待。

## 功能

- **连接设置**：菜单「文件 → 连接设置...」打开弹窗编辑 host / port / websocket path /
  用户名 / 密码 / product_id / device_id / ack 超时 / 订阅 up/+/+，点「保存为默认配置」
  写入 `config.json`（git 已忽略，位于 `tools/mqtt_workflow_gui/config.json`，仅合并更新
  连接字段，不影响自定义预设等其它配置）。主界面只保留「连接/断开」切换按钮与状态。
  旧版本曾把配置写到 `tools/config.json`，启动时会自动迁移一次（原文件保留作备份）。
- **下发消息**：
    - 参数模式：按 method 生成对应表单，自动包裹成 JSON-RPC 信封（内容超出时可纵向滚动）。
    sync_workflow_config 对应 schema v2 全量可视化：**标量字段** + **触发器**（5 种，含 `manual`）
    + **节点**（service / exclusive_gw）+ **动作**（write/copy/add/add_reg/min/max/clamp）
    + **条件网关**（flows/guards，支持字面值或属性引用比较）；op=delete 时仅下发四字段；
    execute_workflow 对已配置且 enabled 的 manual 工作流立即入队（`flow_id` / `revision` / `run_id` / `triggered_by`）；
    set/get_properties 为多行属性表（可添加多组，value 支持数字 / true / false）；
    ota_start 为固件类型/版本/长度/MD5；
    time（时间同步）与 sync_weather（天气/环境信息）为专用表单：常用时区下拉
    （默认 Asia/Shanghai +08:00，可改偏移）、「同步当前时间」按钮，发送前自动把缺失或
    过期的 timestamp/observed_at 刷新为当前时间并重算 timeStr/tz/timezone_id 等派生字段
    （固件会拒绝比上次成功更旧的 timestamp，同 home_id 下 observed_at 须单调递增）；
    两者 ESP32/GD32 均**不发布 MQTT ack**，发送后不等待 ack 直接提示已发送。
  - 原始JSON：完整消息原样发送；「格式化 JSON」按钮将信封重新缩进排版（无效 JSON 时提示不改动）
  - 预置消息：连通性测试 / EMS自身 / 逆变器 / 热泵 / 错误路径；**单击**自动填入下发消息
    （保持当前页签不跳转，后台同步表单与信封），右键「编辑预设...」弹窗修改
    （内置预设编辑后持久化覆盖）、「删除预设」删除自定义预设或持久化隐藏内置预设。
- **结果监控**：消息栏实时解析收/发消息为单行摘要（接收左绿、发送右蓝），点击展开
  完整 JSON；ack 结果显示 code 及含义；右侧日志区保留原始收发细节。
- **状态持久化**：关闭窗口时自动保存 method / qos / request_id 与上次的下发内容
  （`last_params`），下次启动恢复（含参数表单同步）。

## 目录结构

```
tools/mqtt_workflow_gui/
├── app.py                     # 入口
├── requirements.txt
├── config.json                # 本地连接配置（创建后 gitignore）
└── src/mqtt_workflow_gui/
    ├── config.py              # 默认配置 + config.json 读写/迁移 + 合并保存
    ├── presets.py             # 预置消息 + ACK code 对照
    ├── mqtt_worker.py         # MQTT(WSS) daemon 线程 + Qt 信号桥
    ├── cli.py                 # 无 GUI CLI：send / watch / codes
    ├── models/
    │   └── methods.py         # method 常量与参数表单 schema
    └── gui/
        ├── main_window.py     # MainWindow（连接/发送/监控/文件，组合以下 Mixin）
        ├── param_form.py      # 参数表单：字段构建、属性行、工作流编辑器入口
        ├── workflow_editor.py # trigger / nodes 可视化编辑（schema v2）
        ├── envelope_sync.py   # 表单⇄信封双向同步
        └── preset_dialog.py   # PresetEditDialog + 预设 CRUD
```

## 运行测试

```powershell
python -m pytest tests -q   # 需先 pip install pytest（无需真实 MQTT）
```

## 打包为 exe

```powershell
.\build.ps1            # 一键打包：装依赖 -> PyInstaller -> 同步 JSON 到 dist
.\build.ps1 -SkipSync  # 仅打包，不同步 JSON
```

产物位于 `dist\EMS_Workflow_MQTT\EMS_Workflow_MQTT.exe`。将整个 `dist\EMS_Workflow_MQTT\` 文件夹拷贝给用户即可使用。

修改了源码 `config` 目录下的 `config.json / methods.json / codes.json / presets.json / last_params.json`
后，可单独一键同步到 dist：

```powershell
.\sync_json.ps1           # 源码 -> dist（打包已自动执行）
.\sync_json.ps1 -Reverse  # dist -> 源码（备份/还原用）
```

首次运行会在 exe 同目录的 `config` 子目录自动从模板生成以下 JSON 配置文件（可直接编辑）：

- `config/config.json` — 连接参数
- `config/methods.json` — method 表单 schema
- `config/codes.json` — ACK code 对照表
- `config/presets.json` — 预置消息
- `config/last_params.json` — 上次下发内容（运行时自动保存）

```
EMS_Workflow_MQTT/
├── EMS_Workflow_MQTT.exe
├── _internal/          # PyInstaller 依赖，勿删
└── config/
    ├── config.json
    ├── methods.json
    ├── codes.json
    ├── presets.json
    └── last_params.json
```

## 测试流程建议

预置消息按固件真实行为设计（device_index 0=EMS 自身、255=逆变器可透传；device_ids 必须已注册）：

1. 选「1. 连通性测试」发送，等 ack（code=16 说明链路通、GD32 拒空结构）
2. 选「2. EMS 自身」或「3. 逆变器」验证 GD32 是否接受该结构
3. 选「4. 已注册热泵」填入真实已注册设备的 24 位 hex device_id
4. code != 0 时对照 CODE_LEGEND 定位：20=未注册设备、21=广播拒绝、16=GD32 结构错误、22=queued MANUAL 拒绝配置替换
5. 手动控制：先用 `sync_workflow_config` 下发 `trigger.type=manual` 且 enabled 的工作流；再切 `execute_workflow`，`flow_id`/`revision` 须与已存储精确匹配。`code=0` 表示**已入队**（相同 `run_id` 重投仍为 0），不是节点已跑完；execute 失败一律 `code=1`

> ack 等待默认 25s（WORKFLOW_SYNC_REQUEST_BUDGET=20s + 余量；execute_workflow UART 最多 5s）