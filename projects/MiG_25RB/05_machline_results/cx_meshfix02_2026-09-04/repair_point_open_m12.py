"""Build the M=1.2 solver copy from the minimally stripped Point closure mesh."""

import json
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
APP = ROOT / "github_publish" / "RepairMach-Portable" / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from mach_repair import repair_mach_criterion, scan_mach_criterion, summarize_scan  # noqa: E402
from tri_mesh import TriMesh, diagnose, read_tri, repair, write_tri  # noqa: E402


SOURCE = HERE / "mesh" / "MiG25_MeshFix02_coarse8_point_open.tri"
TARGET = HERE / "mesh" / "MiG25_MeshFix02_coarse8_point_open_M1p2.tri"
REPORT = TARGET.with_suffix(".repair.json")

source = read_tri(SOURCE)
before = scan_mach_criterion(source, 1.2)
repaired, repair_log, after_first_pass = repair_mach_criterion(
    source, mach=1.2, flow=(1.0, 0.0, 0.0), safety_margin=0.001, max_repairs=100
)
unresolved = [item for item in after_first_pass if item["mach_margin"] > 0.0]
for item in unresolved:
    component = repaired.components[item["panel_id"] - 1]
    if component not in {2, 3} or item["center"][0] < 65.0:
        raise RuntimeError(
            f"Refusing to remove non-closure panel {item['panel_id']} "
            f"(component={component}, center={item['center']})"
        )

# These unresolved panels belong to the remeshed Point closure fringe.  They
# are the exact analogue of the two tiny nonmanifold closure panels removed in
# the previously converged model, but the new aft closure is more finely split.
remove_indices = {item["panel_id"] - 1 for item in unresolved}
trimmed = TriMesh(
    list(repaired.vertices),
    [face for index, face in enumerate(repaired.faces) if index not in remove_indices],
    [component for index, component in enumerate(repaired.components) if index not in remove_indices],
)
trimmed, trim_compaction = repair(trimmed)
after = scan_mach_criterion(trimmed, 1.2)
bad = [item for item in after if item["mach_margin"] > 0.0]
if bad:
    raise RuntimeError(f"Closure-fringe trim left {len(bad)} superinclined panels")
write_tri(TARGET, trimmed)
payload = {
    "source": str(SOURCE),
    "target": str(TARGET),
    "scan_before": summarize_scan(before),
    "repair_log": repair_log,
    "unresolved_closure_panels_removed": [
        {
            "panel_id": item["panel_id"],
            "component": repaired.components[item["panel_id"] - 1],
            "center": item["center"],
            "area_ft2": item["area"],
            "mach_margin": item["mach_margin"],
        }
        for item in unresolved
    ],
    "unresolved_removed_area_ft2": sum(item["area"] for item in unresolved),
    "trim_compaction": trim_compaction,
    "scan_after": summarize_scan(after),
    "topology_after": diagnose(trimmed),
}
REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({
    "target": str(TARGET),
    "repairs": sum(item.get("status") == "REPAIRED" for item in repair_log),
    "unrepaired_removed_as_closure_fringe": len(unresolved),
    "unresolved_removed_area_ft2": sum(item["area"] for item in unresolved),
    "scan_after": summarize_scan(after),
    "topology_after": diagnose(trimmed),
}, ensure_ascii=False, indent=2))
