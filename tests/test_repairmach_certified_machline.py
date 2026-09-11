import json
import math
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from calculation_scenarios import geometry_sha256
from repairmach_beta import certified_machline_force_contract, _nascart_conditions
from tri_mesh import TriMesh, write_tri


class RepairMachCertifiedMachLineTests(unittest.TestCase):
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
