from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from geometry_action_executor import execute_safe_recertification, resolve_hybrid_corrective_actions
from geometry_certificate import seal_certificate
from geometry_manifest import sha256_file, write_json
from geometry_remediation import build_corrective_action_plan


class GeometryActionExecutorTests(unittest.TestCase):
    def _certificate(self, root: Path, finding: dict) -> Path:
        master = root / "model.vsp3"
        master.write_bytes(b"master")
        plan = build_corrective_action_plan(findings=[finding], verdict="FAIL")
        payload = seal_certificate({
            "schema": "repairmach.geometry-certificate/1.1",
            "method_version": "RM91-GEOMETRY-CERT-1",
            "run_status": "failed", "verdict": "FAIL", "project": "Test",
            "master": {"source_path": str(master), "sha256_before": sha256_file(master)},
            "reference": {"area": 100, "cref": 10, "bref": 20, "center": [5, 0, 0]},
            "scope": {"mach_intervals": [[0, 0.8], [1.2, 2.2]], "alpha_deg": [0, 5], "beta_deg": 0, "horizontal_tail": {"mode": "fixed", "geometry_name": "GO", "incidence_deg": 0}},
            "requested_scope": {"mach_intervals": [[0, 0.8], [1.2, 2.2]], "alpha_deg": [0, 5], "beta_deg": 0, "horizontal_tail": {"mode": "fixed", "geometry_name": "GO", "incidence_deg": 0}},
            "corrective_action_plan": plan,
        })
        path = root / "certificate.json"
        write_json(path, payload)
        return path

    def test_operator_action_never_starts_recertification(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            certificate = self._certificate(root, {
                "code": "GEO-NAME-001", "severity": "BLOCKER", "scope": "master",
                "message": "unknown", "component": "Mystery", "evidence": {},
            })
            with patch("geometry_action_executor.certify_geometry") as certify:
                result = execute_safe_recertification(
                    certificate_path=certificate, output_root=root / "out", executables={}
                )
        self.assertEqual("blocked_by_operator", result["status"])
        certify.assert_not_called()

    def test_internal_action_starts_fresh_complete_recertification(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            certificate = self._certificate(root, {
                "code": "GEO-VSP-003", "severity": "BLOCKER", "scope": "vspaero",
                "message": "numerical", "evidence": {},
            })
            (root / "policy_snapshot.json").write_text(
                (ROOT / "config" / "geometry_certification.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            new_cert = {"certificate_id": "RMC-NEW", "certificate_fingerprint": "f" * 64, "verdict": "PASS_NATIVE"}
            with patch("geometry_action_executor.certify_geometry", return_value={
                "certificate": new_cert, "certificate_path": root / "new.json", "report_path": root / "new.md"
            }) as certify:
                result = execute_safe_recertification(
                    certificate_path=certificate, output_root=root / "out", executables={}
                )
        self.assertEqual("completed_pass", result["status"])
        certify.assert_called_once()

    def test_hybrid_actions_close_only_with_accepted_sources_and_workbook(self):
        plan = build_corrective_action_plan(findings=[], replacement_contract={
            "requirements": [{
                "component": "Fuselage", "method": "machline_pressure_wave_all_points",
                "backend_capability_available": True, "reason": "covered",
            }]
        }, verdict="PASS_WITH_DECLARED_EXCLUSIONS")
        certificate = {"certificate_id": "RMC-1", "corrective_action_plan": plan}
        bundle = {
            "status": "complete",
            "rows": [{"Mach": 1.2, "sources": {"machline": "m.json", "parasite_drag": None}}],
            "replacement_coverage": [{"component": "Fuselage", "satisfied_by": "machline_pressure_wave"}],
        }
        pending = resolve_hybrid_corrective_actions(certificate, bundle)
        complete = resolve_hybrid_corrective_actions(certificate, bundle, workbook_verification={
            "build_status": "passed", "external_links_detected": False, "charts_use_internal_cells": True,
        })
        self.assertEqual("pending", pending["status"])
        self.assertEqual("complete", complete["status"])


if __name__ == "__main__":
    unittest.main()
