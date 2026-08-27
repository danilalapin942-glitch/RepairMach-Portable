from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from calculation_scenarios import (
    build_scenario_manifest,
    component_name_policy,
    load_scenario_catalog,
    scenario_by_id,
    validate_component_names,
)


class CalculationScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_scenario_catalog(ROOT / "config" / "calculation_scenarios.json")

    def test_catalog_has_ready_and_protocol_scenarios(self):
        self.assertEqual("9.1", self.catalog["catalog_version"])
        ready = [item for item in self.catalog["scenarios"] if item["availability"] == "ready"]
        protocol = [item for item in self.catalog["scenarios"] if item["availability"] == "protocol"]
        self.assertGreaterEqual(len(ready), 6)
        self.assertGreaterEqual(len(protocol), 3)

    def test_full_adh_keeps_transonic_gap_split(self):
        scenario = scenario_by_id(self.catalog, "full_adh")
        self.assertEqual(2, len(scenario["vspaero_cases"]))
        self.assertEqual(0.8, scenario["vspaero_cases"][0]["mach_end"])
        self.assertEqual(1.1, scenario["vspaero_cases"][1]["mach_start"])

    def test_component_name_policy_accepts_suffixes_and_roles(self):
        geometries = [
            {"NAME": "Fuselage", "IN_SET_1": True, "IN_SET_2": False},
            {"NAME": "Wing", "IN_SET_1": False, "IN_SET_2": True},
            {"NAME": "GO", "IN_SET_1": False, "IN_SET_2": True},
            {"NAME": "VO_R", "IN_SET_1": False, "IN_SET_2": True},
            {"NAME": "Gondola_L", "IN_SET_1": True, "IN_SET_2": False},
        ]
        result = validate_component_names(geometries, component_name_policy(self.catalog))
        self.assertTrue(result["valid"])
        self.assertFalse(result["warnings"])

    def test_component_name_policy_reports_missing_and_wrong_set(self):
        geometries = [
            {"NAME": "Body", "IN_SET_1": True, "IN_SET_2": False},
            {"NAME": "Wing", "IN_SET_1": True, "IN_SET_2": False},
        ]
        result = validate_component_names(geometries, component_name_policy(self.catalog))
        self.assertFalse(result["valid"])
        self.assertTrue(any("Fuselage" in item for item in result["errors"]))
        self.assertTrue(any("Set_2" in item for item in result["errors"]))
        self.assertTrue(result["warnings"])

    def test_manifest_contains_geometry_hash(self):
        scenario = scenario_by_id(self.catalog, "mesh_convergence")
        with TemporaryDirectory() as tmp:
            geometry = Path(tmp) / "model.vsp3"
            geometry.write_bytes(b"model")
            manifest = build_scenario_manifest(
                scenario,
                repairmach_version="9.1",
                project_name="Test",
                geometry_path=geometry,
            )
        self.assertEqual("mesh_convergence", manifest["scenario"]["id"])
        self.assertEqual(64, len(manifest["geometry"]["sha256"]))


if __name__ == "__main__":
    unittest.main()
