from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from tri_mesh import TriMesh, diagnose, read_tri, repair, write_tri
from mach_repair import repair_mach_criterion, scan_mach_criterion, summarize_scan
from repairmach_beta import freestream_vector


class TriMeshTests(unittest.TestCase):
    def test_freestream_vector_uses_alpha_and_beta(self):
        vector = freestream_vector(30.0, 0.0, 100.0)
        self.assertAlmostEqual(86.6025403784, vector[0])
        self.assertAlmostEqual(0.0, vector[1])
        self.assertAlmostEqual(50.0, vector[2])
        sideslip = freestream_vector(0.0, 30.0, 100.0)
        self.assertAlmostEqual(86.6025403784, sideslip[0])
        self.assertAlmostEqual(50.0, sideslip[1])
        self.assertAlmostEqual(0.0, sideslip[2])

    def test_round_trip_preserves_components(self):
        mesh = TriMesh(
            vertices=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
            faces=[(0, 1, 2)],
            components=[7],
        )
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.tri"
            write_tri(path, mesh)
            loaded = read_tri(path)
        self.assertEqual(mesh, loaded)

    def test_diagnostics_and_conservative_repair(self):
        mesh = TriMesh(
            vertices=[
                (0.0, 0.0, 0.0),
                (1.0, 0.0, 0.0),
                (0.0, 1.0, 0.0),
                (0.0, 0.0, 0.0),  # duplicate of vertex 0
                (4.0, 4.0, 4.0),  # unreferenced
            ],
            faces=[
                (0, 1, 2),
                (3, 1, 2),  # duplicate face after vertex merge
                (0, 0, 1),  # degenerate
                (0, 1, 99),  # invalid index
            ],
            components=[1, 2, 3, 4],
        )
        before = diagnose(mesh)
        self.assertEqual(1, before["duplicate_vertices"])
        self.assertEqual(1, before["repeated_vertex_faces"])
        self.assertEqual(1, before["invalid_index_faces"])

        repaired, changes = repair(mesh)
        after = diagnose(repaired)
        self.assertEqual(1, len(repaired.faces))
        self.assertEqual(3, len(repaired.vertices))
        self.assertEqual(1, changes["removed_duplicate_faces"])
        self.assertEqual(0, after["safe_repairs_available"])
        self.assertTrue(after["machline_safe_topology"])

    def test_orientation_mismatch_is_repaired(self):
        mesh = TriMesh(
            vertices=[
                (0.0, 0.0, 0.0),
                (1.0, 0.0, 0.0),
                (0.0, 1.0, 0.0),
                (1.0, 1.0, 0.0),
            ],
            faces=[(0, 1, 2), (1, 2, 3)],  # shared edge has same direction
            components=[1, 1],
        )
        self.assertEqual(1, diagnose(mesh)["inconsistent_orientation_edges"])
        repaired, _ = repair(mesh)
        self.assertEqual(0, diagnose(repaired)["inconsistent_orientation_edges"])

    def test_mach_criterion_scan_and_repair(self):
        mesh = TriMesh(
            vertices=[(0.0, 0.0, 0.0), (0.0, 0.01, 0.0), (0.0, 0.0, 0.01)],
            faces=[(0, 1, 2)],
            components=[9],
        )
        initial = summarize_scan(scan_mach_criterion(mesh, 1.5))
        self.assertEqual(1, initial["bad_panels"])
        repaired, log, final_scan = repair_mach_criterion(mesh, 1.5, max_repairs=1)
        self.assertEqual(1, sum(item["status"] == "REPAIRED" for item in log))
        self.assertEqual(0, summarize_scan(final_scan)["bad_panels"])
        self.assertEqual([9], repaired.components)


if __name__ == "__main__":
    unittest.main()
