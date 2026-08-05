from __future__ import annotations

from dataclasses import dataclass, field
import json
from copy import deepcopy
from pathlib import Path
from typing import Dict, Iterable, List


@dataclass
class RegisterDefinition:
    address: int
    name: str
    access: str
    default: int = 0
    min_value: int = 0
    max_value: int = 65535
    description: str = ""
    data_type: str = "u16"
    address_label: str = ""
    enum: Dict[str, str] = field(default_factory=dict)
    unit: str = ""
    transfer: str = ""
    synthetic: bool = False

    @property
    def writable(self) -> bool:
        return "w" in self.access

    @property
    def readable(self) -> bool:
        return "r" in self.access

    def clamp(self, value: int) -> int:
        if value < self.min_value:
            return self.min_value
        if value > self.max_value:
            return self.max_value
        return value

    def display_value(self, value: int) -> str:
        label = self.enum.get(str(value)) if self.enum else None
        if label:
            return f"{label} [{value}]"
        return f"{value}{self.unit}" if self.unit and self.unit != "/" else str(value)

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "RegisterDefinition":
        min_value = data.get("min", data.get("min_raw", 0))
        max_value = data.get("max", data.get("max_raw", 65535))
        return cls(
            address=int(data["address"]),
            name=str(data["name"]),
            access=str(data.get("access", "rw")),
            default=int(data.get("default", data.get("default_raw", 0))),
            min_value=0 if min_value is None else int(min_value),
            max_value=65535 if max_value is None else int(max_value),
            description=str(data.get("description", "")),
            data_type=str(data.get("data_type", "u16")),
            address_label=str(data.get("address_label", f"word {data['address']}")),
            enum={str(key): str(value) for key, value in dict(data.get("enum") or {}).items()},
            unit=str(data.get("unit", "")),
            transfer=str(data.get("transfer", "")),
            synthetic=bool(data.get("synthetic", False)),
        )


@dataclass
class CoilDefinition:
    address: int
    name: str
    access: str
    default: bool
    backing_register: int
    bit_offset: int
    description: str = ""
    false_label: str = "False"
    true_label: str = "True"
    data_type: str = "bool"
    address_label: str = ""

    @property
    def writable(self) -> bool:
        return "w" in self.access

    @property
    def readable(self) -> bool:
        return "r" in self.access

    def display_value(self, value: bool) -> str:
        return self.true_label if value else self.false_label

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "CoilDefinition":
        return cls(
            address=int(data["address"]),
            name=str(data["name"]),
            access=str(data.get("access", "rw")),
            default=bool(data.get("default", False)),
            backing_register=int(data["backing_register"]),
            bit_offset=int(data["bit_offset"]),
            description=str(data.get("description", "")),
            false_label=str(data.get("false_label", "False")),
            true_label=str(data.get("true_label", "True")),
            data_type=str(data.get("data_type", "bool")),
            address_label=str(data.get("address_label", f"bit {data['address']}")),
        )


class DeviceProfile:
    def __init__(
        self,
        profile_id: str,
        name: str,
        description: str,
        slave_id: int,
        baudrate: int,
        function_codes: Iterable[int],
        registers: List[RegisterDefinition],
        coils: List[CoilDefinition],
        status_fields: List[Dict[str, object]],
        bindings: Dict[str, str],
        startup_preset: str = "",
        raw_data: Dict[str, object] | None = None,
    ) -> None:
        self.profile_id = profile_id
        self.name = name
        self.description = description
        self.startup_preset = startup_preset
        self._raw_data = deepcopy(raw_data) if raw_data is not None else None
        self.slave_id = slave_id
        self.baudrate = baudrate
        self.function_codes = list(function_codes)
        self.registers = registers
        self.coils = coils
        self.status_fields = status_fields
        self.bindings = bindings
        self.by_address = {register.address: register for register in registers}
        self.by_name = {register.name: register for register in registers}
        self.coil_by_address = {coil.address: coil for coil in coils}
        self.coil_by_name = {coil.name: coil for coil in coils}

    @classmethod
    def from_json(cls, path: Path) -> "DeviceProfile":
        data = json.loads(path.read_text(encoding="utf-8"))
        registers = [RegisterDefinition.from_dict(item) for item in data["registers"]]
        coils = [CoilDefinition.from_dict(item) for item in data.get("coils", [])]
        return cls(
            profile_id=str(data.get("profile_id", data["name"])),
            name=str(data["name"]),
            description=str(data.get("description", "")),
            slave_id=int(data.get("slave_id", 1)),
            baudrate=int(data.get("baudrate", 115200)),
            function_codes=data.get("function_codes", [3, 6, 16]),
            registers=registers,
            coils=coils,
            status_fields=list(data.get("status_fields", [])),
            bindings=dict(data.get("bindings", {})),
            startup_preset=str(data.get("startup_preset", "")),
            raw_data=data,
        )

    def to_dict(self) -> Dict[str, object]:
        """Return the original profile document so export does not lose protocol metadata."""
        if self._raw_data is not None:
            return deepcopy(self._raw_data)
        return {
            "profile_id": self.profile_id,
            "name": self.name,
            "description": self.description,
            "slave_id": self.slave_id,
            "baudrate": self.baudrate,
            "function_codes": list(self.function_codes),
            "startup_preset": self.startup_preset,
            "status_fields": deepcopy(self.status_fields),
            "bindings": deepcopy(self.bindings),
            "registers": [
                {
                    "address": item.address,
                    "address_label": item.address_label,
                    "name": item.name,
                    "access": item.access,
                    "default_raw": item.default,
                    "min_raw": item.min_value,
                    "max_raw": item.max_value,
                    "description": item.description,
                    "data_type": item.data_type,
                    "enum": dict(item.enum),
                    "unit": item.unit,
                    "transfer": item.transfer,
                    "synthetic": item.synthetic,
                }
                for item in self.registers
            ],
            "coils": [
                {
                    "address": item.address,
                    "address_label": item.address_label,
                    "name": item.name,
                    "access": item.access,
                    "default": item.default,
                    "backing_register": item.backing_register,
                    "bit_offset": item.bit_offset,
                    "description": item.description,
                    "false_label": item.false_label,
                    "true_label": item.true_label,
                    "data_type": item.data_type,
                }
                for item in self.coils
            ],
        }
