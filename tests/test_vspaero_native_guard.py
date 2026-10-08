from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from vspaero_runner import generate_vspaero_sweep_script,parse_set_report

BASE='''REPAIRMACH_SET_REPORT_BEGIN
REPAIRMACH_SET_REPORT_END
REPAIRMACH_TAIL_SETUP;MODE=model_default;VALID=true
REPAIRMACH_ENGINE_SETUP;MODE=model;VALID=true
REPAIRMACH_SETUP_VALIDATION=OK
REPAIRMACH_SET_VALIDATION=OK
REPAIRMACH_VSPAERO_COMPLETE=1
'''
REQUIRED='REPAIRMACH_NATIVE_GUARD_REQUIRED=1\n'
OK='REPAIRMACH_NATIVE_MESH_VALIDATION=OK\n'
ABORT='REPAIRMACH_ABORTED_NATIVE_MESH=1\n'

class NativeGuardTests(unittest.TestCase):
    def test_legacy_does_not_claim_native_validation(self):
        r=parse_set_report(BASE)
        self.assertTrue(r['calculation_complete'])
        self.assertFalse(r['native_mesh_validated'])

    def test_missing_guard_confirmation_overrides_completion(self):
        r=parse_set_report(REQUIRED+BASE)
        self.assertFalse(r['valid'])
        self.assertFalse(r['calculation_complete'])

    def test_native_abort_overrides_false_success(self):
        for text in (BASE+ABORT,REQUIRED+OK+BASE+ABORT):
            r=parse_set_report(text)
            self.assertFalse(r['valid'])
            self.assertFalse(r['calculation_complete'])
            self.assertFalse(r['native_mesh_validated'])

    def test_complete_gate_evidence(self):
        r=parse_set_report(REQUIRED+OK+BASE)
        self.assertTrue(r['valid'])
        self.assertTrue(r['native_mesh_validated'])

    def test_embedded_or_trailing_text_not_proof(self):
        for text in ('prefix '+OK,OK.strip()+' extra\n'):
            self.assertFalse(parse_set_report(REQUIRED+BASE+text)['valid'])

    def test_native_success_does_not_override_solver_errors(self):
        self.assertFalse(parse_set_report(REQUIRED+OK+BASE+'REPAIRMACH_ABORTED_SOLVER_ERRORS=1')['calculation_complete'])

    def test_missing_template_stops_script_generation(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            with patch('pathlib.Path.read_text',side_effect=FileNotFoundError('missing gate')):
                with self.assertRaisesRegex(FileNotFoundError,'missing gate'): self.generate(root)
            self.assertFalse((root/'run.vspscript').exists())

    def generate(self,root):
        (root/'model.vsp3').write_text('fixture',encoding='utf-8')
        generate_vspaero_sweep_script(root/'run.vspscript',root/'model.vsp3',root/'results.csv',
            mach_start=.4,mach_end=.4,mach_points=1,alpha_start=0,alpha_end=0,alpha_points=1,
            beta_deg=0,reference_area=10,reference_chord=2,reference_span=5,center=[0,0,0])

    def test_order_and_same_actual_native(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            self.generate(root)
            text=(root/'run.vspscript').read_text()
        self.assertLess(text.index('Print( "REPAIRMACH_NATIVE_GUARD_REQUIRED'),text.index('ExecAnalysis( compute_name )'))
        self.assertLess(text.index('ExecAnalysis( compute_name )'),text.index('native_ok = RMNative('))
        self.assertLess(text.index('native_ok = RMNative('),text.index('ExecAnalysis( sweep_name )'))
        self.assertIn('GetStringResults( compute_result, "VSPGeomFileName" )',text)
        self.assertIn('GetStringResults( compute_result, "Mesh_GeomID" )',text)
        self.assertIn('REPAIRMACH_ABORTED_NATIVE_MESH=1',text)
        self.assertEqual(text.count('ExecAnalysis( compute_name )'),1)
        self.assertEqual(text.count('ExecAnalysis( sweep_name )'),1)

if __name__=='__main__': unittest.main()
