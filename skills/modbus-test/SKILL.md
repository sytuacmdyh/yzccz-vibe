---
name: yzc-modbus-test
description: Run CSV-driven Modbus serial tests. Supports single file or folder batch mode. Triggers when user mentions modbus test, serial test, integration test, CSV test, or wants to verify register behavior via serial port.
metadata:
  short-description: Modbus CSV serial test runner
---

# Modbus Test Skill

Run CSV-driven Modbus serial tests against a target device via USB/serial port.

## When to Use

- User wants to run Modbus serial test
- User mentions "modbus test", "serial test", "integration test", "CSV test"
- User wants to verify register behavior via serial communication
- User provides a CSV test file and asks to run it against a device

## Parameters

The user may provide:
- **path** (required): CSV file or folder path (relative to current project or absolute)
- **--port**: Serial port (default: auto-detect)
- **--baudrate**: Baud rate (default: 115200)
- **--slave-id**: Modbus slave ID (default: 1)
- **--dry-run**: Parse only, no Modbus I/O
- **--continue-on-fail**: Continue on verification failure

## Execution Steps

1. **Determine path**: Resolve the CSV file or folder path from user input. If relative, resolve from current working directory.
2. **Detect port**: If `--port` not specified, auto-detect serial port. If multiple ports found, list them and ask user to specify.
3. **Build command**:
   ```bash
   uv run --with "pymodbus>=3.0,<4.0" --with "pyserial>=3.5,<4.0" \
     ~/.claude/skills/modbus-test/scripts/modbus_test.py <path> --port <port> [--baudrate 115200] [--slave-id 1]
   ```
   Add `--dry-run` or `--continue-on-fail` if user requested.
4. **Show command**: Display the full command before execution.
5. **Execute**: Run the command. Timeout: 120s per file, folder mode = file_count * 120s.
6. **Summarize**: Parse output and present a summary table to the user.

## Output Format

The script outputs step-by-step results (PASS/FAIL) and a summary.

**Single file**: Shows each step result and final pass/fail count.
**Folder batch**: Shows per-file results + summary table + JSON.

## CSV Format

```csv
function,address,value,description
write,607,1,Force restart
delay,0,5,Wait 5s
write,615,0,Heating mode
write,636,350,Target temp 35.0C
delay,0,1,Wait 1s
write,600,1,Power on
delay,0,2,Wait 2s
read,600,1,Verify power on
read,4250,350,Verify target temp
```

### Operations

| Operation | Behavior |
|-----------|----------|
| `write` | Write single holding register |
| `read` | Read holding register and compare (exact/range/bit) |
| `delay` | Sleep (address=0: use value as seconds; address!=0: add register value) |
| `wait` | Poll register until match or timeout; supports per-step timeout overrides |
| `read_start_time` | Read register 3080 as elapsed time baseline |

### Read Value Formats

- Integer `350`: exact match
- Range `10,500`: min <= actual <= max
- Bit `b3`: bit 3 is set

### Wait Value Formats

- `1`: use global `--wait-timeout` and `--wait-interval`
- `10,500`: range match with global wait settings
- `b3`: bit match with global wait settings
- `1;timeout=8`: exact match with an 8 second per-step timeout
- `10,500;timeout=12;interval=0.2`: range match with a 12 second timeout and 0.2 second polling interval
- `b3;timeout=5`: bit match with a 5 second per-step timeout

When inline wait options are used:
- `timeout` is required and must be greater than 0
- `interval` is optional and must be greater than 0
- Inline wait settings override the global wait loop for that CSV row only

## Constraints

- All register operations target holding registers (FC 03/06)
- Serial connection shared across entire run (no per-file reconnect)
- Device state carries over between files in batch mode
- Folder scan is non-recursive (top-level *.csv only)
- CSV encoding: UTF-8 with BOM (utf-8-sig)
- Chinese column headers supported (功能/目标地址/目标值/说明)
