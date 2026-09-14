# Live Cortex-M debugging from WSL

Use this workflow for low-disturbance inspection or controlled RAM experiments on firmware built with Keil. It supplements the build/flash workflow; it does not replace exact-snapshot handling or Flash Download verification.

## 1. Establish the test identity

Record these before attaching:

- repository, `base_commit`, `snapshot_commit`, tree hash, and whether untracked files were included;
- exact `.uvprojx` path and target name;
- board identity and expected probe type;
- AXF, MAP, HEX, and disassembly paths from the same build;
- Flash log result and the `.uvoptx` SHA-256 when a local options file was used;
- initial operating state, safety preconditions, and which outputs may move.

The build helper's disposable Windows worktree is removed after each run. Its logs and summary survive, but its linker products do not. Capture the AXF/MAP during that build when symbol-level inspection is planned. Never substitute an AXF from an older or merely similar build.

For Arm Compiler 5 projects, Keil's `fromelf.exe` can produce useful evidence from the retained AXF:

```text
fromelf.exe --fieldoffsets --output <OFFSETS_TXT> <IMAGE_AXF>
fromelf.exe --text -c --output <DISASSEMBLY_TXT> <IMAGE_AXF>
```

Use the tool from the same Keil installation that built the image. Read symbol addresses from the current MAP and structure member offsets from the current AXF. Do not calculate target layout with the host compiler's ABI.

## 2. Resolve probe ownership and identity

Windows owns the probe in this workflow even when commands are initiated from WSL. Absence from WSL USB tools does not prove that Windows cannot see it.

1. Enumerate all probes from Windows and record their unique IDs. Do not select the first device: a machine may expose multiple CMSIS-DAP and ST-Link probes.
2. Match the requested board to an explicit unique ID and use that ID for every connection.
3. If enumeration or attach reports access denied or a busy probe, ask the user to stop the active Keil Debug session. Closing the editor is normally unnecessary.
4. Do not terminate unrelated `UV4.exe` processes automatically. Inspect window titles or ask the user which session owns the probe.
5. Re-enumerate after ownership changes; do not continue using a stale assumption about probe order.

When WSL Python cannot access a Windows-owned probe, run pyOCD with Windows Python or Windows `uv.exe`. A typical discovery command is:

```text
<WINDOWS_UV_EXE> run --with pyocd --with libusb-package pyocd list
```

Paths and installed runners are machine-specific. Discover them from Windows rather than hard-coding a username or installation directory into a repository or skill.

## 3. Attach with minimum disturbance

For observation of a running timing-sensitive target:

- use attach mode rather than reset or halt mode;
- select the probe by unique ID;
- start at a conservative debug clock, such as 1 MHz, and increase only if needed;
- disable pyOCD memory and register caches for live reads;
- verify the core is running before sampling and again before disconnecting;
- avoid breakpoints, single-step, reset, and ISR halts unless the experiment specifically requires them.

A pyOCD session used for this purpose normally sets the equivalents of:

```python
options = {
    "connect_mode": "attach",
    "frequency": 1_000_000,
    "cache.enable_memory": False,
    "cache.enable_register": False,
    "resume_on_disconnect": False,
}
```

Supply the chosen probe `unique_id`. Use the device-specific target whenever it is supported. `target_override="cortex_m"` is an emergency fallback for core and raw-memory inspection of a known Cortex-M device; it lacks a device flash algorithm and must not be used to program the board.

Debugger reads still consume bus and probe bandwidth, so attach mode is low-disturbance, not zero-disturbance. Poll only as fast as the diagnostic needs, and avoid large repeated memory reads during DMA/interrupt timing investigations.

## 4. Prove image and symbol compatibility

Before any symbol-based RAM write, prove that the retained AXF describes the firmware on the board.

1. Inspect the AXF program headers and identify executable/loadable segments that reside in the target's documented flash range.
2. Read those exact address ranges from the board.
3. Compare every byte, or compare cryptographic digests computed over identical ranges.
4. Abort the write experiment on any mismatch, unreadable range, unexpected reset, or firmware identity failure.

Do not infer compatibility from a matching version string alone. Link addresses can move after an unrelated source or linker change. Conversely, exclude RAM initialization/zero-fill from a flash comparison unless the image format explicitly stores those bytes in flash.

For read-only triage, a full image comparison is strongly preferred but may be waived when the observation uses absolute hardware registers or a firmware-exported identity block. State that limitation in the result.

## 5. Design a live experiment

Prefer firmware instrumentation over stopping the core for timing, DMA, interrupt, stack, or concurrency faults.

Use this experiment shape:

1. Define a falsifiable observation: which counter, sequence, minimum/maximum, fault flag, or invariant distinguishes the hypotheses.
2. Read and save all initial values before changing anything.
3. Validate a diagnostic magic/version field when the firmware provides one.
4. Capture a baseline window while the target remains in its natural state.
5. Change one RAM control variable at a time.
6. Discard a short transition interval, then capture a bounded test window.
7. Continuously check core state, firmware identity, reset/fault counters, and safety preconditions.
8. Store raw timestamped samples in addition to the conclusion.
9. Restore original values in a `finally`-style cleanup path, even after timeout or assertion failure.
10. Allow filters, histories, and state machines to recover, then confirm final values and that the core is running.

Every wait and poll loop needs a timeout. An unchanged signal is a valid observation; it must not turn the debug session into an unbounded monitor.

RAM injection may flow into normal control logic. A bare board or disabled power state reduces risk but does not make actuator paths intrinsically isolated. Check output ownership, interlocks, and the current operating mode before injecting data.

## 6. Keil Watch and breakpoints

- Keil Watch is appropriate for slowly changing state. A structure can update while the debugger reads its fields, so do not require cross-field totals to be instantaneously equal unless firmware publishes them atomically.
- Hardware watchpoints observe CPU accesses; they may not catch DMA writes or debugger-originated writes.
- A data breakpoint may stop after the writing instruction. Inspect the preceding instruction and call context before assigning causality.
- Install stack or memory write watchpoints only after initialization/painting has completed, otherwise expected initialization writes create false evidence.
- Halting a Cortex-M core does not guarantee every peripheral, DMA engine, or external device halts. Avoid halt-based conclusions for timing-sensitive behavior.

For a fault stop, capture at least `CFSR`, `HFSR`, `MMFAR`, `BFAR`, their validity bits, `MSP`, `PSP`, `LR`, and `PC` before reset or further execution.

## 7. Cleanup and reporting

Cleanup is part of the experiment, not an optional final step:

- restore every modified RAM value from the captured originals;
- disable diagnostic injection and temporary triggers;
- resume the core only when that matches its pre-test state and the requested outcome;
- verify final power/control state and release the probe;
- do not leave Keil and pyOCD competing for the same probe;
- report any cleanup failure prominently.

Report the exact snapshot and artifacts, probe unique ID, attach mode and clock, whether the target remained running, image-match result, baseline/test time ranges, variables read or written, raw data location, cleanup result, and remaining limitations.

Starting a simulator is not proof of communication coverage. When simulated fans, drives, or other Modbus peers are part of the setup, separately verify successful transactions or device logs before attributing a result to that simulated load.
