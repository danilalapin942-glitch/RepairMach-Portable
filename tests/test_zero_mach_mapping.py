from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from aero_hybrid import validate_vspaero_run_outputs, _parse_vspaero_log_convergence
from pressure_export import export_vspaero_pressure
from test_pressure_export import adb_fixture


def log_text(requested=0, effective=.001, l2=-1, maximum=-.5, alpha=0):
    values=[80,effective,alpha,0]+[0]*16+[l2,maximum,3]
    return f'Solving... Mach: {requested:.6f} ... Alpha: {alpha:.6f} ... Beta: 0\n'+ ' '.join(map(str,values))+'\n'


class ZeroMachMappingTests(unittest.TestCase):
    def validate(self, root, text, mach=0):
        polar,log=root/'model.polar',root/'probe.log'
        polar.write_text(f'Beta Mach AoA CLtot CDi\n0 {mach} 0 .2 .01\n')
        log.write_text(text)
        return validate_vspaero_run_outputs(polar,log,mach_start=mach,mach_end=mach,mach_points=1,
            alpha_start=0,alpha_end=0,alpha_points=1,max_log10_l2_residual=-.3,
            max_log10_max_residual=0,allow_zero_mach_without_logged_residual=True)

    def test_record_nominal_and_effective_and_enforce_residuals(self):
        with TemporaryDirectory() as tmp:
            quality=self.validate(Path(tmp),log_text())
            point=quality['points'][0]
            self.assertEqual(0,point['Mach'])
            self.assertEqual(.001,point['solver_mach'])
            self.assertEqual('zero_to_0.001',point['mach_mapping'])
            self.assertEqual('logged_residual',point['convergence_evidence'])
            self.assertEqual(-1,point['L2Res'])
            self.assertEqual(-.5,point['MaxRes'])

    def test_real_solver_banner_already_contains_effective_mach(self):
        with TemporaryDirectory() as tmp:
            quality=self.validate(Path(tmp),log_text(requested=.001))
            point=quality['points'][0]
            self.assertEqual(0,point['Mach'])
            self.assertEqual(.001,point['solver_mach'])
            self.assertEqual('logged_residual',point['convergence_evidence'])
            self.assertEqual('zero_to_0.001',point['mach_mapping'])
            with self.assertRaises(ValueError):
                self.validate(Path(tmp),log_text(requested=.001,maximum=.1))

    def test_bad_residual_cannot_hide_behind_legacy_zero_mach_exception(self):
        for l2,maximum in ((.1,-.5),(-1,.1)):
            with self.subTest(l2=l2,maximum=maximum),TemporaryDirectory() as tmp:
                with self.assertRaises(ValueError):
                    self.validate(Path(tmp),log_text(l2=l2,maximum=maximum))

    def test_nonzero_mach_mismatch_remains_rejected(self):
        for actual in (.40005,.401):
            with self.subTest(actual=actual),TemporaryDirectory() as tmp:
                with self.assertRaises(ValueError):
                    self.validate(Path(tmp),log_text(.4,actual),mach=.4)

    def test_no_mapping_for_near_zero_negative_or_wrong_actual(self):
        for nominal,actual in ((-.001,.001),(.00001,.001),(0,.002)):
            self.assertEqual([],_parse_vspaero_log_convergence(log_text(nominal,actual)))

    def test_legacy_missing_log_exception_cannot_hide_wrong_or_broken_native_record(self):
        texts=(log_text(requested=.002,effective=.002),
               log_text(requested=.001,effective=.002),
               'Solving... Mach: 0.001000 ... Alpha: 0.000000 ... Beta: 0\ncorrupt final row\n')
        for text in texts:
            with self.subTest(text=text),TemporaryDirectory() as tmp:
                with self.assertRaises(ValueError):
                    self.validate(Path(tmp),text)

    def test_export_requires_log_link_and_preserves_both_conditions(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            quality=self.validate(root,log_text())
            adb=root/'model.adb'
            adb.write_bytes(adb_fixture(conditions=[(.001,0,0)]))
            requested=[{'mach':0,'alpha_deg':0,'beta_deg':0}]
            missing=export_vspaero_pressure(adb,root/'missing',expected_conditions=requested)
            self.assertEqual('failed',missing['status'])
            result=export_vspaero_pressure(adb,root/'with_evidence',quality=quality,expected_conditions=requested)
            self.assertEqual('exported',result['status'])
            p=result['points'][0]
            self.assertEqual(0,p['requested_condition']['mach'])
            self.assertAlmostEqual(.001,p['condition']['mach'])
            self.assertFalse(p['solver_accepted'])

    def test_mapping_does_not_relax_angle_match(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            quality=self.validate(root,log_text())
            adb=root/'model.adb'
            adb.write_bytes(adb_fixture(conditions=[(.001,.1,0)]))
            result=export_vspaero_pressure(adb,root/'out',quality=quality,
                expected_conditions=[{'mach':0,'alpha_deg':0,'beta_deg':0}])
            self.assertEqual('failed',result['status'])

    def test_duplicate_regularized_and_nominal_evidence_is_ambiguous(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                self.validate(Path(tmp),log_text()+log_text(effective=0))


if __name__=='__main__':
    unittest.main()
