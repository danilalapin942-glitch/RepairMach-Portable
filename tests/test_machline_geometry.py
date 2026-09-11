from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from machline_geometry import (
    nascart_freestream,
    read_vspgeom_thin,
    remove_redundant_duplicate_skins,
    replace_downstream_axial_caps,
    scan_scope,
)
from tri_mesh import TriMesh, diagnose


class MachLineGeometryTests(unittest.TestCase):
    def test_vspgeom_thin_parser_transforms_axes_and_splits_shared_parts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            geometry = root / "thin.vspgeom"
            key = root / "thin.vkey"
            geometry.write_text(
                "\n".join([
                    "# VSPGEOM v3",
                    "test",
                    "4 2 0",
                    "0 0 0",
                    "1 2 3",
                    "2 0 0",
                    "2 2 3",
                    "2",
                    "3 1 2 3",
                    "3 2 4 3",
                    "1 1",
                    "2 1",
                ]) + "\n",
                encoding="utf-8",
            )
            key.write_text(
                "1,a,b,Wing,c,0\n2,a,b,VO,c,0\n",
                encoding="utf-8",
            )
            mesh, names = read_vspgeom_thin(geometry, key, component_offset=7)
        self.assertEqual({8: "Wing", 9: "VO"}, names)
        self.assertEqual([8, 9], mesh.components)
        self.assertEqual(6, len(mesh.vertices))
        self.assertIn((1.0, 3.0, -2.0), mesh.vertices)

    def test_nascart_coordinate_transform_preserves_openvsp_alpha_and_beta(self):
        alpha = nascart_freestream(30.0, 0.0)
        self.assertAlmostEqual(0.8660254038, alpha[0])
        self.assertAlmostEqual(0.5, alpha[1])
        self.assertAlmostEqual(0.0, alpha[2])
        beta = nascart_freestream(0.0, 30.0)
        self.assertAlmostEqual(0.8660254038, beta[0])
        self.assertAlmostEqual(0.0, beta[1])
        self.assertAlmostEqual(-0.5, beta[2])

    def test_exact_duplicate_membrane_is_removed_only_when_topology_improves(self):
        mesh = TriMesh(
            vertices=[
                (0.0, 0.0, 0.0),
                (1.0, 0.0, 0.0),
                (0.0, 1.0, 0.0),
                (0.2, 0.2, 1.0),
                (0.2, 0.2, -1.0),
            ],
            faces=[
                (0, 1, 3), (1, 2, 3), (2, 0, 3),
                (1, 0, 4), (2, 1, 4), (0, 2, 4),
                (0, 1, 2), (0, 1, 2),
            ],
            components=[1] * 8,
        )
        self.assertGreater(diagnose(mesh)["nonmanifold_edges"], 0)
        repaired, log = remove_redundant_duplicate_skins(mesh)
        self.assertTrue(log["accepted"])
        self.assertEqual(2, log["removed_faces"])
        self.assertEqual(0, diagnose(repaired)["nonmanifold_edges"])
        self.assertEqual(0, diagnose(repaired)["boundary_edges"])

    def test_downstream_disk_gets_tagged_pointed_closure_with_force_mask(self):
        vertices = [
            (0.0, -1.0, -1.0), (0.0, 1.0, -1.0),
            (0.0, 1.0, 1.0), (0.0, -1.0, 1.0),
            (1.0, -1.0, -1.0), (1.0, 1.0, -1.0),
            (1.0, 1.0, 1.0), (1.0, -1.0, 1.0),
        ]
        faces = [
            (0, 2, 1), (0, 3, 2),
            (4, 5, 6), (4, 6, 7),
            (0, 1, 5), (0, 5, 4),
            (1, 2, 6), (1, 6, 5),
            (2, 3, 7), (2, 7, 6),
            (3, 0, 4), (3, 4, 7),
        ]
        mesh = TriMesh(vertices, faces, [1] * len(faces))
        scope = {
            "mach_intervals": [[1.2, 2.2]],
            "alpha_deg": [0.0, 5.0],
            "beta_deg": 0.0,
        }
        policy = {
            "enabled": True,
            "component_name_prefixes": ["Fuselage"],
            "plane_tolerance_cref": 1.0e-7,
            "axial_normal_min": 0.995,
            "safety_angle_deg": 2.0,
            "max_extension_over_cref": 0.5,
            "max_removed_area_over_sref": 0.05,
            "min_boundary_edges": 4,
        }
        prepared, log = replace_downstream_axial_caps(
            mesh,
            scope=scope,
            component_names={1: "Fuselage_S_Surf0"},
            policy=policy,
            reference={"area": 100.0, "cref": 10.0},
        )
        self.assertTrue(log["accepted"])
        self.assertEqual([2], log["surrogate_component_ids"])
        self.assertEqual([2], log["force_integration_contract"]["exclude_component_ids"])
        final = diagnose(prepared)
        self.assertTrue(final["machline_safe_topology"])
        self.assertTrue(final["watertight"])
        scans = scan_scope(prepared, scope, {1: "Fuselage", 2: "closure"})
        for scan in scans:
            self.assertFalse(any(
                item["component_id"] == 2 for item in scan["bad_by_component"]
            ))


if __name__ == "__main__":
    unittest.main()
