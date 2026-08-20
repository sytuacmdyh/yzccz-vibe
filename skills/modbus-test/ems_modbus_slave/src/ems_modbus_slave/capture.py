from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import threading

from .device_profile import DeviceProfile
from .modbus_rtu import (
    FC_WRITE_MULTIPLE,
    FC_WRITE_MULTIPLE_COILS,
    FC_WRITE_SINGLE,
    FC_WRITE_SINGLE_COIL,
    RequestFrame,
)


PointKey = tuple[str, int]
COIL_WRITE_FUNCTIONS = {FC_WRITE_SINGLE_COIL, FC_WRITE_MULTIPLE_COILS}
REGISTER_WRITE_FUNCTIONS = {FC_WRITE_SINGLE, FC_WRITE_MULTIPLE}


@dataclass(frozen=True)
class CaptureMatch:
    points: tuple[str, ...]
    addresses: tuple[PointKey, ...]


def format_address_summary(addresses: tuple[PointKey, ...]) -> str:
    if not addresses:
        return "-"
    kind = addresses[0][0]
    nums = sorted({address for point_kind, address in addresses if point_kind == kind})
    if not nums:
        return "-"
    prefix = "bit" if kind == "coil" else "word"
    if len(nums) == 1:
        return f"{prefix} {nums[0]}"
    if nums == list(range(nums[0], nums[-1] + 1)):
        return f"{prefix} {nums[0]}~{nums[-1]}"
    return ", ".join(f"{prefix} {number}" for number in nums)


@dataclass(frozen=True)
class CaptureRecord:
    timestamp: str
    profile_name: str
    function_code: int
    points: tuple[str, ...]
    addresses: tuple[PointKey, ...]
    request: bytes
    response: bytes
    request_message: str
    response_message: str
    is_error: bool

    def address_summary(self) -> str:
        return format_address_summary(self.addresses)

    @classmethod
    def create(
        cls,
        profile: DeviceProfile,
        request: RequestFrame,
        response: bytes,
        match: CaptureMatch,
        request_message: str,
        response_message: str,
        is_error: bool,
    ) -> "CaptureRecord":
        return cls(
            timestamp=datetime.now().strftime("%H:%M:%S"),
            profile_name=profile.name,
            function_code=request.function_code,
            points=match.points,
            addresses=match.addresses,
            request=request.raw,
            response=response,
            request_message=request_message,
            response_message=response_message,
            is_error=is_error,
        )


class CaptureTracker:
    """Keeps runtime-only point selections and matches valid Modbus requests."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._enabled: set[PointKey] = set()

    def is_enabled(self, kind: str, address: int) -> bool:
        with self._lock:
            return (kind, address) in self._enabled

    def toggle(self, kind: str, address: int) -> bool:
        key = (kind, address)
        with self._lock:
            if key in self._enabled:
                self._enabled.remove(key)
                return False
            self._enabled.add(key)
            return True

    def retain_available(self, profile: DeviceProfile) -> None:
        available = {
            *(('register', address) for address in profile.by_address),
            *(('coil', address) for address in profile.coil_by_address),
        }
        with self._lock:
            self._enabled.intersection_update(available)

    def enabled_points(self) -> list[dict[str, object]]:
        with self._lock:
            return [
                {"kind": kind, "address": address}
                for kind, address in sorted(self._enabled)
            ]

    def set_enabled_points(self, points: list[dict[str, object]], profile: DeviceProfile) -> None:
        available = {
            *(('register', address) for address in profile.by_address),
            *(('coil', address) for address in profile.coil_by_address),
        }
        requested = {
            (str(item.get("kind", "")), int(item.get("address", -1)))
            for item in points
        }
        with self._lock:
            self._enabled = requested.intersection(available)

    def matching_capture(self, profile: DeviceProfile, request: RequestFrame) -> CaptureMatch | None:
        function_code = request.function_code
        if function_code in COIL_WRITE_FUNCTIONS:
            kind = "coil"
            definitions = profile.coil_by_address
        elif function_code in REGISTER_WRITE_FUNCTIONS:
            kind = "register"
            definitions = profile.by_address
        else:
            return None
        if len(request.raw) < 6:
            return None

        start = (request.raw[2] << 8) | request.raw[3]
        count = 1 if function_code in (FC_WRITE_SINGLE_COIL, FC_WRITE_SINGLE) else (request.raw[4] << 8) | request.raw[5]
        if count <= 0:
            return None
        with self._lock:
            addresses = [
                address
                for address in range(start, start + count)
                if (kind, address) in self._enabled and address in definitions
            ]
        if not addresses:
            return None
        return CaptureMatch(
            points=tuple(
                f"{definitions[address].name} ({definitions[address].address_label})"
                for address in addresses
            ),
            addresses=tuple((kind, address) for address in addresses),
        )

    def matching_points(self, profile: DeviceProfile, request: RequestFrame) -> tuple[str, ...]:
        match = self.matching_capture(profile, request)
        return match.points if match is not None else ()
