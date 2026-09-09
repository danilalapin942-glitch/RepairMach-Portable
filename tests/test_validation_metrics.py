import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from validation_metrics import (
    combined_acceptance,
    cy_alpha_per_degree,
    evaluate_numerical_convergence,
    evaluate_reference_points,
    finite_wing_cy_alpha_per_degree,
    hybrid_cy_alpha_point,
    hybrid_cy_alpha_rm921_point,
    hybrid_cy_alpha_sweep_refined_point,
    sweep_refined_finite_wing_cy_alpha_per_degree,
)


class ValidationMetricTests(unittest.TestCase):
    def test_cy_alpha_uses_direct_zero_to_one_degree_increment(self):
        rows = [
            {"Mach": 0.8, "AoA": 0.0, "CLtot": 0.05},
            {"Mach": 0.8, "AoA": 1.0, "CLtot": 0.105},
            {"Mach": 1.2, "AoA": 0.0, "CLtot": 0.02},
            {"Mach": 1.2, "AoA": 1.0, "CLtot": 0.09},
        ]
        result = cy_alpha_per_degree(rows)
        self.assertEqual(2, len(result))
        self.assertAlmostEqual(0.055, result[0]["Cy_alpha_per_deg"])
        self.assertEqual("direct_VSPAERO", result[0]["source"])

    def test_cy_alpha_rejects_missing_direct_point(self):
        with self.assertRaisesRegex(ValueError, "прямой расчёт"):
            cy_alpha_per_degree(
                [{"Mach": 0.8, "AoA": 0.0, "CLtot": 0.05}]
            )

    def test_total_error_limits(self):
        calculated = [
            {"Mach": 1.2, "Cy_alpha_per_deg": 0.099},
            {"Mach": 1.3, "Cy_alpha_per_deg": 0.091},
        ]
        reference = [
            {"Mach": 1.2, "Cy_alpha_per_deg": 0.1},
            {"Mach": 1.3, "Cy_alpha_per_deg": 0.1},
        ]
        result = evaluate_reference_points(calculated, reference)
        self.assertTrue(result["summary"]["accepted"])
        self.assertAlmostEqual(5.0, result["summary"]["mean_absolute_error_percent"])
        self.assertAlmostEqual(-5.0, result["summary"]["signed_bias_percent"])

    def test_point_ceiling_blocks_overfit_result(self):
        calculated = [
            {"Mach": 1.2, "Cy_alpha_per_deg": 0.075},
            {"Mach": 1.3, "Cy_alpha_per_deg": 0.1},
            {"Mach": 1.4, "Cy_alpha_per_deg": 0.1},
        ]
        reference = [
            {"Mach": 1.2, "Cy_alpha_per_deg": 0.1},
            {"Mach": 1.3, "Cy_alpha_per_deg": 0.1},
            {"Mach": 1.4, "Cy_alpha_per_deg": 0.1},
        ]
        result = evaluate_reference_points(calculated, reference)
        self.assertLess(result["summary"]["mean_absolute_error_percent"], 11.0)
        self.assertFalse(result["summary"]["accepted"])

    def test_combined_gate_requires_numerical_and_total_acceptance(self):
        medium = [{"Mach": 1.2, "Cy_alpha_per_deg": 0.098}]
        fine = [{"Mach": 1.2, "Cy_alpha_per_deg": 0.1}]
        numerical = evaluate_numerical_convergence(medium, fine)
        reference = evaluate_reference_points(fine, fine)
        self.assertTrue(combined_acceptance(numerical, reference)["accepted"])

    def test_subsonic_hybrid_replaces_only_wing_slope(self):
        semi = finite_wing_cy_alpha_per_degree(0.5, 2.86665)
        result = hybrid_cy_alpha_point(
            mach=0.5,
            full_vspaero=0.055780270747,
            wing_vspaero=0.043861894718,
            aspect_ratio=2.86665,
        )
        self.assertAlmostEqual(
            0.055780270747 + semi - 0.043861894718,
            result["Cy_alpha_per_deg"],
        )
        self.assertEqual("finite_wing_replacement", result["correction_method"])

    def test_lower_supersonic_hybrid_uses_thick_body_increment(self):
        result = hybrid_cy_alpha_point(
            mach=1.2,
            full_vspaero=0.078366978621,
            wing_vspaero=0.056039628949,
            thin_all_vspaero=0.059062073921,
            aspect_ratio=2.86665,
        )
        self.assertAlmostEqual(0.097671883321, result["Cy_alpha_per_deg"])
        self.assertEqual(
            "thick_body_shock_interference_surrogate",
            result["correction_method"],
        )

    def test_high_supersonic_hybrid_keeps_full_result(self):
        result = hybrid_cy_alpha_point(
            mach=1.6,
            full_vspaero=0.069633216254,
            wing_vspaero=0.046379232516,
            thin_all_vspaero=0.051123321538,
            aspect_ratio=2.86665,
            full_source="component_interpolation",
        )
        self.assertAlmostEqual(0.069633216254, result["Cy_alpha_per_deg"])
        self.assertEqual(0.0, result["selected_correction"])

    def test_hybrid_rejects_transonic_gap(self):
        with self.assertRaisesRegex(ValueError, "транзвуковом"):
            hybrid_cy_alpha_point(
                mach=1.0,
                full_vspaero=0.08,
                wing_vspaero=0.06,
                aspect_ratio=2.86665,
            )

    def test_sweep_refinement_activates_on_normal_mach(self):
        low = sweep_refined_finite_wing_cy_alpha_per_degree(1.2, 2.9157554, 43.0)
        high = sweep_refined_finite_wing_cy_alpha_per_degree(2.2, 2.9157554, 43.0)
        self.assertEqual(0.0, low["sweep_weight"])
        self.assertEqual(1.0, high["sweep_weight"])
        self.assertLess(high["Cy_alpha_blended"], high["Cy_alpha_unswept"])

    def test_sweep_refined_hybrid_retains_only_body_residual(self):
        result = hybrid_cy_alpha_sweep_refined_point(
            mach=1.7,
            full_vspaero=0.032334660311,
            thin_all_vspaero=0.032412154314,
            aspect_ratio=2.91575539564,
            leading_edge_sweep_deg=43.0,
        )
        self.assertAlmostEqual(0.046360994145, result["Cy_alpha_per_deg"], places=10)
        self.assertEqual("RM92-CYA-WINGBODY-2-candidate", result["method_version"])

    def test_cy_alpha_rejects_nonfinite_values(self):
        with self.assertRaisesRegex(ValueError, "NaN"):
            cy_alpha_per_degree(
                [
                    {"Mach": 1.5, "AoA": 0.0, "CLtot": 0.0},
                    {"Mach": 1.5, "AoA": 1.0, "CLtot": float("nan")},
                ]
            )

    def test_rm921_uses_one_global_efficiency_and_residual_budget(self):
        result = hybrid_cy_alpha_rm921_point(
            mach=1.7,
            full_vspaero=0.032334660311,
            thin_all_vspaero=0.032412154314,
            aspect_ratio=2.91575539564,
            leading_edge_sweep_deg=43.0,
        )
        self.assertAlmostEqual(0.042064246049, result["Cy_alpha_per_deg"], places=10)
        self.assertEqual(0.93, result["supersonic_efficiency"])
        self.assertEqual("RM92.1-CYA-WINGBODY-3-working", result["method_version"])

    def test_rm921_caps_large_component_residual(self):
        result = hybrid_cy_alpha_rm921_point(
            mach=0.8,
            full_vspaero=0.035502433024,
            thin_all_vspaero=0.030297798436,
            aspect_ratio=2.91575539564,
            leading_edge_sweep_deg=43.0,
        )
        self.assertLess(
            abs(result["thick_body_interference_residual_limited"]),
            abs(result["thick_body_interference_residual_raw"]),
        )


if __name__ == "__main__":
    unittest.main()
