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

The JSON result names each missing or invalid field and reports the resolved `source.mode` and `source.windows_repo`. Ask only for information that cannot be derived or was not already supplied. A detected Keil executable candidate is only a suggestion and must not be silently accepted.

Store the Windows Keil executable:

```bash
uv run --script "$SKILL_ROOT/scripts/keil_build.py" config set-keil \
  --uv4 '<WINDOWS_PATH_TO_UV4_EXE>'
```

## Direct WSL access (default)

When no Windows-clone mapping exists, the script converts the current repository root with `wslpath -w` and validates access using Windows Git. This supports opening the same project in Windows Keil through `\\wsl.localhost\<distribution>\...` or `\\wsl$\<distribution>\...`; no second clone or manually supplied Windows repository path is needed. Repositories on mounted Windows drives also use their converted drive path.

To switch an existing mapping to the current WSL checkout:

```bash
uv run --script "$SKILL_ROOT/scripts/keil_build.py" config set-project --repo . --wsl
```

This stores `{"mode": "wsl"}` under the repository's normalized remote ID. It derives the current checkout's path on each invocation rather than storing a distribution name or an absolute WSL path. Both modes still require an `origin` remote.

For WSL UNC paths, each Windows Git command adds `-c safe.directory=<exact-repository-path>`. This handles the Windows/WSL ownership mismatch without changing global Git configuration, disabling checks for other repositories, or requiring `GIT_CONFIG_*`/`WSLENV` overrides.

Direct access selects the source repository; builds still run against a disposable snapshot under Windows TEMP. Edits saved in Windows Keil are included in that snapshot. This validates compilation of the current files, not Keil's ability to place build outputs directly on the UNC share.

## Separate Windows clone

Associate the current repository's normalized `origin` remote with its matching Windows clone when this setup is desired:

```bash
uv run --script "$SKILL_ROOT/scripts/keil_build.py" config set-project \
  --repo '<WSL_REPOSITORY_PATH>' \
  --windows-repo '<WINDOWS_REPOSITORY_PATH>'
```

The remote, not a WSL worktree path, is the project key. Multiple WSL worktrees for the same remote therefore share one Windows-clone mapping. Common SSH, HTTPS, and SCP-style remotes normalize to the same host-and-repository identifier when they refer to the same endpoint.

Existing `windows_repo` mappings remain supported and take precedence over automatic WSL access. An invalid explicit mapping is reported rather than silently replaced by WSL mode.

## Stored shape

The script writes this versioned structure atomically and restricts its permissions where the filesystem permits. A project entry may be omitted for automatic WSL access, contain `{"mode": "wsl"}` to select it explicitly, or contain a Windows-clone mapping:

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
- Windows Git access to the current WSL repository, or a separate Windows clone whose normalized `origin` matches it

The source repository's current branch, index, and files are left alone. Compilation occurs in a temporary detached worktree at an exact snapshot of the WSL worktree. WSL mode shares the source's Git objects and needs no remote fetch or bundle transfer. A separate Windows clone may receive fetched objects; uncommitted snapshots use a Git bundle instead of relying on the remote.

## Local Flash options

The `flash` command looks beside the selected project in the source repository (the current WSL checkout or configured Windows clone) for a same-named `.uvoptx` file. For example, flashing `App/MDK_Keil/App.uvprojx` uses `App/MDK_Keil/App.uvoptx` when that local sidecar exists. It is copied into the disposable worktree before invoking Keil and its SHA-256 is recorded in the run summary. The legacy `copied_from_windows_clone` flag also covers copies from the WSL source.

This supports programmer selections and serial numbers that should remain machine-local. Keep personal `.uvoptx` files Git-ignored, including when opening the WSL checkout directly in Keil. If the source has no local sidecar, the script preserves a tracked `.uvoptx` from the exact Git snapshot; if neither exists, the `.uvprojx` must contain sufficient Flash configuration.

The selected programmer and board must be connected and usable from Windows. WSL does not access the USB device directly; the Windows `UV4.exe` process does.
