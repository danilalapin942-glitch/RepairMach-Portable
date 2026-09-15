"""Build a symmetry-preserving M=1.2 half mesh for alpha=-1/0/+1.

The original VSP3 and the full raw meshes are never modified.  This script
starts from the minimally opened Point-closure solver copy, keeps y >= 0,
applies only conservative Mach-criterion vertex moves, and removes only any
unresolved terminal Point-closure fringe panels.  MachLine mirrors the result
about xz during the solve.
"""

from collections import Counter, defaultdict, deque
import json
import math
import os
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
HALF_SIDE = int(os.environ.get("REPAIRMACH_HALF_SIDE", "1"))
if HALF_SIDE not in {-1, 1}:
    raise ValueError("REPAIRMACH_HALF_SIDE must be -1 or 1")
SIDE_TAG = "ypos" if HALF_SIDE > 0 else "yneg"
TARGET = HERE / "mesh" / f"MiG25_MeshFix02_coarse8_day_half_common_M1p2_Apm1_{SIDE_TAG}.tri"
REPORT = TARGET.with_suffix(".audit.json")
ALPHAS = (-1.0, 0.0, 1.0)
TOL = 1.0e-8


def flow(alpha):
    angle = math.radians(alpha)
    return math.cos(angle), 0.0, math.sin(angle)


def boundary_audit(mesh):
    edge_counts = Counter()
    for a, b, c in mesh.faces:
        for edge in ((a, b), (b, c), (c, a)):
            edge_counts[tuple(sorted(edge))] += 1
    boundary_edges = [edge for edge, count in edge_counts.items() if count == 1]
    plane_edges = [
        edge for edge in boundary_edges
        if all(abs(mesh.vertices[vertex][1]) <= TOL for vertex in edge)
    ]
    plane_edge_set = set(plane_edges)
    physical_edges = [edge for edge in boundary_edges if edge not in plane_edge_set]
    graph = defaultdict(set)
    for a, b in physical_edges:
        graph[a].add(b)
        graph[b].add(a)
    components = []
    unseen = set(graph)
    while unseen:
        start = unseen.pop()
        queue = deque([start])
        component = {start}
        while queue:
            current = queue.popleft()
            for neighbor in graph[current]:
                if neighbor not in component:
                    component.add(neighbor)
                    unseen.discard(neighbor)
                    queue.append(neighbor)
        components.append(component)
    return {
        "boundary_edges": len(boundary_edges),
        "symmetry_plane_boundary_edges": len(plane_edges),
        "physical_open_boundary_edges": len(physical_edges),
        "physical_boundary_component_vertex_counts": sorted(len(item) for item in components),
    }


source = read_tri(SOURCE)
crossing = [
    index for index, face in enumerate(source.faces)
    if min(source.vertices[vertex][1] for vertex in face) < -TOL
    and max(source.vertices[vertex][1] for vertex in face) > TOL
]
if crossing:
    raise RuntimeError(f"Mesh has {len(crossing)} faces crossing y=0; refusing ragged half cut")

kept = [
    (face, tag) for face, tag in zip(source.faces, source.components)
    if all(HALF_SIDE * source.vertices[vertex][1] >= -TOL for vertex in face)
]
used = sorted({vertex for face, _ in kept for vertex in face})
remap = {old: new for new, old in enumerate(used)}
mesh = TriMesh(
    [source.vertices[index] for index in used],
    [tuple(remap[vertex] for vertex in face) for face, _ in kept],
    [tag for _, tag in kept],
)
mesh, half_compaction = repair(mesh)

before = {
    f"M1.2_A{alpha:+.0f}": summarize_scan(scan_mach_criterion(mesh, 1.2, flow(alpha)))
    for alpha in ALPHAS
}
repair_log = []
for pass_index in range(3):
    for alpha in ALPHAS:
        mesh, entries, _ = repair_mach_criterion(
            mesh,
            mach=1.2,
            flow=flow(alpha),
            safety_margin=0.001,
            max_repairs=100,
        )
        repair_log.append({
            "pass": pass_index + 1,
            "alpha_deg": alpha,
            "repairs": sum(item.get("status") == "REPAIRED" for item in entries),
            "unresolved": sum(item.get("status") != "REPAIRED" for item in entries),
            "entries": entries,
        })

unresolved = {}
for alpha in ALPHAS:
    for item in scan_mach_criterion(mesh, 1.2, flow(alpha)):
        if item["mach_margin"] <= 0.0:
            continue
        panel_id = item["panel_id"]
        component = mesh.components[panel_id - 1]
        expected_component = 3 if HALF_SIDE > 0 else 2
        if component != expected_component or item["center"][0] < 65.0:
            raise RuntimeError(
                f"Refusing to remove non-terminal panel {panel_id}: "
                f"component={component}, center={item['center']}"
            )
        entry = unresolved.setdefault(panel_id, {
            "panel_id": panel_id,
            "component": component,
            "center": item["center"],
            "area_ft2": item["area"],
            "trigger_alphas_deg": [],
        })
        entry["trigger_alphas_deg"].append(alpha)

remove_indices = {panel_id - 1 for panel_id in unresolved}
mesh = TriMesh(
    list(mesh.vertices),
    [face for index, face in enumerate(mesh.faces) if index not in remove_indices],
    [tag for index, tag in enumerate(mesh.components) if index not in remove_indices],
)
mesh, final_compaction = repair(mesh)

validation = {}
for alpha in ALPHAS:
    summary = summarize_scan(scan_mach_criterion(mesh, 1.2, flow(alpha)))
    validation[f"M1.2_A{alpha:+.0f}"] = summary
    if summary["bad_panels"]:
        raise RuntimeError(f"Half common mesh still has bad panels at alpha={alpha}: {summary}")

topology = diagnose(mesh)
if not topology["machline_safe_topology"]:
    raise RuntimeError(f"Half common mesh topology is unsafe: {topology}")

write_tri(TARGET, mesh)
payload = {
    "method": "symmetry_preserving_half_mesh_from_minimal_Point_closure_strip",
    "retained_side": "y >= 0" if HALF_SIDE > 0 else "y <= 0",
    "source": str(SOURCE),
    "target": str(TARGET),
    "mirror_about": "xz",
    "crossing_faces": len(crossing),
    "half_compaction": half_compaction,
    "scan_before": before,
    "repair_log": repair_log,
    "unresolved_terminal_panels_removed": [unresolved[key] for key in sorted(unresolved)],
    "unresolved_removed_area_ft2": sum(item["area_ft2"] for item in unresolved.values()),
    "final_compaction": final_compaction,
    "validation": validation,
    "topology": topology,
    "boundaries": boundary_audit(mesh),
}
REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({
    "target": str(TARGET),
    "removed_terminal_panels": len(unresolved),
    "validation": validation,
    "topology": topology,
    "boundaries": payload["boundaries"],
}, ensure_ascii=False, indent=2))
