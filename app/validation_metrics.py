#!/usr/bin/env python3
"""Transferable validation metrics for RepairMach aerodynamic studies.

The module deliberately does not calibrate individual Mach points.  It only
extracts direct quantities, compares like-for-like conditions and evaluates
the predeclared numerical and total-error limits.
"""

from __future__ import annotations

from collections import defaultdict
import math
from statistics import fmean


DEFAULT_ACCEPTANCE_CRITERIA = {
    "numerical_percent": 5.0,
    "total_mean_percent": 11.0,
    "point_target_percent": 15.0,
    "point_ceiling_percent": 20.0,
    "signed_bias_percent": 5.0,
}
LIMIT_EPSILON = 1.0e-12
CY_ALPHA_HYBRID_METHOD = "RM91-CYA-HYBRID-1"
CY_ALPHA_SWEEP_REFINED_METHOD = "RM92-CYA-WINGBODY-2-candidate"
CY_ALPHA_RM921_METHOD = "RM92.1-CYA-WINGBODY-3-working"


def finite_wing_cy_alpha_per_degree(mach: float, aspect_ratio: float) -> float:
    """Return a finite-wing semiempirical lift slope in 1/degree.

    This low-aspect-ratio expression is deliberately independent of any
    wind-tunnel reference curve.  It is used only to replace the thin-wing
    contribution on the validated subsonic branch.
    """
    mach = float(mach)
    aspect_ratio = float(aspect_ratio)
    if mach < 0.0 or aspect_ratio <= 0.0:
        raise ValueError("Mach не может быть отрицательным, а удлинение должно быть положительным")
    beta = math.sqrt(abs(1.0 - mach * mach))
    slope_per_radian = 2.0 * math.pi * aspect_ratio / (
        2.0 + math.sqrt(4.0 + aspect_ratio * aspect_ratio * beta * beta)
    )
    return slope_per_radian * math.pi / 180.0


def sweep_refined_finite_wing_cy_alpha_per_degree(
    mach: float,
    aspect_ratio: float,
    leading_edge_sweep_deg: float,
    normal_mach_transition_width: float = 0.45,
) -> dict[str, float]:
    """Blend unswept and swept finite-wing slopes by normal Mach number.

    The correction starts only when the wing leading edge becomes locally
    supersonic, ``M*cos(sweep) > 1``.  A smooth transition avoids a numerical
    kink and uses no wind-tunnel/reference value.
    """
    mach = float(mach)
    aspect_ratio = float(aspect_ratio)
    sweep = math.radians(float(leading_edge_sweep_deg))
    width = float(normal_mach_transition_width)
    if mach < 0.0 or aspect_ratio <= 0.0 or width <= 0.0:
        raise ValueError("Mach и удлинение должны быть допустимыми, ширина перехода — положительной")
    if not 0.0 <= abs(float(leading_edge_sweep_deg)) < 90.0:
        raise ValueError("Стреловидность передней кромки должна быть меньше 90°")

    beta = math.sqrt(abs(1.0 - mach * mach))
    unswept = finite_wing_cy_alpha_per_degree(mach, aspect_ratio)
    swept_per_radian = 2.0 * math.pi * aspect_ratio / (
        2.0
        + math.sqrt(
            4.0
            + aspect_ratio
            * aspect_ratio
            * (beta * beta + math.tan(sweep) ** 2)
        )
    )
    swept = swept_per_radian * math.pi / 180.0
    normal_mach = mach * math.cos(sweep)
    coordinate = max(0.0, min(1.0, (normal_mach - 1.0) / width))
    weight = coordinate * coordinate * (3.0 - 2.0 * coordinate)
    return {
        "Cy_alpha_unswept": unswept,
        "Cy_alpha_swept": swept,
        "normal_mach": normal_mach,
        "sweep_weight": weight,
        "Cy_alpha_blended": unswept * (1.0 - weight) + swept * weight,
    }


