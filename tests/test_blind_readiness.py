from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from blind_readiness import assess_blind_readiness, verify_blind_readiness
from geometry_remediation import build_corrective_action_plan


class BlindReadinessTests(unittest.TestCase):
    def _inputs(self, root: Path):
        geometry = root / "model.vsp3"
        certificate = root / "certificate.json"
        policy = root / "hybrid.json"
        geometry.write_bytes(b"master")
        policy.write_text("{}", encoding="utf-8")
        plan = build_corrective_action_plan(findings=[], replacement_contract={
            "requirements": [{
                "component": "Fuselage", "method": "machline_pressure_wave_all_points",
                "backend_capability_available": True, "reason": "covered",
            }]
        }, verdict="PASS_WITH_DECLARED_EXCLUSIONS")
        certificate.write_text(json.dumps({
            "certificate_id": "RMC-1", "certificate_fingerprint": "a" * 64,
            "corrective_action_plan": plan,
            "backends": {"hybrid": {"replacement_contract": {
                "requirements": [{
                    "component": "Fuselage", "method": "machline_pressure_wave_all_points",
                    "backend_capability_available": True,
                }]
            }}},
        }), encoding="utf-8")
        scenario = {"id": "fixed_go_full_adh"}
        method = {
            "method_version": "frozen", "pointwise_tuning": False,
            "geometry_adjustment_after_seal": False,
            "replacement_methods": [{"component": "Fuselage", "method": "machline_pressure_wave_all_points"}],
        }
        return geometry, certificate, policy, scenario, method

    def test_certified_declared_method_is_ready(self):
        with TemporaryDirectory() as tmp:
            geometry, certificate, policy, scenario, method = self._inputs(Path(tmp))
            with patch("blind_readiness.verify_certificate", return_value={"valid": True, "errors": []}):
                result = assess_blind_readiness(
                    geometry_path=geometry, certificate_path=certificate,
                    selected_scenario=scenario, method_declaration=method,
                    additional_inputs=[("hybrid_method_policy", policy)],
                )
        self.assertTrue(result["valid"])
        self.assertEqual([], verify_blind_readiness(result))

    def test_undeclared_replacement_blocks(self):
        with TemporaryDirectory() as tmp:
            geometry, certificate, policy, scenario, method = self._inputs(Path(tmp))
            method["replacement_methods"] = []
            with patch("blind_readiness.verify_certificate", return_value={"valid": True, "errors": []}):
                result = assess_blind_readiness(
                    geometry_path=geometry, certificate_path=certificate,
                    selected_scenario=scenario, method_declaration=method,
                    additional_inputs=[("hybrid_method_policy", policy)],
                )
        self.assertFalse(result["valid"])
        self.assertTrue(any("замены" in error for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
