from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from vspaero_runner import _wake_iteration_guard_script, generate_vspaero_sweep_script, parse_set_report


class WakeControlsTests(unittest.TestCase):
    def test_noninteger_values_are_not_silently_truncated(self):
        for value in (True, False, 0, -1, 1.5, 160., '160'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _wake_iteration_guard_script(value)

    def test_backend_limits_are_dynamic_and_readback_is_required(self):
        text = _wake_iteration_guard_script(320)
        for token in ('FindContainer( "VSPAEROSettings", 0 )',
                      'GetParmLowerLimit( wake_iter_parm )',
                      'GetParmUpperLimit( wake_iter_parm )',
                      'GetParmVal( wake_iter_parm )',
                      'actual_wake_iter != 320',
                      'REPAIRMACH_ABORTED_NUMERICAL_CONTROLS=1'):
            self.assertIn(token, text)
        self.assertNotIn('SetParmUpperLimit', text)
        self.assertNotIn('WriteVSPFile', text)

    def test_guard_runs_before_geometry_and_sweep(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / 'model.vsp3'
            model.touch()
            script = root / 'probe.vspscript'
            generate_vspaero_sweep_script(script, model, root / 'out.csv',
                mach_start=.4, mach_end=.4, mach_points=1,
                alpha_start=0., alpha_end=0., alpha_points=1, beta_deg=0.,
                reference_area=10., reference_chord=2., reference_span=5.,
                center=[0., 0., 0.], wake_num_iter=320)
            text = script.read_text(encoding='utf-8')
        self.assertLess(text.index('REPAIRMACH_WAKE_ITER_LIMITS'), text.index('ExecAnalysis( compute_name )'))
        self.assertLess(text.index('REPAIRMACH_WAKE_ITER_CONFIRMED'), text.index('ExecAnalysis( sweep_name )'))
        self.assertIn('array<int> wake_iterations(1, 320)', text)

    def test_abort_cannot_be_overridden_by_completion_marker(self):
        log = '''REPAIRMACH_SET_REPORT_BEGIN
REPAIRMACH_SET_REPORT_END
REPAIRMACH_ENGINE_SETUP;MODE=model;VALID=true
REPAIRMACH_TAIL_SETUP;MODE=model_default;VALID=true
REPAIRMACH_SETUP_VALIDATION=OK
REPAIRMACH_SET_VALIDATION=OK
REPAIRMACH_ABORTED_NUMERICAL_CONTROLS=1
REPAIRMACH_VSPAERO_COMPLETE=1'''
        report = parse_set_report(log)
        self.assertFalse(report['valid'])
        self.assertFalse(report['calculation_complete'])


if __name__ == '__main__':
    unittest.main()
