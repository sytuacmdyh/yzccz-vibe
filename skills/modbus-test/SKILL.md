---
name: yzc-modbus-test
description: Run CSV-driven Modbus serial tests and DeviceSimulator control. Supports directory mode or an ordered list of files. Triggers when user mentions modbus test, serial test, integration test, CSV test, simulator test, sim test, or wants to verify register behavior via serial port or simulator. Also drives EMS MQTT message workflows (mqtt_start/mqtt_send/mqtt_wait/mqtt_watch/mqtt_set) via the bundled EMS MQTT master for debugging.
metadata:
  short-description: Modbus CSV serial test runner with DeviceSimulator and EMS MQTT support
---

# Modbus Test Skill

Run CSV-driven Modbus serial tests against a target device via USB/serial port, with optional DeviceSimulator remote control and EMS MQTT message automation.

## When to Use

- User wants to run Modbus serial test
- User mentions "modbus test", "serial test", "integration test", "CSV test", "simulator test", "sim test"
- User wants to verify register behavior via serial communication
- User wants to control DeviceSimulator (power on/off, set properties, wait for state changes)
- User wants to send or verify EMS MQTT messages (sync commands, time sync, workflow config) as part of a test or debugging session
- User provides a CSV test file and asks to run it against a device, simulator, or MQTT broker

## Parameters

The user may provide:
- **paths** (required): Either one CSV directory or one or more CSV file paths. Directory and file-list modes cannot be mixed.
- **--port**: Serial port (default: auto-detect)
- **--baudrate**: Baud rate (default: 115200)
- **--bytesize**: Serial data bits (default: 8)
- **--parity**: Serial parity `N`, `E`, or `O` (default: `N`)
- **--stopbits**: Serial stop bits `1`, `1.5`, or `2` (default: `1`)
- **--slave-id**: Modbus slave ID (default: 1)
- **--time-addr**: Device logic time register address (default: 4399)
- **--session-timeout**: Maximum run time in seconds for the whole session (default: 120)
- **--recursive**: Recursively search subdirectories in directory mode; invalid with a file list
- **--dry-run**: Parse only, no Modbus I/O, no HTTP calls
- **--sim-api**: DeviceSimulator API base URL (default: `http://127.0.0.1:9090`)
- **--sim-http-timeout**: HTTP request timeout for DeviceSimulator API (default: 5.0s)
- **--slave-app**: Override path to the EMS Modbus Slave `app.py` (default: the `ems_modbus_slave/app.py` bundled with this skill, auto-detected; only needed to point elsewhere)
- **--slave-port**: Serial port for the slave child process (default: auto-detect)
- **--slave-profile**: Profile path or profile_id for the slave (default: `dm_hp3_rs48_v2`)
- **--slave-preset**: Preset JSON path to apply to the slave (optional)
- **--slave-baudrate**: Override the slave profile baudrate (optional)
- **--slave-slave-id**: Default slave ID for the child process (default: 1)
- **--slave-respond-1-40**: Make the child respond to Modbus slave ID 1..40 (group control bench)
- **--slave-ready-timeout**: Seconds to wait for the slave ready event (default: 10.0)
- **--slave-stop-timeout**: Seconds to wait for graceful slave shutdown before kill (default: 5.0)
- **--mqtt-app**: Override path to the EMS MQTT master `app_cli.py` (default: the `ems_mqtt_master/app_cli.py` bundled with this skill, auto-detected; only needed to point elsewhere)
- **--mqtt-config**: Path to a broker config JSON to use instead of the bundled `config/config.json` (generated from `config.template.json` on first run; optional)
- **--mqtt-connect-timeout**: Seconds to wait for the MQTT broker connection (default: 15.0)
- **--mqtt-stop-timeout**: Seconds to wait for graceful MQTT daemon shutdown before kill (default: 5.0)
- **--log-dir**: Directory for log files (default: `./logs`)
- **--no-log**: Disable file logging

## Execution Steps

