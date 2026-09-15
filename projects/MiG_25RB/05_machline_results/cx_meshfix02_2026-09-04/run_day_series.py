"""Validated daytime M=1.2 Cx0 series on the corrected common mesh."""

from __future__ import annotations

import csv
import json
import math
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MACHLINE = ROOT / "RepairMach_Portable_Candidate" / "engines" / "MachLine" / "machline.exe"
MESH = "mesh/MiG25_MeshFix02_coarse8_day_common_M1p2_Apm1.tri"
MESH_AUDIT = HERE / "mesh" / "MiG25_MeshFix02_coarse8_day_common_M1p2_Apm1.audit.json"
INPUTS = HERE / "day_inputs"
REPORTS = HERE / "day_reports"
LOGS = HERE / "day_logs"
VTK = HERE / "day_vtk"
STATUS = HERE / "day_status.json"
SUMMARY = HERE / "day_run_summary.csv"
RESULT = HERE / "day_cx0_result.json"
REPORT_MD = HERE / "DAY_REPORT.md"
MACH = 1.2
SREF = 660.904
ALPHAS = (0.0, -1.0, 1.0)
TIMEOUT_S = 75.0 * 60.0


FIELDS = [
    "case_id", "mach", "alpha_deg", "status", "exit_code", "wall_runtime_s",
    "runtime_s", "system_dimension", "Cx_body", "Cy_body", "Cz_body",
    "Cdrag_freestream", "Cnormal_freestream", "residual_max", "residual_norm",
    "solver_status_code", "report_path", "log_path", "vtk_path",
]


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat()


def alpha_tag(alpha):
    return f"{alpha:+.0f}".replace("+", "p").replace("-", "m")


def case_id(alpha):
    return f"day_M1p20_A{alpha_tag(alpha)}_ise"


