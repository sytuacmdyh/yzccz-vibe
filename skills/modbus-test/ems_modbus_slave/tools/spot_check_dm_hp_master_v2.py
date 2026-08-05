from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "profiles" / "dm_hp_master_v2.json"
PROTOCOL_PATH = ROOT.parent / "造梦者热泵" / "造梦者热泵主控通信协议V2.0.md"


def unescape_md(text: str) -> str:
    for old, new in [
        ("<br>", "\n"),
        ("\\n", "\n"),
        ("\\-", "-"),
        ("\\.", "."),
        ("\\~", "~"),
        ("\\+", "+"),
    ]:
        text = text.replace(old, new)
    return text.strip()


def split_cells(line: str) -> list[str]:
    body = line.strip().strip("|")
    return [unescape_md(cell) for cell in body.split("|")]


def infer_access(address: int) -> str:
    if address <= 599:
        return "rw"
    if address <= 679:
        return "r"
    if address <= 692:
        return "rw"
    if address <= 829:
        return "r"
    if address <= 899:
        return "rw"
    if address <= 949:
        return "r"
    return "rw"


def normalize_transfer(raw: str, description: str = "", note: str = "") -> str:
    text = raw.strip()
    if not text or text == "/":
        if "÷10" in note or "寄存器值÷10" in note:
            return "传输值=实际值*10"
        if "0.1" in description:
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
    return text


def parse_enum(text: str) -> dict[str, str]:
    enum: dict[str, str] = {}
    for line in text.splitlines():
        segments = re.split(r"[，,]", line) if re.search(r"[，,]", line) else [line]
        for segment in segments:
            match = re.match(r"\s*(-?\d+)\s*[：:]\s*(.+?)\s*$", segment.strip())
            if match:
                enum[match.group(1)] = match.group(2).strip()
    return enum


@dataclass
class ParsedRow:
    line: int
    addresses: list[int]
    name: str
    range_text: str
    transfer_raw: str
    unit: str
    note: str


def parse_row(line_no: int, raw: str) -> ParsedRow | None:
    if not raw.strip().startswith("|") or raw.strip().startswith("|地址|"):
        return None
    if re.fullmatch(r"\|[-:\s|]+\|", raw.strip()):
        return None
    cells = split_cells(raw)
    if not cells or not cells[0]:
        return None
    compact = cells[0].replace(" ", "")
    addresses: list[int] = []
    m = re.fullmatch(r"(\d+)-(\d+)", compact)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        addresses = list(range(a, b + 1))
    elif compact.isdigit():
        addresses = [int(compact)]
    else:
        nums = re.findall(r"\d+", compact)
        if len(nums) >= 2 and "\n" in cells[0]:
            addresses = [int(x) for x in nums[:2]]
        elif compact.isdigit() or (nums and compact == nums[0]):
            addresses = [int(nums[0])]
        else:
            return None
    name = cells[1] if len(cells) > 1 else ""
    if not name:
        return None
    if len(cells) >= 7:
        return ParsedRow(line_no, addresses, name, cells[2], cells[3], cells[5], cells[6])
    if len(cells) == 4:
        return ParsedRow(line_no, addresses, name, cells[2], "", "", cells[3])
    return None


def sample_registers(registers: list[dict], count: int = 50) -> list[dict]:
    buckets = [(0, 99), (100, 199), (200, 299), (300, 499), (500, 599), (600, 679), (700, 829), (860, 1005)]
    rng = random.Random(42)
    picked: dict[int, dict] = {}
    per_bucket = max(1, count // len(buckets))
    for lo, hi in buckets:
        bucket = [r for r in registers if lo <= r["address"] <= hi]
        for item in rng.sample(bucket, min(per_bucket, len(bucket))):
            picked[item["address"]] = item
    for addr in (0, 1, 2, 11, 100, 225, 472, 500, 538, 600, 616, 647, 701, 745, 800, 860, 901, 950):
        by_addr = {r["address"]: r for r in registers}
        if addr in by_addr:
            picked[addr] = by_addr[addr]
    return sorted(picked.values(), key=lambda r: r["address"])[:count]


def compare_name(profile_name: str, proto_name: str, address: int) -> str | None:
    pn = profile_name.strip()
    tn = proto_name.strip()
    if pn == tn:
        return None
    if proto_name == "预留" and pn == f"预留 {address}":
        return None
    if pn.startswith(tn.rstrip()) or tn.rstrip() in pn:
        return None
    if "(低字)" in pn or "(高字)" in pn:
        if tn.split("(")[0].strip() in pn:
            return None
    return f"名称: profile={pn!r}, proto={tn!r}"


def main() -> int:
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    protocol_lines = PROTOCOL_PATH.read_text(encoding="utf-8").splitlines()
    sample = sample_registers(profile["registers"], 50)

    ok = 0
    minor = 0
    issues: list[tuple[str, str, list[str]]] = []

    for reg in sample:
        addr = reg["address"]
        row_no = reg.get("source", {}).get("row")
        problems: list[str] = []
        severity = "ok"

        if not row_no or row_no > len(protocol_lines):
            problems.append("缺少有效 source.row，无法对照协议行")
            severity = "issue"
        else:
            parsed = parse_row(row_no, protocol_lines[row_no - 1])
            if parsed is None:
                problems.append(f"source.row={row_no} 无法解析协议行")
                severity = "issue"
            else:
                if addr not in parsed.addresses:
                    problems.append(
                        f"地址不在协议行内: profile={addr}, row_addrs={parsed.addresses}"
                    )
                    severity = "issue"

                name_issue = compare_name(reg["name"], parsed.name, addr)
                if name_issue:
                    if name_issue.endswith("\\n'") or "\\n" in parsed.name:
                        minor += 1
                        severity = "minor"
                    else:
                        problems.append(name_issue)
                        severity = "issue"

                expected_access = infer_access(addr)
                if reg["access"] != expected_access:
                    problems.append(f"access: profile={reg['access']}, expected={expected_access}")
                    severity = "issue"

                expected_transfer = normalize_transfer(parsed.transfer_raw, parsed.range_text, parsed.note)
                if reg["transfer"] != expected_transfer:
                    problems.append(f"transfer: profile={reg['transfer']!r}, expected={expected_transfer!r}")
                    severity = "issue"

                if reg["data_type"] != "s16":
                    problems.append(f"data_type 应为 s16, 实际={reg['data_type']}")
                    severity = "issue"

                proto_enum = parse_enum("\n".join([parsed.range_text, parsed.note]))
                for key, label in proto_enum.items():
                    if key not in reg.get("enum", {}):
                        problems.append(f"enum 缺少 {key}={label}")
                        severity = "issue"
                    elif reg["enum"][key] != label and label.rstrip("\\") not in reg["enum"][key]:
                        problems.append(f"enum[{key}] 不一致")
                        severity = "minor"

        if severity == "ok":
            ok += 1
        elif problems:
            issues.append((severity, f"word {addr} ({reg['name']})", problems))

    print(f"抽检 {len(sample)} 条")
    print(f"  完全通过: {ok}")
    print(f"  轻微差异(名称换行等): {minor}")
    print(f"  需关注: {len(issues)}")
    for severity, title, problems in issues:
        print(f"[{severity}] {title}")
        for p in problems:
            print(f"    - {p}")

    # warnings assessment
    print("\n=== 提取警告评估 ===")
    warnings = [
        "line 750-753: 830-859 设备信息段缺地址",
    ]
    for w in warnings:
        print(f"- {w}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
