from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from vspaero_runner import (
    generate_vspaero_sweep_script,
    parse_set_report,
    standard_vspaero_cases,
    user_set_to_api_index,
)


class VSPAeroRunnerTests(unittest.TestCase):
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
        self.assertEqual(1.1, combined[1]["mach_start"])
        self.assertEqual(2.2, combined[1]["mach_end"])
        self.assertEqual(12, combined[1]["mach_points"])

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
        self.assertIn('SetParmVal( tail_id, "Y_Rel_Rotation", "XForm", -2', text)
        self.assertIn("REPAIRMACH_TAIL_INCIDENCE_APPLIED=-2", text)

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
