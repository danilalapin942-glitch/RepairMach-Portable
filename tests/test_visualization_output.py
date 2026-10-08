import hashlib
import json
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from pressure_export import export_vspaero_pressure, export_machline_pressure
from test_pressure_export import adb_fixture, vtk_fixture


class VisualizationOutputTests(unittest.TestCase):
    def fixture(self, root, rows="0.4 0 0 first\n0.4 5 0 second\n"):
        adb = root / "a model.adb"
        adb.write_bytes(adb_fixture())
        if rows is not None:
            Path(str(adb) + ".cases").write_text(rows)
        adb.with_suffix(".vspaero").write_text("Mach = 0.4\n")
        return adb

    def test_each_case_gets_native_scene_and_no_automatic_gui(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            adb = self.fixture(root)
            with patch("subprocess.run", side_effect=AssertionError("GUI/solver not allowed")):
                result = export_vspaero_pressure(adb, root / "out")
            self.assertEqual("exported", result["viewer_export"]["status"])
            for point in result["points"]:
                folder = (root / "out" / point["file"]).parent
                self.assertTrue((folder / "Open_ParaView.cmd").is_file())
                scene = (folder / "scene.py").read_text()
                compile(scene, str(folder / "scene.py"), "exec")
                self.assertIn("('CELLS', chosen)", scene)
                self.assertNotIn("CellDatatoPointData", scene)
                self.assertIn("DIAGNOSTIC / NOT ACCEPTED", scene)
                config = json.loads((folder / "open.json").read_text())
                for entry in config["files"]:
                    self.assertEqual(entry["sha256"], hashlib.sha256((folder / entry["file"]).read_bytes()).hexdigest())

    def test_viewer_exact_bundle_and_runtime_hint(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            adb = self.fixture(root)
            executable = root / "Release & space" / "vspviewer.exe"
            executable.parent.mkdir()
            executable.write_bytes(b"not executed")
            result = export_vspaero_pressure(adb, root / "out", accepted=True, viewer_executable=executable)
            viewer = root / "out/viewer"
            self.assertEqual(adb.read_bytes(), (viewer / "model.adb").read_bytes())
            self.assertEqual(Path(str(adb) + ".cases").read_bytes(), (viewer / "model.adb.cases").read_bytes())
            self.assertEqual(adb.with_suffix(".vspaero").read_bytes(), (viewer / "model.vspaero").read_bytes())
            config = json.loads((viewer / "open.json").read_text())
            self.assertEqual(str(executable.resolve()), config["executable_hint"])
            self.assertEqual(hashlib.sha256(executable.read_bytes()).hexdigest(), config["executable_sha256"])
            self.assertFalse(result["viewer_export"]["qualification"])
            self.assertNotIn(str(executable), (viewer / "open.ps1").read_text(encoding="utf-8-sig"))

    def test_missing_bad_count_wrong_order_nonfinite_sidecar_only_block_viewer(self):
        for rows in (None, "0.4 0 0\n", "0.4 5 0\n0.4 0 0\n", "NaN 0 0\n0.4 5 0\n", "bad\n0.4 5 0\n"):
            with self.subTest(rows=rows), TemporaryDirectory() as tmp:
                root = Path(tmp)
                result = export_vspaero_pressure(self.fixture(root, rows), root / "out")
                self.assertEqual("exported", result["status"])
                self.assertEqual("unavailable", result["viewer_export"]["status"])
                self.assertTrue(result["viewer_export"]["errors"])
                self.assertFalse((root / "out/viewer/Open_Viewer.cmd").exists())

    def test_incomplete_adb_never_generates_openable_scene(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            adb = self.fixture(root)
            adb.write_bytes(adb.read_bytes()[:-1])
            result = export_vspaero_pressure(adb, root / "out")
            self.assertEqual("failed", result["status"])
            self.assertEqual([], list((root / "out").rglob("*.cmd")))
            self.assertEqual("unavailable", result["viewer_export"]["status"])

    def test_mismatched_requested_point_cannot_open_native_viewer(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = export_vspaero_pressure(self.fixture(root), root / "out",
                expected_conditions=[{"mach": .8, "alpha_deg": 0, "beta_deg": 0}])
            self.assertEqual("failed", result["status"])
            self.assertFalse((root / "out/viewer").exists())

    def test_relocated_package_uses_relative_dataset_paths(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = export_vspaero_pressure(self.fixture(root), root / "out")
            shutil.move(root / "out", root / "moved & space")
            for point in result["points"]:
                folder = (root / "moved & space" / point["file"]).parent
                config = json.loads((folder / "open.json").read_text())
                self.assertTrue(all(not Path(entry["file"]).is_absolute() for entry in config["files"]))
                self.assertNotIn(str(root), (folder / "scene.py").read_text())

    def test_failed_machline_without_body_has_explicit_manifest(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = export_machline_pressure(root / "missing.vtk", root / "out", condition={}, quality={"valid": False})
            self.assertEqual("failed", result["status"])
            self.assertFalse(result["solver_accepted"])
            self.assertTrue((root / "out/point.json").is_file())
            self.assertFalse((root / "out/Open_ParaView.cmd").exists())

    def test_machline_native_fields_and_unsupported_viewer(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            vtk = root / "body.vtk"
            vtk.write_text(vtk_fixture())
            result = export_machline_pressure(vtk, root / "out", condition={"mach": 1.2}, quality={"valid": False})
            self.assertEqual("exported", result["status"])
            self.assertEqual("unsupported", result["viewer_export"]["status"])
            self.assertEqual(vtk.read_bytes(), (root / "out/body.vtk").read_bytes())
            self.assertTrue((root / "out/Open_ParaView.cmd").is_file())

    def test_stale_machline_rejected(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            vtk = root / "body.vtk"
            vtk.write_text(vtk_fixture())
            result = export_machline_pressure(vtk, root / "out", condition={}, quality={}, source_is_fresh=False)
            self.assertEqual("failed", result["status"])
            self.assertFalse((root / "out/body.vtk").exists())

    def test_machline_colliding_extras_fail_without_overwriting(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            vtk = root / "body.vtk"
            vtk.write_text(vtk_fixture())
            other = root / "other/body.vtk"
            other.parent.mkdir()
            other.write_text("different")
            result = export_machline_pressure(vtk, root / "out", condition={}, quality={}, extras=[other])
            self.assertEqual("failed", result["status"])
            self.assertFalse((root / "out/body.vtk").exists())

    def test_incomplete_nonfinite_unknown_machline_fields_fail_closed(self):
        for data in (vtk_fixture().rsplit('\n', 2)[0], vtk_fixture(float('nan')),
                     vtk_fixture().replace('ASCII', 'BINARY'),
                     vtk_fixture().replace('CELL_DATA 1', 'CELL_DATA 2'),
                     vtk_fixture().replace('3 0 1 2', '3 0 1 99')):
            with self.subTest(data=data), TemporaryDirectory() as tmp:
                root = Path(tmp)
                vtk = root / 'body.vtk'
                vtk.write_text(data)
                result = export_machline_pressure(vtk, root / 'out', condition={}, quality={})
                self.assertEqual('failed', result['status'])
                self.assertFalse((root / 'out/Open_ParaView.cmd').exists())


if __name__ == "__main__":
    unittest.main()
