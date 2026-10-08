from copy import deepcopy
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from vspaero_runner import generate_vspaero_sweep_script
from geometry_rules import validate_geometry_policy


class ImplicitWakeTests(unittest.TestCase):
    def generate(self, **overrides):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            model = root / "model.vsp3"
            model.touch()
            args = dict(mach_start=.4, mach_end=.4, mach_points=1,
                        alpha_start=5., alpha_end=5., alpha_points=1, beta_deg=0.,
                        reference_area=10., reference_chord=2., reference_span=5.,
                        center=[0., 0., 0.], wake_num_iter=80)
            args.update(overrides)
            script = root / "probe.vspscript"
            generate_vspaero_sweep_script(script, model, root / "result.csv", **args)
            return script.read_text(encoding="utf-8")

    def test_default_preserves_model_setting(self):
        self.assertNotIn('"ImplicitWake"', self.generate())

    def test_requested_flag_and_start_are_read_back(self):
        script = self.generate(implicit_wake=True, implicit_wake_start_iter=8)
        self.assertIn('SetIntAnalysisInput( sweep_name, "ImplicitWake", implicit_wake_flag )', script)
        self.assertIn('GetIntAnalysisInput( sweep_name, "ImplicitWakeStartIteration" )', script)
        self.assertIn('actual_implicit[0] != 1', script)
        self.assertIn('actual_implicit_start[0] != 8', script)

    def test_explicit_request_can_disable_model_implicit_flag(self):
        self.assertIn('actual_implicit[0] != 0', self.generate(implicit_wake=False))

    def test_invalid_controls_rejected(self):
        for override in ({"implicit_wake": "true"}, {"implicit_wake": 1},
                         {"implicit_wake_start_iter": -1}, {"implicit_wake_start_iter": True},
                         {"implicit_wake": True, "implicit_wake_start_iter": 80}):
            with self.subTest(override=override), self.assertRaises(ValueError):
                self.generate(**override)

    def test_policy_validates_all_stages_without_relaxing_acceptance(self):
        base = json.loads((ROOT / "config/geometry_certification.json").read_text(encoding="utf-8"))
        for where in ("baseline", "recovery", "fallback"):
            policy = deepcopy(base)
            probe = policy["probes"]["vspaero"]
            controls = (probe if where == "baseline" else probe["numerical_recovery"]["controls"]
                        if where == "recovery" else probe["numerical_recovery"]["fallback_controls"][0])
            controls.update(implicit_wake=True, implicit_wake_start_iter=8, wake_num_iter=80)
            validate_geometry_policy(policy)
            self.assertEqual(base["convergence"], policy["convergence"])
            self.assertEqual(-.3, probe["max_log10_l2_residual"])
            self.assertEqual(0., probe["max_log10_max_residual"])
            controls["implicit_wake_start_iter"] = 80
            with self.assertRaises(ValueError):
                validate_geometry_policy(policy)


if __name__ == "__main__":
    unittest.main()
