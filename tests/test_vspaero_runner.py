from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from vspaero_runner import (
    generate_vspaero_sweep_script,
    parse_set_report,
    standard_vspaero_cases,
    user_set_to_api_index,
)
import openvsp_runner


class VSPAeroRunnerTests(unittest.TestCase):
    def test_windows_process_enumeration_uses_accessible_powershell_query(self):
        with patch.object(openvsp_runner.os, "name", "nt"), patch.object(
            openvsp_runner.subprocess,
            "run",
            return_value=SimpleNamespace(stdout="104\n208\ninvalid\n"),
        ) as mocked_run:
            self.assertEqual(
                {104, 208},
                openvsp_runner._windows_process_ids("vspaero.exe"),
            )
        command = mocked_run.call_args.args[0]
        self.assertEqual("powershell.exe", command[0])
        self.assertIn("Get-Process -Name 'vspaero'", command[-1])
        self.assertNotIn("tasklist", " ".join(command).lower())

    def test_openvsp_user_set_numbering(self):
        self.assertEqual(4, user_set_to_api_index(1))
        self.assertEqual(5, user_set_to_api_index(2))

    def test_standard_case_grid(self):
        subsonic = standard_vspaero_cases("1")
        self.assertEqual(1, len(subsonic))
        self.assertEqual((0.0, 0.8, 9), (
            subsonic[0]["mach_start"],
            subsonic[0]["mach_end"],
            subsonic[0]["mach_points"],
        ))
        self.assertEqual((0.0, 5.0, 6), (
            subsonic[0]["alpha_start"],
            subsonic[0]["alpha_end"],
            subsonic[0]["alpha_points"],
        ))
        combined = standard_vspaero_cases("3")
        self.assertEqual(2, len(combined))
        self.assertEqual(1.2, combined[1]["mach_start"])
        self.assertEqual(2.2, combined[1]["mach_end"])
        self.assertEqual(11, combined[1]["mach_points"])

    def test_generated_script_uses_mixed_set_roles(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / "aircraft.vsp3"
            model.write_text("placeholder", encoding="utf-8")
            script = root / "run.vspscript"
            generate_vspaero_sweep_script(
                script,
                model,
                root / "results.csv",
                mach_start=0.8,
                mach_end=0.8,
                mach_points=1,
                alpha_start=0.0,
                alpha_end=4.0,
                alpha_points=3,
                beta_deg=0.0,
                reference_area=100.0,
                reference_chord=5.0,
                reference_span=20.0,
                center=[1.0, 0.0, 0.0],
            )
            text = script.read_text(encoding="utf-8")
        self.assertIn("int thick_set = 4;", text)
        self.assertIn("int thin_set = 5;", text)
        self.assertIn('SetIntAnalysisInput( compute_name, "GeomSet", thick_input )', text)
        self.assertIn('SetIntAnalysisInput( compute_name, "ThinGeomSet", thin_input )', text)
        self.assertIn(
            'SetDoubleAnalysisInput( sweep_name, "ForwardGMRESConvergenceFactor", gmres_factor )',
            text,
        )
        self.assertIn('SetIntAnalysisInput( sweep_name, "WakeNumIter", wake_iterations )', text)
        self.assertIn('SetIntAnalysisInput( sweep_name, "NumWakeNodes", wake_nodes )', text)
        self.assertIn('SetDoubleAnalysisInput( sweep_name, "WakeRelax", wake_relaxation )', text)
        self.assertIn("REPAIRMACH_ABORTED_SET_VALIDATION", text)

    def test_generated_script_can_apply_fixed_tail_incidence(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / "aircraft.vsp3"
            model.write_text("placeholder", encoding="utf-8")
            script = root / "tail.vspscript"
            generate_vspaero_sweep_script(
                script,
                model,
                root / "results.csv",
                mach_start=0.8,
                mach_end=0.8,
                mach_points=1,
                alpha_start=0.0,
                alpha_end=5.0,
                alpha_points=6,
                beta_deg=0.0,
                reference_area=100.0,
                reference_chord=5.0,
                reference_span=20.0,
                center=[1.0, 0.0, 0.0],
                tail_geometry_name="GO",
                tail_incidence_deg=-2.0,
            )
            text = script.read_text(encoding="utf-8")
        self.assertIn('FindGeomsWithName( "GO" )', text)
        self.assertIn('FindParm( tail_id, "Y_Rel_Rotation", "XForm" )', text)
        self.assertIn('SetParmVal( tail_rotation_parm, -2', text)
        self.assertIn("GetParmVal( tail_rotation_parm )", text)
        self.assertIn("REPAIRMACH_TAIL_SETUP;MODE=fixed_incidence", text)
        self.assertIn("REPAIRMACH_TAIL_INCIDENCE_APPLIED=", text)
        self.assertLess(text.index("bool invalid_setup = false;"), text.index("invalid_setup = true;"))
        self.assertLess(
            text.index("REPAIRMACH_ABORTED_SETUP_VALIDATION=1"),
            text.index("REPAIRMACH_SET_VALIDATION=OK"),
        )

    def test_generated_script_applies_to_face_engine_boundary(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / "aircraft.vsp3"
            model.write_text("placeholder", encoding="utf-8")
            script = root / "to_face.vspscript"
            generate_vspaero_sweep_script(
                script,
                model,
                root / "results.csv",
                mach_start=1.2,
                mach_end=1.2,
                mach_points=1,
                alpha_start=0.0,
                alpha_end=1.0,
                alpha_points=2,
                beta_deg=0.0,
                reference_area=100.0,
                reference_chord=5.0,
                reference_span=20.0,
                center=[1.0, 0.0, 0.0],
                engine_boundary="to_face",
                engine_geometry_aliases=["LeftEngine", "RightEngine"],
            )
            text = script.read_text(encoding="utf-8")
        self.assertIn("array<string> all_engine_geoms = FindGeoms();", text)
        self.assertIn('gondola_name == "Gondola"', text)
        self.assertIn('gondola_name.substr( 0, 8 ) == "Gondola_"', text)
        self.assertIn('gondola_name == "LeftEngine"', text)
        self.assertIn('gondola_name == "RightEngine"', text)
        self.assertIn('FindParm( gondola_id, "GeomInType", "EngineModel" )', text)
        self.assertIn("SetParmVal( geom_in_parm, 3 )", text)
        self.assertIn("GetParmVal( geom_in_parm )", text)
        self.assertIn("GEOM_IN_TYPE=", text)
        self.assertIn("REPAIRMACH_ENGINE_COMPONENT;MODE=to_face", text)
        self.assertIn("engine_applied_count == engine_expected_count", text)
        self.assertIn("REPAIRMACH_ENGINE_BOUNDARY_APPLIED=to_face", text)
        self.assertLess(
            text.rindex('Print( "OPENVSP_ERROR="'),
            text.index('Print( "REPAIRMACH_VSPAERO_COMPLETE=1" )'),
        )

    def test_generated_script_can_prepare_thin_only_diagnostic(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / "aircraft.vsp3"
            model.write_text("placeholder", encoding="utf-8")
            script = root / "thin_only.vspscript"
            generate_vspaero_sweep_script(
                script,
                model,
                root / "results.csv",
                mach_start=1.2,
                mach_end=1.2,
                mach_points=1,
                alpha_start=0.0,
                alpha_end=1.0,
                alpha_points=2,
                beta_deg=0.0,
                reference_area=100.0,
                reference_chord=5.0,
                reference_span=20.0,
                center=[1.0, 0.0, 0.0],
                fuselage_user_set=0,
                wing_user_set=2,
                require_nonempty_fuselage_set=False,
                require_all_geometries_assigned=False,
            )
            text = script.read_text(encoding="utf-8")
        self.assertIn("int thick_set = 3;", text)
        self.assertIn("DIAGNOSTIC=Empty thick-geometry set accepted", text)
        self.assertNotIn("Geometry is not assigned to Set_0 or Set_2", text)

    def test_scenario_script_requires_canonical_primary_names(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / "aircraft.vsp3"
            model.write_text("placeholder", encoding="utf-8")
            script = root / "canonical.vspscript"
            generate_vspaero_sweep_script(
                script,
                model,
                root / "results.csv",
                mach_start=0.8,
                mach_end=0.8,
                mach_points=1,
                alpha_start=0.0,
                alpha_end=0.0,
                alpha_points=1,
                beta_deg=0.0,
                reference_area=100.0,
                reference_chord=5.0,
                reference_span=20.0,
                center=[1.0, 0.0, 0.0],
                require_canonical_components=True,
            )
            text = script.read_text(encoding="utf-8")
        self.assertIn('FindGeomsWithName( "Fuselage" )', text)
        self.assertIn('FindGeomsWithName( "Wing" )', text)
        self.assertIn("Expected exactly one primary geometry named Wing", text)

    def test_set_report_parser(self):
        log = """
REPAIRMACH_SET_REPORT_BEGIN
ROLE=fuselage_and_nacelles;USER_SET=1;API_INDEX=4;NAME=Set_1;COUNT=2
ROLE=wing_and_empennage;USER_SET=2;API_INDEX=5;NAME=Set_2;COUNT=3
GEOM;ID=A;NAME=Fuselage;TYPE=Fuselage;IN_SET_1=true;IN_SET_2=false
GEOM;ID=B;NAME=Wing;TYPE=Wing;IN_SET_1=false;IN_SET_2=true
TAIL;MODE=fixed_incidence;NAME=GO;ID=B;TYPE=Wing;ANGLE_DEG=-2
REPAIRMACH_SET_REPORT_END
REPAIRMACH_ENGINE_SETUP;MODE=model;VALID=true
REPAIRMACH_TAIL_SETUP;MODE=fixed_incidence;NAME=GO;ID=B;PARM_ID=P;REQUESTED_ANGLE_DEG=-2;ACTUAL_ANGLE_DEG=-2;VALID=true
REPAIRMACH_SETUP_VALIDATION=OK
REPAIRMACH_SET_VALIDATION=OK
REPAIRMACH_VSPAERO_COMPLETE=1
"""
        report = parse_set_report(log)
        self.assertTrue(report["valid"])
        self.assertTrue(report["calculation_complete"])
        self.assertEqual(2, report["roles"]["fuselage_and_nacelles"]["COUNT"])
        self.assertTrue(report["geometries"][0]["IN_SET_1"])
        self.assertEqual("GO", report["tail"]["NAME"])
        self.assertEqual(-2.0, report["tail"]["ANGLE_DEG"])
        self.assertEqual(-2.0, report["tail_setup"]["ACTUAL_ANGLE_DEG"])
        self.assertEqual("model", report["engine_setup"]["MODE"])

    def test_set_report_rejects_claimed_tail_and_engine_values_without_actual_match(self):
        log = """
REPAIRMACH_SET_REPORT_BEGIN
ROLE=fuselage_and_nacelles;USER_SET=1;API_INDEX=4;NAME=Set_1;COUNT=1
ROLE=wing_and_empennage;USER_SET=2;API_INDEX=5;NAME=Set_2;COUNT=1
REPAIRMACH_SET_REPORT_END
REPAIRMACH_TAIL_SETUP;MODE=fixed_incidence;REQUESTED_ANGLE_DEG=-2;ACTUAL_ANGLE_DEG=0;VALID=true
REPAIRMACH_ENGINE_COMPONENT;MODE=to_face;NAME=Gondola_left;ID=G1;GEOM_IN_TYPE=3;GEOM_OUT_TYPE=3;INLET_MODE_TYPE=4;OUTLET_MODE_TYPE=0;VALID=true
REPAIRMACH_ENGINE_SETUP;MODE=to_face;EXPECTED_COUNT=1;APPLIED_COUNT=1;VALID=true
REPAIRMACH_SETUP_VALIDATION=OK
REPAIRMACH_SET_VALIDATION=OK
REPAIRMACH_VSPAERO_COMPLETE=1
"""
        report = parse_set_report(log)
        self.assertFalse(report["valid"])
        self.assertFalse(report["calculation_complete"])
        self.assertTrue(any("угол ГО" in item for item in report["errors"]))
        self.assertTrue(any("OUTLET_MODE_TYPE" in item for item in report["errors"]))

    def test_set_report_accepts_complete_multi_gondola_to_face_readback(self):
        log = """
REPAIRMACH_SET_REPORT_BEGIN
ROLE=fuselage_and_nacelles;USER_SET=1;API_INDEX=4;NAME=Set_1;COUNT=3
ROLE=wing_and_empennage;USER_SET=2;API_INDEX=5;NAME=Set_2;COUNT=1
REPAIRMACH_SET_REPORT_END
REPAIRMACH_TAIL_SETUP;MODE=model_default;VALID=true
REPAIRMACH_ENGINE_COMPONENT;MODE=to_face;NAME=Gondola_left;ID=G1;GEOM_IN_TYPE=3;GEOM_OUT_TYPE=3;INLET_MODE_TYPE=4;OUTLET_MODE_TYPE=4;VALID=true
REPAIRMACH_ENGINE_COMPONENT;MODE=to_face;NAME=Gondola_right;ID=G2;GEOM_IN_TYPE=3;GEOM_OUT_TYPE=3;INLET_MODE_TYPE=4;OUTLET_MODE_TYPE=4;VALID=true
REPAIRMACH_ENGINE_SETUP;MODE=to_face;EXPECTED_COUNT=2;APPLIED_COUNT=2;VALID=true
REPAIRMACH_SETUP_VALIDATION=OK
REPAIRMACH_SET_VALIDATION=OK
REPAIRMACH_VSPAERO_COMPLETE=1
"""
        report = parse_set_report(log)
        self.assertTrue(report["valid"])
        self.assertTrue(report["calculation_complete"])
        self.assertEqual(2, report["engine_setup"]["EXPECTED_COUNT"])
        self.assertEqual(["G1", "G2"], [item["ID"] for item in report["engine_components"]])

    def test_set_report_rejects_partial_multi_gondola_confirmation(self):
        log = """
REPAIRMACH_SET_REPORT_BEGIN
REPAIRMACH_SET_REPORT_END
REPAIRMACH_TAIL_SETUP;MODE=model_default;VALID=true
REPAIRMACH_ENGINE_COMPONENT;MODE=to_face;NAME=Gondola_left;ID=G1;GEOM_IN_TYPE=3;GEOM_OUT_TYPE=3;INLET_MODE_TYPE=4;OUTLET_MODE_TYPE=4;VALID=true
REPAIRMACH_ENGINE_SETUP;MODE=to_face;EXPECTED_COUNT=2;APPLIED_COUNT=2;VALID=true
REPAIRMACH_SETUP_VALIDATION=OK
REPAIRMACH_SET_VALIDATION=OK
REPAIRMACH_VSPAERO_COMPLETE=1
"""
        report = parse_set_report(log)
        self.assertFalse(report["valid"])
        self.assertTrue(any("неполный набор" in item for item in report["errors"]))

    def test_unassigned_geometry_invalidates_report(self):
        log = """
REPAIRMACH_SET_REPORT_BEGIN
ERROR=Geometry is not assigned to Set_1 or Set_2;ID=C;NAME=Nacelle
REPAIRMACH_SET_REPORT_END
REPAIRMACH_ABORTED_SET_VALIDATION=1
"""
        report = parse_set_report(log)
        self.assertFalse(report["valid"])
        self.assertIn("Nacelle", report["errors"][0])


if __name__ == "__main__":
    unittest.main()