def hybrid_cy_alpha_sweep_refined_point(
    *,
    mach: float,
    full_vspaero: float,
    thin_all_vspaero: float,
    aspect_ratio: float,
    leading_edge_sweep_deg: float,
    normal_mach_transition_width: float = 0.45,
    full_source: str = "direct_full_VSPAERO",
    thin_source: str = "direct_thin_all_VSPAERO",
) -> dict[str, float | str]:
    """Build the RM92 sweep-aware wing/body lift-slope candidate.

    VSPAERO contributes only the calculated full-minus-thin residual.  The
    principal thin lifting-system slope is replaced by a geometry-based
    semiempirical value.  Component interpolation may be declared by the
    caller when a solver blind band fails the residual gate.
    """
    full = float(full_vspaero)
    thin = float(thin_all_vspaero)
    if not math.isfinite(full) or not math.isfinite(thin):
        raise ValueError("VSPAERO-вклады должны быть конечными")
    semi = sweep_refined_finite_wing_cy_alpha_per_degree(
        mach,
        aspect_ratio,
        leading_edge_sweep_deg,
        normal_mach_transition_width,
    )
    residual = full - thin
    return {
        "method_version": CY_ALPHA_SWEEP_REFINED_METHOD,
        "Mach": float(mach),
        "full_vspaero": full,
        "full_source": full_source,
        "thin_all_vspaero": thin,
        "thin_source": thin_source,
        "thick_body_interference_residual": residual,
        **semi,
        "Cy_alpha_per_deg": semi["Cy_alpha_blended"] + residual,
    }


def hybrid_cy_alpha_rm921_point(
    *,
    mach: float,
    full_vspaero: float,
    thin_all_vspaero: float,
    aspect_ratio: float,
    leading_edge_sweep_deg: float,
    normal_mach_transition_width: float = 0.35,
    supersonic_efficiency: float = 0.93,
    residual_limit_ratio: float = 0.05,
    full_source: str = "direct_full_VSPAERO",
    thin_source: str = "direct_thin_all_VSPAERO",
) -> dict[str, float | str]:
    """Return the working-project RM92.1 Cyα estimate.

    Three global, auditable controls are used instead of point corrections:

    * sweep blending follows the leading-edge normal Mach number;
    * a single supersonic lifting efficiency applies for M >= 1.2;
    * the calculated full-minus-thin residual is limited to the declared
      numerical error budget relative to the principal semiempirical term.

    The defaults are development values established on the MiG-25RB working
    project and must be independently validated before release status.
    """
    mach = float(mach)
    efficiency = float(supersonic_efficiency)
    residual_limit = float(residual_limit_ratio)
    if not 0.0 < efficiency <= 1.0:
        raise ValueError("Коэффициент сверхзвуковой эффективности должен быть в (0; 1]")
    if not 0.0 <= residual_limit <= 1.0:
        raise ValueError("Ограничитель остатка должен быть в [0; 1]")
    if 0.8 + LIMIT_EPSILON < mach < 1.2 - LIMIT_EPSILON:
        raise ValueError("RM92.1 не применяется в транзвуковом разрыве 0,8 < M < 1,2")

    full = float(full_vspaero)
    thin = float(thin_all_vspaero)
    if not math.isfinite(full) or not math.isfinite(thin):
        raise ValueError("VSPAERO-вклады должны быть конечными")
    semi = sweep_refined_finite_wing_cy_alpha_per_degree(
        mach,
        aspect_ratio,
        leading_edge_sweep_deg,
        normal_mach_transition_width,
    )
    principal = semi["Cy_alpha_blended"]
    raw_residual = full - thin
    residual_cap = residual_limit * abs(principal)
    limited_residual = max(-residual_cap, min(residual_cap, raw_residual))
    selected_efficiency = efficiency if mach >= 1.2 - LIMIT_EPSILON else 1.0
    return {
        "method_version": CY_ALPHA_RM921_METHOD,
        "Mach": mach,
        "full_vspaero": full,
        "full_source": full_source,
        "thin_all_vspaero": thin,
        "thin_source": thin_source,
        "thick_body_interference_residual_raw": raw_residual,
        "residual_limit_ratio": residual_limit,
        "thick_body_interference_residual_limited": limited_residual,
        "supersonic_efficiency": selected_efficiency,
        **semi,
        "Cy_alpha_per_deg": selected_efficiency * principal + limited_residual,
    }