1. **Determine inputs**: Use either one directory or an explicit list of CSV files. Preserve the user's order for a file list. In directory mode, scan the top level by default and add `--recursive` only when requested.
2. **Detect port**: If `--port` not specified, auto-detect serial port. If multiple ports found, list them and ask user to specify. **Note**: Serial connection is skipped when CSV only contains `sim_*`, `slave_*`, `mqtt_*` and `delay(0)` operations.
3. **Locate bundled script**: Resolve `SKILL_ROOT` before building the command. Check these directories in order and use the first one that contains `scripts/modbus_test.py`:
   - `$PWD/.agents/skills/yzc-modbus-test` for a project-level `npx skills` install
   - `$HOME/.agents/skills/yzc-modbus-test` for a global `npx skills` install
   - `$PWD/skills/modbus-test` when running directly from this repository
   If none exists, stop and report that the skill installation is incomplete.
4. **Build command**:
   ```bash
   uv run --with "pymodbus>=3.0,<4.0" --with "pyserial>=3.5,<4.0" \
     "$SKILL_ROOT/scripts/modbus_test.py" <directory> --port <port> [--recursive]
   ```
   Or pass an explicit ordered file list:
   ```bash
   uv run --with "pymodbus>=3.0,<4.0" --with "pyserial>=3.5,<4.0" \
     "$SKILL_ROOT/scripts/modbus_test.py" <file-1.csv> <file-2.csv> [<file-N.csv>] --port <port>
   ```
   Add `--dry-run`, `--time-addr`, or other supported connection/time options if requested.
   Add `--sim-api http://127.0.0.1:9090` when CSV contains `sim_*` operations.
   No `--slave-app` needed: the EMS Modbus Slave ships with this skill at `$SKILL_ROOT/ems_modbus_slave/app.py` and is auto-detected. Only pass `--slave-app <path>` (plus `--slave-port`, `--slave-preset`, etc. as needed) when a CSV contains `slave_*` operations and a different slave copy is wanted.
   The skill runs entirely through CLI, stdio JSON-RPC, and HTTP. Neither `slave_*` nor `mqtt_*` requires PySide6 or a desktop environment. The bundled GUI entry points are optional and are not used by the skill.
   When a CSV contains `mqtt_*` operations, the `ems_mqtt_master` daemon (which depends on `paho-mqtt` and `websocket-client`) is spawned. Add these to the `uv run --with` list so the daemon can import them:
   ```bash
   uv run --with "pymodbus>=3.0,<4.0" --with "pyserial>=3.5,<4.0" \
     --with "paho-mqtt>=2.0,<3" --with "websocket-client>=1.7" \
     "$SKILL_ROOT/scripts/modbus_test.py" <file.csv> --mqtt-config <broker-config.json>
   ```
   No `--mqtt-app` needed: the EMS MQTT master ships with this skill at `$SKILL_ROOT/ems_mqtt_master/app_cli.py` and is auto-detected. Only pass `--mqtt-app <path>` (plus `--mqtt-config` as needed) when a CSV contains `mqtt_*` operations and a different master copy is wanted.
   Add `--log-dir <path>` to customize log output directory. Add `--no-log` to disable file logging.
5. **Show command**: Display the full command before execution.
6. **Execute**: Run the command. Default timeout: 120s for the entire session. When specifying `--session-timeout`, reserve enough margin based on test case count and content (e.g., `delay`/`wait` durations, write retry overhead).
7. **Report**: Relay the per-file `RESULTS` and final `ERRORS` list. Use the log file for step details and failure reasons.

## Output Format

The script prints exactly two sections for a normal test session:

```text
=== RESULTS ===
Log: logs/modbus_test_YYYYMMDD_HHMMSS.log
[1/3] tests/a.csv ... PASS (12/12)
[2/3] tests/b.csv ... FAIL (4/20)
[3/3] tests/c.csv ... SKIP (DeviceSimulator unavailable)

=== ERRORS ===
tests/b.csv
```

