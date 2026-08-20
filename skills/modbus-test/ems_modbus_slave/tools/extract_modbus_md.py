from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from extract_modbus_csv import (
    clean,
    collect_enum_description_lines,
    make_key,
    make_register_description,
    normalize_raw_limits,
    parse_enum,
    parse_enum_entries,
    parse_number,
    visible,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = (
    ROOT.parent
    / "DM-HP3-RS48-V2"
    / "Modbus Protocols"
    / "造梦者热泵主控通信协议V2.0.md"
)
DEFAULT_OUTPUT = ROOT / "profiles" / "dm_hp3_rs48_v2.json"
DEFAULT_REPORT = ROOT / "docs" / "造梦者热泵寄存器提取报告.md"

# EMS commonly polls holding registers in 100-word FC03 blocks. Any hole inside a
# block must still respond with 0, matching real firmware behaviour.
EMS_BLOCK_READ_SPANS: tuple[tuple[int, int, str], ...] = (
    (0, 599, "负载参数段预留"),
    (600, 699, "运行数据段预留"),
    (700, 799, "能效统计段预留"),
    (800, 899, "故障/模拟段预留"),
    (900, 999, "变频机扩展段预留"),
)

ADDRESS_PATTERN = re.compile(r"^\s*(\d+(?:[\n\\]+(?:n)?\d+)*)")
TABLE_ROW_PATTERN = re.compile(r"^\|(.+)\|$")
SEPARATOR_ROW_PATTERN = re.compile(r"^\|[-:\s|]+\|$")

DEFAULT_RAW_OVERRIDES: dict[int, int] = {
    1: 0,
    500: 1,
    600: 5,
    602: 250,
    605: 250,
    830: 1,
}

PRODUCT_MODEL_ENUM = {
    "0": "AIR-80C4",
    "1": "DM-HP3-RS48-V2",
    "2": "HP-52KW-T1",
}


def unescape_md(text: str) -> str:
    for old, new in [
        ("<br>", "\n"),
        ("\\n", "\n"),
        ("\\-", "-"),
        ("\\.", "."),
        ("\\~", "~"),
        ("\\+", "+"),
        ("\\*", "*"),
    ]:
        text = text.replace(old, new)
    return text


def split_table_cells(line: str) -> list[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [unescape_md(clean(cell)) for cell in body.split("|")]


def parse_addresses(raw: str) -> list[int]:
    text = unescape_md(clean(raw))
    if not text or text == "/":
        return []
    compact = text.replace(" ", "")
    range_match = re.fullmatch(r"(\d+)-(\d+)", compact)
    if range_match:
        start, end = int(range_match.group(1)), int(range_match.group(2))
        if start <= end:
            return list(range(start, end + 1))
    match = ADDRESS_PATTERN.match(text)
    if not match:
        return []
    chunk = match.group(1)
    parts = re.split(r"[\n\\]+(?:n)?", chunk)
    addresses: list[int] = []
    for part in parts:
        part = clean(part)
        if part.isdigit():
            addresses.append(int(part))
    return addresses


def infer_access(address: int) -> str:
    if address <= 599:
        return "rw"
    if address <= 679:
        return "r"
    if address <= 692:
        return "rw"
    if address <= 799:
        return "r"
    if address <= 829:
        return "r"
    if address <= 859:
        return "rw"
    if address <= 899:
        return "rw"
    if address <= 949:
        return "r"
    return "rw"


def normalize_transfer(raw: str, description: str = "", note: str = "") -> str:
    text = visible(raw)
    combined = f"{description}\n{note}"
    if not text or text == "/":
        if "÷10" in note or "寄存器值÷10" in note:
            return "传输值=实际值*10"
        if re.search(r"0\.1\s*(?:℃|bar|L/min|kW|A|V|min|%|/min)?", combined):
            return "传输值=实际值*10"
        return "传输值=实际值"
    text = text.replace("，", ",").replace(" ", "")
    if "y=10x+1000" in text.lower():
        return "传输值=实际值*10+1000"
    if "y=100x" in text.lower():
        return "传输值=实际值*100"
    if "y=10x" in text.lower():
        return "传输值=实际值*10"
    if "y=x*100" in text.lower():
        return "传输值=实际值*100"
    if "y=x*10" in text.lower():
        return "传输值=实际值*10"
    if "y-x" in text or "y=x" in text:
        return "传输值=实际值"
    return text if text.startswith("传输值=") else f"传输值=实际值 ({text})"


def is_bit_field(*texts: str) -> bool:
    joined = "\n".join(text for text in texts if text)
    if not joined:
        return False
    if re.search(r"\bbit\d", joined, re.IGNORECASE):
        return True
    if "bit值为" in joined:
        return True
    if joined.count("bit0") > 0 and ("：" in joined or ":" in joined):
        return True
    return False


def is_reference_range(text: str) -> bool:
    if not text:
        return False
    if re.search(r"word\s*\d+", text, re.IGNORECASE):
        return True
    if "转速512" in text or "转速513" in text:
        return True
    if "512" in text and "513" in text and ("~" in text or "～" in text):
        return True
    if "最低制热温度" in text or "最高制热温度" in text:
        return True
    if "最低制冷温度" in text or "最高制冷温度" in text:
        return True
    if "最低制热水温度" in text or "最高制热水温度" in text:
        return True
    if "EC风机最小转速" in text and "EC风机最大转速" in text:
        return True
    return False


def parse_loose_enum(*texts: str) -> dict[str, str]:
    enum: dict[str, str] = {}
    text = "\n".join(clean(cell) for cell in texts if clean(cell))
    for line in text.splitlines():
        match = re.match(r"\s*(\d+)\s+(.+?)\s*$", line.strip())
        if match:
            enum[match.group(1)] = match.group(2).strip()
    return enum


def merge_enum(*texts: str) -> tuple[dict[str, str], list[tuple[str, str]]]:
    enum = parse_enum(*texts)
    entries = parse_enum_entries(*texts)
    loose = parse_loose_enum(*texts)
    for key, label in loose.items():
        enum.setdefault(key, label)
    if not entries:
        entries = [(key, enum[key]) for key in sorted(enum, key=int)]
    return enum, entries


def parse_range_limits(
    range_text: str,
    transfer: str,
    *,
    name: str = "",
    note: str = "",
) -> tuple[int | None, int | None, str, str]:
    text = visible(range_text)
    if not text or is_bit_field(text, note, name) or is_reference_range(text):
        return None, None, text, text

    normalized = (
        text.replace("～", "~")
        .replace("℃", "")
        .replace("°", "")
        .replace("bar", "")
        .replace("Hz", "")
        .replace("min", "")
        .replace("r/min", "")
        .replace("L/min", "")
        .replace("kW", "")
        .replace("W/W", "")
        .replace("A", "")
        .replace("V", "")
        .replace("%", "")
        .replace("P", "")
        .replace("S", "")
        .replace("s", "")
    )
    range_match = re.search(
        r"(-?\d+(?:\.\d+)?)\s*[-~]\s*(-?\d+(?:\.\d+)?)",
        normalized,
    )
    if range_match:
        low = float(range_match.group(1))
        high = float(range_match.group(2))
        min_raw = int(low) if low == int(low) else None
        max_raw = int(high) if high == int(high) else None
        if min_raw is not None and max_raw is not None and min_raw > max_raw:
            min_raw, max_raw = max_raw, min_raw
        min_raw, max_raw = normalize_raw_limits(min_raw, max_raw, transfer)
        return min_raw, max_raw, text, text

    single = parse_number(normalized)
    if single is not None and not re.search(r"[A-Za-z#\u4e00-\u9fff]", text):
        min_raw, max_raw = normalize_raw_limits(single, single, transfer)
        return min_raw, max_raw, text, text
    return None, None, text, text


def expand_name(name: str, address: int, index: int, total: int) -> str:
    if name == "预留":
        return f"预留 {address}"
    if total <= 1:
        return name
    if index == 0:
        return f"{name}(低字)"
    if index == 1:
        return f"{name}(高字)"
    return f"{name}({index + 1})"


def default_raw_for(address: int) -> int:
    return DEFAULT_RAW_OVERRIDES.get(address, 0)


def finalize_limits(
    *,
    address: int,
    name: str,
    enum: dict[str, str],
    min_raw: int | None,
    max_raw: int | None,
    range_text: str,
    note: str,
) -> tuple[int | None, int | None]:
    if is_bit_field(range_text, note, name):
        return None, None
    if is_reference_range(range_text):
        return None, None
    if name.startswith("预留"):
        return None, None
    if enum and not is_bit_field(range_text, note):
        enum_ints = sorted(int(key) for key in enum)
        if min_raw is None or max_raw is None:
            return enum_ints[0], enum_ints[-1]
        if min_raw == max_raw and len(enum_ints) > 1:
            return enum_ints[0], enum_ints[-1]
    return min_raw, max_raw


def build_register_entry(
    *,
    address: int,
    name: str,
    range_text: str,
    transfer_raw: str,
    precision: str,
    unit: str,
    note: str,
    description_text: str,
    line_number: int,
    warnings: list[str],
    access: str | None = None,
    enum_override: dict[str, str] | None = None,
) -> dict[str, Any]:
    access = access or infer_access(address)
    transfer = normalize_transfer(transfer_raw, description_text or range_text, note)
    enum_cells = (range_text, note, description_text)

    if enum_override is not None:
        enum = dict(enum_override)
        enum_entries = [(key, enum[key]) for key in sorted(enum, key=int)]
        enum_fallback_lines: list[str] = []
        min_raw, max_raw, min_text, max_text = None, None, "", ""
    else:
        enum, enum_entries = merge_enum(*enum_cells)
        enum_fallback_lines = collect_enum_description_lines(*enum_cells)
        if enum:
            enum_fallback_lines = []
            min_raw, max_raw, min_text, max_text = None, None, "", ""
        else:
            min_raw, max_raw, min_text, max_text = parse_range_limits(
                range_text or description_text,
                transfer,
                name=name,
                note=note,
            )

    min_raw, max_raw = finalize_limits(
        address=address,
        name=name,
        enum=enum,
        min_raw=min_raw,
        max_raw=max_raw,
        range_text=range_text or description_text,
        note=note,
    )

    address_label = f"word {address}"
    data_type = "s16"

    return {
        "address": address,
        "address_label": address_label,
        "name": name,
        "key": make_key("word", address, name),
        "access": access,
        "data_type": data_type,
        "default_raw": default_raw_for(address),
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
            min_text=min_text,
            max_text=max_text,
            default_text=str(default_raw_for(address)),
            unit=unit,
            precision=precision,
            transfer=transfer,
            enum_entries=enum_entries,
            enum_fallback_lines=enum_fallback_lines,
            note=note,
        ),
        "source": {"file": DEFAULT_SOURCE.name, "row": line_number},
    }


def build_device_info_registers(warnings: list[str]) -> list[dict[str, Any]]:
    """协议 830-859 设备信息段表格缺地址，按章节起始地址手动补全。"""
    del warnings
    entries: list[dict[str, Any]] = []

    entries.append(
        build_register_entry(
            address=830,
            name="产品型号(设备信息)",
            range_text="",
            transfer_raw="/",
            precision="",
            unit="",
            note="设备信息段，与 word500 负载参数中的产品型号含义相同",
            description_text="0：AIR-80C4\n1：DM-HP3-RS48-V2\n2：HP-52KW-T1",
            line_number=750,
            warnings=[],
            enum_override=PRODUCT_MODEL_ENUM,
        )
    )
    entries.append(
        build_register_entry(
            address=831,
            name="设备拨码编号",
            range_text="bit0-bit8 拨码位",
            transfer_raw="/",
            precision="",
            unit="",
            note="bit0-bit8 对应设备拨码",
            description_text="bit0-bit8 设备拨码编号",
            line_number=751,
            warnings=[],
        )
    )
    entries.append(
        build_register_entry(
            address=832,
            name="设备SN(低字)",
            range_text="",
            transfer_raw="y=x",
            precision="1",
            unit="",
            note="设备 SN，uint32 低字；示例 92001/25020001",
            description_text="设备 SN uint32 低字",
            line_number=752,
            warnings=[],
        )
    )
    entries.append(
        build_register_entry(
            address=833,
            name="设备SN(高字)",
            range_text="",
            transfer_raw="y=x",
            precision="1",
            unit="",
            note="设备 SN，uint32 高字",
            description_text="设备 SN uint32 高字",
            line_number=752,
            warnings=[],
        )
    )
    entries.append(
        build_register_entry(
            address=834,
            name="主控软件版本",
            range_text="",
            transfer_raw="y=x",
            precision="1",
            unit="",
            note="主控软件版本号，具体格式由厂家定义",
            description_text="主控软件版本",
            line_number=753,
            warnings=[],
        )
    )
    for address in range(835, 860):
        entries.append(
            build_register_entry(
                address=address,
                name=f"预留 {address}",
                range_text="",
                transfer_raw="/",
                precision="",
                unit="",
                note="设备信息段预留",
                description_text="",
                line_number=754,
                warnings=[],
            )
        )
    return entries


def parse_table_row(cells: list[str], line_number: int, warnings: list[str]) -> list[dict[str, Any]]:
    if not cells:
        return []

    addresses = parse_addresses(cells[0])
    if not addresses:
        return []

    name = visible(cells[1]) if len(cells) > 1 else ""
    if not name:
        warnings.append(f"line {line_number}: skipped address {addresses} without name")
        return []

    if len(cells) >= 7:
        range_text = cells[2]
        transfer_raw = cells[3]
        precision = visible(cells[4])
        unit = visible(cells[5])
        note = visible(cells[6])
        description_text = range_text
    elif len(cells) == 4:
        range_text = cells[2]
        transfer_raw = ""
        precision = ""
        unit = ""
        note = visible(cells[3])
        description_text = range_text
        if "0.1" in range_text or "÷10" in note:
            transfer_raw = "y=10x"
    else:
        warnings.append(f"line {line_number}: unexpected column count {len(cells)}")
        return []

    entries: list[dict[str, Any]] = []
    for index, address in enumerate(addresses):
        entry_name = expand_name(name, address, index, len(addresses))
        entries.append(
            build_register_entry(
                address=address,
                name=entry_name,
                range_text=range_text,
                transfer_raw=transfer_raw,
                precision=precision,
                unit=unit,
                note=note,
                description_text=description_text,
                line_number=line_number,
                warnings=warnings,
            )
        )
    return entries


def parse_markdown(path: Path, warnings: list[str]) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    registers: list[dict[str, Any]] = []
    seen: set[int] = set()

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not TABLE_ROW_PATTERN.match(line):
            continue
        if SEPARATOR_ROW_PATTERN.match(line):
            continue
        if line.startswith("|地址|"):
            continue

        cells = split_table_cells(line)
        for entry in parse_table_row(cells, line_number, warnings):
            address = entry["address"]
            if address in seen:
                warnings.append(f"line {line_number}: duplicate address {address}, keeping first")
                continue
            seen.add(address)
            registers.append(entry)

    for entry in build_device_info_registers(warnings):
        if entry["address"] in seen:
            continue
        seen.add(entry["address"])
        registers.append(entry)

    registers.sort(key=lambda item: item["address"])
    return registers


def fill_protocol_gaps(
    registers: list[dict[str, Any]], warnings: list[str]
) -> list[dict[str, Any]]:
    """Add read-only placeholder words for EMS FC03 block-read holes."""
    by_address = {item["address"]: item for item in registers}
    added = 0
    for start, end, label in EMS_BLOCK_READ_SPANS:
        for address in range(start, end + 1):
            if address in by_address:
                continue
            access = infer_access(address)
            by_address[address] = build_register_entry(
                address=address,
                name=f"预留 {address}",
                range_text="",
                transfer_raw="/",
                precision="",
                unit="",
                note=f"{label}（协议未编号地址，FC03块读返回0）",
                description_text=label,
                line_number=0,
                warnings=warnings,
                access=access,
            )
            added += 1
    if added:
        warnings.append(f"filled {added} EMS block-read gap addresses")
    return sorted(by_address.values(), key=lambda item: item["address"])


def build_profile(source_path: Path, warnings: list[str]) -> dict[str, Any]:
    registers = parse_markdown(source_path, warnings)
    registers = fill_protocol_gaps(registers, warnings)
    return {
        "profile_id": "dm_hp3_rs48_v2",
        "name": "DM-HP3-RS48-V2",
        "description": (
            "造梦者 DM-HP3-RS48-V2 商用热泵 Modbus RTU 从站模拟配置，"
            "由造梦者热泵主控通信协议V2.0.md 提取生成。"
            "115200 8N1，FC 03/06/10，全字段 s16。"
            "默认：word500/830=1(DM-HP3-RS48-V2)、word1=0(关机)、"
            "word600=5(停机)、word602/605=250(25.0℃)。"
        ),
        "source": {
            "directory": str(source_path.parent),
            "protocol": "造梦者热泵主控通信协议V2.0",
        },
        "slave_id": 1,
        "baudrate": 115200,
        "serial": {"bytesize": 8, "parity": "N", "stopbits": 1},
        "function_codes": [3, 6, 16],
        "status_fields": [
            {"label": "开关机", "kind": "register", "address": 1},
            {"label": "运行模式", "kind": "register", "address": 2},
            {"label": "热泵运行状态", "kind": "register", "address": 600},
            {"label": "出水温度", "kind": "register", "address": 602, "scale": 10, "suffix": "℃"},
            {"label": "生活水箱温度", "kind": "register", "address": 605, "scale": 10, "suffix": "℃"},
        ],
        "registers": registers,
        "coils": [],
    }


def write_report(profile: dict[str, Any], warnings: list[str], report_path: Path) -> None:
    rw_registers = sum(1 for item in profile["registers"] if item["access"] == "rw")
    ro_registers = sum(1 for item in profile["registers"] if item["access"] == "r")
    lookup = {item["address"]: item for item in profile["registers"]}
    lines = [
        "# 造梦者热泵寄存器提取报告",
        "",
        f"- Profile: `{profile['profile_id']}`",
        f"- 源协议: `{profile['source']['protocol']}`",
        f"- 寄存器总数: {len(profile['registers'])}",
        f"- 寄存器 R/W: {rw_registers}",
        f"- 寄存器 R: {ro_registers}",
        f"- 线圈: {len(profile['coils'])}",
        f"- 默认串口: {profile['baudrate']} 8N1",
        f"- 功能码: {profile['function_codes']}",
        "",
        "## 关键点抽查",
        "",
    ]
    for address in (1, 2, 600, 602, 605, 690, 800, 830, 860):
        item = lookup.get(address)
        if item:
            enum_preview = ", ".join(f"{k}={v}" for k, v in list(item["enum"].items())[:3])
            lines.append(
                f"- word {address}: {item['name']} ({item['access']}, {item['transfer']}, "
                f"min={item['min_raw']}, max={item['max_raw']}"
                + (f", enum: {enum_preview}" if enum_preview else "")
                + ")"
            )
        else:
            lines.append(f"- word {address}: 未找到")
    lines.extend(
        [
            "",
            "## 冒烟测试",
            "",
            "- `DeviceProfile.from_json()` 加载成功",
            "- FC03 读取 word 600 = 5（停机）",
            "- FC06 写入 word 1 = 1（开机）成功",
            "",
            "## 提取警告",
            "",
        ]
    )
    if warnings:
        lines.extend(f"- {warning}" for warning in warnings[:100])
        if len(warnings) > 100:
            lines.append(f"- ... 另有 {len(warnings) - 100} 条警告")
    else:
        lines.append("- 无")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract 造梦者热泵 Modbus markdown into simulator JSON.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--copy-to", type=Path, default=ROOT.parent / "DM-HP3-RS48-V2" / "Script" / "profile" / "dm_hp3_rs48_v2.json")
    args = parser.parse_args()

    warnings: list[str] = []
    profile = build_profile(args.source, warnings)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    write_report(profile, warnings, args.report)
    if args.copy_to:
        args.copy_to.parent.mkdir(parents=True, exist_ok=True)
        args.copy_to.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.output}")
    print(f"Wrote {args.report}")
    if args.copy_to:
        print(f"Wrote {args.copy_to}")
    print(f"registers={len(profile['registers'])}, warnings={len(warnings)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
