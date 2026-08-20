# 从站模拟器 Device Profile JSON 生成规范

## 目的

本规范用于指导下一个 Agent 为 `EMS Modbus Slave` 生成新的设备 JSON Profile。

目标不是“尽量多填”，而是“生成后可直接被模拟器正确加载、通信、显示和编辑”。

当前参考实现：

- `schemas/device_profile.schema.json`
- `profiles/dm_hpwt18_u1.json`

## 总原则

1. JSON 必须优先满足模拟器运行，而不是只满足原始协议表抄录。
2. 地址、访问权限、数据类型、枚举状态必须准确，宁可少写备注，也不能写错映射关系。
3. `registers` 和 `coils` 中的每一项都必须能被当前程序直接消费，不要引入未实现的新字段语义。
4. 除非模拟器代码已支持，否则不要自行扩展 JSON 结构。
5. 所有文本统一使用 UTF-8，中文可直接写入，不要转义成乱码。

## 顶层字段规范

顶层字段按下面结构生成：

```json
{
  "profile_id": "device_id",
  "name": "设备名称",
  "description": "简短说明",
  "source": {
    "directory": "协议目录路径",
    "protocol": "协议名称"
  },
  "slave_id": 1,
  "baudrate": 9600,
  "serial": {
    "bytesize": 8,
    "parity": "N",
    "stopbits": 1
  },
  "function_codes": [1, 3, 5, 6, 15, 16],
  "status_fields": [],
  "registers": [],
  "coils": []
}
```

要求如下：

- `profile_id`
  - 必填。
  - 使用稳定英文 ID，小写，推荐下划线风格，例如 `dm_hpwt18_u1`。
- `name`
  - 必填。
  - 用于界面展示，可写中文。
- `description`
  - 填写设备简介即可，不要塞寄存器明细。
- `source`
  - 尽量保留源协议目录和协议名，方便回溯。
- `slave_id`
  - 默认从站地址。
- `baudrate`
  - 默认波特率。
- `serial`
  - 当前统一写 `8N1`，除非协议明确不同。
- `function_codes`
  - 仅填写该设备实际支持的功能码。
  - 当前模拟器已实现：`1, 3, 5, 6, 15, 16`。
- `status_fields`
  - 用于界面顶部状态卡片。
  - 只放最关键的 3 到 6 个点位，不要把所有寄存器都塞进去。

## register 条目规范

每个寄存器对象至少包含：

```json
{
  "address": 4,
  "address_label": "word 4（YD）",
  "name": "设定开关机",
  "key": "word_4",
  "access": "rw",
  "data_type": "s16",
  "default_raw": 85,
  "min_raw": null,
  "max_raw": null,
  "unit": "",
  "precision": "",
  "transfer": "传输值=实际值",
  "enum": {
    "85": "关机",
    "170": "开机"
  },
  "description": "地址：word 4（YD）\\n读写：RW\\n数据类型：s16\\n默认值：0x55\\n传输关系：传输值=实际值\\n值/状态：\\n0x55 = 关机\\n0xAA = 开机",
  "source": {
    "file": "xxx.csv",
    "row": 8
  }
}
```

字段要求：

- `address`
  - 必填。
  - 必须是实际 Modbus word 地址。
  - 不允许写区间标题、章节标题或说明行。
- `address_label`
  - 推荐保留协议原文，如 `word 4（YD）`。
- `name`
  - 必填。
  - 必须使用协议中的点位名称。
- `key`
  - 推荐唯一、稳定、可读。
  - 推荐格式：`word_<address>` 或 `word_<address>_<ascii_name>`。
- `access`
  - 只允许 `r` 或 `rw`。
  - 不要写 `w`、`R/W`、`read` 之类。
- `data_type`
  - 当前按协议写 `u16`、`s16`、`bool` 等。
  - 若协议未写，寄存器默认 `u16`。
- `default_raw`
  - 必须是原始传输值，不是换算后的工程值。
  - 例如 `0x55` 应写成十进制 `85`。
- `min_raw` / `max_raw`
  - 填原始值范围。
  - 无明确范围时写 `null`。
- `unit`
  - 无单位时写空字符串 `""`，不要写 `/`。
- `precision`
  - 无精度时写空字符串 `""`。
- `transfer`
  - 尽量保留协议中的传输关系，例如 `传输值=实际值*10`。
- `enum`
  - 只有明确离散状态映射时才填写。
  - key 一律使用十进制字符串，例如：
    - 正确：`"85": "关机"`
    - 错误：`"0x55": "关机"`
  - 如果协议写的是十六进制状态值，`description` 可以保留十六进制文本，但 `enum` 仍必须转成十进制 key。
- `description`
  - 这里保存完整静态协议说明。
  - 可以多行。
  - 应包含地址、读写、类型、范围、默认值、单位、精度、传输关系、值/状态、备注中的可用信息。
  - 注意：当前界面 `Description` 列最终展示的是程序拼接后的单行摘要，不会原样完整显示此字段，所以该字段主要用于保真和后续处理。
- `source`
  - 建议保留来源文件名和行号。

