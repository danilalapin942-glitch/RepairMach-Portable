from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from geometry_certification import _recover_vspaero_residual_failure


def residual_failure():
    return {"valid": False, "output_quality_failure_kind": "residual", "finite_rows": 1,
            "health_errors": [], "condition_errors": [],
            "set_validation": {"valid": True, "calculation_complete": True}}


class RecoveryStagesTests(unittest.TestCase):
    def run_recovery(self, responses):
        policy = json.loads((ROOT / "config/geometry_certification.json").read_text())
        with patch("geometry_certification._run_one_vspaero_probe", side_effect=responses) as runner:
            result = _recover_vspaero_residual_failure(
                failed_probe=residual_failure(), model=Path("m.vsp3"), probe_root=Path("out"), mode="lifting", level="fine",
                reference={}, policy=policy, executable=Path("vspscript.exe"), mach=.4, alpha_deg=0, engine_boundary="model")
            return result, runner.call_args_list

    def test_second_stage_requires_two_valid_repeats(self):
        good = {"valid": True, "values": {"CLtot": .137, "CDtot": .00767}}
        result, calls = self.run_recovery([residual_failure(), good, good])
        self.assertTrue(result["valid"])
        self.assertEqual(3, len(calls))
        self.assertEqual(64, calls[1].kwargs["numerical_overrides"]["wake_num_iter"])
        self.assertEqual(1, len(result["prior_stages"]))
        json.dumps(result)  # no circular provenance

    def test_nonfinite_or_setup_failure_stops_without_fallback(self):
        result, calls = self.run_recovery([{"valid": False, "health_errors": ["nan_or_inf"]}])
        self.assertFalse(result["valid"])
        self.assertEqual(1, len(calls))

    def test_disagreeing_repeats_not_accepted(self):
        result, _ = self.run_recovery([residual_failure(),
            {"valid": True, "values": {"CLtot": .137, "CDtot": .00767}},
            {"valid": True, "values": {"CLtot": .3, "CDtot": .02}}])
        self.assertFalse(result["valid"])


if __name__ == "__main__":
    unittest.main()