def write_status(**payload):
    STATUS.write_text(json.dumps({"updated_at": now_iso(), **payload}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def make_input(alpha):
    cid = case_id(alpha)
    angle = math.radians(alpha)
    input_path = INPUTS / f"{cid}.json"
    report_path = REPORTS / f"{cid}_report.json"
    log_path = LOGS / f"{cid}.log"
    vtk_path = VTK / f"{cid}_body.vtk"
    payload = {
        "flow": {
            "freestream_velocity": [100.0 * math.cos(angle), 0.0, 100.0 * math.sin(angle)],
            "gamma": 1.4,
            "freestream_mach_number": MACH,
        },
        "geometry": {
            "file": MESH,
            "spanwise_axis": "+y",
            "wake_model": {
                "trefftz_distance": 80.0,
                "wake_present": False,
                "append_wake": False,
                "wake_shedding_angle": 150.0,
            },
            "reference": {"area": SREF},
        },
        "solver": {
            "formulation": "dirichlet-morino",
            "matrix_solver": "FQRUP",
            "control_point_offset": 0.0001,
            "control_point_offset_type": "local",
        },
        "post_processing": {
            "pressure_rules": {
                "isentropic": True,
                "second-order": True,
                "slender-body": True,
                "linear": True,
            },
            "pressure_for_forces": "isentropic",
        },
        "output": {
            "body_file": f"day_vtk/{vtk_path.name}",
            "wake_file": "none",
            "control_point_file": "none",
            "report_file": f"day_reports/{report_path.name}",
        },
    }
    input_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return input_path, report_path, log_path, vtk_path


def parse_report(report_path, alpha):
    report = json.loads(report_path.read_text(encoding="utf-8"))
    solver = report["solver_results"]
    forces = report["total_forces"]
    angle = math.radians(alpha)
    cx = float(forces["Cx"])
    cy = float(forces["Cy"])
    cz = float(forces["Cz"])
    return {
        "runtime_s": float(report.get("total_runtime", 0.0)),
        "system_dimension": int(solver["system_dimension"]),
        "Cx_body": cx,
        "Cy_body": cy,
        "Cz_body": cz,
        "Cdrag_freestream": cx * math.cos(angle) + cz * math.sin(angle),
        "Cnormal_freestream": -cx * math.sin(angle) + cz * math.cos(angle),
        "residual_max": float(solver["residual"]["max"]),
        "residual_norm": float(solver["residual"]["norm"]),
        "solver_status_code": int(solver["solver_status_code"]),
    }


def append_row(row):
    new_file = not SUMMARY.exists()
    with SUMMARY.open("a", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, delimiter=";", fieldnames=FIELDS, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        writer.writerow(row)


def existing_rows():
    if not SUMMARY.exists():
        return {}
    with SUMMARY.open("r", encoding="utf-8-sig", newline="") as stream:
        return {row["case_id"]: row for row in csv.DictReader(stream, delimiter=";") if row.get("status") == "OK"}


def interpolate_zero_normal(rows):
    ordered = sorted(rows, key=lambda row: float(row["Cnormal_freestream"]))
    bracket = None
    for left, right in zip(ordered, ordered[1:]):
        n1 = float(left["Cnormal_freestream"])
        n2 = float(right["Cnormal_freestream"])
        if n1 <= 0.0 <= n2:
            bracket = (left, right)
            break
    if bracket is None:
        raise RuntimeError("The three valid points do not bracket Cnormal=0")
    left, right = bracket
    n1 = float(left["Cnormal_freestream"])
    n2 = float(right["Cnormal_freestream"])
    fraction = -n1 / (n2 - n1)
    alpha0 = float(left["alpha_deg"]) + fraction * (float(right["alpha_deg"]) - float(left["alpha_deg"]))
    cx0 = float(left["Cdrag_freestream"]) + fraction * (
        float(right["Cdrag_freestream"]) - float(left["Cdrag_freestream"])
    )
    return cx0, alpha0, left["case_id"], right["case_id"]


def main():
    for directory in (INPUTS, REPORTS, LOGS, VTK):
        directory.mkdir(parents=True, exist_ok=True)
    if not MACHLINE.is_file() or not (HERE / MESH).is_file() or not MESH_AUDIT.is_file():
        raise FileNotFoundError("MachLine, common mesh, or its audit is missing")
    audit = json.loads(MESH_AUDIT.read_text(encoding="utf-8"))
    if any(value["bad_panels"] for value in audit["validation"].values()):
        raise RuntimeError("Common mesh audit contains superinclined panels")

    started_at = now_iso()
    done = existing_rows()
    valid_rows = []
    environment = dict(os.environ)
    environment["OMP_NUM_THREADS"] = "1"

    for index, alpha in enumerate(ALPHAS, 1):
        cid = case_id(alpha)
        input_path, report_path, log_path, vtk_path = make_input(alpha)
        if cid in done and report_path.is_file():
            row = done[cid]
            valid_rows.append(row)
            continue
        write_status(
            state="running", pid=os.getpid(), started_at=started_at,
            current_case=cid, current_index=index, total_cases=len(ALPHAS),
            completed_cases=len(valid_rows), mesh=MESH,
        )
        began = time.monotonic()
        try:
            completed = subprocess.run(
                [str(MACHLINE)], input=f"{input_path.relative_to(HERE).as_posix()}\n",
                text=True, capture_output=True, cwd=HERE, env=environment, timeout=TIMEOUT_S,
            )
            exit_code = completed.returncode
            log_path.write_text(completed.stdout + "\n" + completed.stderr, encoding="utf-8")
        except subprocess.TimeoutExpired as exc:
            exit_code = -9
            log_path.write_text((exc.stdout or "") + "\n" + (exc.stderr or "") + "\nTIMEOUT\n", encoding="utf-8")
        row = {
            "case_id": cid, "mach": MACH, "alpha_deg": alpha,
            "status": "FAILED", "exit_code": exit_code,
            "wall_runtime_s": time.monotonic() - began,
            "report_path": str(report_path), "log_path": str(log_path), "vtk_path": str(vtk_path),
        }
        if exit_code == 0 and report_path.is_file():
            row.update(parse_report(report_path, alpha))
            if row["solver_status_code"] == 0 and row["residual_norm"] <= 1.0e-5:
                row["status"] = "OK"
        append_row(row)
        if row["status"] != "OK":
            write_status(
                state="diagnostic_failed", pid=os.getpid(), started_at=started_at,
                failed_case=cid, residual_norm=row.get("residual_norm"), exit_code=exit_code,
                completed_cases=len(valid_rows), total_cases=len(ALPHAS), mesh=MESH,
            )
            return
        valid_rows.append(row)

    cx0, alpha0, left_case, right_case = interpolate_zero_normal(valid_rows)
    result = {
        "state": "complete",
        "updated_at": now_iso(),
        "mach": MACH,
        "mesh": MESH,
        "mesh_audit": str(MESH_AUDIT),
        "solver": "MachLine dirichlet-morino / FQRUP / local offset 1e-4",
        "pressure_rule": "isentropic",
        "acceptance": "solver_status_code=0 and residual_norm<=1e-5",
        "Cx0_at_Cnormal_zero": cx0,
        "alpha_at_Cnormal_zero_deg": alpha0,
        "interpolation_cases": [left_case, right_case],
        "points": valid_rows,
    }
    RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Дневной контроль Cx0 МиГ-25РБ, M=1,2",
        "",
        "Все точки рассчитаны на одной сетке и приняты только при `solver_status_code=0` и `residual_norm<=1e-5`.",
        "",
        "| α, град | Cx (поток) | Cn (поток) | residual norm |",
        "|---:|---:|---:|---:|",
    ]
    for row in sorted(valid_rows, key=lambda item: float(item["alpha_deg"])):
        lines.append(
            f"| {float(row['alpha_deg']):+.1f} | {float(row['Cdrag_freestream']):.8f} | "
            f"{float(row['Cnormal_freestream']):.8f} | {float(row['residual_norm']):.3e} |"
        )
    lines += [
        "", f"Интерполяция при Cn=0: **Cx0={cx0:.8f}**, α={alpha0:+.5f}°.",
        "", "Сетка построена после минимального удаления Point-замыкания сопел; геометрия исходного VSP3 не изменялась.",
    ]
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_status(
        state="complete", pid=os.getpid(), started_at=started_at,
        completed_cases=len(valid_rows), total_cases=len(ALPHAS), mesh=MESH,
        result=str(RESULT), report=str(REPORT_MD), Cx0_at_Cnormal_zero=cx0,
        alpha_at_Cnormal_zero_deg=alpha0,
    )


if __name__ == "__main__":
    main()