`RESULTS` contains one line per CSV. `ERRORS` contains only failed file names and prints `None` when there are no failures. Detailed steps, timing, and failure reasons are written to the log. `--no-log` displays `Log: disabled`; a log setup failure displays `Log: unavailable`.

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
| `slave_start` | Launch the EMS Modbus Slave child process (`app.py --cli --stdio-control`) with `--slave-*` settings; address=0; waits for the ready event |
| `slave_stop` | Gracefully stop the slave child process (shutdown command, kill fallback); address=0 |
| `slave_enable` | Enable/disable a simulated node's RTU responses via stdio; address=Modbus slave ID (1–247), value=`0` (disable) or `1` (enable); requires `slave_start` |
| `slave_write` | Write slave register(s) via the stdio control channel; address=DeviceIndex(>0), value=`addr:value[;addr:value][;slave_id=N]` (register injection bypasses writable restrictions) |
| `slave_read` | Read slave register and compare; address=DeviceIndex(>0), value=`addr:expected[;slave_id=N]` (exact/range/bit) |
| `slave_wait` | Poll slave register until match or timeout; address=DeviceIndex(>0), value=`addr:expected[;timeout=N][;interval=M][;slave_id=N]` |
| `mqtt_start` | Start the EMS MQTT master daemon and connect to the broker; address=0; waits for the ready event |
| `mqtt_stop` | Disconnect from the broker and stop the MQTT daemon; address=0 |
| `mqtt_send` | Send one MQTT request envelope and await its ack; address=0, value=JSON envelope, optional trailing `;expect=N;timeout=M` |
| `mqtt_wait` | Wait for an incoming MQTT message matching method/id/code; address=0, value=`method=X[;id=N][;code=N][;timeout=M]` |
| `mqtt_watch` | Record incoming MQTT messages for N seconds and report how many were received; address=0, value=seconds |
| `mqtt_set` | Override broker config keys for the session (applies to the next `mqtt_start`); address=0, value=`key=value;key=value` |
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
| `fan_supply_demand` | int | 0–3 | Simulator-only write override for read-only thermostat property `3/7` |
| `floor_supply_demand` | int | 0–3 | Simulator-only write override for read-only thermostat property `3/8` |

Supply-demand values are `0=none`, `1=increase`, `2=decrease`, and `3=hold`. These properties remain read-only in the real thermostat protocol; DeviceSimulator accepts writes only to inject deterministic integration-test states.

**`sim_power` vs `sim_control power`**:

Both control device "power" but target different layers:

| | `sim_power` | `sim_control power` |
|---|---|---|
| **What it does** | Control LAN UDP + MQTT connection (connect/disconnect) | Write hardware power register (`2_1`) |
| **API endpoint** | `POST /api/devices/{sn}/power` | `POST /api/devices/{sn}/control` |
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
| `fan_supply_demand` | `3_7` | int | Fan-coil supply-temperature demand |
| `floor_supply_demand` | `3_8` | int | Floor-heating supply-temperature demand |

Value format for `sim_read`: `property:expected`
- For bool properties (`power`): `true`/`false`/`on`/`off`/`1`/`0`
- For numeric properties: exact integer, range `"min,max"` (quote in CSV), or bit `bN`

Value format for `sim_wait`: `property:expected[;timeout=N][;interval=M]`
- `timeout=N`: seconds (time-based); defaults to `--wait-timeout` value when omitted
- `interval=M`: poll interval in seconds (default: 1.0)

**Note**: `--wait-timeout` has dual meaning: poll attempts for Modbus `wait`, seconds for `sim_wait`.

### EMS MQTT Master Operations

`mqtt_*` operations spawn the EMS MQTT master (`ems_mqtt_master/app_cli.py`, a bundled copy of the `mqtt_workflow_gui` program from the EMS_Mqtt_Test repo) as a stdio daemon child process and talk to it over a JSON-RPC channel. One daemon holds a single broker connection for the whole session; `mqtt_start`/`mqtt_stop` manage its lifecycle. `mqtt_set` accumulates per-session config overrides that are applied on the next `mqtt_start`. MQTT steps do not need a serial port.

```csv
function,address,value,description
mqtt_set,0,"host=wss://mqtt.example.com;product_id=prod_a;subscribe_all=true",Point at broker
mqtt_start,0,,Connect to broker
mqtt_send,0,"{""id"":1,""method"":""sync_weather"",""params"":{}};expect=0;timeout=15",Sync weather and expect ack code 0
mqtt_wait,0,"method=sync_weather;id=1;code=0;timeout=15",Wait for the ack response
mqtt_watch,0,10,Collect inbound messages for 10s
mqtt_stop,0,,Disconnect
```

Value formats:

