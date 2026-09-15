"""Remove only the planar nozzle end caps from the fresh coarse-8 CFD mesh."""

from __future__ import annotations

import json
import math
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
APP = ROOT / "github_publish" / "RepairMach-Portable" / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from mach_repair import repair_mach_criterion, scan_mach_criterion, summarize_scan  # noqa: E402
from tri_mesh import TriMesh, diagnose, read_tri, repair, write_tri  # noqa: E402


SOURCE = Path(r"C:\MiG-25\MiG25_MeshFix02_coarse8_closed.tri")
OPEN_TARGET = HERE / "mesh" / "MiG25_MeshFix02_coarse8_true_open.tri"
SOLVER_TARGET = HERE / "mesh" / "MiG25_MeshFix02_coarse8_true_open_M1p2.tri"
REPORT = HERE / "mesh" / "strip_true_exit_caps_report.json"
TARGET_COMPONENTS = {2, 3}


mesh = read_tri(SOURCE)
diagonal = math.dist(
    tuple(min(point[axis] for point in mesh.vertices) for axis in range(3)),
    tuple(max(point[axis] for point in mesh.vertices) for axis in range(3)),
)
tolerance = max(diagonal * 1.0e-8, 1.0e-7)
exit_x = max(
    mesh.vertices[index][0]
    for face, component in zip(mesh.faces, mesh.components)
    if component in TARGET_COMPONENTS
    for index in face
)

kept_faces = []
kept_components = []
removed_ids = []
removed_area_proxy = 0.0
for panel_id, (face, component) in enumerate(zip(mesh.faces, mesh.components), 1):
    is_cap = component in TARGET_COMPONENTS and all(
        abs(mesh.vertices[index][0] - exit_x) <= tolerance for index in face
    )
    if is_cap:
        removed_ids.append(panel_id)
    else:
        kept_faces.append(face)
        kept_components.append(component)

if not removed_ids:
    raise RuntimeError("No planar Gondola exit-cap faces found")

opened, compact_log = repair(TriMesh(list(mesh.vertices), kept_faces, kept_components))
write_tri(OPEN_TARGET, opened)
scan_before = scan_mach_criterion(opened, 1.2)
bad_before = [item for item in scan_before if item["mach_margin"] > 0.0]
repaired, repair_log, scan_after = repair_mach_criterion(
    opened, mach=1.2, flow=(1.0, 0.0, 0.0), safety_margin=0.001, max_repairs=100
)
bad_after = [item for item in scan_after if item["mach_margin"] > 0.0]
if bad_after:
    raise RuntimeError(f"True-open mesh still has {len(bad_after)} superinclined panels at M=1.2")
write_tri(SOLVER_TARGET, repaired)

payload = {
    "source": str(SOURCE),
    "open_target": str(OPEN_TARGET),
    "solver_target": str(SOLVER_TARGET),
    "target_components": sorted(TARGET_COMPONENTS),
    "exit_x_ft": exit_x,
    "tolerance_ft": tolerance,
    "removed_cap_faces": len(removed_ids),
    "removed_panel_ids": removed_ids,
    "remaining_faces": len(opened.faces),
    "compaction": compact_log,
    "topology_open": diagnose(opened),
    "mach_1p2_before": summarize_scan(scan_before),
    "mach_repair_log": repair_log,
    "mach_1p2_after": summarize_scan(scan_after),
    "topology_solver": diagnose(repaired),
}
REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(payload, ensure_ascii=False, indent=2))
