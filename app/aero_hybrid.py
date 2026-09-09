#!/usr/bin/env python3
"""Parsers and traceable hybrid-result assembly for RepairMach 9."""

from __future__ import annotations

import csv
import json
import math
import re
from pathlib import Path


def _number(value: str):
    text = value.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return text


def _vspaero_float(token: str) -> float:
    """Parse normal numbers plus MSVC spellings such as ``-nan(ind)``."""
    normalized = token.strip().lower()
    if re.fullmatch(r"[+-]?nan(?:\([^)]*\))?", normalized):
        return math.nan
    if re.fullmatch(r"[+-]?(?:inf|infinity)(?:\([^)]*\))?", normalized):
        return -math.inf if normalized.startswith("-") else math.inf
    return float(token)


def parse_vspaero_polar(
    path: Path,
    *,
    required_fields: tuple[str, ...] = ("Mach", "AoA", "CLtot"),
) -> list[dict[str, float]]:
    """Read the numeric table from a VSPAERO ``.polar`` file.

    ``required_fields`` makes the parser suitable both for lightweight legacy
    inspection and for fail-closed production gates.  A requested field must
    exist in the header and be finite in every returned row; malformed rows are
    never silently converted to zero.
    """
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    header_index = next(
        (
            index
            for index, line in enumerate(lines)
            if "Mach" in line and "AoA" in line and "CLtot" in line
        ),
        None,
    )
    if header_index is None:
        raise ValueError("В POLAR не найдена таблица VSPAERO с Mach, AoA и CLtot")

    headers = lines[header_index].split()
    missing_headers = [name for name in required_fields if name not in headers]
    if missing_headers:
        raise ValueError(
            "В POLAR отсутствуют обязательные столбцы: " + ", ".join(missing_headers)
        )
    rows = []
    for line in lines[header_index + 1:]:
        values = line.split()
        if len(values) < len(headers):
            continue
        try:
            row = {name: _vspaero_float(values[index]) for index, name in enumerate(headers)}
        except ValueError:
            continue
        if not all(
            key in row and isinstance(row[key], (int, float)) and math.isfinite(row[key])
            for key in required_fields
        ):
            continue
        # VSPAERO legitimately writes ``inf`` for ratios such as L/D at zero
        # drag.  Those diagnostic columns must not discard an otherwise valid
        # force point.  Non-finite optional values are kept out of the result.
        row = {name: value for name, value in row.items() if math.isfinite(value)}
        rows.append(row)
    if not rows:
        raise ValueError("В POLAR не найдено ни одной числовой точки")
    return rows


_VSPAERO_FATAL_PATTERNS = {
    "timeout": r"REPAIRMACH_VSPSCRIPT_TIMEOUT",
    # Infinite L/D or efficiency is legitimate at a zero-drag diagnostic
    # point.  Required aerodynamic coefficients are checked separately.
    "nonfinite_solver": r"(?i)(?:^|[^A-Za-z])nan(?:\([^)]*\))?(?:[^A-Za-z]|$)|floating point exception",
    "upwind_loop": r"(?i)upwind.*loop",
    "fatal": r"(?i)(?:segmentation fault|access violation|fatal error)",
    "setup_validation": r"REPAIRMACH_ABORTED_SETUP_VALIDATION",
}


