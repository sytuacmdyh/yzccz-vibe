"""stdio 控制通道冒烟测试。

用法：
  python tools\\smoke_test_stdio_control.py            # 仅单元式（分发器直驱，无串口依赖）
  python tools\\smoke_test_stdio_control.py --port COM5  # 追加端到端（拉起 --cli --stdio-control 子进程）

单元式覆盖：set/get_register、get_registers、slave_id 分槽、set/get_coil、snapshot、
get_profile、未知 op、字段缺失/类型错误。
端到端覆盖：ready 事件、请求/响应往返、shutdown 优雅退出（退出码 0）。
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
SRC = APP_ROOT / "src"
APP_PY = APP_ROOT / "app.py"
PROFILE = APP_ROOT / "profiles" / "dm_hp3_rs48_v2.json"

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f" - {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def unit_tests() -> None:
    sys.path.insert(0, str(SRC))
    from ems_modbus_slave.device_profile import DeviceProfile
    from ems_modbus_slave.register_model import RegisterBank
    from ems_modbus_slave.stdio_control import handle_command

    bank = RegisterBank(DeviceProfile.from_json(PROFILE))

    def call(op: str, **kwargs) -> dict:
        return handle_command(bank, {"op": op, **kwargs})

    ok = call("set_register", address=604, value=200)
    check("set_register ok", ok.get("ok") is True, str(ok))
    check("set_register applied", bank.get(604) == 200)

    got = call("get_register", address=604)
    check("get_register value", got.get("value") == 200, str(got))

    slotted = call("set_register", address=11, value=450, slave_id=2)
    check("set_register slave_id ok", slotted.get("ok") is True, str(slotted))
    check("per-slave slot isolated", bank.get(11, 2) == 450 and bank.get(11) != 450)

    rng = call("get_registers", address=600, count=3)
    check("get_registers count", isinstance(rng.get("values"), list) and len(rng["values"]) == 3, str(rng))

    coil_addr = next(iter(bank.profile.coil_by_address), None)
    if coil_addr is None:
        print("[SKIP] profile has no coils - coil ops not exercised")
    else:
        check("set_coil ok", call("set_coil", address=coil_addr, value=True).get("ok") is True)
        check("get_coil value", call("get_coil", address=coil_addr).get("value") == 1)
        check("set_coil string", call("set_coil", address=coil_addr, value="false").get("value") == 0)

    snap = call("snapshot")
    expected_rows = len(bank.profile.registers) + len(bank.profile.coils)
    check("snapshot rows", len(snap.get("rows", [])) == expected_rows, f"{len(snap.get('rows', []))} != {expected_rows}")

    prof = call("get_profile")
    check("get_profile id", prof.get("profile_id") == "dm_hp3_rs48_v2", str(prof))

    check("unknown op rejected", call("nope").get("ok") is False)
    check("missing address rejected", call("set_register", value=1).get("ok") is False)
    check("bad value type rejected", call("set_register", address=604, value="x").get("ok") is False)


def e2e_test(port: str) -> None:
    exe = [sys.executable, str(APP_PY), "--cli", "--stdio-control",
           "--port", port, "--profile", "dm_hp3_rs48_v2"]
    proc = subprocess.Popen(
        exe, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    lines: list[str] = []
    lock = threading.Lock()

    def reader() -> None:
        for line in proc.stdout:
            with lock:
                lines.append(line.strip())

    threading.Thread(target=reader, daemon=True).start()

    def wait_for(predicate, timeout: float = 10.0) -> str | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with lock:
                for line in lines:
                    if predicate(line):
                        return line
            time.sleep(0.05)
        return None

    try:
        ready = wait_for(lambda line: '"type": "ready"' in line)
        check("e2e ready event", ready is not None, ready or "no ready within 10s")

        def send(op: str, req_id: int, **kwargs) -> str | None:
            proc.stdin.write(json.dumps({"type": "request", "id": req_id, "op": op, **kwargs}) + "\n")
            proc.stdin.flush()
            return wait_for(lambda line: f'"id": {req_id}' in line, timeout=5.0)

        set_resp = send("set_register", 1, address=604, value=200)
        check("e2e set_register response", set_resp is not None and '"ok": true' in set_resp, str(set_resp))
        get_resp = send("get_register", 2, address=604)
        check("e2e get_register value", get_resp is not None and '"value": 200' in get_resp, str(get_resp))

        shutdown_resp = send("shutdown", 3)
        check("e2e shutdown response", shutdown_resp is not None and '"ok": true' in shutdown_resp, str(shutdown_resp))
        try:
            exit_code = proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            exit_code = proc.wait()
        check("e2e graceful exit code 0", exit_code == 0, f"exit={exit_code}")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def main() -> int:
    unit_tests()
    if "--port" in sys.argv:
        port = sys.argv[sys.argv.index("--port") + 1]
        e2e_test(port)
    else:
        print("[SKIP] e2e not run - pass --port <COMx> to enable")
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s): {FAILURES}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
