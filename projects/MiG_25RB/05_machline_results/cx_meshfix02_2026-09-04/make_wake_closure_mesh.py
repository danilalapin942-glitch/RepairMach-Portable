"""Close the two nozzle exits with long, force-excluded numerical wake cones.

The cones exist only to make the Dirichlet boundary-value problem watertight.
At supersonic speed they are placed wholly downstream and their panels receive
separate component IDs so their force contribution can be removed in the audit.
"""

from collections import defaultdict
import json
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
APP = ROOT / "github_publish" / "RepairMach-Portable" / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from mach_repair import scan_mach_criterion, summarize_scan  # noqa: E402
from tri_mesh import TriMesh, diagnose, read_tri, write_tri  # noqa: E402


SOURCE = HERE / "mesh" / "MiG25_MeshFix02_coarse8_open_gondola_pad020_M1p2.tri"
TARGET = HERE / "mesh" / "MiG25_MeshFix02_coarse8_wakecone_M1p2.tri"
REPORT = TARGET.with_suffix(".wakecone.json")
CONE_LENGTH_FT = 30.0


def directed_boundary_edges(mesh):
    occurrences = defaultdict(list)
    for face_index, (a, b, c) in enumerate(mesh.faces):
        for start, end in ((a, b), (b, c), (c, a)):
            occurrences[tuple(sorted((start, end)))].append((face_index, start, end))
    return [(start, end) for entries in occurrences.values() if len(entries) == 1 for _, start, end in entries]


def ordered_loops(edges):
    outgoing = defaultdict(list)
    for start, end in edges:
        outgoing[start].append(end)
    if any(len(ends) != 1 for ends in outgoing.values()):
        raise RuntimeError("Boundary is not a collection of consistently oriented simple loops")
    remaining = set(edges)
    loops = []
    while remaining:
        first = next(iter(remaining))
        start, current = first
        loop = [start]
        remaining.remove(first)
        while current != start:
            loop.append(current)
            next_edge = (current, outgoing[current][0])
            if next_edge not in remaining:
                raise RuntimeError("Boundary loop is open or self-intersecting")
            remaining.remove(next_edge)
            current = next_edge[1]
        loops.append(loop)
    return loops


mesh = read_tri(SOURCE)
before = diagnose(mesh)
loops = ordered_loops(directed_boundary_edges(mesh))
if sorted(len(loop) for loop in loops) != [59, 63]:
    raise RuntimeError(f"Expected the two audited nozzle loops [59, 63], got {sorted(map(len, loops))}")

vertices = list(mesh.vertices)
faces = list(mesh.faces)
components = list(mesh.components)
loop_reports = []
first_component = max(components) + 1

for loop_index, loop in enumerate(sorted(loops, key=lambda item: sum(vertices[i][1] for i in item) / len(item))):
    center_y = sum(vertices[i][1] for i in loop) / len(loop)
    center_z = sum(vertices[i][2] for i in loop) / len(loop)
    exit_x_min = min(vertices[i][0] for i in loop)
    exit_x_max = max(vertices[i][0] for i in loop)
    apex = (exit_x_max + CONE_LENGTH_FT, center_y, center_z)
    apex_index = len(vertices)
    vertices.append(apex)
    component = first_component + loop_index

    # Existing boundary uses start->end. Reverse that edge on the new face so
    # the shared edge has consistent manifold orientation.
    for start, end in zip(loop, loop[1:] + loop[:1]):
        faces.append((end, start, apex_index))
        components.append(component)

    loop_reports.append({
        "component": component,
        "vertices": len(loop),
        "exit_x_min": exit_x_min,
        "exit_x_max": exit_x_max,
        "center_y": center_y,
        "center_z": center_z,
        "apex": apex,
    })

closed = TriMesh(vertices, faces, components)
after = diagnose(closed)
scan = scan_mach_criterion(closed, 1.2)
bad = [item for item in scan if item["mach_margin"] > 0.0]
bad_closures = [item for item in bad if closed.components[item["panel_id"] - 1] >= first_component]
if after["boundary_edges"] or after["nonmanifold_edges"] or after["inconsistent_orientation_edges"]:
    raise RuntimeError(f"Numerical closure did not produce an oriented watertight mesh: {after}")
if bad:
    raise RuntimeError(f"Numerical closure mesh still has {len(bad)} superinclined panels")

write_tri(TARGET, closed)
payload = {
    "source": str(SOURCE),
    "target": str(TARGET),
    "purpose": "watertight numerical closure; exclude listed closure components from force integration",
    "cone_length_ft": CONE_LENGTH_FT,
    "closure_component_ids": [item["component"] for item in loop_reports],
    "loops": loop_reports,
    "topology_before": before,
    "topology_after": after,
    "mach_1p2_scan": summarize_scan(scan),
    "bad_closure_panels": len(bad_closures),
}
REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(payload, ensure_ascii=False, indent=2))
