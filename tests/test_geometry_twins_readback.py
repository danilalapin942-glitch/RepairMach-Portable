from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from geometry_twins import generate_apply_script, apply_model_actions, _final_state_checks


class PersistentReadbackTests(unittest.TestCase):
    def action(self, **kwargs):
        return dict(action='set_parameter', target_twins=['test'],
                    parm_id='p1', before=1.0, after=2.0, **kwargs)

    def script(self, actions=None, empty=None):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            generate_apply_script(p/'run.vspscript', p/'source.vsp3', p/'out.vsp3',
                                  [self.action()] if actions is None else actions,
                                  twin_name='test', empty_thick_user_set=empty)
            return (p/'run.vspscript').read_text()

    def test_checks_after_update_and_after_reload_before_ok(self):
        s = self.script()
        post = s.index('POST_UPDATE_PARAMETER_MISMATCH')
        self.assertLess(s.index('APPLIED;ACTION=set_parameter'), s.rfind('Update();', 0, post))
        self.assertLess(post, s.index('WriteVSPFile('))
        self.assertLess(s.index('WriteVSPFile('), s.rindex('ClearVSPModel();'))
        self.assertLess(s.rindex('ReadVSPFile('), s.index('PERSISTED_PARAMETER_MISMATCH'))
        self.assertLess(s.index('PERSISTED_PARAMETER_MISMATCH'), s.index('REPAIRMACH_APPLY_OK=1'))
        self.assertLess(s.rindex('OPENVSP_ERROR='), s.index('REPAIRMACH_APPLY_OK=1'))

    def test_no_extra_applied_markers(self):
        self.assertEqual(self.script().count('Print( "APPLIED;'), 1)

    def test_missing_parameter_and_geometry_checked(self):
        s = self.script()
        for marker in ('POST_UPDATE_PARAMETER_MISSING', 'PERSISTED_PARAMETER_MISSING',
                       'PERSISTED_GEOMETRY_COUNT', 'PERSISTED_GEOMETRY_ID'):
            self.assertIn(marker, s)

    def test_noop_still_checks_saved_geometry(self):
        s = self.script([])
        self.assertIn('PERSISTED_GEOMETRY_COUNT', s)
        self.assertIn('OPENVSP_ERRORS_DURING_READBACK', s)

    def test_ignores_actions_for_other_twin(self):
        a = self.action(); a['target_twins'] = ['other']
        self.assertEqual(_final_state_checks([a], 'test', 'PERSISTED', None), '')

    def test_last_parameter_value_wins(self):
        a = self.action(); b = dict(a, before=2.0, after=3.0)
        s = _final_state_checks([a,b], 'test', 'PERSISTED', None)
        self.assertEqual(s.count('_PARAMETER_MISMATCH'), 1)
        self.assertIn('REQUESTED=3;', s)

    def test_final_assignments_account_for_clear(self):
        actions = [dict(action='set_assignment', target_twins=['test'], geom_id='g',
                        expected_user_set=1, other_user_set=2),
                   dict(action='clear_sets', target_twins=['test'], geom_id='g', user_sets=[1])]
        s = _final_state_checks(actions, 'test', 'PERSISTED', None)
        self.assertIn('GetSetFlag( "g", 4 ) != false', s)
        self.assertIn('GetSetFlag( "g", 5 ) != false', s)

    def test_empty_set_overrides_assignment(self):
        a = dict(action='set_assignment', target_twins=['test'], geom_id='g',
                 expected_user_set=1, other_user_set=2)
        s = _final_state_checks([a], 'test', 'PERSISTED', 1)
        self.assertIn('GetSetFlag( "g", 4 ) != false', s)
        self.assertIn('PERSISTED_EMPTY_SET_MISMATCH', s)

    def test_persistent_failure_cannot_be_accepted_with_old_ok(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            def runner(exe, script, log, **kwargs):
                log.write_text('APPLIED;ACTION=set_parameter\nREPAIRMACH_APPLY_OK=1\n'
                               'REPAIRMACH_APPLY_ABORT=PERSISTED_PARAMETER_MISMATCH\n')
                return 0
            with patch('geometry_twins.run_vspscript', side_effect=runner):
                result = apply_model_actions(source_path=p/'source.vsp3', output_path=p/'out.vsp3',
                    actions=[self.action()], twin_name='test', vspscript_executable=p/'vsp.exe', work_dir=p)
            self.assertFalse(result['valid'])
            self.assertTrue(any('PERSISTED_PARAMETER_MISMATCH' in e for e in result['errors']))


if __name__ == '__main__':
    unittest.main()