def validate_vspaero_run_outputs(
    polar_path: Path,
    log_path: Path,
    *,
    mach_start: float,
    mach_end: float,
    mach_points: int,
    alpha_start: float,
    alpha_end: float,
    alpha_points: int,
    mach_tolerance: float = 1.0e-6,
    alpha_tolerance: float = 1.0e-5,
    max_log10_l2_residual: float = -0.3,
    allow_zero_mach_without_logged_residual: bool = False,
) -> dict:
    """Validate finite coefficients and exact requested Mach/alpha coverage."""
    if mach_points < 1 or alpha_points < 1:
        raise ValueError("Число точек Mach и alpha должно быть положительным")
    log_text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    health_signals = [
        name for name, pattern in _VSPAERO_FATAL_PATTERNS.items()
        if re.search(pattern, log_text)
    ]
    if health_signals:
        raise ValueError(
            "Лог VSPAERO содержит признаки недостоверного расчёта: "
            + ", ".join(health_signals)
        )

    rows = parse_vspaero_polar(
        Path(polar_path), required_fields=("Mach", "AoA", "CLtot", "CDi")
    )
    convergence_rows = _parse_vspaero_log_convergence(log_text)
    mach_grid = _linear_grid(mach_start, mach_end, mach_points)
    alpha_grid = _linear_grid(alpha_start, alpha_end, alpha_points)
    expected = [(mach, alpha) for mach in mach_grid for alpha in alpha_grid]
    used: set[int] = set()
    checked = []
    for mach, alpha in expected:
        matches = [
            index for index, row in enumerate(rows)
            if abs(float(row["Mach"]) - mach) <= mach_tolerance
            and abs(float(row["AoA"]) - alpha) <= alpha_tolerance
        ]
        if len(matches) != 1:
            raise ValueError(
                "POLAR не содержит ровно одну запрошенную точку "
                f"M={mach:g}, alpha={alpha:g}: найдено {len(matches)}"
            )
        index = matches[0]
        if index in used:
            raise ValueError("Одна строка POLAR сопоставлена нескольким условиям")
        used.add(index)
        row = rows[index]
        convergence_matches = [
            item for item in convergence_rows
            if abs(item["Mach"] - mach) <= mach_tolerance
            and abs(item["alpha_deg"] - alpha) <= alpha_tolerance
        ]
        incompressible_log_exception = (
            allow_zero_mach_without_logged_residual
            and abs(mach) <= mach_tolerance
            and len(convergence_matches) == 0
        )
        if len(convergence_matches) != 1 and not incompressible_log_exception:
            raise ValueError(
                "В логе VSPAERO нет однозначной записи конечной невязки для "
                f"M={mach:g}, alpha={alpha:g}"
            )
        convergence = convergence_matches[0] if convergence_matches else None
        residual = convergence["L2Res"] if convergence is not None else None
        if residual is not None and residual > max_log10_l2_residual:
            raise ValueError(
                "Точка VSPAERO не прошла контроль невязки: "
                f"M={mach:g}, alpha={alpha:g}, log10(L2)={float(residual):.4g}"
            )
        checked_point = {
            "Mach": float(row["Mach"]),
            "alpha_deg": float(row["AoA"]),
            "CLtot": float(row["CLtot"]),
            "CDi": float(row["CDi"]),
            "L2Res": float(residual) if residual is not None else None,
            "MaxRes": float(convergence["MaxRes"]) if convergence is not None else None,
            "wake_iteration": int(convergence["wake_iteration"]) if convergence is not None else None,
            "convergence_evidence": (
                "logged_residual" if convergence is not None else "three_grid_only"
            ),
        }
        checked.append(checked_point)
    if len(used) != len(rows):
        raise ValueError(
            f"POLAR содержит незапрошенные или повторные строки: {len(rows) - len(used)}"
        )
    return {
        "valid": True,
        "expected_points": len(expected),
        "actual_points": len(rows),
        "fatal_health_signals": [],
        "max_log10_l2_residual": max_log10_l2_residual,
        "points": checked,
    }


def _linear_grid(start: float, end: float, points: int) -> list[float]:
    if points == 1:
        return [float(start)]
    return [
        float(start) + (float(end) - float(start)) * index / (points - 1)
        for index in range(points)
    ]


def _parse_vspaero_log_convergence(log_text: str) -> list[dict[str, float]]:
    """Extract the final finite convergence row for every ``Solving`` block."""
    solving_pattern = re.compile(
        r"Solving\.\.\.\s*Mach:\s*([+-]?[0-9.eE]+)\s*\.\.\.\s*"
        r"Alpha:\s*([+-]?[0-9.eE]+)",
        re.IGNORECASE,
    )
    starts = list(solving_pattern.finditer(log_text))
    result = []
    numeric_row = re.compile(r"^\s*\d+\s+", re.MULTILINE)
    for index, marker in enumerate(starts):
        segment_end = starts[index + 1].start() if index + 1 < len(starts) else len(log_text)
        segment = log_text[marker.end():segment_end]
        candidates = []
        for row_marker in numeric_row.finditer(segment):
            line_end = segment.find("\n", row_marker.start())
            line = segment[row_marker.start():line_end if line_end >= 0 else len(segment)]
            tokens = line.split()
            if len(tokens) < 23:
                continue
            try:
                values = [float(token) for token in tokens]
            except ValueError:
                continue
            if not all(math.isfinite(value) for value in (values[1], values[2], values[20], values[21])):
                continue
            candidates.append({
                "wake_iteration": int(values[0]),
                "Mach": values[1],
                "alpha_deg": values[2],
                "L2Res": values[20],
                "MaxRes": values[21],
            })
        if not candidates:
            continue
        final = candidates[-1]
        requested_mach = float(marker.group(1))
        requested_alpha = float(marker.group(2))
        if abs(final["Mach"] - requested_mach) <= 1.0e-4 and abs(final["alpha_deg"] - requested_alpha) <= 1.0e-4:
            result.append(final)
    return result


