import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError, URLError


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "modbus-test"
    / "scripts"
    / "modbus_test.py"
)
SPEC = importlib.util.spec_from_file_location("yzc_modbus_test", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


CSV_HEADER = "function,address,value,description\n"


def write_csv(path: Path, row: str = "delay,0,0,noop\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(CSV_HEADER + row, encoding="utf-8")


def run_main(*args: str) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with mock.patch.object(sys, "argv", [str(SCRIPT_PATH), *args]):
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = MODULE.main()
    return result, stdout.getvalue(), stderr.getvalue()


class SupplyDemandPropertyTests(unittest.TestCase):
    def test_property_names_map_to_thermostat_keys(self):
        self.assertEqual(MODULE.SIM_PROP_MAP["fan_supply_demand"], "3_7")
        self.assertEqual(MODULE.SIM_PROP_MAP["floor_supply_demand"], "3_8")

    def test_control_accepts_supply_demand_enum(self):
        for prop in MODULE.SIM_SUPPLY_DEMAND_PROPS:
            for value in range(4):
                with self.subTest(prop=prop, value=value):
                    MODULE._validate_sim_control_value(f"{prop}:{value}", 2)

    def test_control_rejects_invalid_supply_demand(self):
        for prop in MODULE.SIM_SUPPLY_DEMAND_PROPS:
            for value in ("-1", "4", "1.5", "invalid"):
                with self.subTest(prop=prop, value=value):
                    with self.assertRaises(MODULE.CsvParseError):
                        MODULE._validate_sim_control_value(f"{prop}:{value}", 2)

    def test_snapshot_read_and_wait_accept_supply_demand(self):
        for prop in MODULE.SIM_SUPPLY_DEMAND_PROPS:
            with self.subTest(prop=prop):
                MODULE._validate_sim_read_value(f"{prop}:2", 2)
                MODULE._validate_sim_wait_value(
                    f"{prop}:3;timeout=2;interval=0.1", 2
                )


class InputResolutionTests(unittest.TestCase):
    def test_file_list_preserves_argument_order_and_display_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first.csv"
            second = root / "second.csv"
            write_csv(first)
            write_csv(second)

            resolved = MODULE.resolve_input_files(
                [str(second), str(first), str(second)]
            )

            self.assertEqual(
                [item.path for item in resolved],
                [second, first, second],
            )
            self.assertEqual(
                [item.display_name for item in resolved],
                [str(second), str(first), str(second)],
            )

    def test_directory_mode_uses_natural_order_and_relative_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_csv(root / "case10.csv")
            write_csv(root / "case2.csv")
            write_csv(root / "nested" / "case1.csv")

            top_level = MODULE.resolve_input_files([str(root)])
            recursive = MODULE.resolve_input_files([str(root)], recursive=True)

            self.assertEqual(
                [item.display_name for item in top_level],
                ["case2.csv", "case10.csv"],
            )
            self.assertEqual(
                [item.display_name for item in recursive],
                ["nested/case1.csv", "case2.csv", "case10.csv"],
            )

    def test_directory_and_file_modes_are_mutually_exclusive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            csv_path = root / "case.csv"
            write_csv(csv_path)

            with self.assertRaises(MODULE.CsvParseError):
                MODULE.resolve_input_files([str(root), str(csv_path)])
            with self.assertRaises(MODULE.CsvParseError):
                MODULE.resolve_input_files([str(csv_path)], recursive=True)

    def test_non_csv_file_is_prepared_as_a_file_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            text_path = Path(tmp) / "case.txt"
            text_path.write_text("not csv", encoding="utf-8")
            input_file = MODULE.InputFile(text_path, str(text_path))

            prepared = MODULE.prepare_input_file(input_file, "utf-8-sig")

            self.assertIsNone(prepared.steps)
            self.assertIn("not a CSV file", prepared.error)

    def test_removed_options_are_rejected(self):
        for option in ("--continue-on-fail", "--stats"):
            with self.subTest(option=option):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        MODULE.parse_args(["case.csv", option])


class SimulatorAvailabilityTests(unittest.TestCase):
    def test_network_error_is_classified_as_unavailable(self):
        sim = MODULE.SimContext("http://127.0.0.1:9090", 1.0)
        with mock.patch.object(
            MODULE.urllib.request,
            "urlopen",
            side_effect=URLError("connection refused"),
        ):
            with self.assertRaises(MODULE.SimUnavailableError):
                MODULE._sim_http_json(sim, "GET", "/api/devices")

    def test_timeout_is_classified_as_unavailable(self):
        sim = MODULE.SimContext("http://127.0.0.1:9090", 1.0)
        with mock.patch.object(
            MODULE.urllib.request,
            "urlopen",
            side_effect=TimeoutError("timed out"),
        ):
            with self.assertRaises(MODULE.SimUnavailableError):
                MODULE._sim_http_json(sim, "GET", "/api/devices")

    def test_http_error_is_not_classified_as_unavailable(self):
        sim = MODULE.SimContext("http://127.0.0.1:9090", 1.0)
        error = HTTPError(
            "http://127.0.0.1:9090/api/devices",
            500,
            "server error",
            None,
            None,
        )
        with mock.patch.object(MODULE.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(MODULE.SimApiError) as raised:
                MODULE._sim_http_json(sim, "GET", "/api/devices")
        self.assertNotIsInstance(raised.exception, MODULE.SimUnavailableError)

    def test_invalid_device_list_shape_is_an_api_error(self):
        sim = MODULE.SimContext("http://127.0.0.1:9090", 1.0)
        with self.assertRaises(MODULE.SimApiError):
            MODULE.update_sim_device_map(sim, {})

    def test_duplicate_device_index_remains_an_api_error(self):
        sim = MODULE.SimContext("http://127.0.0.1:9090", 1.0)
        devices = [
            {"sn": "first", "device_index": 1},
            {"sn": "second", "device_index": 1},
        ]
        with self.assertRaises(MODULE.SimApiError):
            MODULE.update_sim_device_map(sim, devices)


class MainFlowTests(unittest.TestCase):
    def test_invalid_file_fails_and_next_file_still_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            valid = Path(tmp) / "valid.csv"
            missing = Path(tmp) / "missing.csv"
            write_csv(valid)

            exit_code, stdout, stderr = run_main(
                str(missing),
                str(valid),
                "--dry-run",
                "--no-log",
            )

            self.assertEqual(exit_code, 1)
            self.assertEqual(stderr, "")
            self.assertEqual(
                stdout,
                "=== RESULTS ===\n"
                "Log: disabled\n"
                f"[1/2] {missing} ... FAIL\n"
                f"[2/2] {valid} ... PASS (1/1)\n"
                "\n=== ERRORS ===\n"
                f"{missing}\n",
            )

    def test_simulator_unavailable_skips_related_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sim_file = root / "sim.csv"
            regular_file = root / "regular.csv"
            write_csv(
                sim_file,
                "sim_read,1,power:true,read simulator\n"
                "read,600,1,serial step that must also be skipped\n",
            )
            write_csv(regular_file)

            with mock.patch.object(
                MODULE,
                "_sim_http_json",
                side_effect=MODULE.SimUnavailableError("connection refused"),
            ):
                exit_code, stdout, stderr = run_main(
                    str(sim_file),
                    str(regular_file),
                    "--no-log",
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(stderr, "")
            self.assertIn(
                f"[1/2] {sim_file} ... SKIP (DeviceSimulator unavailable)",
                stdout,
            )
            self.assertIn(f"[2/2] {regular_file} ... PASS (1/1)", stdout)
            self.assertTrue(stdout.endswith("=== ERRORS ===\nNone\n"))

    def test_all_simulator_files_skipped_returns_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            sim_file = Path(tmp) / "sim.csv"
            write_csv(sim_file, "sim_read,1,power:true,read simulator\n")

            with mock.patch.object(
                MODULE,
                "_sim_http_json",
                side_effect=MODULE.SimUnavailableError("connection refused"),
            ):
                exit_code, stdout, stderr = run_main(str(sim_file), "--no-log")

            self.assertEqual(exit_code, 0)
            self.assertEqual(stderr, "")
            self.assertIn(
                f"[1/1] {sim_file} ... SKIP (DeviceSimulator unavailable)",
                stdout,
            )
            self.assertTrue(stdout.endswith("=== ERRORS ===\nNone\n"))

    def test_dry_run_does_not_probe_simulator(self):
        with tempfile.TemporaryDirectory() as tmp:
            sim_file = Path(tmp) / "sim.csv"
            write_csv(sim_file, "sim_read,1,power:true,read simulator\n")

            with mock.patch.object(MODULE, "_sim_http_json") as sim_http:
                exit_code, stdout, stderr = run_main(
                    str(sim_file),
                    "--dry-run",
                    "--no-log",
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(stderr, "")
            sim_http.assert_not_called()
            self.assertIn(f"[1/1] {sim_file} ... PASS (1/1)", stdout)

    def test_simulator_protocol_error_remains_a_startup_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            sim_file = Path(tmp) / "sim.csv"
            write_csv(sim_file, "sim_read,1,power:true,read simulator\n")

            with mock.patch.object(MODULE, "_sim_http_json", return_value={}):
                exit_code, stdout, stderr = run_main(str(sim_file), "--no-log")

            self.assertEqual(exit_code, 2)
            self.assertEqual(stdout, "")
            self.assertIn("DeviceSimulator API error", stderr)

    def test_runtime_simulator_outage_fails_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            sim_file = Path(tmp) / "sim.csv"
            write_csv(sim_file, "sim_read,1,power:true,read simulator\n")

            with mock.patch.object(
                MODULE,
                "_sim_http_json",
                side_effect=[[], MODULE.SimUnavailableError("connection lost")],
            ):
                exit_code, stdout, stderr = run_main(str(sim_file), "--no-log")

            self.assertEqual(exit_code, 1)
            self.assertEqual(stderr, "")
            self.assertIn(f"[1/1] {sim_file} ... FAIL (0/1)", stdout)
            self.assertTrue(stdout.endswith(f"=== ERRORS ===\n{sim_file}\n"))

    def test_runtime_failure_does_not_stop_next_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first.csv"
            second = Path(tmp) / "second.csv"
            write_csv(first)
            write_csv(second)
            step_results = [
                MODULE.StepResult(2, MODULE.FUNC_DELAY, "FAIL", "delay", "failed"),
                MODULE.StepResult(2, MODULE.FUNC_DELAY, "PASS", "delay"),
            ]

            with mock.patch.object(MODULE, "execute_step", side_effect=step_results):
                exit_code, stdout, _ = run_main(
                    str(first),
                    str(second),
                    "--no-log",
                )

            self.assertEqual(exit_code, 1)
            self.assertIn(f"[1/2] {first} ... FAIL (0/1)", stdout)
            self.assertIn(f"[2/2] {second} ... PASS (1/1)", stdout)
            self.assertTrue(stdout.endswith(f"=== ERRORS ===\n{first}\n"))

    def test_session_timeout_skips_remaining_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first.csv"
            second = Path(tmp) / "second.csv"
            write_csv(first)
            write_csv(second)
            first_result = MODULE.FileResult(
                name=str(first),
                path=str(first),
                status="fail",
                passed=0,
                total=1,
                duration_s=120.0,
                step_results=[
                    MODULE.StepResult(
                        2,
                        MODULE.FUNC_DELAY,
                        "FAIL",
                        "delay",
                        "session timeout exceeded",
                    )
                ],
            )

            with mock.patch.object(MODULE, "run_file", return_value=first_result):
                with mock.patch.object(
                    MODULE.time,
                    "monotonic",
                    side_effect=[0.0, 0.0, 121.0],
                ):
                    exit_code, stdout, _ = run_main(
                        str(first),
                        str(second),
                        "--no-log",
                    )

            self.assertEqual(exit_code, 1)
            self.assertIn(
                f"[2/2] {second} ... SKIP (session timeout)",
                stdout,
            )
            self.assertTrue(stdout.endswith(f"=== ERRORS ===\n{first}\n"))

    def test_results_show_created_log_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            csv_path = root / "case.csv"
            log_dir = root / "logs"
            write_csv(csv_path)

            exit_code, stdout, stderr = run_main(
                str(csv_path),
                "--dry-run",
                "--log-dir",
                str(log_dir),
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(stderr, "")
            log_files = list(log_dir.glob("modbus_test_*.log"))
            self.assertEqual(len(log_files), 1)
            self.assertIn(f"Log: {log_files[0]}\n", stdout)


if __name__ == "__main__":
    unittest.main()
