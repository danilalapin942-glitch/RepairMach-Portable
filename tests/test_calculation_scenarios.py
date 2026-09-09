from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from calculation_scenarios import (
    build_scenario_manifest,
    component_name_policy,
    expand_vspaero_cases,
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
        self.assertEqual(1.2, scenario["vspaero_cases"][1]["mach_start"])

    def test_fixed_go_scenario_is_reproducible(self):
        scenario = scenario_by_id(self.catalog, "fixed_go_full_adh")
        self.assertEqual("fixed", scenario["tail_incidence"])
        self.assertEqual("GO", scenario["tail_geometry_name"])
        self.assertEqual(0.0, scenario["tail_incidence_deg"])
        self.assertEqual(5.0, scenario["acceptance_criteria"]["numerical_percent"])
        self.assertEqual(11.0, scenario["acceptance_criteria"]["total_mean_percent"])
        m12 = next(case for case in scenario["vspaero_cases"] if case["mach_start"] == 1.2)
        self.assertEqual("independent_alpha", m12["point_execution"])
        self.assertEqual("to_face", m12["engine_boundary"])
        supersonic = next(case for case in scenario["vspaero_cases"] if case["mach_start"] == 1.3)
        self.assertEqual("independent_mach_alpha", supersonic["point_execution"])
        self.assertEqual("to_face", supersonic["engine_boundary"])

    def test_fixed_go_screening_uses_only_direct_derivative_angles(self):
        scenario = scenario_by_id(self.catalog, "fixed_go_screening")
        self.assertEqual("fixed", scenario["tail_incidence"])
        self.assertEqual([0.8, 1.2, 1.7], [case["mach_start"] for case in scenario["vspaero_cases"]])
        for case in scenario["vspaero_cases"]:
            self.assertEqual((0.0, 1.0, 2), (
                case["alpha_start"], case["alpha_end"], case["alpha_points"]
            ))
        m12 = scenario["vspaero_cases"][1]
        self.assertEqual("independent_alpha", m12["point_execution"])
        self.assertEqual("to_face", m12["engine_boundary"])
        m17 = scenario["vspaero_cases"][2]
        self.assertEqual("independent_alpha", m17["point_execution"])
        self.assertEqual("to_face", m17["engine_boundary"])

    def test_independent_alpha_expands_to_fresh_solver_cases(self):
        scenario = scenario_by_id(self.catalog, "fixed_go_screening")
        expanded = expand_vspaero_cases(scenario["vspaero_cases"])
        m12 = [case for case in expanded if case.get("parent_case_name") == "screen_GO0_M1p2"]
        self.assertEqual(2, len(m12))
        self.assertEqual([0.0, 1.0], [case["alpha_start"] for case in m12])
        self.assertTrue(all(case["alpha_points"] == 1 for case in m12))
        self.assertTrue(all(case["engine_boundary"] == "to_face" for case in m12))

    def test_independent_mach_alpha_isolates_every_solver_point(self):
        scenario = scenario_by_id(self.catalog, "fixed_go_full_adh")
        source = next(case for case in scenario["vspaero_cases"] if case["mach_start"] == 1.3)
        expanded = expand_vspaero_cases([source])
        self.assertEqual(60, len(expanded))
        self.assertTrue(all(case["mach_points"] == 1 for case in expanded))
        self.assertTrue(all(case["alpha_points"] == 1 for case in expanded))
        self.assertEqual(10, len({case["mach_start"] for case in expanded}))
        self.assertEqual(6, len({case["alpha_start"] for case in expanded}))
        self.assertTrue(all(case["engine_boundary"] == "to_face" for case in expanded))

    def test_fixed_go_numerical_verification_uses_strict_solver(self):
        scenario = scenario_by_id(self.catalog, "fixed_go_numerical_verification")
        controls = scenario["solver_controls"]
        self.assertEqual(2.0, controls["forward_gmres_convergence_factor"])
        self.assertEqual(16, controls["wake_num_iter"])
        self.assertEqual(24, controls["num_wake_nodes"])

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

    def test_component_name_policy_explains_legacy_names(self):
        geometries = [
            {"NAME": "FuselageGeom", "IN_SET_1": True, "IN_SET_2": False},
            {"NAME": "WingGeom", "IN_SET_1": False, "IN_SET_2": True},
        ]
        result = validate_component_names(geometries, component_name_policy(self.catalog))
        self.assertFalse(result["valid"])
        self.assertTrue(any("Устаревшее имя WingGeom" in item for item in result["warnings"]))

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