def select_vspaero_point(
    rows: list[dict[str, float]],
    mach: float,
    alpha_deg: float,
    mach_tolerance: float = 0.02,
    alpha_tolerance: float = 0.02,
    max_log10_l2_residual: float = -0.3,
) -> dict[str, float]:
    point = min(
        rows,
        key=lambda row: (abs(row["Mach"] - mach), abs(row["AoA"] - alpha_deg)),
    )
    mach_error = abs(point["Mach"] - mach)
    alpha_error = abs(point["AoA"] - alpha_deg)
    if mach_error > mach_tolerance or alpha_error > alpha_tolerance:
        raise ValueError(
            "В POLAR нет точки для условий MachLine: "
            f"нужно M={mach:.6g}, alpha={alpha_deg:.6g}; "
            f"ближайшая M={point['Mach']:.6g}, alpha={point['AoA']:.6g}"
        )
    for key in ("Mach", "AoA", "CLtot"):
        if key not in point or not math.isfinite(float(point[key])):
            raise ValueError(f"Выбранная точка VSPAERO содержит некорректный {key}")
    residual = point.get("L2Res")
    if residual is not None and float(residual) > max_log10_l2_residual:
        raise ValueError(
            "Точка VSPAERO не прошла контроль невязки: "
            f"log10(L2)={float(residual):.4g} > {max_log10_l2_residual:.4g}"
        )
    return point


def parse_openvsp_results_csv(path: Path, *, strict: bool = False) -> dict:
    """Parse a CSV produced by OpenVSP WriteResultsCSVFile."""
    raw: dict[str, list] = {}
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        for row in csv.reader(handle):
            if not row:
                continue
            raw[row[0].strip()] = [_number(value) for value in row[1:] if value.strip()]

    total_values = raw.get("Total_CD_Total", [])
    total_cd = float(total_values[0]) if total_values else 0.0
    component_keys = {
        "label": "Comp_Label",
        "id": "Comp_ID",
        "surface_number": "Comp_SurfNum",
        "cf": "Comp_Cf",
        "form_factor_equation": "Comp_FFEqnName",
        "form_factor_input": "Comp_FFIn",
        "form_factor": "Comp_FFOut",
        "fineness_or_thickness_ratio": "Comp_FineRat",
        "interference_factor": "Comp_Q",
        "reference_length": "Comp_Lref",
        "reynolds": "Comp_Re",
        "wetted_area": "Comp_Swet",
        "cd": "Comp_CD",
    }
    count_values = raw.get("Num_Comp", [])
    count = int(float(count_values[0])) if count_values else 0
    if count == 0:
        count = max((len(raw.get(key, [])) for key in component_keys.values()), default=0)

    components = []
    for index in range(count):
        item = {}
        for output_key, raw_key in component_keys.items():
            values = raw.get(raw_key, [])
            item[output_key] = values[index] if index < len(values) else None
        components.append(item)

    result = {
        "total_cd": total_cd,
        "components": components,
        "mach": (raw.get("FC_Mach") or [None])[0],
        "altitude": (raw.get("FC_Alt") or [None])[0],
        "reference_area": (raw.get("FC_Sref") or [None])[0],
        "turbulent_cf_method": (raw.get("TurbCfEqnName") or [None])[0],
        "laminar_cf_method": (raw.get("LamCfEqnName") or [None])[0],
        "raw": raw,
    }
    if strict:
        if not total_values or not math.isfinite(total_cd) or total_cd < 0.0:
            raise ValueError("Parasite Drag не содержит конечный неотрицательный Total_CD_Total")
        if not components:
            raise ValueError("Parasite Drag не содержит ни одного компонента")
        for index, component in enumerate(components, start=1):
            value = component.get("cd")
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError(
                    f"Parasite Drag: у компонента {index} отсутствует конечный Comp_CD"
                )
        mach = result.get("mach")
        sref = result.get("reference_area")
        if not isinstance(mach, (int, float)) or not math.isfinite(float(mach)):
            raise ValueError("Parasite Drag не содержит конечный FC_Mach")
        if not isinstance(sref, (int, float)) or not math.isfinite(float(sref)) or float(sref) <= 0.0:
            raise ValueError("Parasite Drag не содержит положительный FC_Sref")
    return result


def load_machline_report(path: Path) -> dict:
    report = json.loads(path.read_text(encoding="utf-8"))
    try:
        report["total_forces"]["Cx"]
        report["total_forces"]["Cz"]
        report["input"]["flow"]["freestream_mach_number"]
        report["input"]["flow"]["freestream_velocity"]
        report["solver_results"]["solver_status_code"]
        report["solver_results"]["residual"]["norm"]
        report["solver_results"]["residual"]["max"]
    except (KeyError, TypeError) as exc:
        raise ValueError(
            "JSON не похож на полный отчёт MachLine с явным статусом и невязкой"
        ) from exc
    return report


