---
name: yzc-modbus-test
description: Run CSV-driven Modbus serial tests and DeviceSimulator control. Supports single file or folder batch mode. Triggers when user mentions modbus test, serial test, integration test, CSV test, simulator test, sim test, or wants to verify register behavior via serial port or simulator.
metadata:
  short-description: Modbus CSV serial test runner with DeviceSimulator support
---

# Modbus Test Skill

Run CSV-driven Modbus serial tests against a target device via USB/serial port, with optional DeviceSimulator remote control.

## When to Use

- User wants to run Modbus serial test
- User mentions "modbus test", "serial test", "integration test", "CSV test", "simulator test", "sim test"
- User wants to verify register behavior via serial communication
- User wants to control DeviceSimulator (power on/off, set properties, wait for state changes)
- User provides a CSV test file and asks to run it against a device or simulator

## Parameters

The user may provide:
- **path** (required): CSV file or folder path (relative to current project or absolute)
- **--port**: Serial port (default: auto-detect)
- **--baudrate**: Baud rate (default: 115200)
- **--slave-id**: Modbus slave ID (default: 1)
- **--time-addr**: Device logic time register address (default: 4399)
- **--session-timeout**: Maximum run time in seconds for the whole session (default: 120)
- **--recursive**: Recursively search subdirectories for CSV files
- **--dry-run**: Parse only, no Modbus I/O, no HTTP calls
- **--continue-on-fail**: On file failure, continue with the next CSV instead of stopping the batch
- **--stats**: Print per-function-type timing statistics (count/total/avg/min/max)
- **--sim-api**: DeviceSimulator API base URL (default: `http://127.0.0.1:9090`)
- **--sim-http-timeout**: HTTP request timeout for DeviceSimulator API (default: 5.0s)
- **--log-dir**: Directory for log files (default: `./logs`)
- **--no-log**: Disable file logging

## Execution Steps

1. **Determine path**: Resolve the CSV file or folder path from user input. If relative, resolve from current working directory.
2. **Detect port**: If `--port` not specified, auto-detect serial port. If multiple ports found, list them and ask user to specify. **Note**: Serial connection is skipped when CSV only contains `sim_*` and `delay(0)` operations.
3. **Build command**:
   ```bash
   uv run --with "pymodbus>=3.0,<4.0" --with "pyserial>=3.5,<4.0" \
     ~/.claude/skills/modbus-test/scripts/modbus_test.py <path> --port <port> [--baudrate 115200] [--slave-id 1] [--time-addr 4399] [--recursive]
   ```
   Add `--dry-run`, `--continue-on-fail`, `--stats`, or `--time-addr` if user requested.
   Add `--sim-api http://127.0.0.1:9090` when CSV contains `sim_*` operations.
   Add `--log-dir <path>` to customize log output directory. Add `--no-log` to disable file logging.
4. **Show command**: Display the full command before execution.
5. **Execute**: Run the command. Default timeout: 120s for the entire session. When specifying `--session-timeout`, reserve enough margin based on test case count and content (e.g., `delay`/`wait` durations, write retry overhead).
6. **Summarize**: Parse output and present a summary table to the user.

## Output Format

The script outputs step-by-step results (PASS/FAIL) and a summary.

**Single file**: Shows each step result and final pass/fail count.
**Folder batch**: Shows per-file results + summary table + JSON.
**With `--stats`**: Adds a per-function-type timing table (write/read/delay/wait etc.) with count, total, avg, min, max columns.

**File logging**: By default, a detailed log file is written to `./logs/modbus_test_YYYYMMDD_HHMMSS.log` containing step-by-step execution details, timing, and errors. The log file path is printed to stderr at startup.

## CSV Format

```csv
function,address,value,description
write,607,1,Force restart
delay,0,5,Wait 5s
write,615,0,Heating mode
write,636,350,Target temp 35.0C
write_multi,600,"1,0,350",Write 3 consecutive regs
delay,0,1,Wait 1s
write,600,1,Power on
delay,0,2,Wait 2s
read,600,1,Verify power on
read,4250,350,Verify target temp
```

