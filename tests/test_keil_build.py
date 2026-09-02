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