def machline_wind_axes(report: dict) -> dict[str, float]:
    velocity = [float(value) for value in report["input"]["flow"]["freestream_velocity"]]
    if (
        len(velocity) != 3
        or not all(math.isfinite(value) for value in velocity)
        or math.hypot(velocity[0], velocity[2]) == 0.0
    ):
        raise ValueError("Не удалось определить угол атаки по freestream_velocity MachLine")
    alpha = math.atan2(velocity[2], velocity[0])
    forces = report["total_forces"]
    cx = float(forces["Cx"])
    cz = float(forces["Cz"])
    mach = float(report["input"]["flow"]["freestream_mach_number"])
    cy = float(forces.get("Cy", 0.0))
    if not all(math.isfinite(value) for value in (mach, cx, cy, cz)):
        raise ValueError("Отчёт MachLine содержит нечисловые Mach или силы")
    cd = cx * math.cos(alpha) + cz * math.sin(alpha)
    cl = -cx * math.sin(alpha) + cz * math.cos(alpha)
    if not all(math.isfinite(value) for value in (cd, cl)):
        raise ValueError("Преобразование сил MachLine дало нечисловой результат")
    return {
        "mach": mach,
        "alpha_deg": math.degrees(alpha),
        "cd": cd,
        "cl": cl,
        "cx": cx,
        "cy": cy,
        "cz": cz,
    }


def build_hybrid_report(
    machline_report_path: Path,
    vspaero_polar_path: Path,
    parasite_results_path: Path | None = None,
    include_vspaero_induced_drag: bool = True,
    base_drag: float = 0.0,
    external_drag: float = 0.0,
) -> dict:
    """Build a non-calibrated, auditable combination of solver results."""
    machline_report = load_machline_report(machline_report_path)
    ml = machline_wind_axes(machline_report)
    vsp_point = select_vspaero_point(
        parse_vspaero_polar(vspaero_polar_path), ml["mach"], ml["alpha_deg"]
    )

    induced_drag = float(vsp_point.get("CDi", 0.0)) if include_vspaero_induced_drag else 0.0
    parasite = None
    parasite_drag = 0.0
    if parasite_results_path is not None:
        parasite = parse_openvsp_results_csv(parasite_results_path)
        parasite_drag = float(parasite["total_cd"])

    warnings = []
    wake_panels = int(machline_report.get("mesh_info", {}).get("N_wake_panels", 0))
    if include_vspaero_induced_drag and wake_panels > 0:
        warnings.append(
            "MachLine-отчёт содержит след, а в сумму добавлен VSPAERO CDi: "
            "возможна доля двойного учёта индуктивного сопротивления."
        )
    if parasite is not None and ml["mach"] >= 1.0:
        warnings.append(
            "OpenVSP Parasite Drag документирован для дозвукового режима; "
            "его добавка при M >= 1 помечена как исследовательская."
        )
    if parasite is not None and not parasite["components"]:
        warnings.append(
            "OpenVSP вернул ноль компонентов Parasite Drag. Проверьте типы геометрии, "
            "Set и параметры ParasiteDragProps в VSP3."
        )
    if ml["cd"] < 0.0:
        warnings.append(
            "MachLine дал отрицательную составляющую CD в скоростных осях; "
            "проверьте ориентацию сетки, нормали и выбранное правило давления."
        )

    total_cd = ml["cd"] + induced_drag + parasite_drag + float(base_drag) + float(external_drag)
    return {
        "schema": "repairmach.hybrid-result/1.0",
        "repairmach_version": "9.0",
        "method": "non-calibrated component build-up",
        "conditions": {
            "mach": ml["mach"],
            "alpha_deg": ml["alpha_deg"],
        },
        "sources": {
            "machline_report": str(machline_report_path.resolve()),
            "vspaero_polar": str(vspaero_polar_path.resolve()),
            "openvsp_parasite_results": (
                str(parasite_results_path.resolve()) if parasite_results_path else None
            ),
        },
        "coefficients": {
            "CL": float(vsp_point["CLtot"]),
            "CD": total_cd,
            "CY": float(vsp_point.get("CStot", 0.0)),
        },
        "drag_build_up": {
            "machline_pressure_wave_candidate": ml["cd"],
            "vspaero_induced": induced_drag,
            "openvsp_viscous_form_factor": parasite_drag,
            "base": float(base_drag),
            "external": float(external_drag),
            "sum": total_cd,
        },
        "machline": {
            "wind_axes": ml,
            "wake_panels": wake_panels,
            "residual": machline_report.get("solver_results", {}).get("residual"),
        },
        "vspaero": {
            "selected_point": vsp_point,
        },
        "parasite_drag": parasite,
        "calibration": {
            "used": False,
            "constant_offset": 0.0,
        },
        "warnings": warnings,
    }
