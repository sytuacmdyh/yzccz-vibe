"""外部配置文件加载（config.json / methods.json / codes.json）。"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any


class ConfigError(Exception):
    """配置文件缺失或无效。"""


def app_root() -> Path:
    """应用根目录：开发时为项目根；打包后为 exe 所在目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def bundle_root() -> Path:
    """打包资源根目录：开发时等同 app_root；frozen 时为 PyInstaller 解压目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return app_root()


def config_dir() -> Path:
    """运行时配置目录：应用根目录下的 config 子目录。"""
    return app_root() / "config"


def config_path() -> Path:
    return config_dir() / "config.json"


def config_template_path() -> Path:
    return bundle_root() / "config" / "config.template.json"


def methods_path() -> Path:
    return config_dir() / "methods.json"


def methods_template_path() -> Path:
    return bundle_root() / "config" / "methods.template.json"


def codes_path() -> Path:
    return config_dir() / "codes.json"


def codes_template_path() -> Path:
    return bundle_root() / "config" / "codes.template.json"


def presets_path() -> Path:
    return config_dir() / "presets.json"


def presets_template_path() -> Path:
    return bundle_root() / "config" / "presets.template.json"


def last_params_path() -> Path:
    return config_dir() / "last_params.json"


def _atomic_write_text(path: Path, text: str) -> None:
    """同目录临时文件 + os.replace 原子替换，避免写一半损坏 JSON。"""
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise ConfigError(f"写入失败: {path} ({exc})") from exc


def _write_json(path: Path, data: Any) -> None:
    _atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _normalize_presets(data: Any) -> dict[str, str]:
    """presets.json 支持 {名称: 信封对象} 或 {名称: JSON 字符串}。"""
    if not isinstance(data, dict):
        return {}
    presets: dict[str, str] = {}
    for name, payload in data.items():
        key = str(name)
        if isinstance(payload, str):
            presets[key] = payload
        else:
            presets[key] = json.dumps(payload, ensure_ascii=False, indent=2)
    return presets


def _presets_to_file_objects(presets: dict[str, str]) -> dict[str, Any]:
    objects: dict[str, Any] = {}
    for name, text in presets.items():
        objects[name] = json.loads(text)
    return objects


def _read_json_optional(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _upgrade_methods_schema(data: dict[str, Any], template: dict[str, Any]) -> bool:
    """把模板里新增的 method / 中文名 / 表单字段补进已有 methods.json。"""
    changed = False
    tmpl_methods = template.get("methods")
    if isinstance(tmpl_methods, list):
        methods = list(data.get("methods") or [])
        for index, method in enumerate(tmpl_methods):
            if method in methods:
                continue
            insert_at = len(methods)
            if index > 0:
                prev = tmpl_methods[index - 1]
                if prev in methods:
                    insert_at = methods.index(prev) + 1
            methods.insert(insert_at, method)
            changed = True
        if methods != data.get("methods"):
            data["methods"] = methods
            changed = True
    for key in ("method_cn", "method_hints", "param_fields"):
        src = template.get(key)
        if not isinstance(src, dict):
            continue
        dst = data.get(key)
        if not isinstance(dst, dict):
            data[key] = dict(src)
            changed = True
            continue
        for name, value in src.items():
            if name not in dst:
                dst[name] = value
                changed = True
            elif key == "method_hints" and dst[name] != value:
                dst[name] = value
                changed = True
    return changed


def _upgrade_codes(data: list[Any], template: list[Any]) -> bool:
    """补齐模板新增错误码，并用模板文案更新已有 code 说明。"""
    changed = False
    by_code: dict[int, int] = {}
    for index, item in enumerate(data):
        if isinstance(item, list) and len(item) == 2:
            try:
                by_code[int(item[0])] = index
            except (TypeError, ValueError):
                continue
    for item in template:
        if not (isinstance(item, list) and len(item) == 2):
            continue
        try:
            code = int(item[0])
        except (TypeError, ValueError):
            continue
        text = str(item[1])
        if code not in by_code:
            data.append([code, text])
            by_code[code] = len(data) - 1
            changed = True
        elif str(data[by_code[code]][1]) != text:
            data[by_code[code]][1] = text
            changed = True
    return changed


def _upgrade_presets(data: dict[str, Any], template: dict[str, Any]) -> bool:
    """只追加模板中尚未存在的预置，不覆盖用户已改过的同名项。"""
    changed = False
    for name, payload in template.items():
        if name not in data:
            data[name] = payload
            changed = True
    return changed


def _read_json(path: Path, label: str) -> Any:
    if not path.exists():
        raise ConfigError(f"{label}不存在: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{label} JSON 无效: {path}") from exc
    return data


def _ensure_from_template(target: Path, template: Path, label: str) -> None:
    """目标 JSON 不存在时，从同名模板复制一份。"""
    if target.exists():
        return
    if not template.exists():
        raise ConfigError(
            f"{label}不存在且无模板: {target}\n请复制 {template.name} 为 {target.name} 后填写"
        )
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(template, target)
    except OSError as exc:
        raise ConfigError(f"从模板创建 {label} 失败: {exc}") from exc
    print(f"[config] 已从模板创建 {label}: {template.name} -> {target.name}")


def _migrate_legacy_config(
    legacy_path: Path | None = None, new_path: Path | None = None
) -> None:
    """旧版本曾把 config.json 写到 tools/config.json（parents[3]），启动时迁移一次。"""
    new_path = new_path or config_path()
    if new_path.exists():
        return
    legacy_path = legacy_path or Path(__file__).resolve().parents[3] / "config.json"
    if not legacy_path.exists():
        return
    try:
        new_path.parent.mkdir(parents=True, exist_ok=True)
        new_path.write_text(legacy_path.read_text(encoding="utf-8"), encoding="utf-8")
    except OSError as exc:
        print(f"[config] 旧配置迁移失败: {exc}")
        return
    print(f"[config] 已从旧路径迁移配置: {legacy_path} -> {new_path}")


def load_config() -> dict[str, Any]:
    _migrate_legacy_config()
    _ensure_from_template(config_path(), config_template_path(), "config.json")
    data = _read_json(config_path(), "config.json")
    if not isinstance(data, dict):
        raise ConfigError(f"config.json 必须是 JSON 对象: {config_path()}")
    migrate_split_embedded_config(data)
    return data


def load_presets() -> dict[str, str]:
    _ensure_from_template(presets_path(), presets_template_path(), "presets.json")
    data = _read_json(presets_path(), "presets.json")
    if not isinstance(data, dict):
        raise ConfigError(f"presets.json 必须是 JSON 对象: {presets_path()}")
    template = _read_json_optional(presets_template_path())
    if isinstance(template, dict) and _upgrade_presets(data, template):
        _write_json(presets_path(), data)
        print("[config] 已从模板补齐 presets.json 新增预置")
    return _normalize_presets(data)


def save_presets(presets: dict[str, str]) -> None:
    _write_json(presets_path(), _presets_to_file_objects(presets))


def load_last_params() -> str:
    path = last_params_path()
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ""
    if isinstance(data, str):
        return data
    return json.dumps(data, ensure_ascii=False, indent=2)


def save_last_params(text: str) -> None:
    path = last_params_path()
    stripped = text.strip()
    if not stripped:
        if path.exists():
            path.unlink()
        return
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return
    _write_json(path, data)


def migrate_split_embedded_config(cfg: dict[str, Any]) -> None:
    """将 config.json 内嵌的 custom_presets / last_params 迁到独立文件。"""
    dirty = False
    if "custom_presets" in cfg:
        embedded = cfg.pop("custom_presets")
        if embedded:
            merged: dict[str, str] = {}
            if presets_path().exists():
                try:
                    merged = _normalize_presets(_read_json(presets_path(), "presets.json"))
                except ConfigError:
                    merged = {}
            merged.update(_normalize_presets(embedded))
            save_presets(merged)
        dirty = True
    if "last_params" in cfg:
        last_params = cfg.pop("last_params")
        if isinstance(last_params, str) and last_params.strip() and not last_params_path().exists():
            save_last_params(last_params)
        dirty = True
    if dirty:
        save_config(cfg)


def load_methods_schema() -> dict[str, Any]:
    _ensure_from_template(methods_path(), methods_template_path(), "methods.json")
    data = _read_json(methods_path(), "methods.json")
    if not isinstance(data, dict):
        raise ConfigError(f"methods.json 必须是 JSON 对象: {methods_path()}")
    template = _read_json_optional(methods_template_path())
    if isinstance(template, dict) and _upgrade_methods_schema(data, template):
        _write_json(methods_path(), data)
        print("[config] 已从模板补齐 methods.json 新增 method")
    for key in ("methods", "method_cn", "row_methods", "param_fields"):
        if key not in data:
            raise ConfigError(f"methods.json 缺少字段: {key}")
    return data


def load_code_legend() -> list[tuple[int, str]]:
    _ensure_from_template(codes_path(), codes_template_path(), "codes.json")
    data = _read_json(codes_path(), "codes.json")
    if not isinstance(data, list):
        raise ConfigError(f"codes.json 必须是 JSON 数组: {codes_path()}")
    template = _read_json_optional(codes_template_path())
    if isinstance(template, list) and _upgrade_codes(data, template):
        _write_json(codes_path(), data)
        print("[config] 已从模板补齐 codes.json 新增错误码")
    legend: list[tuple[int, str]] = []
    for item in data:
        if not (isinstance(item, list) and len(item) == 2):
            raise ConfigError("codes.json 每项应为 [code, 说明]")
        try:
            code = int(item[0])
        except (TypeError, ValueError):
            raise ConfigError(f"codes.json 的 code 必须为数字: {item[0]!r}") from None
        legend.append((code, str(item[1])))
    return legend


def save_config(cfg: dict[str, Any]) -> None:
    _atomic_write_text(
        config_path(), json.dumps(cfg, ensure_ascii=False, indent=2) + "\n"
    )


def merge_and_save(updates: dict[str, Any]) -> None:
    """部分字段更新保存：仅合并 config.json 中的连接与界面字段。"""
    cfg = load_config()
    for key in ("custom_presets", "last_params"):
        updates.pop(key, None)
    cfg.update(updates)
    save_config(cfg)