## coil 条目规范

每个线圈对象至少包含：

```json
{
  "address": 6400,
  "address_label": "bit 6400",
  "name": "超强",
  "key": "bit_6400",
  "access": "rw",
  "default": false,
  "backing_register": 400,
  "bit_offset": 0,
  "false_label": "关闭",
  "true_label": "开启",
  "ui_level": "用户级",
  "data_type": "bool",
  "description": "地址：bit 6400\\n读写：RW\\n映射寄存器：word 400 bit 0\\n数据类型：bool\\n状态说明：FALSE = 关闭；TRUE = 开启\\nUI 层级：用户级\\n备注：不记忆",
  "source": {
    "file": "xxx.csv",
    "row": 3
  }
}
```

字段要求：

- `address`
  - 必填。
  - 必须是 bit 地址。
- `access`
  - 只允许 `r` 或 `rw`。
- `default`
  - 当前默认一般填 `false`，除非协议明确要求上电默认为 1。
- `backing_register`
  - 必填。
  - 必须是线圈映射到的 word 地址。
- `bit_offset`
  - 必填。
  - 范围 `0~15`。
  - 计算方式通常为 `address % 16`。
- `false_label` / `true_label`
  - 有明确状态文案时必须填写。
  - 若协议为空，可写空字符串。
- `ui_level`
  - 保留协议中的层级说明，例如 `用户级`、`工程级`、`厂家级`。
- `data_type`
  - 当前统一写 `bool`。
- `description`
  - 保存静态说明。
  - 可多行。

## status_fields 规范

用于顶部状态展示，格式如下：

```json
{
  "label": "设定开关机",
  "kind": "register",
  "address": 4
}
```

可选字段：

```json
{
  "label": "出水温",
  "kind": "register",
  "address": 320,
  "scale": 10,
  "suffix": "℃"
}
```

要求：

- `kind` 只允许 `register` 或 `coil`。
- `address` 必须与 `registers` 或 `coils` 中的实际地址一致。
- `scale` 仅用于顶部状态展示缩放，不影响底层寄存器值。
- 不要引用不存在的点位。

## Description 与界面展示的关系

当前程序行为如下：

- `Value` 列显示当前原始值。
- `Description` 列显示程序运行时拼接后的单行摘要。

当前单行摘要规则：

- 对寄存器：
  - 有 `enum` 时：`值/状态：85=关机, 170=开机 | 当前值：170 | 当前状态：开机`
  - 无 `enum` 时：`当前值：7`
- 对线圈：
  - `值/状态：0=关闭, 1=开启 | 当前值：1 | 当前状态：开启`

因此：

1. `enum`、`false_label`、`true_label` 的准确性非常重要。
2. `description` 原始长文本仍要保留，但不要依赖它决定界面当前值显示。
3. 若希望界面出现“当前状态”，必须提供可解析的枚举或布尔状态标签。

## 严禁事项

1. 不要把区间标题、分组说明、空白行生成为寄存器条目。
   - 例如 `word 300~999用于只读寄存器` 这类标题不能写入 `registers`。
2. 不要生成重复地址。
   - 同一个 `address` 在 `registers` 中只能出现一次。
   - 同一个 `address` 在 `coils` 中只能出现一次。
3. 不要把十六进制文本直接作为 `enum` key。
4. 不要把 `/` 当作真实值保留到最终 JSON 中。
   - 应转换为空字符串或 `null`。
5. 不要给当前代码未使用的字段发明新规则。
6. 不要将工程值误写到 `default_raw`、`min_raw`、`max_raw`。

## 生成后自检清单

生成 JSON 后，至少检查以下内容：

1. 文件能被 UTF-8 正常读取，不乱码。
2. 顶层字段齐全：`profile_id`、`name`、`slave_id`、`baudrate`、`registers`、`coils`。
3. 所有 `register.address` 唯一。
4. 所有 `coil.address` 唯一。
5. 所有 `coil.backing_register` 与 `bit_offset` 可正确映射。
6. `enum` key 均为十进制字符串。
7. `unit`、`precision`、`transfer` 中无无意义的 `/` 占位。
8. 至少抽查以下点位：
   - 一个带枚举的寄存器
   - 一个普通数值寄存器
   - 一个读写线圈
   - 一个只读线圈
9. 载入模拟器后，`Registers` 表中：
   - `Value` 显示原始值
   - `Description` 能显示正确的值/状态摘要

## 推荐生成流程

1. 从协议 CSV 或原始表中提取寄存器和线圈。
2. 过滤标题行、说明行、空白行。
3. 将十六进制默认值和枚举值统一转为十进制原始值。
4. 生成 `registers`、`coils`、`status_fields`。
5. 保留 `description` 静态说明和 `source` 来源。
6. 最后做重复地址、自定义字段、编码和关键点位抽查。

## 当前基准设备

当前基准设备为：

- `dm_hpwt18_u1`
- 文件：`profiles/dm_hpwt18_u1.json`

若下一个 Agent 为新设备生成 JSON，应优先对齐该文件的字段风格和当前模拟器的消费方式。
