# 从站模拟器 JSON 最小模板

## 用途

给下一个 Agent 直接复制使用，用最少的结构快速生成一个可被当前模拟器加载的设备 Profile。

完整规则请同时参考：

- `docs/01-ProfileJSON生成规范.md`（同目录）

## 最小可用模板

```json
{
  "profile_id": "new_device_id",
  "name": "新设备名称",
  "description": "设备简介",
  "source": {
    "directory": "协议文件目录",
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
  "status_fields": [
    {
      "label": "开关机",
      "kind": "register",
      "address": 4
    },
    {
      "label": "运行状态",
      "kind": "coil",
      "address": 8000
    }
  ],
  "registers": [
    {
      "address": 4,
      "address_label": "word 4",
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
      "description": "地址：word 4\\n读写：RW\\n数据类型：s16\\n默认值：0x55\\n传输关系：传输值=实际值\\n值/状态：\\n0x55 = 关机\\n0xAA = 开机",
      "source": {
        "file": "协议.csv",
        "row": 8
      }
    },
    {
      "address": 7,
      "address_label": "word 7",
      "name": "设定目标温度",
      "key": "word_7",
      "access": "rw",
      "data_type": "s16",
      "default_raw": 250,
      "min_raw": 50,
      "max_raw": 600,
      "unit": "℃",
      "precision": "0.1",
      "transfer": "传输值=实际值*10",
      "enum": {},
      "description": "地址：word 7\\n读写：RW\\n数据类型：s16\\n取值范围：50 ~ 600\\n单位：℃\\n精度：0.1\\n传输关系：传输值=实际值*10",
      "source": {
        "file": "协议.csv",
        "row": 11
      }
    }
  ],
  "coils": [
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
      "description": "地址：bit 6400\\n读写：RW\\n映射寄存器：word 400 bit 0\\n数据类型：bool\\n状态说明：FALSE = 关闭；TRUE = 开启",
      "source": {
        "file": "协议.csv",
        "row": 3
      }
    },
    {
      "address": 8000,
      "address_label": "bit 8000",
      "name": "压缩机状态",
      "key": "bit_8000",
      "access": "r",
      "default": false,
      "backing_register": 500,
      "bit_offset": 0,
      "false_label": "关",
      "true_label": "开",
      "ui_level": "",
      "data_type": "bool",
      "description": "地址：bit 8000\\n读写：R\\n映射寄存器：word 500 bit 0\\n数据类型：bool\\n状态说明：FALSE = 关；TRUE = 开",
      "source": {
        "file": "协议.csv",
        "row": 3
      }
    }
  ]
}
```

## 直接套用时必须改的内容

1. `profile_id`
2. `name`
3. `source.directory`
4. `source.protocol`
5. `slave_id`
6. `baudrate`
7. `function_codes`
8. `status_fields`
9. `registers`
10. `coils`

## 必须遵守的最小规则

1. `registers[].address` 不能重复。
2. `coils[].address` 不能重复。
3. `access` 只能写 `r` 或 `rw`。
4. `enum` 的 key 必须是十进制字符串。
5. `default_raw`、`min_raw`、`max_raw` 必须是原始传输值。
6. `unit`、`precision`、`transfer` 没有值时写空字符串，不写 `/`。
7. 不要把标题行、说明行、区间行写进 `registers` 或 `coils`。

## 生成后快速检查

1. JSON 用 UTF-8 保存。
2. 至少检查一个带枚举寄存器是否能显示：
   - `Value` 为原始值
   - `Description` 为 `值/状态 + 当前值 + 当前状态`
3. 至少检查一个线圈是否能显示：
   - `值/状态：0=... , 1=...`
4. 确认没有重复地址。

## 推荐参考

优先参考当前基准文件：

- `profiles/dm_hpwt18_u1.json`