- **`mqtt_send`**: JSON request envelope (must contain `method`; typically also `id`). Optional trailing `;expect=N` (expected ack code, FAIL if different) and `;timeout=M` (seconds to wait for the ack). The parser splits from the right, so semicolons inside the JSON string are fine. The envelope supports `{host}`, `{port}`, `{product_id}`, `{device_id}`, `{username}`, `{password}`, `{client_id}` style placeholders (syntax `${key}`) that are expanded from the effective broker config (`--mqtt-config` + `mqtt_set` overrides) at run time — e.g. the workflow write target can use `"product_id":"${product_id}"` instead of hardcoding credentials.
- **`mqtt_wait`**: `method=X` and/or `id=N` and/or `code=N` (at least one required), plus optional `;timeout=M` (defaults to `--wait-timeout`). Matches the next incoming message satisfying all criteria.
- **`mqtt_watch`**: seconds to record inbound messages; `0`/empty means watch the whole remaining session time; `received=N` in the detail reports how many messages arrived.
- **`mqtt_set`**: `key=value` pairs. Recognized keys: `host`, `port`, `client_id`, `product_id`, `device_id`, `username`, `password`, `qos`, `expect_ack_code`, `subscribe_all` (bool), `timeout` (alias `ack_timeout`). Unknown keys are a parse error.

Notes:
- All `mqtt_*` steps require `address=0`; a non-zero address is a parse error.
- Config resolution at `mqtt_start`: bundled `config/config.json` (auto-generated from `config.template.json`, credentials are placeholders) → `--mqtt-config <file>` → `mqtt_set` overrides → applied when the daemon connects. Real broker credentials never enter the repo; pass them via `--mqtt-config` or `mqtt_set` at run time. `mqtt_send` envelopes may reference config values as `${key}` placeholders (e.g. `${product_id}`, `${device_id}`); they are expanded from the same effective config before sending.
- The daemon auto-refreshes time fields for `NO_ACK`/time-sync methods before sending.
- `--mqtt-app` defaults to the bundled `ems_mqtt_master/app_cli.py` (auto-detected next to the skill); a missing bundle or an invalid explicit path is a setup error (exit code 2). Runtime failures (spawn, connect, timeout) FAIL the current CSV.
- The daemon is force-killed at session end if `mqtt_stop` was not reached (logged as a warning).
- The daemon needs `paho-mqtt` and `websocket-client` installed (add them to the `uv run --with` list). It uses ordinary Python callbacks and threading, not Qt.

### EMS Modbus Slave Operations

`slave_*` operations launch and drive the EMS Modbus Slave simulator as the heatpump side of the bench. The simulator ships with this skill at `ems_modbus_slave/` (a self-contained copy of `tests/EMS Modbus Slave` from the hp-ctrl-box-gd32 repo: `app.py` + `src/` + `profiles/` + `presets/`; `dist/`, `logs/` and build artifacts are excluded). The script spawns `app.py --cli --stdio-control` as a child process and talks to it over a stdio JSON-RPC channel (no network ports). Set the launch configuration with the `--slave-*` flags; `slave_start`/`slave_stop` manage the lifecycle.

Typical topology: GD32 EMS board (Modbus master, RS485) `<->` slave child process (USB-RS485). The CSV drives the EMS board registers over `--port`, while `slave_*` inspects/injects the heatpump-side registers to verify the EMS control chain.

```csv
function,address,value,description
slave_start,0,start,Launch heatpump slave (--slave-port COM5, group-ctrl preset)
slave_write,1,"604:200",Inject buffer temp 20.0C
delay,0,2,Wait for EMS poll cycle
write,5011,350,EMS writes heating target 35.0C (proto3 11)
slave_wait,1,"11:350;timeout=10",Verify heatpump received target temp
slave_stop,0,stop,Tear down slave
```

Notes:
- Slave registers use **proto3 addresses** (the slave profile spans 0..1005): e.g. the slave-side address of EMS register `5011` is `11`. See `docs/protocal/heatpump/hs_proto3.md` and the slave profile JSON for the mapping.
- `slave_write` injects via the slave's `set_direct` (bypasses writable restrictions); use it to simulate sensor values, not to judge the protocol.
- `slave_id=N` on read/write/wait targets the per-slave register slots maintained by the slave (group control bench).
- `--slave-app` defaults to the bundled `ems_modbus_slave/app.py` (auto-detected next to the skill); a missing bundle or an invalid explicit path is a setup error (exit code 2). Runtime failures (spawn, timeout, process death) FAIL the current CSV.
- The child process is force-killed at session end if `slave_stop` was not reached (logged as a warning).

#### Per-node RTU Response Control