### Operations

| Operation | Behavior |
|-----------|----------|
| `write` | Write single holding register; on failure wait 1s and retry up to 3 times |
| `write_multi` | Write multiple consecutive holding registers (FC16); address=starting register, value=comma-separated integers (must be quoted in CSV); max 123 registers per step, each value 0–65535; on failure wait 1s and retry up to 3 times |
| `set_slave` | Switch current Modbus slave ID for remaining steps in this file; value=new slave ID (1-247); address unused. Resets to `--slave-id` at next file in batch mode. Clears `read_start_time` baseline. |
| `read` | Read holding register and compare (exact/range/bit) |
| `delay` | Sleep (address=0: use value as seconds; address!=0: add register value); **host wall-clock time** |
| `wait` | Poll register until match or timeout; supports per-step `timeout=` (host time) or `logic_timeout=` (device time) |
| `read_start_time` | Read register at `--time-addr` (default 4399) as elapsed time observation baseline |
| `logic_delay` | Poll device logic time register until elapsed seconds; address=0 uses `--time-addr`; **device logic time** |
| `sim_power` | Power on/off device via DeviceSimulator API (controls LAN UDP connection); address=DeviceIndex(>0) |
| `sim_control` | Set device property via DeviceSimulator API; address=DeviceIndex(>0), value=`property:value` |
| `sim_read` | Read device hardware snapshot and compare; address=DeviceIndex(>0), value=`property:expected` |
| `sim_wait` | Poll device hardware snapshot until match or timeout; address=DeviceIndex(>0), value=`property:expected[;timeout=N][;interval=M]` |

### Simulator Operations

Simulator operations (`sim_*`) communicate with DeviceSimulator via HTTP API, not serial port.

**DeviceIndex**: The `address` column is a DeviceIndex (>0), mapped from `GET /api/devices` at startup.

#### sim_control

Set device properties. Value format: `property:value`

| Property | Value Type | Range | Note |
|----------|-----------|-------|------|
| `power` | bool (true/false) | — | Only writes hardware state, does NOT control network connection |
| `mode` | int | — | Mode |
| `fan_level` | int | — | Fan level |
| `target_temp` | int | 16–32 | **Celsius degrees**, not register value (e.g. use 24 for 24°C, not 240) |

**`sim_power` vs `sim_control power`**:

Both control device "power" but target different layers:

| | `sim_power` | `sim_control power` |
|---|---|---|
| **What it does** | Control LAN UDP + MQTT connection (connect/disconnect) | Write hardware power register (`2_1`) |
| **API endpoint** | `POST /api/devices/{sn}/power` | `POST /api/devices/{sn}/property` |
| **Analogy** | Plug in / unplug the device | Press the power button on the device |
| **Affects `connected`** | Yes (UDP/MQTT online status) | No |
| **Affects hardware `power`** | No | Yes |
| **Verifiable via `sim_read`** | No (no `connected` property) | Yes (`sim_read power:true`) |

**When to use which**:
- Full device power-up: `sim_power on` → then `sim_control power:true` (establish connection + set hardware state)
- Only change hardware state without touching connection: `sim_control power:true`
- Only connect/disconnect UDP/MQTT: `sim_power on/off`
- Power down: `sim_control power:false` → then `sim_power off`

#### sim_power

Power on/off device (LAN UDP + MQTT connection). Value: `on`/`off`/`true`/`false`/`1`/`0`

#### sim_read / sim_wait — Hardware Snapshot Properties

