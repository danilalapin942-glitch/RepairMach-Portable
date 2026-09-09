from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from geometry_certification import (
    _backend_eligible,
    _run_one_vspaero_probe,
    classify_vspaero_health,
    parse_vspaero_health,
)
from geometry_rules import load_geometry_policy, validate_geometry_policy
from repairmach_beta import _certified_vspaero_study_geometry


POLICY_PATH = ROOT / "config" / "geometry_certification.json"


class GeometryFailClosedTests(unittest.TestCase):
    def setUp(self):
        self.policy = load_geometry_policy(POLICY_PATH)

    def test_backend_gate_rejects_global_failure_master_change_and_dependency_failure(self):
        base = {
            "local_eligible": True,
            "blockers": [],
            "master_unchanged": True,
            "run_complete": True,
            "verdict": "PASS_NATIVE",
        }
        self.assertTrue(_backend_eligible(**base))
        for override in (
            {"verdict": "FAIL"},
            {"master_unchanged": False},
            {"run_complete": False},
            {"blockers": [{"severity": "BLOCKER"}]},
            {"dependency_eligible": False},
        ):
            self.assertFalse(_backend_eligible(**{**base, **override}))

    def test_interactive_vspaero_study_never_inherits_certificate_binding(self):
        with patch("repairmach_beta.certified_solver_geometry") as certified:
            result = _certified_vspaero_study_geometry(
                Path("project"),
                Path("master.vsp3"),
                {"vspscript": Path("vspscript.exe"), "vspaero": Path("vspaero.exe")},
                None,
            )
        self.assertIsNone(result)
        certified.assert_not_called()

    def test_policy_requires_finite_probe_residual_threshold(self):
        for value in (None, float("nan"), float("inf")):
            policy = deepcopy(self.policy)
            if value is None:
                policy["probes"]["vspaero"].pop("max_log10_l2_residual")
            else:
                policy["probes"]["vspaero"]["max_log10_l2_residual"] = value
            with self.assertRaisesRegex(ValueError, "max_log10_l2_residual"):
                validate_geometry_policy(policy)

    def test_probe_requires_strict_output_quality_and_policy_residual(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / "model.vsp3"
            model.write_text("<Vsp_Geometry/>", encoding="utf-8")
            polar = root / "probe.polar"
            polar.write_text("stub", encoding="utf-8")

            def fake_generate(_script, _model, results_csv, **_kwargs):
                Path(results_csv).write_text("stub", encoding="utf-8")

            def fake_run(_executable, _script, log, **_kwargs):
                Path(log).write_text("ordinary solver output", encoding="utf-8")
                return 0

            with (
                patch(
                    "geometry_certification.generate_vspaero_sweep_script",
                    side_effect=fake_generate,
                ),
                patch("geometry_certification.run_vspscript", side_effect=fake_run),
                patch("geometry_certification.find_generated_polar", return_value=polar),
                patch(
                    "geometry_certification.parse_set_report",
                    return_value={"valid": True, "calculation_complete": True},
                ),
                patch(
                    "geometry_certification.parse_vspaero_polar",
                    return_value=[{
                        "Mach": 0.8,
                        "AoA": 1.0,
                        "Beta": 0.0,
                        "CLtot": 0.1,
                        "CDtot": 0.02,
                    }],
                ),
                patch(
                    "geometry_certification.validate_vspaero_run_outputs",
                    return_value={"valid": False, "errors": ["residual rejected"]},
                ) as strict_validator,
            ):
                result = _run_one_vspaero_probe(
                    model=model,
                    probe_dir=root / "run",
                    mode="mixed",
                    level="coarse",
                    reference={
                        "area": 10.0,
                        "cref": 2.0,
                        "bref": 5.0,
                        "center": [0.0, 0.0, 0.0],
                    },
                    policy=self.policy,
                    vspscript_executable=root / "vspscript.exe",
                    mach=0.8,
                    alpha_deg=1.0,
                )

        self.assertFalse(result["valid"])
        self.assertFalse(result["output_quality"]["valid"])
        self.assertEqual(
            self.policy["probes"]["vspaero"]["max_log10_l2_residual"],
            strict_validator.call_args.kwargs["max_log10_l2_residual"],
        )

    def _run_probe_with_actual_conditions(
        self,
        root: Path,
        *,
        beta: float,
        tail_setup: dict,
        engine_setup: dict,
    ) -> dict:
        model = root / "model.vsp3"
        model.write_text("<Vsp_Geometry/>", encoding="utf-8")
        polar = root / "probe.polar"
        polar.write_text("stub", encoding="utf-8")

        def fake_generate(_script, _model, results_csv, **_kwargs):
            Path(results_csv).write_text("stub", encoding="utf-8")

        def fake_run(_executable, _script, log, **_kwargs):
            Path(log).write_text("machine-readable fixture", encoding="utf-8")
            return 0

        with (
            patch(
                "geometry_certification.generate_vspaero_sweep_script",
                side_effect=fake_generate,
            ),
            patch("geometry_certification.run_vspscript", side_effect=fake_run),
            patch("geometry_certification.find_generated_polar", return_value=polar),
            patch(
                "geometry_certification.parse_set_report",
                return_value={
                    "valid": True,
                    "calculation_complete": True,
                    "tail_setup": tail_setup,
                    "engine_setup": engine_setup,
                },
            ),
            patch(
                "geometry_certification.parse_vspaero_polar",
                return_value=[{
                    "Beta": beta,
                    "Mach": 0.8,
                    "AoA": 1.0,
                    "CLtot": 0.1,
                    "CDtot": 0.02,
                }],
            ),
            patch(
                "geometry_certification.validate_vspaero_run_outputs",
                return_value={"valid": True},
            ),
        ):
            return _run_one_vspaero_probe(
                model=model,
                probe_dir=root / "run",
                mode="mixed",
                level="coarse",
                reference={
                    "area": 10.0,
                    "cref": 2.0,
                    "bref": 5.0,
                    "center": [0.0, 0.0, 0.0],
                },
                policy=self.policy,
                vspscript_executable=root / "vspscript.exe",
                mach=0.8,
                alpha_deg=1.0,
                engine_boundary="model",
            )

    def test_probe_rejects_polar_beta_different_from_qualification(self):
        with TemporaryDirectory() as tmp:
            result = self._run_probe_with_actual_conditions(
                Path(tmp),
                beta=1.0,
                tail_setup={
                    "MODE": "fixed_incidence",
                    "NAME": "GO",
                    "ACTUAL_ANGLE_DEG": 0.0,
                },
                engine_setup={"MODE": "model"},
            )
        self.assertFalse(result["valid"])
        self.assertTrue(any("Beta" in item for item in result["condition_errors"]))

    def test_probe_rejects_tail_and_engine_readback_from_another_setup(self):
        with TemporaryDirectory() as tmp:
            result = self._run_probe_with_actual_conditions(
                Path(tmp),
                beta=0.0,
                tail_setup={"MODE": "model_default"},
                engine_setup={"MODE": "to_face"},
            )
        self.assertFalse(result["valid"])
        self.assertTrue(any("ГО" in item for item in result["condition_errors"]))
        self.assertTrue(any("Engine boundary" in item for item in result["condition_errors"]))

    def test_optional_infinite_ratio_is_not_a_fatal_health_signal(self):
        signals = parse_vspaero_health("E = inf\nLoD = -inf\n")
        errors, _warnings = classify_vspaero_health(signals)
        self.assertNotIn("nan_or_inf", signals)
        self.assertEqual([], errors)


if __name__ == "__main__":
    unittest.main()
