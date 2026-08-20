from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Dict, List, TYPE_CHECKING

if TYPE_CHECKING:
    from .register_model import RegisterBank


@dataclass
class EmsWebRegister:
    address: int
    value: int
    note: str = ""
    phase: str = ""


@dataclass
class SimulatorPreset:
    preset_id: str
    name: str
    profile_id: str
    description: str = ""
    registers: Dict[int, int] = field(default_factory=dict)
    coils: Dict[int, bool] = field(default_factory=dict)
    ems_web_registers: List[EmsWebRegister] = field(default_factory=list)
    capture_points: List[Dict[str, object]] = field(default_factory=list)
    state_fields: List[Dict[str, object]] = field(default_factory=list)

    @classmethod
    def from_json(cls, path: Path) -> "SimulatorPreset":
        data = json.loads(path.read_text(encoding="utf-8"))
        ems_rows = [
            EmsWebRegister(
                address=int(item["address"]),
                value=int(item["value"]),
                note=str(item.get("note", "")),
                phase=str(item.get("phase", "")),
            )
            for item in data.get("ems_web_registers", [])
        ]
        return cls(
            preset_id=str(data["preset_id"]),
            name=str(data["name"]),
            profile_id=str(data["profile_id"]),
            description=str(data.get("description", "")),
            registers={int(key): int(value) for key, value in dict(data.get("registers", {})).items()},
            coils={int(key): bool(value) for key, value in dict(data.get("coils", {})).items()},
            ems_web_registers=ems_rows,
            capture_points=[
                {"kind": str(item["kind"]), "address": int(item["address"])}
                for item in data.get("capture_points", [])
            ],
            state_fields=[dict(item) for item in data.get("state_fields", [])],
        )

    def to_dict(self) -> Dict[str, object]:
        data: Dict[str, object] = {
            "preset_id": self.preset_id,
            "name": self.name,
            "profile_id": self.profile_id,
            "description": self.description,
            "registers": {str(address): value for address, value in sorted(self.registers.items())},
            "coils": {str(address): value for address, value in sorted(self.coils.items())},
        }
        if self.ems_web_registers:
            data["ems_web_registers"] = [
                {
                    "phase": row.phase,
                    "address": row.address,
                    "value": row.value,
                    "note": row.note,
                }
                for row in self.ems_web_registers
            ]
        data["capture_points"] = [dict(item) for item in self.capture_points]
        data["state_fields"] = [dict(item) for item in self.state_fields]
        return data


def resolve_preset_path(root: Path, profile_path: Path, startup_preset: str) -> Path:
    preset_path = Path(startup_preset)
    if preset_path.is_absolute():
        return preset_path
    candidates = [
        profile_path.parent / preset_path,
        root / preset_path,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return root / preset_path


def load_startup_preset(root: Path, profile_path: Path, startup_preset: str | None) -> SimulatorPreset | None:
    if not startup_preset:
        return None
    preset_path = resolve_preset_path(root, profile_path, startup_preset)
    if not preset_path.exists():
        raise FileNotFoundError(f"Startup preset not found: {preset_path}")
    return SimulatorPreset.from_json(preset_path)


def apply_preset_to_bank(bank: "RegisterBank", preset: SimulatorPreset) -> None:
    with bank._lock:
        bank._apply_preset_unlocked(preset)