| Property | Internal Key | Type | Description |
|----------|-------------|------|-------------|
| `power` | `2_1` | bool → 1/0 | On/off |
| `mode` | `2_3` | int | Mode |
| `fan_level` | `2_4` | int | Fan level |
| `target_temp` | `2_5` | int | Target temp |
| `indoor_temp` | `3_1` | int | Indoor temp (×10) |
| `indoor_humi` | `3_2` | int | Indoor humidity |
| `fault_status` | `2_11` | int | Fault status |
| `cur_fan_speed` | `3_4` | int | Current fan speed |
| `comp_status` | `3_5` | int | Compressor status |

Value format for `sim_read`: `property:expected`
- For bool properties (`power`): `true`/`false`/`on`/`off`/`1`/`0`
- For numeric properties: exact integer, range `"min,max"` (quote in CSV), or bit `bN`

Value format for `sim_wait`: `property:expected[;timeout=N][;interval=M]`
- `timeout=N`: seconds (time-based); defaults to `--wait-timeout` value when omitted
- `interval=M`: poll interval in seconds (default: 1.0)

**Note**: `--wait-timeout` has dual meaning: poll attempts for Modbus `wait`, seconds for `sim_wait`.

#### Mixed Mode Example

```csv
function,address,value,description
sim_control,1,power:true,Set hardware power on
delay,0,3,Wait 3s
sim_control,1,mode:3,Set heating mode
sim_control,1,target_temp:24,Set target temp 24C
delay,0,2,Wait 2s
sim_read,1,power:true,Verify power on
sim_wait,1,indoor_temp:240;timeout=15,Wait indoor temp >= 24.0C
```

> **CSV tip**: Range values containing commas must be quoted: `"indoor_temp:200,260"`, not `indoor_temp:200,260`.

> **CSV tip**: `write_multi` values containing commas must be quoted: `write_multi,600,"1,2,3",Write 3 regs`, not `write_multi,600,1,2,3,Write 3 regs` (the latter splits across columns and triggers a parse error).

### Read Value Formats

- Integer `350`: exact match
- Range `10,500`: min <= actual <= max
- Bit `b3`: bit 3 is set

### Wait Value Formats

- `1`: use global `--wait-timeout` and `--wait-interval`
- `10,500`: range match with global wait settings
- `b3`: bit match with global wait settings
- `1;timeout=8`: exact match with an 8 second per-step timeout (**host wall-clock time**)
- `10,500;timeout=12;interval=0.2`: range match with a 12 second timeout and 0.2 second polling interval
- `b3;timeout=5`: bit match with a 5 second per-step timeout
- `1;logic_timeout=10`: exact match with a 10 second device logic time timeout
- `10,500;logic_timeout=15;interval=0.5`: range match with 15s device logic timeout and 0.5s polling interval

When inline wait options are used:
- Exactly one of `timeout` or `logic_timeout` is required (mutually exclusive)
- `timeout` uses host wall-clock time; `logic_timeout` uses device logic time from `--time-addr`
- `interval` is optional, must be > 0, uses host wall-clock time
- Inline wait settings override the global wait loop for that CSV row only

### Device Logic Time Contract

The register at `--time-addr` (default 4399) is a **uint16 seconds counter** that increments each second and wraps from 65535 to 0. Logic elapsed is computed as `(now - start) & 0xFFFF`. Fractional logic durations (e.g. `0.5`) are rounded up with `math.ceil` to at least 1 second, since the register granularity is 1 second.

## Constraints

- Read uses FC03, single write uses FC06, multi-register write uses FC16
- Serial connection shared across entire run (no per-file reconnect)
- Serial connection is skipped when CSV only contains `sim_*` and `delay(0)` operations (no serial port needed)
- Device state carries over between files in batch mode
- Folder scan is non-recursive by default (top-level *.csv only); use `--recursive` to search subdirectories
- CSV encoding: UTF-8 with BOM (utf-8-sig)
- Chinese column headers supported (功能/目标地址/目标值/说明)
- `--sim-api` defaults to `http://127.0.0.1:9090` (not required for `--dry-run`)
- API errors and unreachable DeviceSimulator produce step FAIL results, not script aborts
- Duplicate DeviceIndex values cause startup error
