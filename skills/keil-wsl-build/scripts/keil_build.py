#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Build Keil projects from an exact snapshot of a WSL Git worktree."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Sequence
from urllib.parse import urlsplit


CONFIG_ENV = "YZC_KEIL_BUILD_CONFIG"
CONFIG_DIR_NAME = "yzc-keil-wsl-build"
CONFIG_FILE_NAME = "config.json"
STATE_DIR_NAME = "yzc-keil-wsl-build"
DEFAULT_TIMEOUT = 300
LOG_RESULT_RE = re.compile(
    r"(\d+)\s+Error\(s\),\s*(\d+)\s+Warning\(s\)", re.IGNORECASE
)
SCP_REMOTE_RE = re.compile(r"^(?:[^@/]+@)?([^:/]+):(.+)$")


class SetupError(RuntimeError):
    """A local setup or repository precondition is not satisfied."""


@dataclass(frozen=True)
class ProjectTarget:
    project: str
    target: str


@dataclass
class BuildResult:
    project: str
    target: str
    errors: int | None
    warnings: int | None
    uv4_exit_code: int | None
    status: str
    log: str | None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "target": self.target,
            "errors": self.errors,
            "warnings": self.warnings,
            "uv4_exit_code": self.uv4_exit_code,
            "status": self.status,
            "log": self.log,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class SourceSnapshot:
    base_commit: str
    commit: str
    tree_hash: str
    dirty: bool
    includes_untracked: bool


