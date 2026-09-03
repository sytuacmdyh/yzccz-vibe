from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).parents[1]
    / "skills"
    / "keil-wsl-build"
    / "scripts"
    / "keil_build.py"
)
SPEC = importlib.util.spec_from_file_location("keil_build", SCRIPT)
assert SPEC and SPEC.loader
keil_build = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = keil_build
SPEC.loader.exec_module(keil_build)


def initialize_repository(path: Path) -> str:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Test User"], check=True
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.com"],
        check=True,
    )
    (path / "tracked.txt").write_text("base\n", encoding="utf-8")
    (path / "staged.txt").write_text("base\n", encoding="utf-8")
    (path / "deleted.txt").write_text("base\n", encoding="utf-8")
    (path / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-qm", "initial"], check=True
    )
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        ("git@example.com:group/project.git", "example.com/group/project"),
        ("ssh://git@example.com/group/project.git", "example.com/group/project"),
        ("ssh://git@example.com:2222/group/project.git", "example.com:2222/group/project"),
        ("https://EXAMPLE.com/group/project.git", "example.com/group/project"),
        ("https://example.com/group/project/", "example.com/group/project"),
        ("/srv/git/group/project.git", "srv/git/group/project"),
    ],
)
def test_normalize_remote(remote: str, expected: str) -> None:
    assert keil_build.normalize_remote(remote) == expected


def test_default_config_path_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    explicit = tmp_path / "explicit.json"
    monkeypatch.setenv(keil_build.CONFIG_ENV, str(explicit))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert keil_build.default_config_path() == explicit

    monkeypatch.delenv(keil_build.CONFIG_ENV)
    assert keil_build.default_config_path() == (
        tmp_path / "xdg" / "yzc-keil-wsl-build" / "config.json"
    )


def test_save_and_load_config_atomically_with_private_permissions(tmp_path: Path) -> None:
    path = tmp_path / "private" / "config.json"
    config = {
        "version": 1,
        "keil": {"uv4": "<UV4>"},
        "projects": {"example.com/group/repo": {"windows_repo": "<REPO>"}},
    }

    keil_build.save_config(path, config)

    assert keil_build.load_config(path) == config
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert list(path.parent.glob(f".{path.name}.*")) == []


def test_configuration_status_reports_missing_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(keil_build, "require_tools", lambda *args: None)
    monkeypatch.setattr(
        keil_build,
        "repo_remote",
        lambda repo: ("git@example.com:group/project.git", "example.com/group/project"),
    )
    monkeypatch.setattr(keil_build, "suggested_uv4", lambda: None)

    status = keil_build.configuration_status(tmp_path, tmp_path / "missing.json")

    assert status["ok"] is False
    assert status["remote_id"] == "example.com/group/project"
    assert status["missing"] == [
        "keil.uv4",
        "projects.example.com/group/project.windows_repo",
    ]
    assert status["invalid"] == []