Use `slave_enable` to control whether a simulated node responds to actual RTU requests. No custom `--slave-app` or project adapter is required.

```csv
slave_enable,1,0,Disable node 1 RTU responses
slave_enable,1,1,Restore node 1 responses without clearing telemetry
slave_enable,2,0,Disable node 2 independently
```

The address column is the **Modbus slave ID (1–247)**, not DeviceIndex. The value must be `0` (disable) or `1` (enable). The runner sends the stdio command `set_enabled` with `slave_id` and boolean `enabled`, and waits for its acknowledgement. No Modbus control frame or special register is involved. Disabled nodes ignore RTU reads and writes; other nodes remain online, the serial port stays open, and register data is retained. New children and `reset_defaults` restore all nodes to responding. Register address `65535` has no special meaning; the former register-control interface is removed.

#### EC137 Fan Profile

For the hp-52kw bench, the main board is connected to the runner's `--port` on RS485-1 at 115200, while the fan-bus child uses `--slave-port` on RS485-2 at 19200 8N1. Fan node IDs are 1 and 2. Select the bundled `ec137_a500_c40` profile and pass `--slave-respond-1-40` so one child answers both nodes:

```text
python skills/modbus-test/ems_modbus_slave/app.py --cli --stdio-control \\
  --port <fan-port> --profile ec137_a500_c40 --respond-1-40
```

The hp-52kw fan sequence configures D16C=0 (signal source), D101=1 (speed-way), and D119=925 (maximum speed), then writes the target to D001. Fault reset is D000=4 (bit2). Telemetry is FC04 starting at D010 for five registers: D010 is speed raw (`rpm = raw * 925 / 64000`, integer truncation), D011 is motor status/fault, D012 is warning, D013 is voltage (`V = raw * 5 / 256`), and D014 is current (`A = raw * 0.2 / 256`). D010..D014 remain independently injectable through `slave_write`; the generic child does not derive telemetry from D001.

```csv
function,address,value,description
slave_start,0,start,Start EC137 child on RS485-2 (--slave-profile ec137_a500_c40 --slave-respond-1-40)
slave_write,1,"53264:32000;53265:0;53266:0;53267:1229;53268:1280;slave_id=1",Inject 1# D010..D014 raw telemetry
slave_write,1,"53264:64000;53265:0;53266:0;53267:1280;53268:2560;slave_id=2",Inject 2# D010..D014 raw telemetry
write,570,1,Enter board maintenance communication mode
write,572,600,Set board fan target
delay,0,1,Allow the board to poll both fan IDs
write,472,32,Request EC fan fault reset through board
slave_read,1,"53264:32000;slave_id=1",Verify injected 1# telemetry remains available
slave_stop,0,stop,Stop EC137 child
```

`slave_write`/`slave_read` operate on the child's stdio state and do not exercise the physical fan wire. The runner's ordinary `write`, `read`, and `wait` operations exercise the board on `--port`, whose fan traffic then crosses RS485-2 to the child. The existing hp-52kw `tests/csv/fault/ec_fan_comm.csv` uses internal RAM RESPOND/TIMEOUT simulation and therefore does not prove RS485-2, node protocol frames, or D000/D001 wire behavior.

#### Rujing Compressor Inverter V1.3 Profile

Select `--slave-profile rujing_compressor_inverter_v13` for the Rujing compressor protocol: **4800 8N1**, FC03/06/16, FC03 limited to 50 registers per request. All CSV/stdio addresses are **document address minus one** (frequency setpoint: 1999; control word: 2000; status starts at 2099). Telemetry and faults are manually injected and remain independent of control writes. Fan A/B points are excluded.

For raw units, node isolation, protocol examples and a runnable two-adapter CSV, read [Rujing V1.3 usage](ems_modbus_slave/docs/rujing_compressor_inverter_v13.md). Its ordinary CSV reads/writes directly target the inverter protocol; board integration requires the board's own upstream addresses and serial settings.

#### Compressor Inverter V2.4 Profile

This profile is only for the heat-pump compressor inverter bus, not the fan protocol. The hp-52kw firmware uses inverter node IDs 1 and 2 on `RS485_3`/USART2. Use `compressor_inverter_v24` for the inverter-side child and `--slave-respond-1-40` to keep ID 1 and ID 2 state isolated while allowing both nodes:

