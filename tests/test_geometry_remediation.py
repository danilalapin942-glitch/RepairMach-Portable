from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from geometry_certificate import write_certificate_report
from geometry_remediation import (
    CORRECTIVE_PLAN_SCHEMA,
    build_corrective_action_plan,
    corrective_action_plan_errors,
)


class GeometryRemediationTests(unittest.TestCase):
    def test_clean_certificate_requires_no_action(self):
        plan = build_corrective_action_plan(
            findings=[],
            replacement_contract={"requirements": []},
            verdict="PASS_NATIVE",
        )
        self.assertEqual(CORRECTIVE_PLAN_SCHEMA, plan["schema"])
        self.assertEqual("no_action_required", plan["status"])
        self.assertTrue(plan["calculation_release_ready"])
        self.assertTrue(all(plan["backend_action_free"].values()))
        self.assertEqual([], plan["items"])
        self.assertEqual([], corrective_action_plan_errors(plan))

    def test_replacement_contract_routes_missing_coverage_and_base_drag(self):
        contract = {
            "requirements": [
                {
                    "component": "VO",
                    "method": "machline_pressure_wave_all_points",
                    "backend_capability_available": False,
                    "reason": "component_absent_from_certified_machline_pressure_mesh",
                },
                {
                    "component": "RM_NUMERICAL_AFT_CLOSURE",
                    "method": "base_drag_semiempirical",
                    "backend_capability_available": True,
                    "source_required_at_hybrid_build": True,
                    "reason": "numerical_aft_closure_force_is_masked",
                },
            ]
        }
        plan = build_corrective_action_plan(
            findings=[], replacement_contract=contract, verdict="PASS_WITH_DECLARED_EXCLUSIONS"
        )
        self.assertEqual("blocked", plan["status"])
        self.assertEqual(2, plan["summary"]["required_count"])
        self.assertEqual(
            ["RESTORE_REPLACEMENT_COVERAGE", "CALCULATE_SEMIEMPIRICAL_BASE_DRAG"],
            [item["action_code"] for item in plan["items"]],
        )
        self.assertTrue(plan["backend_action_free"]["vspaero"])
        self.assertFalse(plan["backend_action_free"]["machline"])
        self.assertFalse(plan["backend_action_free"]["hybrid"])
        self.assertTrue(plan["minimum_retest"]["new_geometry_certificate_required"])
        self.assertTrue(plan["minimum_retest"]["new_hybrid_package_required"])
        self.assertEqual([], corrective_action_plan_errors(plan))

    def test_semiempirical_component_replacement_routes_to_automatic_hybrid_action(self):
        plan = build_corrective_action_plan(
            findings=[],
            replacement_contract={"requirements": [{
                "component": "VO",
                "method": "semiempirical_component_pressure_wave_all_points",
                "backend_capability_available": True,
                "source_required_at_hybrid_build": True,
                "reason": "sealed_component_method_required_at_hybrid_build",
            }]},
            verdict="PASS_WITH_DECLARED_EXCLUSIONS",
        )
        item = plan["items"][0]
        self.assertEqual(
            "BIND_SEMIEMPIRICAL_COMPONENT_PRESSURE_SERIES",
            item["action_code"],
        )
        self.assertEqual("repairmach", item["owner"])
        self.assertFalse(item["new_geometry_certificate_required"])
        self.assertIn("seal_method_passport", item["allowed_automatic_actions"])
        self.assertEqual([], corrective_action_plan_errors(plan))

    def test_localized_vspaero_failure_is_split_by_component(self):
        plan = build_corrective_action_plan(
            findings=[{
                "code": "GEO-VSP-004",
                "severity": "WARNING",
                "scope": "vspaero_lifting",
                "message": "localized",
                "evidence": {
                    "localized_failures": [
                        {"groups": ["VO", "GO"]},
                        {"groups": ["VO"]},
                    ]
                },
            }],
            verdict="PASS_WITH_DECLARED_EXCLUSIONS",
        )
        self.assertEqual(["GO", "VO"], [item["component"] for item in plan["items"]])
        self.assertTrue(all(item["new_geometry_certificate_required"] for item in plan["items"]))

    def test_unknown_blocker_fails_closed_and_plan_is_deterministic(self):
        findings = [{
            "code": "GEO-FUTURE-999",
            "severity": "BLOCKER",
            "scope": "master",
            "message": "new diagnostic",
        }]
        first = build_corrective_action_plan(findings=findings, verdict="FAIL")
        second = build_corrective_action_plan(findings=findings, verdict="FAIL")
        self.assertEqual(first, second)
        self.assertEqual("MANUAL_FAIL_CLOSED_REVIEW", first["items"][0]["action_code"])
        self.assertEqual("operator", first["items"][0]["owner"])
        self.assertEqual([], corrective_action_plan_errors(first))

        tampered = deepcopy(first)
        tampered["items"][0]["action"] = "unsafe shortcut"
        self.assertTrue(
            any("целостность" in error for error in corrective_action_plan_errors(tampered))
        )

    def test_report_renders_machine_plan_and_minimum_retest(self):
        plan = build_corrective_action_plan(
            findings=[{
                "code": "GEO-NAME-001",
                "severity": "BLOCKER",
                "scope": "master",
                "component": "UnknownPart",
                "message": "unknown",
            }],
            verdict="FAIL",
        )
        certificate = {
            "certificate_id": "RMC-ACTIONS",
            "method_version": "test",
            "verdict": "FAIL",
            "flags": {},
            "master": {},
            "qualification": {},
            "backends": {},
            "findings": [],
            "corrective_action_plan": plan,
            "artifacts": {"corrective_action_plan": "corrective_action_plan.json"},
        }
        with TemporaryDirectory() as tmp:
            report = Path(tmp) / "certificate.md"
            write_certificate_report(report, certificate)
            text = report.read_text(encoding="utf-8")
        self.assertIn("План дальнейших действий", text)
        self.assertIn("ASSIGN_CANONICAL_COMPONENT_NAME", text)
        self.assertIn("UnknownPart", text)
        self.assertIn("Новый сертификат геометрии: **да**", text)


if __name__ == "__main__":
    unittest.main()
