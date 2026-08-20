from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT.parent / "DM-HPWT18-U1" / "Modbus Protocols"
DEFAULT_OUTPUT = ROOT / "profiles" / "dm_hpwt18_u1.json"
DEFAULT_REPORT = ROOT / "docs" / "18一体机寄存器提取报告.md"


def clean(value: str | None) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_access(value: str) -> str:
    text = clean(value).upper().replace("/", "")
    return "rw" if "W" in text else "r"


def parse_address(value: str, prefix: str) -> int | None:
    match = re.search(rf"{prefix}\s*(\d+)", clean(value), re.IGNORECASE)
    return int(match.group(1)) if match else None


def parse_number(value: str) -> int | None:
    text = clean(value)
    if not text or text == "/":
        return None
    match = re.search(r"-?0x[0-9a-fA-F]+|-?\d+", text)
    if not match:
        return None
    token = match.group(0)
    return int(token, 16) if "0x" in token.lower() else int(token)


def to_u16(value: int | None, default: int = 0) -> int:
    if value is None:
        value = default
    return max(0, min(0xFFFF, value))


def normalize_raw_limits(
    min_raw: int | None,
    max_raw: int | None,
    transfer: str,
) -> tuple[int | None, int | None]:
    """CSV documents physical values for *10 fields; simulator clamps raw transmission values."""
    if "实际值*10" not in transfer:
        return min_raw, max_raw
    if max_raw is not None and max_raw <= 200:
        min_raw = None if min_raw is None else min_raw * 10
        max_raw = max_raw * 10
    return min_raw, max_raw


def visible(value: str | None) -> str:
    text = clean(value)
    return "" if not text or text == "/" else text


def parse_enum(*cells: str) -> dict[str, str]:
    enum: dict[str, str] = {}
    text = "\n".join(clean(cell) for cell in cells if clean(cell) and clean(cell) != "/")
    for line in text.splitlines():
        segments = re.split(r"[，,]", line) if re.search(r"[，,]", line) else [line]
        for segment in segments:
            match = re.match(r"\s*(-?0x[0-9a-fA-F]+|-?\d+)\s*[：:]\s*(.+?)\s*$", segment.strip())
            if not match:
                continue
            raw = parse_number(match.group(1))
            if raw is None:
                continue
            enum[str(raw)] = match.group(2).strip()
    return enum


def parse_enum_entries(*cells: str) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    text = "\n".join(clean(cell) for cell in cells if clean(cell) and clean(cell) != "/")
    for line in text.splitlines():
        segments = re.split(r"[，,]", line) if re.search(r"[，,]", line) else [line]
        for segment in segments:
            match = re.match(r"\s*(-?0x[0-9a-fA-F]+|-?\d+)\s*[：:]\s*(.+?)\s*$", segment.strip())
            if not match:
                continue
            entry = (match.group(1).strip(), match.group(2).strip())
            if entry not in seen:
                entries.append(entry)
                seen.add(entry)
    return entries


def collect_enum_description_lines(*cells: str) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    text = "\n".join(clean(cell) for cell in cells if clean(cell) and clean(cell) != "/")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and stripped not in seen:
            lines.append(stripped)
            seen.add(stripped)
    return lines


def join_sections(*sections: str) -> str:
    return "\n".join(section for section in sections if section)


def make_register_description(
    *,
    address_label: str,
    access: str,
    data_type: str,
    min_text: str,
    max_text: str,
    default_text: str,
    unit: str,
    precision: str,
    transfer: str,
    enum_entries: list[tuple[str, str]],
    enum_fallback_lines: list[str],
    note: str,
) -> str:
    lines: list[str] = []

    range_text = ""
    if min_text and max_text:
        range_text = f"{min_text} ~ {max_text}"
    elif min_text:
        range_text = f"最小值 {min_text}"
    elif max_text:
        range_text = f"最大值 {max_text}"
    if range_text:
        lines.append(range_text)

    if unit:
        lines.append(f"单位：{unit}")
    if precision:
        lines.append(f"精度：{precision}")
    if enum_entries:
        lines.extend(f"{raw} = {label}" for raw, label in enum_entries)
    elif enum_fallback_lines:
        lines.extend(enum_fallback_lines)
    if note:
        lines.append(f"备注：{note}")
    return "\n".join(lines)


def make_coil_description(
    *,
    address_label: str,
    access: str,
    backing_register: int,
    bit_offset: int,
    false_label: str,
    true_label: str,
    ui_level: str,
    data_type: str,
    note: str,
) -> str:
    lines = [
        f"映射寄存器：word {backing_register} bit {bit_offset}",
    ]
    if false_label or true_label:
        if false_label and true_label:
            lines.append(f"状态说明：FALSE = {false_label}；TRUE = {true_label}")
        elif false_label:
            lines.append(f"FALSE 状态：{false_label}")
        elif true_label:
            lines.append(f"TRUE 状态：{true_label}")
    if ui_level:
        lines.append(f"UI 层级：{ui_level}")
    if note:
        lines.append(f"备注：{note}")
    return "\n".join(lines)


