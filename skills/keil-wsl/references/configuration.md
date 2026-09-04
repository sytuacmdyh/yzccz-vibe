# Local configuration

The script deliberately keeps machine-specific Windows paths outside Git-managed skill and project directories. The default file is:

```text
${XDG_CONFIG_HOME:-$HOME/.config}/yzc-keil-wsl-build/config.json
```

Set `YZC_KEIL_BUILD_CONFIG` or pass `--config <PATH>` to use another file. This is useful for isolated tests; do not add the resulting file to a repository.

## First use

Run the non-interactive status command first:

```bash
uv run --script "$SKILL_ROOT/scripts/keil_build.py" config status --repo . --json
```

The JSON result names each missing or invalid field. Ask the user for those values. A detected Keil candidate is only a suggestion and must not be silently accepted.

Store the Windows Keil executable:

```bash
uv run --script "$SKILL_ROOT/scripts/keil_build.py" config set-keil \
  --uv4 '<WINDOWS_PATH_TO_UV4_EXE>'
```

Associate the current repository's normalized `origin` remote with its matching Windows clone:

```bash
uv run --script "$SKILL_ROOT/scripts/keil_build.py" config set-project \
  --repo '<WSL_REPOSITORY_PATH>' \
  --windows-repo '<WINDOWS_REPOSITORY_PATH>'
```

The remote, not a WSL worktree path, is the project key. Multiple WSL worktrees for the same remote therefore share one Windows-clone mapping. Common SSH, HTTPS, and SCP-style remotes normalize to the same host-and-repository identifier when they refer to the same endpoint.

## Stored shape

The script writes this versioned structure atomically and restricts its permissions where the filesystem permits:

```json
{
  "version": 1,
  "keil": {
    "uv4": "<WINDOWS_PATH_TO_UV4_EXE>"
  },
  "projects": {
    "<NORMALIZED_REMOTE_ID>": {
      "windows_repo": "<WINDOWS_REPOSITORY_PATH>"
    }
  }
}
```

## Runtime requirements

- WSL commands: `git`, `uv`, and `wslpath`
- Windows commands visible from WSL: `git.exe` and `cmd.exe`
- A Windows Keil installation containing `UV4.exe`
- A Windows clone whose normalized `origin` matches the WSL repository

The Windows clone's current branch, checkout, and dirty files are left alone. Git objects may be fetched into it, while compilation occurs in a temporary detached worktree at an exact snapshot of the WSL worktree. For an uncommitted snapshot, the script transfers a temporary commit through a Git bundle instead of relying on the remote.

## Local Flash options

The `flash` command looks beside the selected project in the configured Windows clone for a same-named `.uvoptx` file. For example, flashing `App/MDK_Keil/App.uvprojx` uses `App/MDK_Keil/App.uvoptx` when that local sidecar exists. It is copied into the disposable worktree before invoking Keil and its SHA-256 is recorded in the run summary.

This supports programmer selections and serial numbers that should remain machine-local. Do not commit a personal `.uvoptx` merely to make automated Flash Download work. If the Windows clone has no local sidecar, the script preserves a tracked `.uvoptx` from the exact Git snapshot; if neither exists, the `.uvprojx` must contain sufficient Flash configuration.

The selected programmer and board must be connected and usable from Windows. WSL does not access the USB device directly; the Windows `UV4.exe` process does.
