"""Diagnose the rejected alpha=0 half-mesh solve with bounded safe variants."""

import csv
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MACHLINE = ROOT / "RepairMach_Portable_Candidate" / "engines" / "MachLine" / "machline.exe"
MESH = "mesh/MiG25_MeshFix02_coarse8_day_half_common_M1p2_Apm1.tri"
AUDIT = HERE / "mesh" / "MiG25_MeshFix02_coarse8_day_half_common_M1p2_Apm1.audit.json"
STATUS = HERE / "day_half_variant_status.json"
SUMMARY = HERE / "day_half_variant_summary.csv"
INPUTS = HERE / "day_half_variant_inputs"
REPORTS = HERE / "day_half_variant_reports"
LOGS = HERE / "day_half_variant_logs"
VTK = HERE / "day_half_variant_vtk"
TIMEOUT_S = 20.0 * 60.0
VARIANTS = (
    ("neumann", "neumann-mass-flux", "FQRUP", 1.0e-4, "direct"),
    ("neumann_doublet", "neumann-doublet-only-mass-flux", "FQRUP", 1.0e-4, "direct"),
    ("source_free", "dirichlet-source-free", "FQRUP", 1.0e-4, "local"),
    ("morino_direct", "dirichlet-morino", "FQRUP", 1.0e-4, "direct"),
    ("morino_local_1e3", "dirichlet-morino", "FQRUP", 1.0e-3, "local"),
    ("morino_local_1e5", "dirichlet-morino", "FQRUP", 1.0e-5, "local"),
)
FIELDS = [
    "variant", "formulation", "matrix_solver", "offset", "offset_type", "status",
    "exit_code", "wall_runtime_s", "residual_max", "residual_norm",
    "solver_status_code", "Cx", "Cy", "Cz", "report", "log", "vtk",
]


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat()


def write_status(**payload):
    STATUS.write_text(json.dumps({"updated_at": now_iso(), **payload}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    for directory in (INPUTS, REPORTS, LOGS, VTK):
        directory.mkdir(parents=True, exist_ok=True)
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    if any(item["bad_panels"] for item in audit["validation"].values()):
        raise RuntimeError("Mesh audit contains superinclined panels")
    environment = dict(os.environ)
    environment["OMP_NUM_THREADS"] = "1"
    started = now_iso()
    rows = []
    for index, (name, formulation, solver, offset, offset_type) in enumerate(VARIANTS, 1):
        cid = f"day_half_M1p20_Ap0_{name}"
        input_path = INPUTS / f"{cid}.json"
        report_path = REPORTS / f"{cid}_report.json"
        log_path = LOGS / f"{cid}.log"
        vtk_path = VTK / f"{cid}_body.vtk"
        payload = {
            "flow": {
                "freestream_velocity": [100.0, 0.0, 0.0],
                "gamma": 1.4,
                "freestream_mach_number": 1.2,
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
                "reference": {"area": 660.904},
            },
            "solver": {
                "formulation": formulation,
                "matrix_solver": solver,
                "control_point_offset": offset,
                "control_point_offset_type": offset_type,
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
                "body_file": f"day_half_variant_vtk/{vtk_path.name}",
                "wake_file": "none",
                "control_point_file": "none",
                "report_file": f"day_half_variant_reports/{report_path.name}",
            },
        }
        input_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        write_status(
            state="running", pid=os.getpid(), started_at=started,
            current_variant=name, current_index=index, total_variants=len(VARIANTS),
            completed_variants=len(rows), mesh=MESH,
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
            "variant": name, "formulation": formulation, "matrix_solver": solver,
            "offset": offset, "offset_type": offset_type, "status": "FAILED",
            "exit_code": exit_code, "wall_runtime_s": time.monotonic() - began,
            "report": str(report_path), "log": str(log_path), "vtk": str(vtk_path),
        }
        if exit_code == 0 and report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
            result = report["solver_results"]
            forces = report["total_forces"]
            row.update({
                "residual_max": float(result["residual"]["max"]),
                "residual_norm": float(result["residual"]["norm"]),
                "solver_status_code": int(result["solver_status_code"]),
                "Cx": float(forces["Cx"]), "Cy": float(forces["Cy"]), "Cz": float(forces["Cz"]),
            })
            if row["solver_status_code"] == 0 and row["residual_norm"] <= 1.0e-5:
                row["status"] = "OK"
        rows.append(row)
        with SUMMARY.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, delimiter=";", fieldnames=FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        if row["status"] == "OK":
            write_status(
                state="complete", pid=os.getpid(), started_at=started,
                accepted_variant=name, completed_variants=len(rows), total_variants=len(VARIANTS),
                residual_norm=row["residual_norm"], mesh=MESH, summary=str(SUMMARY),
            )
            return
    write_status(
        state="diagnostic_failed", pid=os.getpid(), started_at=started,
        completed_variants=len(rows), total_variants=len(VARIANTS),
        mesh=MESH, summary=str(SUMMARY),
    )


if __name__ == "__main__":
    main()
