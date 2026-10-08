import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import ExitStack


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from calculation_scenarios import geometry_sha256
from repairmach_beta import certified_machline_force_contract, _nascart_conditions
import repairmach_beta as app
from tri_mesh import TriMesh, write_tri
from test_pressure_export import vtk_fixture


class RepairMachCertifiedMachLineTests(unittest.TestCase):
    def test_failed_solver_keeps_fresh_diagnostic_pressure_but_not_stale_fields(self):
        for refresh in (True, False):
            with self.subTest(refresh=refresh), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                exe, body = root / "machline.exe", root / "body.vtk"
                exe.write_text("fixture")
                body.write_text(vtk_fixture())
                input_path = root / "input.json"
                input_path.write_text(json.dumps({"output": {"body_file": str(body)}}))
                with ExitStack() as stack:
                    for name, value in (("load_settings", {}), ("find_machline", exe),
                                        ("default_project_path", root), ("machline_working_dir", root)):
                        stack.enter_context(patch.object(app, name, return_value=value))
                    stack.enter_context(patch.object(app, "ROOT", root))
                    stack.enter_context(patch("builtins.input", return_value=str(input_path)))
                    stack.enter_context(patch("builtins.print"))
                    def failed_solver(*args, **kwargs):
                        if refresh:
                            body.write_text(vtk_fixture() + "\n")
                        return SimpleNamespace(returncode=2, stdout="failed after output")
                    stack.enter_context(patch.object(app.subprocess, "run", side_effect=failed_solver))
                    app.run_machline()
                manifests = list((root / "05_machline_results/paraview").rglob("point.json"))
                self.assertEqual(1, len(manifests))
                record = json.loads(manifests[0].read_text())
                self.assertFalse(record["solver_accepted"])
                self.assertEqual("exported" if refresh else "failed", record["status"])

    def test_pressure_menu_only_opens_selected_existing_launcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            launcher = root / "run/Open_ParaView.cmd"
            launcher.parent.mkdir()
            launcher.write_text("fixture")
            (launcher.parent / "open.json").write_text("{}")
            with patch.object(app, "default_project_path", return_value=root), \
                 patch("builtins.input", return_value="1"), patch("builtins.print"), \
                 patch.object(app.os, "startfile", create=True) as opened:
                app.open_pressure_viewer_workflow()
                opened.assert_called_once_with(str(launcher))
            with patch.object(app, "default_project_path", return_value=root), \
                 patch("builtins.input", return_value=""), patch("builtins.print"), \
                 patch.object(app.os, "startfile", create=True) as opened:
                app.open_pressure_viewer_workflow()
                opened.assert_not_called()

    def test_completed_run_packages_native_pressure_under_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exe, tri, report_path, body = [root / name for name in ("machline.exe", "mesh.tri", "report.json", "body.vtk")]
            for path in (exe, tri, report_path):
                path.write_text("fixture", encoding="utf-8")
            body.write_text(vtk_fixture(), encoding="utf-8")
            input_path = root / "input.json"
            input_path.write_text(json.dumps({"output": {"report_file": str(report_path), "body_file": str(body)}}))
            report = {"solver_results": {"solver_status_code": 0, "residual": {"norm": 1e-8, "max": 1e-8}},
                      "input": {"geometry": {"file": str(tri)}}}
            with ExitStack() as stack:
                for name, value in (("load_settings", {}), ("find_machline", exe), ("default_project_path", root),
                                    ("machline_working_dir", root), ("load_machline_report", report),
                                    ("certified_machline_force_contract", None),
                                    ("machline_wind_axes", {"mach":1.2, "alpha_deg":0, "beta_deg":0})):
                    stack.enter_context(patch.object(app, name, return_value=value))
                stack.enter_context(patch.object(app, "ROOT", root))
                stack.enter_context(patch("builtins.input", return_value=str(input_path)))
                stack.enter_context(patch("builtins.print"))
                def completed_solver(*args, **kwargs):
                    body.write_text(body.read_text() + "\n")
                    return SimpleNamespace(returncode=0, stdout="OK")
                stack.enter_context(patch.object(app.subprocess, "run", side_effect=completed_solver))
                app.run_machline()
            manifest = json.loads((root / "report_manifest.json").read_text())
            self.assertEqual("exported", manifest["pressure_export"]["status"])
            self.assertEqual(body.read_bytes(), (root / "05_machline_results/paraview/report/body.vtk").read_bytes())

    def _fixture(self, root: Path) -> Path:
        tri = root / "certified_mesh.tri"
        write_tri(
            tri,
            TriMesh(
                [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)],
                [(0, 1, 2), (0, 3, 1)],
                [1, 2],
            ),
        )
        (root / "force_integration_mask.json").write_text(
            json.dumps({
                "schema": "repairmach.machline-force-mask/1.0",
                "certified_tri_sha256": geometry_sha256(tri),
                "include_component_ids": [1],
                "exclude_component_ids": [2],
                "output_kind": "masked_thick_body_pressure_wave",
                "base_drag_replacement_required": True,
            }),
            encoding="utf-8",
        )
        return tri

    def test_certified_contract_is_accepted_only_with_matching_mesh(self):
        with tempfile.TemporaryDirectory() as tmp:
            tri = self._fixture(Path(tmp))
            contract = certified_machline_force_contract(tri)
        self.assertIsNotNone(contract)
        self.assertEqual([1], contract[0]["include_component_ids"])

    def test_stale_mask_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tri = self._fixture(root)
            tri.write_text(tri.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "не соответствует"):
                certified_machline_force_contract(tri)

    def test_nascart_conditions_restore_openvsp_alpha_and_beta(self):
        alpha = math.radians(5.0)
        beta = math.radians(3.0)
        report = {
            "input": {"flow": {
                "freestream_mach_number": 1.6,
                "freestream_velocity": [
                    100.0 * math.cos(alpha) * math.cos(beta),
                    100.0 * math.sin(alpha) * math.cos(beta),
                    -100.0 * math.sin(beta),
                ],
            }},
        }
        conditions = _nascart_conditions(report)
        self.assertAlmostEqual(1.6, conditions["mach"])
        self.assertAlmostEqual(5.0, conditions["alpha_deg"])
        self.assertAlmostEqual(3.0, conditions["beta_deg"])


if __name__ == "__main__":
    unittest.main()