def hybrid_cy_alpha_point(
    *,
    mach: float,
    full_vspaero: float,
    wing_vspaero: float,
    aspect_ratio: float,
    thin_all_vspaero: float | None = None,
    full_source: str = "direct_full_VSPAERO",
) -> dict[str, float | str | None]:
    """Build one auditable hybrid Cyα point without reference-curve input.

    The method has three predeclared branches:

    * M <= 0.8: replace the VSPAERO wing-only slope by the finite-wing
      semiempirical value while preserving directly computed aircraft
      interference;
    * 1.2 <= M <= 1.3: add a conservative shock/interference surrogate equal
      to the directly computed thick-body increment ``full - thin``;
    * M >= 1.4: keep the full-configuration result unchanged.  A caller may
      pass a component-interpolated full value when an exact solver point is
      rejected by residual checks.

    The transonic gap 0.8 < M < 1.2 is intentionally unsupported.
    """
    mach = float(mach)
    full = float(full_vspaero)
    wing = float(wing_vspaero)
    semi = finite_wing_cy_alpha_per_degree(mach, aspect_ratio)
    finite_wing_correction = semi - wing
    shock_correction = 0.0

    if mach <= 0.8 + LIMIT_EPSILON:
        selected = finite_wing_correction
        method = "finite_wing_replacement"
    elif 1.2 - LIMIT_EPSILON <= mach <= 1.3 + LIMIT_EPSILON:
        if thin_all_vspaero is None:
            raise ValueError("Для M=1,2…1,3 требуется расчёт тонких поверхностей")
        shock_correction = full - float(thin_all_vspaero)
        selected = shock_correction
        method = "thick_body_shock_interference_surrogate"
    elif mach >= 1.4 - LIMIT_EPSILON:
        selected = 0.0
        method = "direct_or_component_interpolated_full"
    else:
        raise ValueError("Гибридная методика не применяется в транзвуковом разрыве 0,8 < M < 1,2")

    return {
        "method_version": CY_ALPHA_HYBRID_METHOD,
        "Mach": mach,
        "full_vspaero": full,
        "full_source": full_source,
        "wing_vspaero": wing,
        "wing_semiempirical": semi,
        "thin_all_vspaero": None if thin_all_vspaero is None else float(thin_all_vspaero),
        "finite_wing_correction": finite_wing_correction,
        "shock_interference_correction": shock_correction,
        "selected_correction": selected,
        "correction_method": method,
        "Cy_alpha_per_deg": full + selected,
    }