def make_key(prefix: str, address: int, name: str) -> str:
    ascii_name = re.sub(r"[^0-9A-Za-z]+", "_", name).strip("_").lower()
    return f"{prefix}_{address}" if not ascii_name else f"{prefix}_{address}_{ascii_name}"


def read_csv(path: Path) -> list[list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.reader(handle))


def find_file(source_dir: Path, keyword: str) -> Path:
    matches = sorted(path for path in source_dir.glob("*.csv") if keyword in path.name)
    if not matches:
        raise FileNotFoundError(f"Cannot find CSV containing {keyword!r} in {source_dir}")
    return matches[0]


def parse_registers(path: Path, access: str, warnings: list[str]) -> list[dict[str, Any]]:
    rows = read_csv(path)
    registers: list[dict[str, Any]] = []
    for row_number, row in enumerate(rows[3:], start=4):
        row = row + [""] * 12
        address_label = clean(row[0])
        if "~" in address_label or "～" in address_label:
            continue
        address = parse_address(address_label, "word")
        if address is None:
            if any(clean(cell) for cell in row):
                warnings.append(f"{path.name}:{row_number} skipped: missing word address")
            continue

        name = clean(row[2]) or f"word {address}"
        min_raw = parse_number(row[3])
        max_raw = parse_number(row[4])
        transfer = visible(row[9] if access == "rw" else row[8])
        min_raw, max_raw = normalize_raw_limits(min_raw, max_raw, transfer)
        default_raw = parse_number(row[8]) if access == "rw" else 0
        enum = parse_enum(row[5], row[11] if access == "rw" else row[10])
        enum_entries = parse_enum_entries(row[5], row[11] if access == "rw" else row[10])
        enum_fallback_lines = collect_enum_description_lines(row[5])
        address_label = address_label or f"word {address}"
        unit = visible(row[6])
        precision = visible(row[7])
        data_type = visible(row[10] if access == "rw" else row[9]) or "u16"
        note = visible(row[11] if access == "rw" else row[10])
        registers.append(
            {
                "address": address,
                "address_label": address_label,
                "name": name,
                "key": make_key("word", address, name),
                "access": access,
                "data_type": data_type,
                "default_raw": to_u16(default_raw),
                "min_raw": min_raw,
                "max_raw": max_raw,
                "unit": unit,
                "precision": precision,
                "transfer": transfer,
                "enum": enum,
                "description": make_register_description(
                    address_label=address_label,
                    access=access,
                    data_type=data_type,
                    min_text=visible(row[3]),
                    max_text=visible(row[4]),
                    default_text=visible(row[8]) if access == "rw" else "",
                    unit=unit,
                    precision=precision,
                    transfer=transfer,
                    enum_entries=enum_entries,
                    enum_fallback_lines=enum_fallback_lines if not enum_entries else [],
                    note=note,
                ),
                "source": {"file": path.name, "row": row_number},
            }
        )
    return registers


def parse_coils(path: Path, access: str, warnings: list[str]) -> list[dict[str, Any]]:
    rows = read_csv(path)
    coils: list[dict[str, Any]] = []
    current_word: int | None = None
    for row_number, row in enumerate(rows[2:], start=3):
        row = row + [""] * 10
        word = parse_address(row[0], "word")
        if word is not None:
            current_word = word
        address = parse_address(row[2], "bit")
        if address is None:
            if any(clean(cell) for cell in row):
                warnings.append(f"{path.name}:{row_number} skipped: missing bit address")
            continue
        if current_word is None:
            warnings.append(f"{path.name}:{row_number} skipped: missing backing word")
            continue

        expected_word = address // 16
        if expected_word != current_word:
            warnings.append(
                f"{path.name}:{row_number}: bit {address} maps to word {expected_word}, CSV says word {current_word}"
            )
            current_word = expected_word

        name = clean(row[4]) or f"bit {address}"
        address_label = clean(row[2]) or f"bit {address}"
        false_label = visible(row[5])
        true_label = visible(row[6])
        ui_level = visible(row[7] if access == "rw" else "")
        data_type = visible(row[8] if access == "rw" else row[7]) or "bool"
        note = visible(row[9] if access == "rw" else row[8])
        coils.append(
            {
                "address": address,
                "address_label": address_label,
                "name": name,
                "key": make_key("bit", address, name),
                "access": access,
                "default": False,
                "backing_register": expected_word,
                "bit_offset": address % 16,
                "false_label": false_label,
                "true_label": true_label,
                "ui_level": ui_level,
                "data_type": data_type,
                "description": make_coil_description(
                    address_label=address_label,
                    access=access,
                    backing_register=expected_word,
                    bit_offset=address % 16,
                    false_label=false_label,
                    true_label=true_label,
                    ui_level=ui_level,
                    data_type=data_type,
                    note=note,
                ),
                "source": {"file": path.name, "row": row_number},
            }
        )
    return coils


