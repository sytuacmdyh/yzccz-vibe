# 儒竞压缩机变频器 V1.3

来源：`儒竞驱动板-热泵通用驱动协议-V1.3.pdf`，文档标题为《爱美泰热泵驱动(MODBUS)协议》，V1.3 修订日期 2023-08-22，第 3–14 页。本 Profile 仅包含压缩机相关点位，不包含风机 A/B、FCT 或厂家内部测试点。

## 选择型号

```text
python skills/modbus-test/ems_modbus_slave/app.py --cli --stdio-control \
  --port <inverter-port> --profile rujing_compressor_inverter_v13 --respond-1-40
```

默认串口 **4800 8N1**，默认节点 ID=1；`--respond-1-40` 可同时模拟节点 1/2，全部已定义寄存器按节点独立保存。`--slave-baudrate` 只覆盖子进程波特率，其余串口参数来自 Profile。

支持 FC03、FC06、FC16（0x10）。FC03 每帧最多读取 **50** 个寄存器，超过限制返回 `83 03`（非法数据值）；FC16 使用现有 Modbus 最大 123 字限制。Profile 可选 `max_read_registers` 范围 1–125，缺省 125，限制只用于 FC03。

## 地址与原始值

**CSV 和 stdio 均使用线上地址：文档地址减 1。** 所有字按标准 16 位大端发送，CRC 低字节先发。Profile 标签同时包含文档地址和线上地址。

| 文档地址 | 线上地址（十进制） | 用途 / 原始值 |
|---|---|---|
| 2000 | 1999 | 目标机械频率，0–120 Hz，普通精度 |
| 2001 | 2000 | bit0 压缩机启停，bit1 PFC（单相），bit2 预热；bit3 高精度暂时无效 |
| 2002–2006 | 2001–2005 | 环境温度、输入电流限制、机型选择、升降频参数；除机型选择外均标注预留未使用 |
| 2100–2102 | 2099–2101 | 运行状态、停机故障、报警故障，位定义见 Profile |
| 2103–2104 | 2102–2103 | 运行机械频率和允许最大频率，1 Hz；允许最大频率默认 120 |
| 2105 | 2104 | 散热器降频值，℃ = raw −55，预留 |
| 2106–2109 | 2105–2108 | AC电压（V）、AC电流（raw/10 A）、相电流（raw/10 A）、母线电压（V） |
| 2110–2113 | 2109–2112 | 停机故障2、散热器温度（raw−55 ℃）、累计运行小时、输出相电压（V） |
| 2114–2126 | 2113–2125 | 软件/EEPROM版本、项目编号和产品名称原始字 |
| 2127–2128 | 2126–2127 | PIM/IPM温度（raw−55 ℃）、压缩机机型码 |
| 2140 | 2139 | PFC温度（raw−55 ℃） |

环境温度是 `int16(raw)/10 ℃`：例如 `-990` 以补码 `64546 / 0xFC22` 传输。散热器/PIM/PFC 温度不是有符号值：例如 `35` 表示 `-20 ℃`。GUI 温度监控明确显示原始编码及换算公式。

2000–2006 可读写；其他已定义点位只读。`slave_write` 可绕过读写权限注入状态；未知点位不能注入或写入，连续 FC03 读取未建模点位时沿用模拟器补零行为，该行为不代表实现了风机等省略点位。目标频率沿用模拟器区间钳制到 0–120，其他点位保留完整 16 位原始字，文档中的物理范围仅作说明。

## 模拟行为

控制写入只保存原始值，不自动更新运行频率、状态、故障或版本，也不模拟掉电保持、升降频、通讯故障计时或 EEPROM 行为。默认原始值为零，只有允许最大频率默认 120；原始温度零意味着 −55 ℃，不是正常运行预设。

文档升降频描述同时出现默认 1Hz/s 与越界回退 200=2Hz/s；这些参数标为预留未使用，模拟器不推断回退规则。高精度频率暂不实现。协议没有定义独立故障复位命令，不套用 V2.4 的 `0x8000=4`。

## CSV 回环示例

可执行示例：[rujing_compressor_inverter_v13.csv](../examples/rujing_compressor_inverter_v13.csv)。使用两只互连的 USB-RS485 适配器：runner 的 `--port` 作为主站，子进程的 `--slave-port` 作为模拟驱动板从站。

```text
python skills/modbus-test/scripts/modbus_test.py \
  skills/modbus-test/ems_modbus_slave/examples/rujing_compressor_inverter_v13.csv \
  --port <master-port> --baudrate 4800 --bytesize 8 --parity N --stopbits 1 \
  --slave-port <inverter-port> --slave-profile rujing_compressor_inverter_v13 \
  --slave-respond-1-40
```

普通 `write`、`write_multi`、`read` 和 `wait` 操作经过物理串口；`slave_write`、`slave_read` 只经过子进程 stdio。示例直接访问驱动板地址，不可当作 hp-52kw 主板的上位机寄存器地址使用。与主板联调时，子进程仍接驱动总线，但普通读写须使用主板自身协议及串口参数。

PDF 第14页的参考帧已纳入无硬件测试：FC16 从 `0x07CF` 写7字（60Hz启动及0Hz停机示例均保留控制字3），FC03 从 `0x0833` 读22字。模拟器不会由频率0自动推断停止状态。

仅验证 CSV 解析（无串口访问）：

```text
python skills/modbus-test/scripts/modbus_test.py \
  skills/modbus-test/ems_modbus_slave/examples/rujing_compressor_inverter_v13.csv --dry-run --no-log
```
