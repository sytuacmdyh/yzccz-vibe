import importlib.util
import sys
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
