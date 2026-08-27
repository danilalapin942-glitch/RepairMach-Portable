from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from aero_hybrid import (
    build_hybrid_report,
    parse_openvsp_results_csv,
    parse_vspaero_polar,
    select_vspaero_point,
)
from openvsp_runner import generate_parasite_drag_script


POLAR = """VSPAERO test
 Beta Mach AoA Re/1e6 CLo CLi CLtot CDo CDi CDtot CStot
 0.0 0.8 0.0 10.0 0.0 0.10 0.10 0.0 0.012 0.012 0.001
 0.0 0.8 2.0 10.0 0.0 0.20 0.20 0.0 0.020 0.020 0.002
"""


PARASITE = """Results_Name,Parasite_Drag
Comp_CD,7.560000000000000000e-03
Comp_Cf,3.000000000000000000e-03
Comp_FFEqnName,DATCOM
Comp_FFOut,1.200000000000000000e+00
Comp_Label,Wing
Comp_Q,1.050000000000000000e+00
Comp_Swet,2.000000000000000000e+02
FC_Mach,8.000000000000000444e-01
FC_Sref,1.000000000000000000e+02
Num_Comp,1
Total_CD_Total,7.560000000000000000e-03
TurbCfEqnName,Blasius Power Law
"""


class AeroHybridTests(unittest.TestCase):
    def test_polar_parser_and_selector(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.polar"
            path.write_text(POLAR, encoding="utf-8")
            rows = parse_vspaero_polar(path)
        self.assertEqual(2, len(rows))
        self.assertAlmostEqual(0.2, select_vspaero_point(rows, 0.8, 2.0)["CLtot"])

    def test_openvsp_component_result_parser(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "parasite.csv"
            path.write_text(PARASITE, encoding="utf-8")
            result = parse_openvsp_results_csv(path)
        self.assertAlmostEqual(0.00756, result["total_cd"])
        self.assertEqual("Wing", result["components"][0]["label"])
        self.assertAlmostEqual(1.2, result["components"][0]["form_factor"])

    def test_hybrid_build_up_has_no_calibration_offset(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_path = root / "machline.json"
            polar_path = root / "sample.polar"
            parasite_path = root / "parasite.csv"
            report_path.write_text(
                json.dumps({
                    "mesh_info": {"N_wake_panels": 0},
                    "solver_results": {"residual": {"max": 1e-8, "norm": 2e-8}},
                    "total_forces": {"Cx": 0.03, "Cy": 0.0, "Cz": 0.10},
                    "input": {
                        "flow": {
                            "freestream_mach_number": 0.8,
                            "freestream_velocity": [100.0, 0.0, 0.0],
                        }
                    },
                }),
                encoding="utf-8",
            )
            polar_path.write_text(POLAR, encoding="utf-8")
            parasite_path.write_text(PARASITE, encoding="utf-8")
            result = build_hybrid_report(report_path, polar_path, parasite_path)
        self.assertAlmostEqual(0.1, result["coefficients"]["CL"])
        self.assertAlmostEqual(0.03 + 0.012 + 0.00756, result["coefficients"]["CD"])
        self.assertFalse(result["calibration"]["used"])
        self.assertEqual(0.0, result["calibration"]["constant_offset"])

    def test_parasite_script_rejects_supersonic_input(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            vsp3 = root / "model.vsp3"
            vsp3.write_text("placeholder", encoding="utf-8")
            with self.assertRaises(ValueError):
                generate_parasite_drag_script(
                    root / "run.vspscript", vsp3, root / "result.csv", 1.2, 0.0, 100.0
                )


if __name__ == "__main__":
    unittest.main()