def build_profile(source_dir: Path, warnings: list[str]) -> dict[str, Any]:
    rw_register_file = find_file(source_dir, "寄存器（可读写）")
    ro_register_file = find_file(source_dir, "寄存器（只读）")
    rw_coil_file = find_file(source_dir, "线圈 （可读写）")
    ro_coil_file = find_file(source_dir, "线圈（只读）")

    registers = parse_registers(rw_register_file, "rw", warnings)
    registers.extend(parse_registers(ro_register_file, "r", warnings))
    coils = parse_coils(rw_coil_file, "rw", warnings)
    coils.extend(parse_coils(ro_coil_file, "r", warnings))

    by_address = {item["address"]: item for item in registers}
    for backing_word in sorted({coil["backing_register"] for coil in coils}):
        if backing_word in by_address:
            continue
        writable = any(coil["backing_register"] == backing_word and coil["access"] == "rw" for coil in coils)
        register = {
            "address": backing_word,
            "address_label": f"word {backing_word}",
            "name": f"线圈映射字 {backing_word}",
            "key": f"word_{backing_word}_coil_bitmap",
            "access": "rw" if writable else "r",
            "data_type": "u16",
            "default_raw": 0,
            "min_raw": 0,
            "max_raw": 65535,
            "unit": "",
            "precision": "",
            "transfer": "bit map",
            "enum": {},
            "description": "Synthetic backing register generated from coil table.",
            "synthetic": True,
            "source": {"file": "generated", "row": 0},
        }
        registers.append(register)
        by_address[backing_word] = register

    registers.sort(key=lambda item: item["address"])
    coils.sort(key=lambda item: item["address"])

    return {
        "profile_id": "dm_hpwt18_u1",
        "name": "DM-HPWT18-U1 18一体机",
        "description": "18一体机 Modbus RTU 从站模拟配置，由主控板-热泵Modbus协议_2024-02-01 CSV 提取生成。",
        "source": {
            "directory": str(source_dir),
            "protocol": "主控板-热泵Modbus协议_2024-02-01",
        },
        "slave_id": 1,
        "baudrate": 9600,
        "serial": {"bytesize": 8, "parity": "N", "stopbits": 1},
        "function_codes": [1, 3, 5, 6, 15, 16],
        "status_fields": [
            {"label": "设定开关机", "kind": "register", "address": 4},
            {"label": "通讯协议版本", "kind": "register", "address": 300},
            {"label": "超强", "kind": "coil", "address": 6400},
            {"label": "压缩机状态", "kind": "coil", "address": 8000},
        ],
        "registers": registers,
        "coils": coils,
    }


def write_report(profile: dict[str, Any], warnings: list[str], report_path: Path) -> None:
    rw_registers = sum(1 for item in profile["registers"] if item["access"] == "rw" and not item.get("synthetic"))
    ro_registers = sum(1 for item in profile["registers"] if item["access"] == "r" and not item.get("synthetic"))
    synthetic_registers = sum(1 for item in profile["registers"] if item.get("synthetic"))
    rw_coils = sum(1 for item in profile["coils"] if item["access"] == "rw")
    ro_coils = sum(1 for item in profile["coils"] if item["access"] == "r")
    lines = [
        "# 18一体机寄存器提取报告",
        "",
        f"- Profile: `{profile['profile_id']}`",
        f"- 寄存器 R/W: {rw_registers}",
        f"- 寄存器 R: {ro_registers}",
        f"- 线圈映射 synthetic word: {synthetic_registers}",
        f"- 线圈 R/W: {rw_coils}",
        f"- 线圈 R: {ro_coils}",
        f"- 默认串口: {profile['baudrate']} 8N1",
        "",
        "## 关键点抽查",
        "",
    ]
    lookup_register = {item["address"]: item for item in profile["registers"]}
    lookup_coil = {item["address"]: item for item in profile["coils"]}
    for address in (4, 300):
        item = lookup_register.get(address)
        lines.append(f"- word {address}: {item['name'] if item else '未找到'}")
    for address in (6400, 8000):
        item = lookup_coil.get(address)
        if item:
            lines.append(
                f"- bit {address}: {item['name']}, backing word {item['backing_register']} bit {item['bit_offset']}"
            )
        else:
            lines.append(f"- bit {address}: 未找到")
    lines.extend(["", "## 提取警告", ""])
    if warnings:
        lines.extend(f"- {warning}" for warning in warnings)
    else:
        lines.append("- 无")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract DM-HPWT18-U1 Modbus CSV files into simulator JSON.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    warnings: list[str] = []
    profile = build_profile(args.source, warnings)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    write_report(profile, warnings, args.report)
    print(f"Wrote {args.output}")
    print(f"Wrote {args.report}")
    print(f"registers={len(profile['registers'])}, coils={len(profile['coils'])}, warnings={len(warnings)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
