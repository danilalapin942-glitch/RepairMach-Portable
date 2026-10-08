import json
import math
from pathlib import Path
import struct
import sys
from tempfile import TemporaryDirectory
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from pressure_export import export_vspaero_pressure, read_adb_v3, export_machline_pressure
from pressure_export import _subset


def vtk_fixture(cp=-0.125):
    return ("# vtk DataFile Version 3.0\nfixture\nASCII\nDATASET POLYDATA\n"
            "POINTS 3 float\n0 0 0\n1 0 0\n0 1 0\nPOLYGONS 1 4\n3 0 1 2\n"
            f"CELL_DATA 1\nSCALARS C_p float 1\nLOOKUP_TABLE default\n{cp}\n")


def adb_fixture(endian="<", conditions=((0.4, 0, 0), (0.4, 5, 0)), cp=-0.125):
    data = bytearray()
    def pack(fmt, *values):
        data.extend(struct.pack(endian + fmt, *values))
    pack("8i", -123789453, 1, 0, 0, 1, 3, 1, 1)
    pack("6f", 10, 1, 5, 0, 0, 0)
    pack("ii100si", 1, 1, b"Wing", 7)
    for mach, alpha, beta in conditions:
        pack("6if", 1, 2, 3, 7, 1, 0, 0.5)
        pack("9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)
        pack("6i", 0, 0, 0, 0, 0, 0)  # rotors/nozzles/levels/Kutta/controls
        pack("5f", mach, math.radians(alpha), math.radians(beta), -1, 1)
        pack("8d", *([0] * 8))  # 40*loops + 24*edges
        pack("3f", cp, 0, 2)
        pack("i", 1)  # one wake line
        pack("idi6d", 1, 0.25, 2, 0, 0, 0, 2, 0, 0)
    return bytes(data)


class PressureExportTests(unittest.TestCase):
    def test_surface_split_remaps_vertices_without_changing_cp(self):
        case = {"points": [(0,0,0),(1,0,0),(0,1,0),(10000,0,0)],
                "triangles": [(1,2,3,1,1,0,.5),(2,4,3,0,0,1,100)],
                "fields": [(-.25,0,1),(0,0,99)]}
        surface = _subset(case, [0], "surface")
        wake = _subset(case, [1], "wake")
        self.assertEqual(case["points"][:3], surface["points"])
        self.assertEqual([(-.25,0,1)], surface["fields"])
        self.assertEqual((1,3,2), wake["triangles"][0][:3])
        self.assertEqual([1], wake["original_cells"])

    def test_multicase_vtu_is_self_contained_and_exact(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            adb = root / "model.adb"
            adb.write_bytes(adb_fixture())
            result = export_vspaero_pressure(adb, root / "paraview", accepted=True,
                expected_conditions=[{"mach": .4, "alpha_deg": a, "beta_deg": 0} for a in (0, 5)])
            self.assertEqual("exported", result["status"])
            self.assertEqual(2, len(result["points"]))
            for row in result["points"]:
                tree = ET.parse(root / "paraview" / row["file"])
                arrays = {a.attrib.get("Name"): a.text for a in tree.iter("DataArray")}
                self.assertEqual("0 1 2", arrays["connectivity"])
                self.assertEqual("-0.125", arrays["Cp_or_DeltaCp"])
                self.assertEqual("1", arrays["SolverAccepted"])
                self.assertEqual("7", arrays["ComponentID"])
                self.assertNotIn("Source=", (root / "paraview" / row["file"]).read_text())

    def test_big_endian_and_angle_conversion(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "model.adb"
            path.write_bytes(adb_fixture(">", [(0.7, -1, 3)]))
            _, cases = read_adb_v3(path)
            self.assertAlmostEqual(-1, cases[0]["condition"]["alpha_deg"], places=6)
            self.assertAlmostEqual(3, cases[0]["condition"]["beta_deg"], places=6)

    def test_rejected_point_stays_diagnostic(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "model.adb"
            path.write_bytes(adb_fixture())
            result = export_vspaero_pressure(path, Path(tmp) / "out", accepted=False)
            self.assertFalse(result["solver_accepted"])
            self.assertTrue(all(p["status"] == "diagnostic_not_accepted" for p in result["points"]))

    def test_mismatch_or_missing_conditions_never_exports_wrong_point(self):
        for expected in ([{"mach": .8, "alpha_deg": 0, "beta_deg": 0}], []):
            with TemporaryDirectory() as tmp:
                path = Path(tmp) / "model.adb"
                path.write_bytes(adb_fixture())
                result = export_vspaero_pressure(path, Path(tmp) / "out", expected_conditions=expected)
                self.assertEqual("failed", result["status"])
                self.assertEqual([], result["points"])

    def test_truncated_unknown_and_nonfinite_fail_explicitly(self):
        for data in (b"bad", adb_fixture()[:-1], b"xxxx" + adb_fixture()[4:], adb_fixture(cp=float("nan"))):
            with TemporaryDirectory() as tmp:
                path = Path(tmp) / "model.adb"
                path.write_bytes(data)
                result = export_vspaero_pressure(path, Path(tmp) / "out")
                self.assertEqual("failed", result["status"])
                self.assertEqual([], result["points"])
                self.assertTrue(result["errors"])
                self.assertEqual("failed", json.loads((Path(tmp) / "out/manifest.json").read_text())["status"])

    def test_existing_output_never_overwritten(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(FileExistsError):
                export_vspaero_pressure(Path(tmp) / "missing.adb", Path(tmp))

    def test_native_machline_copy_preserves_bytes_and_provenance(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "body.vtk"
            source.write_text(vtk_fixture())
            result = export_machline_pressure(source, root / "out", condition={"mach":1.2}, quality={"valid": True})
            self.assertEqual("exported", result["status"])
            self.assertEqual(source.read_bytes(), (root / "out/body.vtk").read_bytes())
            self.assertEqual(64, len(result["files"][0]["sha256"]))


if __name__ == "__main__":
    unittest.main()
