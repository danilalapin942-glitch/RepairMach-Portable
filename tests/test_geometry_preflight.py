from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from geometry_preflight import run_geometry_preflight, verify_geometry_preflight


def inventory(*, include_unknown=False):
    components = [
        {
            "id": "wing", "name": "Wing", "type": "Wing",
            "sets": {"1": False, "2": True}, "parameters": [],
            "bbox": {"min": [0, 0, 0], "max": [2, 5, 0.2]},
            "main_surfaces": 1, "total_surfaces": 1,
        },
        {
            "id": "body", "name": "Fuselage", "type": "Fuselage",
            "sets": {"1": True, "2": False}, "parameters": [],
            "bbox": {"min": [0, -1, -1], "max": [10, 1, 1]},
            "main_surfaces": 1, "total_surfaces": 1,
        },
    ]
    if include_unknown:
        components.append({
            "id": "mystery", "name": "Mystery", "type": "Pod",
            "sets": {"1": False, "2": False}, "parameters": [],
            "bbox": {"min": [0, 0, 0], "max": [1, 1, 1]},
            "main_surfaces": 1, "total_surfaces": 1,
        })
    return {
        "schema": "repairmach.openvsp-inventory/1.0",
        "openvsp_version": "3.51.0",
        "components": components,
        "sets": [], "errors": [], "valid": True,
    }


class GeometryPreflightTests(unittest.TestCase):
    def _run(self, root: Path, *, include_unknown=False):
        master = root / "model.vsp3"
        executable = root / "vspscript.exe"
        master.write_bytes(b"master")
        executable.write_bytes(b"solver")

        def fake_inventory(model_path, *, vspscript_executable, work_dir, timeout_seconds):
            work_dir.mkdir(parents=True, exist_ok=True)
            script = work_dir / "inventory.vspscript"
            log = work_dir / "inventory.log"
            script.write_text("inventory", encoding="utf-8")
            log.write_text("ok", encoding="utf-8")
            data = inventory(include_unknown=include_unknown)
            data["source_path"] = str(model_path)
            return data, script, log

        with patch("geometry_preflight.inventory_model", side_effect=fake_inventory):
            return run_geometry_preflight(
                master_path=master,
                output_root=root / "output",
                project_name="Test",
                reference={"area": 100, "cref": 10, "bref": 20, "center": [5, 0, 0]},
                policy_path=ROOT / "config" / "geometry_certification.json",
                vspscript_executable=executable,
            )

    def test_clean_model_is_released_without_solver_runs(self):
        with TemporaryDirectory() as tmp:
            result = self._run(Path(tmp))
            verification = verify_geometry_preflight(result["preflight_path"])
        self.assertEqual("READY_FOR_FULL_CERTIFICATION", result["preflight"]["status"])
        self.assertTrue(verification["valid"])
        self.assertEqual(["VSPAERO", "MachLine", "OpenVSP Parasite Drag"], result["preflight"]["summary"]["solver_runs_avoided"])

    def test_unknown_component_blocks_before_expensive_solver(self):
        with TemporaryDirectory() as tmp:
            result = self._run(Path(tmp), include_unknown=True)
        self.assertEqual("OPERATOR_ACTION_REQUIRED", result["preflight"]["status"])
        self.assertGreater(result["preflight"]["summary"]["blocker_count"], 0)

    def test_tampered_preflight_fails_verification(self):
        with TemporaryDirectory() as tmp:
            result = self._run(Path(tmp))
            payload = json.loads(result["preflight_path"].read_text(encoding="utf-8"))
            payload["status"] = "OPERATOR_ACTION_REQUIRED"
            result["preflight_path"].write_text(json.dumps(payload), encoding="utf-8")
            verification = verify_geometry_preflight(result["preflight_path"])
        self.assertFalse(verification["valid"])


if __name__ == "__main__":
    unittest.main()
