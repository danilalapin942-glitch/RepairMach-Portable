"""Run the audited M=1.2 Cx0 series on a symmetry-preserving half mesh."""

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
MESH = "mesh/MiG25_MeshFix02_coarse8_day_half_inserts_M1p2_Apm1.tri"
AUDIT = HERE / "mesh" / "MiG25_MeshFix02_coarse8_day_half_inserts_M1p2_Apm1.audit.json"
INPUTS = HERE / "day_insert_half_inputs"
REPORTS = HERE / "day_insert_half_reports"
LOGS = HERE / "day_insert_half_logs"
VTK = HERE / "day_insert_half_vtk"
STATUS = HERE / "day_insert_half_status.json"
SUMMARY = HERE / "day_insert_half_run_summary.csv"
RESULT = HERE / "day_insert_half_cx0_result.json"
REPORT_MD = HERE / "DAY_INSERT_HALF_REPORT.md"
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


def tag(alpha):
    return f"{alpha:+.0f}".replace("+", "p").replace("-", "m")


def write_status(**payload):
    STATUS.write_text(json.dumps({"updated_at": now_iso(), **payload}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def make_input(alpha):
    cid = f"day_half_M1p20_A{tag(alpha)}_ise"
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
            "mirror_about": "xz",
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
            "body_file": f"day_insert_half_vtk/{vtk_path.name}",
            "wake_file": "none",
            "control_point_file": "none",
            "report_file": f"day_insert_half_reports/{report_path.name}",
        },
    }
    input_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return cid, input_path, report_path, log_path, vtk_path


def parse_report(path, alpha):
    report = json.loads(path.read_text(encoding="utf-8"))
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
    new = not SUMMARY.exists()
    with SUMMARY.open("a", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, delimiter=";", fieldnames=FIELDS, extrasaction="ignore")
        if new:
            writer.writeheader()
        writer.writerow(row)


def interpolate(rows):
    ordered = sorted(rows, key=lambda row: float(row["Cnormal_freestream"]))
    for left, right in zip(ordered, ordered[1:]):
        n1 = float(left["Cnormal_freestream"])
        n2 = float(right["Cnormal_freestream"])
        if n1 <= 0.0 <= n2:
            fraction = -n1 / (n2 - n1)
            alpha = float(left["alpha_deg"]) + fraction * (float(right["alpha_deg"]) - float(left["alpha_deg"]))
            cx0 = float(left["Cdrag_freestream"]) + fraction * (
                float(right["Cdrag_freestream"]) - float(left["Cdrag_freestream"])
            )
            return cx0, alpha, [left["case_id"], right["case_id"]]
    raise RuntimeError("Accepted points do not bracket Cnormal=0")


def main():
    for directory in (INPUTS, REPORTS, LOGS, VTK):
        directory.mkdir(parents=True, exist_ok=True)
    if not MACHLINE.is_file() or not (HERE / MESH).is_file() or not AUDIT.is_file():
        raise FileNotFoundError("MachLine, half mesh, or audit is missing")
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    if audit.get("mirror_about") != "xz" or any(item["bad_panels"] for item in audit["validation"].values()):
        raise RuntimeError("Half-mesh audit is not valid for the requested cases")

    started_at = now_iso()
    environment = dict(os.environ)
    environment["OMP_NUM_THREADS"] = "1"
    valid = []
    for index, alpha in enumerate(ALPHAS, 1):
        cid, input_path, report_path, log_path, vtk_path = make_input(alpha)
        write_status(
            state="running", pid=os.getpid(), started_at=started_at,
            current_case=cid, current_index=index, total_cases=len(ALPHAS),
            completed_cases=len(valid), mesh=MESH, mirror_about="xz",
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
                failed_case=cid, completed_cases=len(valid), total_cases=len(ALPHAS),
                residual_norm=row.get("residual_norm"), exit_code=exit_code,
                mesh=MESH, mirror_about="xz",
            )
            return
        valid.append(row)

    cx0, alpha0, cases = interpolate(valid)
    result = {
        "state": "complete", "updated_at": now_iso(), "mach": MACH,
        "mesh": MESH, "mesh_audit": str(AUDIT), "mirror_about": "xz",
        "solver": "MachLine dirichlet-morino / FQRUP / local offset 1e-4",
        "pressure_rule": "isentropic",
        "acceptance": "solver_status_code=0 and residual_norm<=1e-5; no superinclined panels",
        "Cx0_at_Cnormal_zero": cx0,
        "alpha_at_Cnormal_zero_deg": alpha0,
        "interpolation_cases": cases,
        "points": valid,
    }
    RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Дневной симметричный контроль Cx0 МиГ-25РБ, M=1,2", "",
        "Исходный VSP3 не изменялся. Расчётная полумодель зеркалировалась MachLine относительно плоскости xz.", "",
        "| α, град | Cx (поток) | Cn (поток) | residual norm |", "|---:|---:|---:|---:|",
    ]
    for row in sorted(valid, key=lambda item: float(item["alpha_deg"])):
        lines.append(
            f"| {float(row['alpha_deg']):+.1f} | {float(row['Cdrag_freestream']):.8f} | "
            f"{float(row['Cnormal_freestream']):.8f} | {float(row['residual_norm']):.3e} |"
        )
    lines += ["", f"Интерполяция при Cn=0: **Cx0={cx0:.8f}**, α={alpha0:+.5f}°."]
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_status(
        state="complete", pid=os.getpid(), started_at=started_at,
        completed_cases=len(valid), total_cases=len(ALPHAS), mesh=MESH, mirror_about="xz",
        result=str(RESULT), report=str(REPORT_MD), Cx0_at_Cnormal_zero=cx0,
        alpha_at_Cnormal_zero_deg=alpha0,
    )


if __name__ == "__main__":
    main()
