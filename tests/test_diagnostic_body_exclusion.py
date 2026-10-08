from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import vspaero_runner as runner
import geometry_certification as gc


class DiagnosticBodyExclusion(unittest.TestCase):
    def generate(self, root, **extra):
        model = root/'model.vsp3'
        model.write_text('placeholder', encoding='utf-8')
        args = dict(mach_start=.4,mach_end=.4,mach_points=1,
                    alpha_start=0,alpha_end=0,alpha_points=1,beta_deg=0,
                    reference_area=100,reference_chord=10,reference_span=20,
                    center=[0,0,0],require_canonical_components=True)
        args.update(extra)
        runner.generate_vspaero_sweep_script(root/'probe.vspscript',model,root/'out.csv',**args)
        return (root/'probe.vspscript').read_text(encoding='utf-8')

    def test_default_still_rejects_unassigned(self):
        with TemporaryDirectory() as tmp:
            text = self.generate(Path(tmp))
        self.assertIn('!in_thick && !in_thin && !(false)', text)
        self.assertNotIn('REPAIRMACH_DIAGNOSTIC_EXCLUSION;', text)

    def test_each_exclusion_is_exact_checked_and_before_compute(self):
        for name in ('Fuselage','Gondola'):
            with self.subTest(name=name), TemporaryDirectory() as tmp:
                text = self.generate(Path(tmp),diagnostic_excluded_components=[name])
                self.assertIn(f'!in_thick && !in_thin && !(name == "{name}")',text)
                self.assertIn('excluded_bodies.size() != 1',text)
                self.assertIn('GetGeomTypeName( excluded_bodies[0] ) != "Fuselage"',text)
                self.assertIn('GetSetFlag( excluded_bodies[0], thick_set ) || GetSetFlag( excluded_bodies[0], thin_set )',text)
                self.assertLess(text.index('QUALIFICATION_ALLOWED=0'),text.index('VSPAEROComputeGeometry'))
                self.assertIn('Wing must be assigned to Set_2',text)
                self.assertIn('Set_1 is empty',text)

    def test_invalid_requests_rejected(self):
        for bad in ('Fuselage',['Wing'],['GO'],['VO'],['Fuselage','Gondola'],['Gondola','Gondola'],[None]):
            with self.subTest(bad=bad), TemporaryDirectory() as tmp:
                with self.assertRaises(ValueError):
                    self.generate(Path(tmp),diagnostic_excluded_components=bad)

    def test_strict_validation_cannot_be_disabled(self):
        for setting in ('require_canonical_components','require_nonempty_fuselage_set','require_all_geometries_assigned'):
            with self.subTest(setting=setting), TemporaryDirectory() as tmp:
                with self.assertRaises(ValueError):
                    self.generate(Path(tmp),diagnostic_excluded_components=['Gondola'],**{setting:False})

    def test_to_face_rejected(self):
        with TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            self.generate(Path(tmp),diagnostic_excluded_components=['Gondola'],engine_boundary='to_face')

    def test_probe_rejects_other_mode_before_copy(self):
        with patch.object(gc.shutil,'copy2') as copy:
            with self.assertRaises(ValueError):
                gc._run_one_vspaero_probe(model=Path('x'),probe_dir=Path('y'),mode='lifting',
                    level='coarse',reference={},policy={},vspscript_executable=Path('z'),
                    diagnostic_excluded_components=['Fuselage'])
            copy.assert_not_called()

    def test_probe_keeps_fixed_tail(self):
        with self.assertRaises(ValueError):
            gc._run_one_vspaero_probe(model=Path('x'),probe_dir=Path('y'),mode='mixed',
                level='coarse',reference={},policy={},vspscript_executable=Path('z'),
                diagnostic_excluded_components=['Fuselage'],diagnostic_ignore_fixed_tail=True)


if __name__ == '__main__':
    unittest.main()