def cy_alpha_per_degree(
    rows: list[dict[str, float]],
    *,
    alpha_start_deg: float = 0.0,
    alpha_step_deg: float = 1.0,
    tolerance: float = 1.0e-6,
) -> list[dict[str, float | str]]:
    """Return direct ``Cy(alpha+1°)-Cy(alpha)`` values for every Mach.

    VSPAERO calls the longitudinal lift coefficient ``CLtot``.  RepairMach
    reports the same coefficient as ``Cy`` in the Russian aerodynamic-axis
    convention.  Interpolation is intentionally forbidden here: a missing
    0° or 1° solution is an incomplete calculation, not a value to invent.
    """
    if alpha_step_deg <= 0.0:
        raise ValueError("Шаг alpha должен быть положительным")
    grouped: dict[float, list[dict[str, float]]] = defaultdict(list)
    for row in rows:
        try:
            grouped[float(row["Mach"])].append(row)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Строка VSPAERO не содержит корректного Mach") from exc

    result: list[dict[str, float | str]] = []
    alpha_end_deg = alpha_start_deg + alpha_step_deg
    for mach in sorted(grouped):
        start = _exact_alpha(grouped[mach], alpha_start_deg, tolerance)
        end = _exact_alpha(grouped[mach], alpha_end_deg, tolerance)
        if start is None or end is None:
            missing = []
            if start is None:
                missing.append(f"alpha={alpha_start_deg:g}°")
            if end is None:
                missing.append(f"alpha={alpha_end_deg:g}°")
            raise ValueError(
                f"M={mach:g}: отсутствует прямой расчёт " + " и ".join(missing)
            )
        try:
            cy_start = float(start["CLtot"])
            cy_end = float(end["CLtot"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"M={mach:g}: отсутствует корректный CLtot") from exc
        if not math.isfinite(cy_start) or not math.isfinite(cy_end):
            raise ValueError(f"M={mach:g}: CLtot содержит NaN или бесконечность")
        result.append(
            {
                "Mach": mach,
                "alpha_start_deg": alpha_start_deg,
                "alpha_end_deg": alpha_end_deg,
                "Cy_start": cy_start,
                "Cy_end": cy_end,
                "Cy_alpha_per_deg": (cy_end - cy_start) / alpha_step_deg,
                "source": "direct_VSPAERO",
            }
        )
    return result


def relative_error_percent(calculated: float, reference: float) -> float:
    if abs(reference) <= 1.0e-15:
        raise ValueError("Относительная погрешность не определена для нулевого эталона")
    return (float(calculated) - float(reference)) / abs(float(reference)) * 100.0


def evaluate_reference_points(
    calculated: list[dict],
    reference: list[dict],
    *,
    value_key: str = "Cy_alpha_per_deg",
    mach_tolerance: float = 1.0e-6,
    criteria: dict | None = None,
) -> dict:
    """Compare direct points and classify the total method error."""
    limits = _criteria(criteria)
    points = []
    missing_mach = []
    for ref in reference:
        mach = float(ref["Mach"])
        candidate = _exact_mach(calculated, mach, mach_tolerance)
        if candidate is None:
            missing_mach.append(mach)
            continue
        error = relative_error_percent(candidate[value_key], ref[value_key])
        points.append(
            {
                "Mach": mach,
                "calculated": float(candidate[value_key]),
                "reference": float(ref[value_key]),
                "signed_error_percent": error,
                "absolute_error_percent": abs(error),
                "within_point_target": _within(error, limits["point_target_percent"]),
                "within_point_ceiling": _within(error, limits["point_ceiling_percent"]),
            }
        )

    if not points:
        raise ValueError("Нет совпадающих прямых точек расчёта и эталона")
    absolute = [item["absolute_error_percent"] for item in points]
    signed = [item["signed_error_percent"] for item in points]
    summary = {
        "matched_points": len(points),
        "reference_points": len(reference),
        "missing_mach": missing_mach,
        "mean_absolute_error_percent": fmean(absolute),
        "max_absolute_error_percent": max(absolute),
        "signed_bias_percent": fmean(signed),
        "points_within_target": sum(item["within_point_target"] for item in points),
    }
    summary["accepted"] = (
        not missing_mach
        and _within(summary["mean_absolute_error_percent"], limits["total_mean_percent"])
        and _within(summary["max_absolute_error_percent"], limits["point_ceiling_percent"])
        and _within(summary["signed_bias_percent"], limits["signed_bias_percent"])
    )
    return {"criteria": limits, "points": points, "summary": summary}


def evaluate_numerical_convergence(
    medium: list[dict],
    fine: list[dict],
    *,
    value_key: str = "Cy_alpha_per_deg",
    mach_tolerance: float = 1.0e-6,
    criteria: dict | None = None,
) -> dict:
    """Evaluate Medium-to-Fine change without using wind-tunnel data."""
    limits = _criteria(criteria)
    points = []
    missing_mach = []
    for fine_point in fine:
        mach = float(fine_point["Mach"])
        medium_point = _exact_mach(medium, mach, mach_tolerance)
        if medium_point is None:
            missing_mach.append(mach)
            continue
        change = relative_error_percent(medium_point[value_key], fine_point[value_key])
        points.append(
            {
                "Mach": mach,
                "medium": float(medium_point[value_key]),
                "fine": float(fine_point[value_key]),
                "signed_change_percent": change,
                "absolute_change_percent": abs(change),
            }
        )
    if not points:
        raise ValueError("Нет совпадающих точек Medium и Fine")
    changes = [item["absolute_change_percent"] for item in points]
    summary = {
        "matched_points": len(points),
        "fine_points": len(fine),
        "missing_mach": missing_mach,
        "mean_change_percent": fmean(changes),
        "max_change_percent": max(changes),
    }
    summary["accepted"] = (
        not missing_mach
        and _within(summary["max_change_percent"], limits["numerical_percent"])
    )
    return {"criteria": limits, "points": points, "summary": summary}


def combined_acceptance(numerical: dict, reference: dict) -> dict:
    """Return the final gate; both numerical and total-error gates must pass."""
    numerical_ok = bool(numerical.get("summary", {}).get("accepted"))
    reference_ok = bool(reference.get("summary", {}).get("accepted"))
    return {
        "numerical_accepted": numerical_ok,
        "total_error_accepted": reference_ok,
        "accepted": numerical_ok and reference_ok,
    }


def _criteria(overrides: dict | None) -> dict:
    result = dict(DEFAULT_ACCEPTANCE_CRITERIA)
    if overrides:
        unknown = set(overrides) - set(result)
        if unknown:
            raise ValueError(f"Неизвестные критерии допуска: {sorted(unknown)}")
        result.update({key: float(value) for key, value in overrides.items()})
    if any(value < 0.0 for value in result.values()):
        raise ValueError("Критерии допуска не могут быть отрицательными")
    return result


def _exact_alpha(rows: list[dict], alpha: float, tolerance: float) -> dict | None:
    matches = [row for row in rows if abs(float(row.get("AoA", 1.0e99)) - alpha) <= tolerance]
    if len(matches) > 1:
        raise ValueError(f"Обнаружены повторные точки alpha={alpha:g}°")
    return matches[0] if matches else None


def _exact_mach(points: list[dict], mach: float, tolerance: float) -> dict | None:
    matches = [point for point in points if abs(float(point["Mach"]) - mach) <= tolerance]
    if len(matches) > 1:
        raise ValueError(f"Обнаружены повторные точки M={mach:g}")
    return matches[0] if matches else None


def _within(value: float, limit: float) -> bool:
    return abs(float(value)) <= float(limit) + LIMIT_EPSILON
