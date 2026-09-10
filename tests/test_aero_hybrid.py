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
    validate_vspaero_run_outputs,
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

    def test_polar_parser_rejects_nonfinite_terminal_point(self):
        polar = """VSPAERO test
 Beta Mach AoA CLtot L2Res
 0.0 1.5 1.0 nan nan
"""
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "invalid.polar"
            path.write_text(polar, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "ни одной числовой"):
                parse_vspaero_polar(path)

    def test_polar_parser_ignores_msvc_nan_in_optional_ratio(self):
        polar = """VSPAERO test
 Beta Mach AoA CLtot CDtot E
 0.0 0.8 0.0 0.0 0.0069 -nan(ind)
"""
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "msvc_nan.polar"
            path.write_text(polar, encoding="utf-8")
            rows = parse_vspaero_polar(path)
        self.assertEqual(1, len(rows))
        self.assertNotIn("E", rows[0])
        self.assertAlmostEqual(0.0069, rows[0]["CDtot"])

    def test_selector_rejects_divergent_residual(self):
        rows = [{"Mach": 1.5, "AoA": 1.0, "CLtot": 0.04, "L2Res": 9.0}]
        with self.assertRaisesRegex(ValueError, "контроль невязки"):
            select_vspaero_point(rows, 1.5, 1.0)

    def test_openvsp_component_result_parser(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "parasite.csv"
            path.write_text(PARASITE, encoding="utf-8")
            result = parse_openvsp_results_csv(path)
        self.assertAlmostEqual(0.00756, result["total_cd"])
        self.assertEqual("Wing", result["components"][0]["label"])
        self.assertAlmostEqual(1.2, result["components"][0]["form_factor"])

    def test_strict_parasite_parser_rejects_empty_result(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.csv"
            path.write_text(
                "FC_Mach,0.8\nFC_Sref,100\nNum_Comp,0\nTotal_CD_Total,0\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "ни одного компонента"):
                parse_openvsp_results_csv(path, strict=True)

    def test_vspaero_output_gate_requires_full_grid_and_finite_residual(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            polar = root / "run.polar"
            log = root / "run.log"
            polar.write_text(POLAR, encoding="utf-8")
            log.write_text(
                "Solving... Mach: 0.800000 ... Alpha: 0.000000 ... Beta: 0\n"
                " 8 0.80000 0.00000 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 -1.2 0.2 1\n"
                "Solving... Mach: 0.800000 ... Alpha: 2.000000 ... Beta: 0\n"
                " 8 0.80000 2.00000 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 -1.1 0.3 1\n",
                encoding="utf-8",
            )
            report = validate_vspaero_run_outputs(
                polar,
                log,
                mach_start=0.8,
                mach_end=0.8,
                mach_points=1,
                alpha_start=0.0,
                alpha_end=2.0,
                alpha_points=2,
                max_log10_l2_residual=-1.0,
            )
        self.assertTrue(report["valid"])
        self.assertEqual(2, report["actual_points"])

    def test_vspaero_output_gate_rejects_missing_convergence_record(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            polar = root / "run.polar"
            log = root / "run.log"
            polar.write_text(POLAR, encoding="utf-8")
            log.write_text("REPAIRMACH_COMPUTE_FINISHED\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "конечной невязки"):
                validate_vspaero_run_outputs(
                    polar,
                    log,
                    mach_start=0.8,
                    mach_end=0.8,
                    mach_points=1,
                    alpha_start=0.0,
                    alpha_end=2.0,
                    alpha_points=2,
                )

    def test_vspaero_output_gate_allows_only_explicit_zero_mach_log_exception(self):
        polar_text = """VSPAERO test
 Beta Mach AoA Re/1e6 CLo CLi CLtot CDo CDi CDtot CStot
 0.0 0.0 0.0 10.0 0.0 0.10 0.10 0.0 0.012 0.012 0.001
"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            polar = root / "run.polar"
            log = root / "run.log"
            polar.write_text(polar_text, encoding="utf-8")
            log.write_text("REPAIRMACH_COMPUTE_FINISHED\n", encoding="utf-8")
            report = validate_vspaero_run_outputs(
                polar,
                log,
                mach_start=0.0,
                mach_end=0.0,
                mach_points=1,
                alpha_start=0.0,
                alpha_end=0.0,
                alpha_points=1,
                allow_zero_mach_without_logged_residual=True,
            )
        self.assertTrue(report["valid"])
        self.assertEqual("three_grid_only", report["points"][0]["convergence_evidence"])
        self.assertIsNone(report["points"][0]["L2Res"])

    def test_vspaero_output_gate_allows_only_normalized_zero_rhs_nan(self):
        polar_text = """VSPAERO test
 Beta Mach AoA Re/1e6 CLo CLi CLtot CDo CDi CDtot CStot
 0.0 1.7 0.0 10.0 0.0 0.0 0.0 0.00715 0.0 0.00715 0.0
"""
        log_text = (
            "Solving... Mach: 1.700000 ... Alpha: 0.000000 ... Beta: 0\n"
            "Wake Iter: 1 / 8 ... GMRES Iter: 0 ... Red: -nan(ind) / -1 "
            "... Max: -nan(ind) / 1 ... KTRes: 0.00000\n"
            "Wake Iter: 1 / 8 ... GMRES Iter: 1 ... Red: nan / -1 "
            "... Max: nan / 1\n"
            "1 1.7 0 0 0 0 0 0.00715 0 0.00715 0 0 0 0 0 0 0 0 0 0 "
            "-14.4 -13.4 0.2\n"
        )
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            polar = root / "run.polar"
            log = root / "run.log"
            polar.write_text(polar_text, encoding="utf-8")
            log.write_text(log_text, encoding="utf-8")
            report = validate_vspaero_run_outputs(
                polar,
                log,
                mach_start=1.7,
                mach_end=1.7,
                mach_points=1,
                alpha_start=0.0,
                alpha_end=0.0,
                alpha_points=1,
                allow_zero_rhs_normalization_nan=True,
            )
        self.assertTrue(report["valid"])
        self.assertTrue(report["benign_zero_rhs_normalization_nan"])
        self.assertEqual(["zero_rhs_normalization_nan"], report["health_warnings"])

    def test_vspaero_output_gate_rejects_zero_rhs_nan_when_lift_is_nonzero(self):
        polar_text = """VSPAERO test
 Beta Mach AoA Re/1e6 CLo CLi CLtot CDo CDi CDtot CStot
 0.0 1.7 0.0 10.0 0.0 0.01 0.01 0.00715 0.0 0.00715 0.0
"""
        log_text = (
            "Solving... Mach: 1.700000 ... Alpha: 0.000000 ... Beta: 0\n"
            "Wake Iter: 1 / 8 ... GMRES Iter: 0 ... Red: nan / -1 "
            "... Max: nan / 1 ... KTRes: 0.00000\n"
            "1 1.7 0 0 0 0 0 0.00715 0 0.00715 0 0 0 0 0 0 0 0 0 0 "
            "-14.4 -13.4 0.2\n"
        )
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            polar = root / "run.polar"
            log = root / "run.log"
            polar.write_text(polar_text, encoding="utf-8")
            log.write_text(log_text, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "нулевой правой части"):
                validate_vspaero_run_outputs(
                    polar,
                    log,
                    mach_start=1.7,
                    mach_end=1.7,
                    mach_points=1,
                    alpha_start=0.0,
                    alpha_end=0.0,
                    alpha_points=1,
                    allow_zero_rhs_normalization_nan=True,
                )

    def test_hybrid_build_up_has_no_calibration_offset(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_path = root / "machline.json"
            polar_path = root / "sample.polar"
            parasite_path = root / "parasite.csv"
            report_path.write_text(
                json.dumps({
                    "mesh_info": {"N_wake_panels": 0},
                    "solver_results": {
                        "solver_status_code": 0,
                        "residual": {"max": 1e-8, "norm": 2e-8},
                    },
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