def test_prepare_source_snapshot_reuses_clean_head(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    head = initialize_repository(repository)

    snapshot = keil_build.prepare_source_snapshot(repository, tmp_path / "transfer")

    assert snapshot.base_commit == head
    assert snapshot.commit == head
    assert snapshot.tree_hash == keil_build.git_text(
        repository, "rev-parse", "HEAD^{tree}"
    )
    assert snapshot.dirty is False
    assert snapshot.includes_untracked is False


def test_prepare_source_snapshot_captures_dirty_worktree_without_altering_it(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    transfer = tmp_path / "transfer"
    repository.mkdir()
    transfer.mkdir()
    head = initialize_repository(repository)
    index = Path(
        subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "--git-path", "index"],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()
    )
    if not index.is_absolute():
        index = repository / index

    (repository / "tracked.txt").write_text("unstaged\n", encoding="utf-8")
    (repository / "staged.txt").write_text("staged\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repository), "add", "staged.txt"], check=True
    )
    (repository / "untracked.txt").write_text("untracked\n", encoding="utf-8")
    (repository / "nested").mkdir()
    (repository / "nested" / "new.uvprojx").write_text(
        "<Project/>", encoding="utf-8"
    )
    (repository / "ignored.txt").write_text("ignored\n", encoding="utf-8")
    (repository / "deleted.txt").unlink()
    status_before = keil_build.git(repository, "status", "--porcelain=v1").stdout
    index_before = index.read_bytes()

    snapshot = keil_build.prepare_source_snapshot(repository, transfer)

    assert snapshot.base_commit == head
    assert snapshot.commit != head
    assert snapshot.tree_hash == keil_build.git_text(
        repository, "rev-parse", f"{snapshot.commit}^{{tree}}"
    )
    assert snapshot.dirty is True
    assert snapshot.includes_untracked is True
    assert keil_build.discover_projects(repository, snapshot.commit) == [
        "nested/new.uvprojx"
    ]
    assert keil_build.git_text(repository, "show", f"{snapshot.commit}:tracked.txt") == (
        "unstaged"
    )
    assert keil_build.git_text(repository, "show", f"{snapshot.commit}:staged.txt") == (
        "staged"
    )
    assert keil_build.git_text(repository, "show", f"{snapshot.commit}:untracked.txt") == (
        "untracked"
    )
    assert (
        keil_build.git(
            repository, "cat-file", "-e", f"{snapshot.commit}:deleted.txt", check=False
        ).returncode
        != 0
    )
    assert (
        keil_build.git(
            repository, "cat-file", "-e", f"{snapshot.commit}:ignored.txt", check=False
        ).returncode
        != 0
    )
    assert keil_build.git_text(repository, "rev-parse", "HEAD") == head
    assert index.read_bytes() == index_before
    assert keil_build.git(repository, "status", "--porcelain=v1").stdout == status_before


def test_dirty_snapshot_prefers_bundle_and_removes_temporary_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    transfer = tmp_path / "transfer"
    repository.mkdir()
    transfer.mkdir()
    initialize_repository(repository)
    (repository / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    snapshot = keil_build.prepare_source_snapshot(repository, transfer)
    existence = iter([False, True])
    windows_calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        keil_build, "windows_commit_exists", lambda windows_repo, commit: next(existence)
    )
    monkeypatch.setattr(keil_build, "wsl_to_windows", lambda path: str(path))

    def fake_windows_git(
        windows_repo: str, *args: str, check: bool = True
    ) -> subprocess.CompletedProcess[bytes]:
        windows_calls.append(args)
        return subprocess.CompletedProcess(args, 0, b"", b"")

    monkeypatch.setattr(keil_build, "windows_git", fake_windows_git)

    bundle = keil_build.ensure_windows_commit(
        repository,
        r"C:\repo",
        snapshot.commit,
        transfer,
        prefer_bundle=True,
    )

    assert bundle is not None and bundle.is_file()
    assert all(call[:2] != ("fetch", "origin") for call in windows_calls)
    assert windows_calls[0][0] == "fetch"
    destination = tmp_path / "destination.git"
    subprocess.run(["git", "init", "--bare", "-q", str(destination)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(destination),
            "fetch",
            str(bundle),
            windows_calls[0][-1],
        ],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(destination), "cat-file", "-e", snapshot.commit], check=True
    )
    assert keil_build.git_text(
        repository, "for-each-ref", "--format=%(refname)", "refs/yzc-keil-build"
    ) == ""
    subprocess.run(
        ["git", "-C", str(repository), "bundle", "verify", str(bundle)], check=True
    )


def test_prepare_source_snapshot_rejects_dirty_submodule(tmp_path: Path) -> None:
    submodule = tmp_path / "submodule"
    repository = tmp_path / "repository"
    transfer = tmp_path / "transfer"
    submodule.mkdir()
    repository.mkdir()
    transfer.mkdir()
    initialize_repository(submodule)
    initialize_repository(repository)
    subprocess.run(
        [
            "git",
            "-c",
            "protocol.file.allow=always",
            "-C",
            str(repository),
            "submodule",
            "add",
            "-q",
            str(submodule),
            "deps/submodule",
        ],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-qam", "add submodule"],
        check=True,
    )
    (repository / "deps" / "submodule" / "tracked.txt").write_text(
        "dirty\n", encoding="utf-8"
    )

    with pytest.raises(keil_build.SetupError, match="dirty submodules"):
        keil_build.prepare_source_snapshot(repository, transfer)


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"Program Size: Code=10\r\n0 Error(s), 0 Warning(s).\r\n", (0, 0)),
        (b"Build target X\n0 Error(s), 4 Warning(s)\n", (0, 4)),
        (b"1 Error(s), 2 Warning(s)\n", (1, 2)),
        (b"0 Error(s), 0 Warning(s)\n2 Error(s), 5 Warning(s)\n", (2, 5)),
        (b"no build summary", None),
    ],
)
def test_parse_keil_log(content: bytes, expected: tuple[int, int] | None) -> None:
    assert keil_build.parse_keil_log(content) == expected


