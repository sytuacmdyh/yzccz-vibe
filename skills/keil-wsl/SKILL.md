---
name: yzc-keil-wsl
description: Build, flash, and verify Keil uVision/UV4 projects from WSL at an exact Git snapshot, and perform controlled live Cortex-M diagnostics with Keil, ST-Link, or pyOCD. Use when a user asks to compile, rebuild, program connected hardware, inspect live firmware state, or validate one or more .uvprojx targets, especially when WSL and Windows use separate clones or Git worktrees.
---

# Keil WSL Build, Flash, and Debug

Run Keil through its Windows CLI without opening the GUI. The bundled script validates that the WSL and Windows clones identify the same remote, transfers an exact snapshot of the WSL worktree when necessary, and builds or flashes from a disposable Windows Git worktree.

## Locate the script

Resolve `SKILL_ROOT` from the loaded skill directory. During source-repository development, it can also be found at `skills/keil-wsl`.

Run every command through `uv`:

```bash
uv run --script "$SKILL_ROOT/scripts/keil_build.py" --help
```

## Choose the operating mode

- For build-only or Flash Download work, follow the workflow below.
- For a live target inspection, variable watch, RAM instrumentation, or debugger-assisted experiment, first complete the exact-image build/flash steps that apply, then read [references/live-debugging.md](references/live-debugging.md) before attaching to the probe.
- Keep build, Flash Download, and live debug as distinct operations. Do not use `UV4.exe -d` as a substitute for a controlled command-line diagnostic session.

## Workflow

1. Show the user the command before running it.
2. Check local configuration for the current repository:

   ```bash
   uv run --script "$SKILL_ROOT/scripts/keil_build.py" config status --repo . --json
   ```

3. If the result lists missing or invalid values, read [references/configuration.md](references/configuration.md). Ask the user only for the missing Windows path values. Never invent a path or commit a personal path to Git.
4. Store values with the script's `config set-keil` and `config set-project` commands after the user supplies them, then rerun `config status`.
5. For a build request, build all `*.uvprojx` files and declared targets unless the user narrows the request:

   ```bash
   uv run --script "$SKILL_ROOT/scripts/keil_build.py" build --repo .
   ```

   Use `--project <REPOSITORY_RELATIVE_PATH>` and repeatable `--target <TARGET_NAME>` only when a narrower build was requested.
6. For a hardware programming or modification-validation request, require one explicit project and target:

   ```bash
   uv run --script "$SKILL_ROOT/scripts/keil_build.py" flash --repo . \
     --project '<REPOSITORY_RELATIVE_PATH>' --target '<TARGET_NAME>'
   ```

   The command builds the selected target's detected dependencies first, copies a same-named local `.uvoptx` from the configured Windows clone when present, and flashes only the requested target. A failed required build skips Flash Download.
7. Report the recorded `base_commit`, `snapshot_commit`, `tree_hash`, and `includes_untracked` values; every build target's error and warning counts; the Flash status and options-file hash when applicable; and the summary/log directory. The legacy `commit` field remains an alias for the snapshot commit.

## Live debug authorization

- Probe enumeration, connection diagnostics, and read-only inspection are allowed when the user asks to debug or verify connected hardware.
- Flash only when the user explicitly requests programming or when flashing is an explicit part of the requested hardware validation.
- Write RAM only when the user explicitly asks for live manipulation or an experiment that requires it. Restrict writes to validated symbols in the matching AXF and preserve the original values for cleanup.
- Never write flash through a generic pyOCD `cortex_m` target. Use the Keil Flash Download workflow with the exact project and target.
- Do not change persistent configuration, actuator commands, safety interlocks, or hardware outputs unless the user explicitly includes those effects in scope.

## Preconditions and interpretation

- When the WSL worktree has staged, unstaged, deleted, or untracked files, prefer the script's Git bundle path. It creates a temporary index from `HEAD`, adds the current non-ignored worktree state, writes a temporary commit without moving the current branch, and transfers that commit to Windows in a bundle.
- Continue to reject dirty, uninitialized, conflicted, or `HEAD`-mismatched submodules because their contents cannot be represented by the superproject snapshot alone.
- Do not clean, reset, stash, or otherwise alter either user's main worktree.
- Do not invoke or automate the uVision GUI. Builds use `UV4.exe -r ... -j0`; Flash Download uses `UV4.exe -f ... -j0`. Never use `-d`, which starts a debugging session.
- Treat parsed Keil logs as authoritative. A build with zero errors succeeds even when UV4 returns a nonzero process exit because warnings are present. Flash succeeds only when its log contains `Erase Done.`, `Programming Done.`, and `Verify OK.`.
- Flash only when the user requested hardware programming and the programmer and board are connected. The command requires one explicit project and target, but automatically builds detected producer targets first.
- Treat a same-named `.uvoptx` in the configured Windows clone as local programmer configuration. The script copies it into the disposable worktree, records its SHA-256, and never adds it to the Git snapshot. If no local sidecar exists, a tracked snapshot copy may be used; otherwise Keil must obtain sufficient Flash configuration from the project itself.
- The script may fetch objects into the Windows clone. For an uncommitted snapshot it bypasses the remote fetch and transfers the temporary commit with a Git bundle; clean commits use a bundle only when the commit is unavailable from the Windows clone and origin.
- The temporary index, ref, and bundle are removed after use. The temporary commit and tree objects become unreachable in the WSL object database and are left for normal Git garbage collection.
- Local configuration lives outside the skill and outside project repositories. Build logs and the JSON run summary are retained in the XDG state directory.
- The bundled build script retains logs and the run summary, but currently removes the disposable worktree and its AXF/MAP outputs. A symbol-level debug session therefore needs the AXF, MAP, and preferably a disassembly captured from that exact build before cleanup. If exact artifacts were not retained, rebuild and reflash rather than using artifacts from a different checkout.
