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
INPUTS = HERE / "inputs"
REPORTS = HERE / "reports"
LOGS = HERE / "logs"
STATUS = HERE / "status.json"
SUMMARY = HERE / "run_summary.csv"
MACHLINE = ROOT / "RepairMach_Portable_Candidate" / "engines" / "MachLine" / "machline.exe"
MESH = "mesh/MiG25_MeshFix02_coarse5_open_pad010.tri"
SREF_FT2 = 660.904
MACH = 1.2
ALPHAS_DEG = (0.0, -2.0, 2.0)
CASE_TIMEOUT_S = 45.0 * 60.0


FIELDS = [
    "case_id", "mach", "alpha_deg", "status", "exit_code", "wall_runtime_s",
    "runtime_s", "system_dimension", "Cx_body", "Cy_body", "Cz_body",
    "Cdrag_freestream", "Cnormal_freestream", "residual_max", "residual_norm",
    "solver_status_code", "report_path", "log_path",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def alpha_tag(alpha: float) -> str:
    return f"{alpha:+.1f}".replace("+", "p").replace("-", "m").replace(".", "p")


def case_id(alpha: float) -> str:
    return f"meshfix02_M1p20_A{alpha_tag(alpha)}_ise"


def write_status(**payload) -> None:
    STATUS.write_text(
        json.dumps({"updated_at": now_iso(), **payload}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def make_input(alpha: float) -> tuple[Path, Path, Path]:
    cid = case_id(alpha)
    input_path = INPUTS / f"{cid}.json"
    report_path = REPORTS / f"{cid}_report.json"
    log_path = LOGS / f"{cid}.log"
    angle = math.radians(alpha)
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
            "reference": {"area": SREF_FT2},
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
            "body_file": "none",
            "wake_file": "none",
            "control_point_file": "none",
            "report_file": f"reports/{report_path.name}",
        },
    }
    input_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return input_path, report_path, log_path


def parse_report(path: Path, alpha: float) -> dict:
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


def existing_ok() -> set[str]:
    if not SUMMARY.is_file():
        return set()
    with SUMMARY.open("r", encoding="utf-8-sig", newline="") as stream:
        return {
            row["case_id"]
            for row in csv.DictReader(stream, delimiter=";")
            if row.get("status") == "OK"
        }


def append_row(row: dict) -> None:
    new = not SUMMARY.exists()
    with SUMMARY.open("a", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, delimiter=";", extrasaction="ignore")
        if new:
            writer.writeheader()
        writer.writerow(row)


def main() -> None:
    for directory in (INPUTS, REPORTS, LOGS):
        directory.mkdir(parents=True, exist_ok=True)
    if not MACHLINE.is_file():
        raise FileNotFoundError(MACHLINE)
    if not (HERE / MESH).is_file():
        raise FileNotFoundError(HERE / MESH)

    started_at = now_iso()
    done = existing_ok()
    failures: list[str] = []
    completed = len(done)
    environment = dict(os.environ)
    environment["OMP_NUM_THREADS"] = "1"

    for index, alpha in enumerate(ALPHAS_DEG, 1):
        cid = case_id(alpha)
        input_path, report_path, log_path = make_input(alpha)
        if cid in done and report_path.is_file():
            continue
        write_status(
            state="running",
            pid=os.getpid(),
            started_at=started_at,
            current_case=cid,
            current_index=index,
            total_cases=len(ALPHAS_DEG),
            completed_cases=completed,
            failed_cases=failures,
        )
        began = time.monotonic()
        process = subprocess.Popen(
            [str(MACHLINE)],
            cwd=HERE,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            env=environment,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            output, _ = process.communicate(f"inputs/{input_path.name}\n", timeout=CASE_TIMEOUT_S)
            timed_out = False
        except subprocess.TimeoutExpired:
            process.kill()
            output, _ = process.communicate()
            timed_out = True
        wall_runtime = time.monotonic() - began
        log_path.write_text(output or "", encoding="utf-8")
        row = {
            "case_id": cid,
            "mach": MACH,
            "alpha_deg": alpha,
            "status": "TIMEOUT" if timed_out else "NO_REPORT",
            "exit_code": process.returncode,
            "wall_runtime_s": f"{wall_runtime:.3f}",
            "report_path": str(report_path.resolve()),
            "log_path": str(log_path.resolve()),
        }
        if report_path.is_file():
            try:
                parsed = parse_report(report_path, alpha)
                row.update(parsed)
                row["status"] = (
                    "OK"
                    if parsed["solver_status_code"] == 0 and parsed["residual_norm"] <= 1.0e-5
                    else "UNRELIABLE"
                )
            except Exception as exc:
                row["status"] = f"REPORT_ERROR:{type(exc).__name__}"
        append_row(row)
        if row["status"] == "OK":
            done.add(cid)
            completed += 1
        else:
            failures.append(cid)

    state = "complete" if completed == len(ALPHAS_DEG) and not failures else "complete_with_failures"
    write_status(
        state=state,
        pid=os.getpid(),
        started_at=started_at,
        total_cases=len(ALPHAS_DEG),
        completed_cases=completed,
        failed_cases=failures,
        summary=str(SUMMARY.resolve()),
    )


if __name__ == "__main__":
    main()
