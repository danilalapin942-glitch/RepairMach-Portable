#!/usr/bin/env python3
"""Parsers and traceable hybrid-result assembly for RepairMach 9."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path


def _number(value: str):
    text = value.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return text


def parse_vspaero_polar(path: Path) -> list[dict[str, float]]:
    """Read the numeric table from a VSPAERO ``.polar`` file."""
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
    rows = []
    for line in lines[header_index + 1:]:
        values = line.split()
        if len(values) < len(headers):
            continue
        try:
            row = {name: float(values[index]) for index, name in enumerate(headers)}
        except ValueError:
            continue
        rows.append(row)
    if not rows:
        raise ValueError("В POLAR не найдено ни одной числовой точки")
    return rows


def select_vspaero_point(
    rows: list[dict[str, float]],
    mach: float,
    alpha_deg: float,
    mach_tolerance: float = 0.02,
    alpha_tolerance: float = 0.02,
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
    return point


def parse_openvsp_results_csv(path: Path) -> dict:
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

    return {
        "total_cd": total_cd,
        "components": components,
        "mach": (raw.get("FC_Mach") or [None])[0],
        "altitude": (raw.get("FC_Alt") or [None])[0],
        "reference_area": (raw.get("FC_Sref") or [None])[0],
        "turbulent_cf_method": (raw.get("TurbCfEqnName") or [None])[0],
        "laminar_cf_method": (raw.get("LamCfEqnName") or [None])[0],
        "raw": raw,
    }


def load_machline_report(path: Path) -> dict:
    report = json.loads(path.read_text(encoding="utf-8"))
    try:
        report["total_forces"]["Cx"]
        report["total_forces"]["Cz"]
        report["input"]["flow"]["freestream_mach_number"]
        report["input"]["flow"]["freestream_velocity"]
    except (KeyError, TypeError) as exc:
        raise ValueError("JSON не похож на полный отчёт MachLine") from exc
    return report


def machline_wind_axes(report: dict) -> dict[str, float]:
    velocity = [float(value) for value in report["input"]["flow"]["freestream_velocity"]]
    if len(velocity) != 3 or math.hypot(velocity[0], velocity[2]) == 0.0:
        raise ValueError("Не удалось определить угол атаки по freestream_velocity MachLine")
    alpha = math.atan2(velocity[2], velocity[0])
    forces = report["total_forces"]
    cx = float(forces["Cx"])
    cz = float(forces["Cz"])
    return {
        "mach": float(report["input"]["flow"]["freestream_mach_number"]),
        "alpha_deg": math.degrees(alpha),
        "cd": cx * math.cos(alpha) + cz * math.sin(alpha),
        "cl": -cx * math.sin(alpha) + cz * math.cos(alpha),
        "cx": cx,
        "cy": float(forces.get("Cy", 0.0)),
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