```text
python skills/modbus-test/ems_modbus_slave/app.py --cli --stdio-control \
  --port <inverter-port> --profile compressor_inverter_v24 --respond-1-40
```

The firmware bus format is **9600 8N2**. Configure the ordinary runner with `--baudrate 9600 --bytesize 8 --parity N --stopbits 2`; `--slave-baudrate`, when supplied, overrides only the child baudrate, while the child's parity and stop bits come from its Profile. The firmware reads FC03 from `0x6005` for 18 words (`0x6005..0x6016`); undefined gaps return zero. It writes FC06 to `0x8001` (frequency setpoint, wire value `Hz × 10`, range `0..32767`) and `0x8000` (`0x0401` start, `0x0000` stop, `0x0004` fault reset). A reset is an explicit sequence `0x8000=4`, then `0x8000=0`; the simulator does not synthesize state or clear the reset value automatically.

Profile register raw units: `0x6005` bus voltage (1 V), `0x6006` output frequency (0.1 Hz), `0x6008` output current (0.1 A), `0x6009` output torque (0.1%), `0x600A` output voltage (1 V), `0x600B` output power (0.1 kW), `0x600F` fault code, `0x6016` signed 16-bit inverter temperature. `0x8000` is the control word and `0x8001` is the frequency setpoint; all values remain standard 16-bit big-endian wire words. `slave_write` is only a stdio raw injection and bypasses register write permissions; it does not exercise the physical inverter wire.

The PDF's example uses 8N1 and a 12-word FC03 read. That differs from the current firmware's 8N2 and fixed 18-word FC03 frame; this Profile and runner example follow the firmware implementation. If a field inverter is confirmed to require 8N1, pass `--stopbits 1` to the runner and change the Profile metadata accordingly without changing the register/frame model.

Complete CSV operation example (ordinary `write`/`read`/`wait` exercise the board-side physical bus; `slave_write` injects the inverter-side values):

```csv
function,address,value,description
slave_start,0,start,Start compressor child on RS485-3 with compressor_inverter_v24
slave_write,1,"24581:700;24582:123;24584:456;24585:78;24586:380;24587:250;24591:0;24598:65456;slave_id=1",Inject ID1 inverter status raw values
slave_write,1,"24581:701;24582:124;24584:457;24585:79;24586:381;24587:251;24591:0;24598:65457;slave_id=2",Inject ID2 inverter status raw values
write,32769,1200,Set ID1 frequency to 120.0 Hz on the physical bus
write,32768,1025,Start ID1 with control word 0x0401
read,32769,1200,Verify ID1 setpoint through the physical bus
wait,24582,123,Wait for ID1 output-frequency telemetry
set_slave,0,2,Select inverter ID2
write,32769,800,Set ID2 frequency to 80.0 Hz on the physical bus
read,32769,800,Verify ID2 setpoint through the physical bus
set_slave,0,1,Select inverter ID1
write,32768,4,Issue explicit ID1 fault reset
write,32768,0,Explicitly clear ID1 reset control word
slave_stop,0,stop,Stop compressor child
```

Use the same runner command for that CSV:

```text
python skills/modbus-test/scripts/modbus_test.py <compressor.csv> \
  --port <board-port> --baudrate 9600 --bytesize 8 --parity N --stopbits 2 \
  --slave-port <inverter-port> --slave-profile compressor_inverter_v24 --slave-respond-1-40
```




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
- Serial connection is skipped when CSV only contains `sim_*`/`slave_*`/`mqtt_*` and `delay(0)` operations (no serial port needed)
- Device state carries over between files in batch mode
- Folder scan is non-recursive by default (top-level *.csv only); use `--recursive` to search subdirectories
- File-list mode preserves argument order and may include the same file more than once
- A failed or invalid CSV does not stop later files; execution within one CSV stops at its first failed step
- CSV encoding: UTF-8 with BOM (utf-8-sig)
- Chinese column headers supported (功能/目标地址/目标值/说明)
- `--sim-api` defaults to `http://127.0.0.1:9090` (not required for `--dry-run`)
- If DeviceSimulator is unreachable at session startup, every CSV containing `sim_*` is skipped as a neutral result; unrelated files still run
- HTTP/protocol errors during the startup probe remain setup errors; simulator failures after a successful probe fail the current CSV
- A session timeout fails the current CSV and skips the remaining valid CSV files
- Duplicate DeviceIndex values cause startup error