def test_uv4_command_uses_separate_arguments_for_wsl_interop() -> None:
    command = keil_build.uv4_command_args(
        r"C:\Program Files\Keil\UV4.exe",
        r"C:\Temp Path\firmware.uvprojx",
        "Target With Spaces",
        r"C:\Temp Path\build.log",
    )

    assert command == [
        "cmd.exe",
        "/D",
        "/C",
        r"C:\Program Files\Keil\UV4.exe",
        "-r",
        r"C:\Temp Path\firmware.uvprojx",
        "-t",
        "Target With Spaces",
        "-j0",
        "-o",
        r"C:\Temp Path\build.log",
    ]
    assert all('"' not in argument for argument in command)


def test_parse_project_targets_handles_namespace_and_duplicates(tmp_path: Path) -> None:
    project = tmp_path / "sample.uvprojx"
    project.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<Project xmlns="urn:keil">
  <Targets>
    <Target><TargetName>Application</TargetName></Target>
    <Target><TargetName>Bootloader</TargetName></Target>
    <Target><TargetName>Application</TargetName></Target>
  </Targets>
</Project>
""",
        encoding="utf-8",
    )

    assert keil_build.parse_project_targets(project) == ["Application", "Bootloader"]


def test_discover_projects_returns_only_tracked_uvprojx(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "z.uvprojx").write_text("<Project/>", encoding="utf-8")
    (tmp_path / "a.uvprojx").write_text("<Project/>", encoding="utf-8")
    (tmp_path / "untracked.uvprojx").write_text("<Project/>", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "z.uvprojx", "a.uvprojx"], check=True
    )

    assert keil_build.discover_projects(tmp_path) == ["a.uvprojx", "z.uvprojx"]


def test_select_targets_filters_project_and_target(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    project = tmp_path / "firmware.uvprojx"
    project.write_text(
        "<Project><Targets>"
        "<Target><TargetName>Debug</TargetName></Target>"
        "<Target><TargetName>Release</TargetName></Target>"
        "</Targets></Project>",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(tmp_path), "add", project.name], check=True)

    selected = keil_build.select_targets(tmp_path, project.name, ["Release"])

    assert selected == [keil_build.ProjectTarget("firmware.uvprojx", "Release")]


def test_select_targets_orders_producer_before_referencing_consumer(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "App").mkdir()
    (tmp_path / "Boot").mkdir()
    (tmp_path / "embed.s").write_text(
        r"INCBIN output\Boot\Boot.axf.bin", encoding="utf-8"
    )
    (tmp_path / "App" / "App.uvprojx").write_text(
        "<Project><Targets><Target><TargetName>App</TargetName>"
        "<TargetOption><TargetCommonOption><OutputDirectory>../output/App/</OutputDirectory>"
        "<OutputName>App</OutputName></TargetCommonOption></TargetOption>"
        "<Groups><Group><Files><File><FilePath>../embed.s</FilePath></File>"
        "</Files></Group></Groups></Target></Targets></Project>",
        encoding="utf-8",
    )
    (tmp_path / "Boot" / "Boot.uvprojx").write_text(
        "<Project><Targets><Target><TargetName>Boot</TargetName>"
        "<TargetOption><TargetCommonOption><OutputDirectory>../output/Boot/</OutputDirectory>"
        "<OutputName>Boot</OutputName></TargetCommonOption></TargetOption>"
        "</Target></Targets></Project>",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)

    selected = keil_build.select_targets(tmp_path, None, [])

    assert selected == [
        keil_build.ProjectTarget("Boot/Boot.uvprojx", "Boot"),
        keil_build.ProjectTarget("App/App.uvprojx", "App"),
    ]


def test_windows_cleanup_descendant_guard() -> None:
    parent = r"C:\Temp\yzc-keil-wsl-build"
    assert keil_build.is_windows_descendant(
        r"c:\temp\yzc-keil-wsl-build\token\worktree", parent
    )
    assert not keil_build.is_windows_descendant(parent, parent)
    assert not keil_build.is_windows_descendant(r"C:\Temp\other\worktree", parent)


def test_config_json_contains_no_runtime_only_fields(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    value = keil_build.empty_config()
    keil_build.save_config(path, value)
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk == {"version": 1, "keil": {}, "projects": {}}
    assert "logs" not in on_disk
    assert "worktree" not in on_disk