def run(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            list(args),
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise SetupError(f"required command not found: {args[0]}") from exc
    if check and result.returncode != 0:
        detail = decode_output(result.stderr or result.stdout).strip()
        raise SetupError(f"command failed ({result.returncode}): {quote_command(args)}\n{detail}")
    return result


def decode_output(data: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030", "utf-16", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def quote_command(args: Sequence[str]) -> str:
    return " ".join(shlex.quote(str(arg)) for arg in args)


def git(
    repo: Path,
    *args: str,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    return run(["git", "-C", str(repo), *args], check=check, env=env)


def git_text(repo: Path, *args: str) -> str:
    return decode_output(git(repo, *args).stdout).strip()


def windows_git(
    windows_repo: str, *args: str, check: bool = True
) -> subprocess.CompletedProcess[bytes]:
    return run(["git.exe", "-C", windows_repo, *args], check=check)


def windows_git_text(windows_repo: str, *args: str) -> str:
    return decode_output(windows_git(windows_repo, *args).stdout).strip()


def resolve_repo(value: str) -> Path:
    candidate = Path(value).expanduser().resolve()
    result = run(["git", "-C", str(candidate), "rev-parse", "--show-toplevel"], check=True)
    return Path(decode_output(result.stdout).strip()).resolve()


def strip_dot_git(path: str) -> str:
    path = path.strip().strip("/")
    return path[:-4] if path.lower().endswith(".git") else path


def normalize_remote(remote: str) -> str:
    """Return a stable repository ID for common Git remote URL forms."""
    value = remote.strip()
    if not value:
        raise ValueError("Git remote is empty")

    scp_match = SCP_REMOTE_RE.match(value)
    if scp_match and "://" not in value and not re.match(r"^[A-Za-z]:[\\/]", value):
        host, path = scp_match.groups()
        return f"{host.lower()}/{strip_dot_git(path)}"

    parsed = urlsplit(value)
    if parsed.scheme and parsed.hostname:
        host = parsed.hostname.lower()
        if parsed.port:
            host = f"{host}:{parsed.port}"
        path = strip_dot_git(parsed.path)
        if not path:
            raise ValueError(f"Git remote has no repository path: {remote}")
        return f"{host}/{path}"

    normalized = value.replace("\\", "/").rstrip("/")
    return strip_dot_git(normalized)


def repo_remote(repo: Path) -> tuple[str, str]:
    result = git(repo, "remote", "get-url", "origin", check=False)
    if result.returncode != 0:
        raise SetupError("the WSL repository has no origin remote")
    remote = decode_output(result.stdout).strip()
    try:
        return remote, normalize_remote(remote)
    except ValueError as exc:
        raise SetupError(str(exc)) from exc


def default_config_path() -> Path:
    explicit = os.environ.get(CONFIG_ENV)
    if explicit:
        return Path(explicit).expanduser()
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / CONFIG_DIR_NAME / CONFIG_FILE_NAME


def config_path(explicit: str | None) -> Path:
    return Path(explicit).expanduser() if explicit else default_config_path()


def state_root() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return base / STATE_DIR_NAME


def empty_config() -> dict[str, Any]:
    return {"version": 1, "keil": {}, "projects": {}}


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_config()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SetupError(f"cannot read configuration {path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("version") != 1:
        raise SetupError(f"unsupported configuration format: {path}")
    if not isinstance(value.get("keil", {}), dict) or not isinstance(
        value.get("projects", {}), dict
    ):
        raise SetupError(f"invalid configuration structure: {path}")
    value.setdefault("keil", {})
    value.setdefault("projects", {})
    return value


def save_config(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=True, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temp_path.chmod(0o600)
        os.replace(temp_path, path)
        path.chmod(0o600)
    finally:
        temp_path.unlink(missing_ok=True)


def require_tools(*names: str) -> None:
    missing = [name for name in names if shutil.which(name) is None]
    if missing:
        raise SetupError(f"required command(s) not found: {', '.join(missing)}")


def windows_to_wsl(windows_path: str) -> Path:
    result = run(["wslpath", "-u", windows_path], check=True)
    value = decode_output(result.stdout).strip()
    if not value:
        raise SetupError(f"cannot convert Windows path to WSL path: {windows_path}")
    return Path(value)


def wsl_to_windows(path: Path) -> str:
    result = run(["wslpath", "-w", str(path)], check=True)
    value = decode_output(result.stdout).strip()
    if not value:
        raise SetupError(f"cannot convert WSL path to Windows path: {path}")
    return value


def cmd_environment(name: str) -> str:
    result = run(["cmd.exe", "/D", "/C", f"echo %{name}%"], check=True)
    value = decode_output(result.stdout).strip().replace("\r", "")
    if not value or value == f"%{name}%":
        raise SetupError(f"Windows environment variable {name} is not set")
    return value


def suggested_uv4() -> str | None:
    try:
        local_app_data = cmd_environment("LOCALAPPDATA")
    except SetupError:
        return None
    candidate = str(PureWindowsPath(local_app_data) / "Keil_v5" / "UV4" / "UV4.exe")
    try:
        return candidate if windows_to_wsl(candidate).is_file() else None
    except SetupError:
        return None


def validate_uv4(path: str) -> str | None:
    if not path:
        return "path is empty"
    try:
        if not windows_to_wsl(path).is_file():
            return "UV4.exe does not exist at this Windows path"
    except SetupError as exc:
        return str(exc)
    return None


def validate_windows_repo(path: str, expected_remote: str) -> str | None:
    if not path:
        return "path is empty"
    try:
        top = windows_git_text(path, "rev-parse", "--show-toplevel")
        remote = windows_git_text(top, "remote", "get-url", "origin")
        actual_remote = normalize_remote(remote)
    except (SetupError, ValueError) as exc:
        return str(exc)
    if actual_remote != expected_remote:
        return f"origin identifies {actual_remote!r}, expected {expected_remote!r}"
    return None


def configuration_status(repo: Path, path: Path) -> dict[str, Any]:
    require_tools("git", "git.exe", "cmd.exe", "wslpath")
    _, remote_id = repo_remote(repo)
    config = load_config(path)
    uv4 = config.get("keil", {}).get("uv4")
    project = config.get("projects", {}).get(remote_id, {})
    windows_repo = project.get("windows_repo") if isinstance(project, dict) else None
    missing: list[str] = []
    invalid: list[dict[str, str]] = []

    if not uv4:
        missing.append("keil.uv4")
    else:
        error = validate_uv4(str(uv4))
        if error:
            invalid.append({"field": "keil.uv4", "message": error})

    project_field = f"projects.{remote_id}.windows_repo"
    if not windows_repo:
        missing.append(project_field)
    else:
        error = validate_windows_repo(str(windows_repo), remote_id)
        if error:
            invalid.append({"field": project_field, "message": error})

    candidate = suggested_uv4() if not uv4 else None
    return {
        "ok": not missing and not invalid,
        "config_path": str(path),
        "remote_id": remote_id,
        "missing": missing,
        "invalid": invalid,
        "candidates": {"keil.uv4": candidate} if candidate else {},
        "hints": {
            "set_keil": "config set-keil --uv4 <WINDOWS_PATH_TO_UV4_EXE>",
            "set_project": (
                "config set-project --repo <WSL_REPOSITORY> "
                "--windows-repo <WINDOWS_REPOSITORY_PATH>"
            ),
        },
    }


def print_status(status: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(status, indent=2, ensure_ascii=True))
        return
    print(f"Config: {status['config_path']}")
    print(f"Remote ID: {status['remote_id']}")
    print(f"Status: {'ready' if status['ok'] else 'not ready'}")
    for field in status["missing"]:
        print(f"Missing: {field}")
    for item in status["invalid"]:
        print(f"Invalid: {item['field']}: {item['message']}")
    for field, candidate in status["candidates"].items():
        print(f"Detected candidate for {field}: {candidate}")


def require_clean_submodules(repo: Path) -> None:
    submodules = git(repo, "submodule", "status", "--recursive", check=False)
    if submodules.returncode == 0:
        bad = [
            line
            for line in decode_output(submodules.stdout).splitlines()
            if line and line[0] in "+-U"
        ]
        if bad:
            raise SetupError("submodule state does not match HEAD:\n" + "\n".join(bad))

    dirty = git(
        repo,
        "submodule",
        "foreach",
        "--quiet",
        "--recursive",
        'git diff --quiet && git diff --cached --quiet && '
        'test -z "$(git ls-files --others --exclude-standard)"',
        check=False,
    )
    if dirty.returncode != 0:
        detail = decode_output(dirty.stderr or dirty.stdout).strip()
        raise SetupError(
            "dirty submodules cannot be represented by the superproject snapshot"
            + (f":\n{detail}" if detail else "")
        )


def prepare_source_snapshot(repo: Path, temporary_dir: Path) -> SourceSnapshot:
    require_clean_submodules(repo)
    base_commit = git_text(repo, "rev-parse", "HEAD")
    status = git(
        repo,
        "status",
        "--porcelain=v1",
        "--untracked-files=normal",
        "--ignore-submodules=none",
    )
    changes = tuple(decode_output(status.stdout).splitlines())
    includes_untracked = any(line.startswith("?? ") for line in changes)
    if not changes:
        return SourceSnapshot(
            base_commit=base_commit,
            commit=base_commit,
            tree_hash=git_text(repo, "rev-parse", "HEAD^{tree}"),
            dirty=False,
            includes_untracked=False,
        )

    index = temporary_dir / "snapshot.index"
    snapshot_env = os.environ.copy()
    snapshot_env.update(
        {
            "GIT_INDEX_FILE": str(index),
            "GIT_AUTHOR_NAME": "YZC Keil Build",
            "GIT_AUTHOR_EMAIL": "yzc-keil-build@localhost",
            "GIT_COMMITTER_NAME": "YZC Keil Build",
            "GIT_COMMITTER_EMAIL": "yzc-keil-build@localhost",
        }
    )
    git(repo, "read-tree", "HEAD", env=snapshot_env)
    git(repo, "add", "--all", "--", ".", env=snapshot_env)
    tree = decode_output(git(repo, "write-tree", env=snapshot_env).stdout).strip()
    commit = decode_output(
        git(
            repo,
            "commit-tree",
            tree,
            "-p",
            base_commit,
            "-m",
            "Temporary snapshot for Keil build",
            env=snapshot_env,
        ).stdout
    ).strip()
    return SourceSnapshot(
        base_commit=base_commit,
        commit=commit,
        tree_hash=tree,
        dirty=True,
        includes_untracked=includes_untracked,
    )


def parse_project_targets(path: Path) -> list[str]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise SetupError(f"cannot parse Keil project {path}: {exc}") from exc
    targets: list[str] = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == "TargetName" and element.text:
            name = element.text.strip()
            if name and name not in targets:
                targets.append(name)
    if not targets:
        raise SetupError(f"Keil project contains no targets: {path}")
    return targets


def local_name(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def target_element(project: Path, target_name: str) -> ET.Element | None:
    try:
        root = ET.parse(project).getroot()
    except (OSError, ET.ParseError) as exc:
        raise SetupError(f"cannot parse Keil project {project}: {exc}") from exc
    for element in root.iter():
        if local_name(element) != "Target":
            continue
        names = [
            child.text.strip()
            for child in element
            if local_name(child) == "TargetName" and child.text and child.text.strip()
        ]
        if target_name in names:
            return element
    return None


def first_xml_text(element: ET.Element, name: str) -> str | None:
    for child in element.iter():
        if local_name(child) == name and child.text and child.text.strip():
            return child.text.strip()
    return None


def referenced_text(repo: Path, project: str, target: ET.Element) -> str:
    project_path = repo / project
    chunks = [project_path.read_text(encoding="utf-8", errors="ignore")]
    text_suffixes = {
        ".asm", ".bat", ".c", ".cc", ".cmd", ".cpp", ".h", ".hpp",
        ".inc", ".ini", ".py", ".s", ".txt",
    }
    for element in target.iter():
        if local_name(element) != "FilePath" or not element.text:
            continue
        relative = PurePosixPath(element.text.strip().replace("\\", "/"))
        if relative.is_absolute() or "$" in str(relative):
            continue
        candidate = (project_path.parent / Path(relative)).resolve()
        try:
            candidate.relative_to(repo)
        except ValueError:
            continue
        try:
            if (
                candidate.is_file()
                and candidate.suffix.lower() in text_suffixes
                and candidate.stat().st_size <= 2 * 1024 * 1024
            ):
                chunks.append(candidate.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
    return "\n".join(chunks).replace("\\", "/").casefold()


def target_output_token(repo: Path, item: ProjectTarget, target: ET.Element) -> str | None:
    output_directory = first_xml_text(target, "OutputDirectory")
    output_name = first_xml_text(target, "OutputName")
    if not output_directory or not output_name or "$" in output_directory:
        return None
    relative = PurePosixPath(output_directory.replace("\\", "/"))
    if relative.is_absolute():
        return None
    output_path = ((repo / item.project).parent / Path(relative)).resolve()
    try:
        relative_output = output_path.relative_to(repo).as_posix()
    except ValueError:
        return None
    return f"{relative_output.rstrip('/')}/{output_name}".casefold()


def order_targets_by_dependencies(
    repo: Path, selected: Sequence[ProjectTarget]
) -> list[ProjectTarget]:
    """Stably order targets when their text inputs reference another target's output."""
    elements: dict[ProjectTarget, ET.Element] = {}
    outputs: dict[ProjectTarget, str] = {}
    corpora: dict[ProjectTarget, str] = {}
    for item in selected:
        element = target_element(repo / item.project, item.target)
        if element is None:
            continue
        elements[item] = element
        token = target_output_token(repo, item, element)
        if token:
            outputs[item] = token
        corpora[item] = referenced_text(repo, item.project, element)

    dependencies: dict[ProjectTarget, set[ProjectTarget]] = {
        item: set() for item in selected
    }
    for consumer in selected:
        corpus = corpora.get(consumer, "")
        for producer, token in outputs.items():
            if producer != consumer and token in corpus:
                dependencies[consumer].add(producer)

    remaining = list(selected)
    ordered: list[ProjectTarget] = []
    while remaining:
        ready = [item for item in remaining if not (dependencies[item] & set(remaining))]
        if not ready:
            ordered.extend(remaining)
            break
        for item in ready:
            ordered.append(item)
            remaining.remove(item)
    return ordered


def discover_projects(repo: Path, treeish: str | None = None) -> list[str]:
    if treeish is None:
        result = git(repo, "ls-files", "-z", "--", "*.uvprojx")
    else:
        result = git(
            repo,
            "ls-tree",
            "-r",
            "-z",
            "--name-only",
            treeish,
        )
    projects = [
        item.decode("utf-8", errors="surrogateescape")
        for item in result.stdout.split(b"\0")
        if item
    ]
    return sorted(project for project in projects if project.endswith(".uvprojx"))


def normalize_project_filter(value: str) -> str:
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        raise SetupError("--project must be a repository-relative path")
    return path.as_posix().lstrip("./")


def select_targets(
    repo: Path,
    project_filter: str | None,
    target_filters: Sequence[str],
    treeish: str | None = None,
) -> list[ProjectTarget]:
    projects = discover_projects(repo, treeish)
    if project_filter:
        requested = normalize_project_filter(project_filter)
        projects = [project for project in projects if project == requested]
        if not projects:
            raise SetupError(f"Keil project not found in the build snapshot: {requested}")
    if not projects:
        raise SetupError("no *.uvprojx files were found in the build snapshot")

    selected: list[ProjectTarget] = []
    available_targets: set[str] = set()
    for project in projects:
        targets = parse_project_targets(repo / project)
        available_targets.update(targets)
        chosen = [target for target in targets if not target_filters or target in target_filters]
        selected.extend(ProjectTarget(project, target) for target in chosen)

    unknown = sorted(set(target_filters) - available_targets)
    if unknown:
        raise SetupError(f"Keil target(s) not found: {', '.join(unknown)}")
    if not selected:
        raise SetupError("the filters selected no Keil targets")
    return order_targets_by_dependencies(repo, selected)


def windows_commit_exists(windows_repo: str, commit: str) -> bool:
    result = windows_git(windows_repo, "cat-file", "-e", f"{commit}^{{commit}}", check=False)
    return result.returncode == 0


def ensure_windows_commit(
    wsl_repo: Path,
    windows_repo: str,
    commit: str,
    temporary_dir: Path,
    *,
    prefer_bundle: bool = False,
) -> Path | None:
    if windows_commit_exists(windows_repo, commit):
        return None
    if not prefer_bundle:
        print("Commit is absent from the Windows clone; fetching origin...")
        windows_git(windows_repo, "fetch", "origin", check=False)
        if windows_commit_exists(windows_repo, commit):
            return None

    bundle = temporary_dir / "head.bundle"
    temporary_ref = f"refs/yzc-keil-build/{uuid.uuid4().hex}"
    print("Transferring the build commit with a temporary Git bundle...")
    git(wsl_repo, "update-ref", temporary_ref, commit)
    try:
        git(wsl_repo, "bundle", "create", str(bundle), temporary_ref)
    finally:
        git(wsl_repo, "update-ref", "-d", temporary_ref, check=False)
    windows_bundle = wsl_to_windows(bundle)
    result = windows_git(windows_repo, "fetch", windows_bundle, temporary_ref, check=False)
    if result.returncode != 0 or not windows_commit_exists(windows_repo, commit):
        detail = decode_output(result.stderr or result.stdout).strip()
        raise SetupError(f"failed to transfer commit {commit} to Windows Git: {detail}")
    return bundle


def is_windows_descendant(child: str, parent: str) -> bool:
    child_parts = tuple(part.casefold() for part in PureWindowsPath(child).parts)
    parent_parts = tuple(part.casefold() for part in PureWindowsPath(parent).parts)
    return len(child_parts) > len(parent_parts) and child_parts[: len(parent_parts)] == parent_parts


def sanitize_filename(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return result or "target"


def decode_keil_log(data: bytes) -> str:
    return decode_output(data)


def parse_keil_log(data: bytes) -> tuple[int, int] | None:
    matches = LOG_RESULT_RE.findall(decode_keil_log(data))
    if not matches:
        return None
    errors, warnings = matches[-1]
    return int(errors), int(warnings)


def uv4_command_args(
    uv4: str, project: str, target: str, log: str
) -> list[str]:
    # WSL interop quotes each argv item for Windows. Embedding quotes here makes
    # cmd.exe receive literal backslashes and prevents UV4 from starting.
    return ["cmd.exe", "/D", "/C", uv4, "-r", project, "-t", target, "-j0", "-o", log]


def build_one(
    uv4: str,
    worktree_windows: str,
    worktree_wsl: Path,
    item: ProjectTarget,
    raw_log_dir_windows: str,
    raw_log_dir_wsl: Path,
    saved_log_dir: Path,
    timeout: int,
) -> BuildResult:
    digest = hashlib.sha256(f"{item.project}\0{item.target}".encode()).hexdigest()[:10]
    stem = f"{sanitize_filename(Path(item.project).stem)}-{sanitize_filename(item.target)}-{digest}.log"
    raw_log_windows = str(PureWindowsPath(raw_log_dir_windows) / stem)
    raw_log_wsl = raw_log_dir_wsl / stem
    saved_log = saved_log_dir / stem
    project_windows = str(PureWindowsPath(worktree_windows) / PureWindowsPath(item.project))
    project_cwd = worktree_wsl / PurePosixPath(item.project).parent
    print(f"Building {item.project} :: {item.target}")
    try:
        process = run(
            uv4_command_args(uv4, project_windows, item.target, raw_log_windows),
            cwd=project_cwd,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return BuildResult(
            item.project, item.target, None, None, None, "timeout", None,
            f"UV4 exceeded {timeout} seconds",
        )

    if not raw_log_wsl.is_file():
        detail = decode_output(process.stderr or process.stdout).strip()
        return BuildResult(
            item.project, item.target, None, None, process.returncode, "failed", None,
            "UV4 did not create a build log" + (f": {detail}" if detail else ""),
        )

    shutil.copy2(raw_log_wsl, saved_log)
    counts = parse_keil_log(saved_log.read_bytes())
    if counts is None:
        return BuildResult(
            item.project, item.target, None, None, process.returncode, "failed",
            str(saved_log), "could not find the Keil error/warning summary in the log",
        )
    errors, warnings = counts
    return BuildResult(
        item.project,
        item.target,
        errors,
        warnings,
        process.returncode,
        "success" if errors == 0 else "failed",
        str(saved_log),
    )


def make_run_directory() -> Path:
    timestamp = dt.datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    path = state_root() / "logs" / f"{timestamp}-{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def configured_values(repo: Path, path: Path) -> tuple[str, str, str]:
    status = configuration_status(repo, path)
    if not status["ok"]:
        print_status(status, as_json=True)
        raise SetupError(
            "local configuration is missing or invalid; collect the requested Windows paths "
            "and run the config set commands"
        )
    config = load_config(path)
    remote_id = status["remote_id"]
    return (
        str(config["keil"]["uv4"]),
        str(config["projects"][remote_id]["windows_repo"]),
        remote_id,
    )


def command_build(args: argparse.Namespace) -> int:
    require_tools("git", "git.exe", "cmd.exe", "wslpath")
    repo = resolve_repo(args.repo)
    path = config_path(args.config)
    uv4, windows_repo, remote_id = configured_values(repo, path)
    windows_root = windows_git_text(windows_repo, "rev-parse", "--show-toplevel")
    windows_temp = cmd_environment("TEMP")
    temp_parent_windows = str(PureWindowsPath(windows_temp) / CONFIG_DIR_NAME)
    token = uuid.uuid4().hex
    worktree_windows = str(PureWindowsPath(temp_parent_windows) / token / "worktree")
    if not is_windows_descendant(worktree_windows, temp_parent_windows):
        raise SetupError("refusing to create a temporary worktree outside Windows TEMP")

    temp_parent_wsl = windows_to_wsl(str(PureWindowsPath(temp_parent_windows) / token))
    temp_parent_wsl.mkdir(parents=True, exist_ok=False)
    transfer_dir = Path(tempfile.mkdtemp(prefix=f"{CONFIG_DIR_NAME}-"))
    run_dir = make_run_directory()
    saved_logs = run_dir / "logs"
    saved_logs.mkdir()
    results: list[BuildResult] = []
    cleanup_errors: list[str] = []
    worktree_added = False
    bundle: Path | None = None
    started = dt.datetime.now(dt.timezone.utc)

    try:
        snapshot = prepare_source_snapshot(repo, transfer_dir)
        selected = select_targets(repo, args.project, args.target, snapshot.commit)
        bundle = ensure_windows_commit(
            repo,
            windows_root,
            snapshot.commit,
            transfer_dir,
            prefer_bundle=snapshot.dirty,
        )
        windows_git(
            windows_root, "worktree", "add", "--detach", worktree_windows, snapshot.commit
        )
        worktree_added = True
        worktree_wsl = windows_to_wsl(worktree_windows)
        raw_log_dir_windows = str(PureWindowsPath(worktree_windows) / ".yzc-keil-build-logs")
        raw_log_dir_wsl = worktree_wsl / ".yzc-keil-build-logs"
        raw_log_dir_wsl.mkdir()
        for item in selected:
            results.append(
                build_one(
                    uv4,
                    worktree_windows,
                    worktree_wsl,
                    item,
                    raw_log_dir_windows,
                    raw_log_dir_wsl,
                    saved_logs,
                    args.timeout,
                )
            )
    finally:
        if worktree_added:
            removed = windows_git(
                windows_root, "worktree", "remove", "--force", worktree_windows, check=False
            )
            if removed.returncode != 0:
                cleanup_errors.append(
                    "git worktree remove failed: "
                    + decode_output(removed.stderr or removed.stdout).strip()
                )
        if is_windows_descendant(worktree_windows, temp_parent_windows):
            try:
                shutil.rmtree(temp_parent_wsl, ignore_errors=False)
            except FileNotFoundError:
                pass
            except OSError as exc:
                cleanup_errors.append(f"temporary directory cleanup failed: {exc}")
        else:
            cleanup_errors.append("temporary directory failed the cleanup safety check")
        if bundle is not None:
            bundle.unlink(missing_ok=True)
        shutil.rmtree(transfer_dir, ignore_errors=True)

    finished = dt.datetime.now(dt.timezone.utc)
    summary = {
        "remote_id": remote_id,
        "commit": snapshot.commit,
        "base_commit": snapshot.base_commit,
        "snapshot_commit": snapshot.commit,
        "tree_hash": snapshot.tree_hash,
        "dirty_snapshot": snapshot.dirty,
        "includes_untracked": snapshot.includes_untracked,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "results": [result.to_dict() for result in results],
        "cleanup_errors": cleanup_errors,
    }
    summary_path = run_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

    print(f"Base HEAD: {snapshot.base_commit}")
    print(f"Snapshot commit: {snapshot.commit}")
    print(f"Tree: {snapshot.tree_hash}")
    print(f"Includes untracked files: {'yes' if snapshot.includes_untracked else 'no'}")
    for result in results:
        counts = (
            f"{result.errors} error(s), {result.warnings} warning(s)"
            if result.errors is not None
            else result.detail or "no result"
        )
        print(f"{result.status.upper()}: {result.project} :: {result.target}: {counts}")
    for error in cleanup_errors:
        print(f"CLEANUP ERROR: {error}", file=sys.stderr)
    print(f"Summary: {summary_path}")
    failed = not results or any(result.status != "success" for result in results)
    return 1 if failed or cleanup_errors else 0


def command_config_status(args: argparse.Namespace) -> int:
    repo = resolve_repo(args.repo)
    status = configuration_status(repo, config_path(args.config))
    print_status(status, args.json)
    return 0 if status["ok"] else 2


def command_config_set_keil(args: argparse.Namespace) -> int:
    require_tools("wslpath")
    error = validate_uv4(args.uv4)
    if error:
        raise SetupError(f"invalid --uv4 path: {error}")
    path = config_path(args.config)
    config = load_config(path)
    config["keil"]["uv4"] = args.uv4
    save_config(path, config)
    print(f"Saved Keil path in {path}")
    return 0


def command_config_set_project(args: argparse.Namespace) -> int:
    require_tools("git", "git.exe")
    repo = resolve_repo(args.repo)
    _, remote_id = repo_remote(repo)
    error = validate_windows_repo(args.windows_repo, remote_id)
    if error:
        raise SetupError(f"invalid --windows-repo path: {error}")
    path = config_path(args.config)
    config = load_config(path)
    config["projects"][remote_id] = {"windows_repo": args.windows_repo}
    save_config(path, config)
    print(f"Saved Windows repository for {remote_id} in {path}")
    return 0


def command_config_show(args: argparse.Namespace) -> int:
    repo = resolve_repo(args.repo)
    status = configuration_status(repo, config_path(args.config))
    if args.json:
        print(json.dumps(status, indent=2, ensure_ascii=True))
    else:
        print_status(status, False)
    return 0 if status["ok"] else 2


def add_config_path(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        help=f"configuration file (default: ${CONFIG_ENV}, then the XDG config directory)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    config = commands.add_parser("config", help="inspect or update local configuration")
    config_commands = config.add_subparsers(dest="config_command", required=True)

    status = config_commands.add_parser("status", help="validate configuration for a repository")
    status.add_argument("--repo", default=".", help="WSL repository path")
    status.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    add_config_path(status)
    status.set_defaults(func=command_config_status)

    set_keil = config_commands.add_parser("set-keil", help="store the Windows UV4.exe path")
    set_keil.add_argument("--uv4", required=True, help="Windows path to UV4.exe")
    add_config_path(set_keil)
    set_keil.set_defaults(func=command_config_set_keil)

    set_project = config_commands.add_parser(
        "set-project", help="associate a Git remote with its Windows clone"
    )
    set_project.add_argument("--repo", default=".", help="WSL repository path")
    set_project.add_argument(
        "--windows-repo", required=True, help="path to the matching Windows clone"
    )
    add_config_path(set_project)
    set_project.set_defaults(func=command_config_set_project)

    show = config_commands.add_parser("show", help="show validated configuration status")
    show.add_argument("--repo", default=".", help="WSL repository path")
    show.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    add_config_path(show)
    show.set_defaults(func=command_config_show)

    build = commands.add_parser(
        "build", help="build Keil projects from an exact WSL worktree snapshot"
    )
    build.add_argument("--repo", default=".", help="WSL repository path")
    build.add_argument("--project", help="repository-relative *.uvprojx path")
    build.add_argument(
        "--target", action="append", default=[], help="target name; repeat to select several"
    )
    build.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="seconds per target")
    add_config_path(build)
    build.set_defaults(func=command_build)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "timeout", 1) <= 0:
        parser.error("--timeout must be positive")
    try:
        return int(args.func(args))
    except SetupError as exc:
        print(f"SETUP ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
