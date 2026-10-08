"""Routing regression: an oscillation never passes without extra evidence."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import geometry_certification as gc
from geometry_rules import load_geometry_policy


class BoundedOscillationRetry(unittest.TestCase):
    def setUp(self):
        self.policy = load_geometry_policy(Path(r'C:\RepairMach-Portable\config\geometry_certification.json'))
        self.policy['convergence']['adaptive_refinement']['local_span_plateau']['enabled'] = False
        self.values = {
            'coarse': {'CLtot': .133365589165, 'CDtot': .007552161124},
            'medium': {'CLtot': .131811933642, 'CDtot': .007648721326},
            'fine': {'CLtot': .132991162358, 'CDtot': .007710631972},
            # Synthetic test-only continuation, never a computed aircraft result.
            'extra_fine': {'CLtot': .13310, 'CDtot': .00773},
            'ultra_fine': {'CLtot': .13312, 'CDtot': .00774},
        }

    def ladder(self, invalid_level=None, missing_level=None):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            meshes = {}
            for level in self.values:
                if level == missing_level:
                    continue
                model = root / (level + '.vsp3')
                model.write_text('unit-test fixture', encoding='utf-8')
                meshes[level] = {'path': str(model), 'valid': True,
                                 'mesh_verification': {'valid': True}}

            def probe(**kwargs):
                level = kwargs['level']
                return {'level': level, 'valid': level != invalid_level,
                        'values': deepcopy(self.values[level])}

            with patch.object(gc, '_run_vspaero_probe_with_recovery', side_effect=probe) as mocked:
                result = gc._run_vspaero_ladder(mode='lifting', meshes=meshes, run_dir=root,
                    reference={'area': 100, 'cref': 10, 'bref': 20, 'center': [0, 0, 0]},
                    policy=self.policy, executable=root/'vspscript.exe')
            return result, mocked.call_count

    def test_only_bounded_oscillation_requests_additional_evidence(self):
        result, count = self.ladder()
        self.assertEqual(count, 4)
        self.assertTrue(result['converged'])
        self.assertFalse(result['convergence']['initial_convergence']['converged'])
        self.assertTrue(result['convergence']['adaptive_recoverable_oscillation'])

    def test_unresolved_oscillation_never_becomes_pass(self):
        self.values['extra_fine']['CLtot'] = .1319
        self.values['ultra_fine']['CLtot'] = .1330
        result, count = self.ladder()
        self.assertEqual(count, 5)
        self.assertFalse(result['converged'])

    def test_missing_extra_fine_is_fail_closed(self):
        result, count = self.ladder(missing_level='extra_fine')
        self.assertEqual(count, 3)
        self.assertFalse(result['valid'])
        self.assertFalse(result['converged'])

    def test_invalid_extra_fine_is_fail_closed(self):
        result, count = self.ladder(invalid_level='extra_fine')
        self.assertEqual(count, 4)
        self.assertFalse(result['valid'])
        self.assertFalse(result['converged'])

    def test_large_oscillation_does_not_get_retry(self):
        self.values['fine']['CLtot'] = .15
        result, count = self.ladder()
        self.assertEqual(count, 3)
        self.assertFalse(result['converged'])

    def test_other_quantity_outside_retry_limit_blocks_retry(self):
        self.values['fine']['CDtot'] = .02
        result, count = self.ladder()
        self.assertEqual(count, 3)
        self.assertFalse(result['converged'])

    def test_adaptive_disabled_never_retries(self):
        self.policy['convergence']['adaptive_refinement']['enabled'] = False
        result, count = self.ladder()
        self.assertEqual(count, 3)
        self.assertFalse(result['converged'])


if __name__ == '__main__':
    unittest.main()
