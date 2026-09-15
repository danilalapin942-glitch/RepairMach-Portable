"""Create one audited M=1.2 mesh valid for alpha=-1/0/+1 degrees."""

import json
import math
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
APP = ROOT / "github_publish" / "RepairMach-Portable" / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from mach_repair import scan_mach_criterion, summarize_scan  # noqa: E402
from tri_mesh import TriMesh, diagnose, repair, read_tri, write_tri  # noqa: E402


SOURCE = HERE / "mesh" / "MiG25_MeshFix02_coarse8_point_open_M1p2.tri"
TARGET = HERE / "mesh" / "MiG25_MeshFix02_coarse8_day_common_M1p2_Apm1.tri"
REPORT = TARGET.with_suffix(".audit.json")
ALPHAS = (-1.0, 0.0, 1.0)

mesh = read_tri(SOURCE)
removed = {}
for alpha in ALPHAS:
    angle = math.radians(alpha)
    flow = (math.cos(angle), 0.0, math.sin(angle))
    for item in scan_mach_criterion(mesh, 1.2, flow):
        if item["mach_margin"] <= 0.0:
            continue
        panel_id = item["panel_id"]
        component = mesh.components[panel_id - 1]
        if component not in {2, 3} or item["center"][0] < 64.9:
            raise RuntimeError(
                f"Refusing to omit non-closure panel {panel_id}: component={component}, center={item['center']}"
            )
        entry = removed.setdefault(panel_id, {
            "panel_id": panel_id,
            "component": component,
            "center": item["center"],
            "area_ft2": item["area"],
            "trigger_alphas_deg": [],
        })
        entry["trigger_alphas_deg"].append(alpha)

remove_indices = {panel_id - 1 for panel_id in removed}
conditioned = TriMesh(
    list(mesh.vertices),
    [face for index, face in enumerate(mesh.faces) if index not in remove_indices],
    [component for index, component in enumerate(mesh.components) if index not in remove_indices],
)
conditioned, compact_log = repair(conditioned)
validation = {}
for alpha in ALPHAS:
    angle = math.radians(alpha)
    flow = (math.cos(angle), 0.0, math.sin(angle))
    summary = summarize_scan(scan_mach_criterion(conditioned, 1.2, flow))
    validation[f"M1.2_A{alpha:+.0f}"] = summary
    if summary["bad_panels"]:
        raise RuntimeError(f"Common mesh still has bad panels at alpha={alpha}: {summary}")

write_tri(TARGET, conditioned)
payload = {
    "method": "common_M1p2_alpha_envelope_after_minimal_Point_closure_strip",
    "source": str(SOURCE),
    "target": str(TARGET),
    "removed_panel_count": len(removed),
    "removed_area_ft2": sum(item["area_ft2"] for item in removed.values()),
    "removed_area_fraction_Sref": sum(item["area_ft2"] for item in removed.values()) / 660.904,
    "removed_panels": [removed[key] for key in sorted(removed)],
    "compaction": compact_log,
    "topology": diagnose(conditioned),
    "validation": validation,
}
REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(payload, ensure_ascii=False, indent=2))
