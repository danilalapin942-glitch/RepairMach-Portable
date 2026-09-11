import sys
import tempfile
import unittest
from pathlib import Path


APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from machline_postprocess import masked_force_coefficients, read_vtk_cell_vectors
from tri_mesh import TriMesh


def write_vtk(path: Path, vectors: list[tuple[float, float, float]]) -> None:
    rows = [
        "# vtk DataFile Version 3.0",
        "test",
        "ASCII",
        "DATASET POLYDATA",
        f"CELL_DATA {len(vectors)}",
        "VECTORS dC_f float",
    ]
    rows.extend(" ".join(str(value) for value in vector) for vector in vectors)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


class MachLinePostprocessTests(unittest.TestCase):
    def setUp(self):
        self.mesh = TriMesh(
            vertices=[(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)],
            faces=[(0, 1, 2), (0, 3, 1)],
            components=[1, 2],
        )

    def test_masked_force_proves_alignment_and_excludes_surrogate(self):
        with tempfile.TemporaryDirectory() as tmp:
            vtk = Path(tmp) / "body.vtk"
            write_vtk(vtk, [(2.0, 0.0, 0.0), (-0.5, 1.0, 0.0)])
            result = masked_force_coefficients(
                mesh=self.mesh,
                body_vtk_path=vtk,
                reference_area=10.0,
                include_component_ids=[1],
                exclude_component_ids=[2],
                report_total_forces={"Cx": 0.15, "Cy": 0.1, "Cz": 0.0},
                freestream_velocity=[100.0, 0.0, 0.0],
            )
        self.assertTrue(result["alignment"]["verified"])
        self.assertAlmostEqual(0.2, result["masked_mesh_axes"]["Cx"])
        self.assertAlmostEqual(0.2, result["masked_wind_axes"]["cd"])
        self.assertEqual(1, result["included_panels"])
        self.assertEqual(1, result["excluded_panels"])

    def test_rejects_vtk_report_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            vtk = Path(tmp) / "body.vtk"
            write_vtk(vtk, [(2.0, 0.0, 0.0), (-0.5, 1.0, 0.0)])
            with self.assertRaisesRegex(ValueError, "Порядок VTK-ячеек"):
                masked_force_coefficients(
                    mesh=self.mesh,
                    body_vtk_path=vtk,
                    reference_area=10.0,
                    include_component_ids=[1],
                    exclude_component_ids=[2],
                    report_total_forces={"Cx": 0.0, "Cy": 0.0, "Cz": 0.0},
                    freestream_velocity=[100.0, 0.0, 0.0],
                )

    def test_rejects_unclassified_component(self):
        with tempfile.TemporaryDirectory() as tmp:
            vtk = Path(tmp) / "body.vtk"
            write_vtk(vtk, [(2.0, 0.0, 0.0), (-0.5, 1.0, 0.0)])
            with self.assertRaisesRegex(ValueError, "не классифицирует"):
                masked_force_coefficients(
                    mesh=self.mesh,
                    body_vtk_path=vtk,
                    reference_area=10.0,
                    include_component_ids=[1],
                    exclude_component_ids=[],
                    report_total_forces={"Cx": 0.15, "Cy": 0.1, "Cz": 0.0},
                    freestream_velocity=[100.0, 0.0, 0.0],
                )

    def test_rejects_wrong_cell_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            vtk = Path(tmp) / "body.vtk"
            write_vtk(vtk, [(1.0, 0.0, 0.0)])
            with self.assertRaisesRegex(ValueError, "CELL_DATA"):
                read_vtk_cell_vectors(vtk, "dC_f", expected_count=2)


if __name__ == "__main__":
    unittest.main()
