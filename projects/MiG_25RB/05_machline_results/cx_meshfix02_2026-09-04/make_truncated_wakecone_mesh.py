"""Move each nozzle opening downstream through a long, nearly closed wake tube.

This keeps the model topologically open (avoiding the constant-doublet null
mode of a fully closed Dirichlet body) while reducing the open boundary to a
millimetre-scale numerical hole far downstream.  Added panels have dedicated
component IDs and must be excluded from force integration.
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
TARGET = HERE / "mesh" / "MiG25_MeshFix02_coarse8_truncated_wakecone_M1p2.tri"
REPORT = TARGET.with_suffix(".truncated_wakecone.json")
LENGTH_FT = 30.0
EXIT_SCALE = 0.001


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
        raise RuntimeError("Boundary is not a collection of simple oriented loops")
    remaining = set(edges)
    loops = []
    while remaining:
        start, current = next(iter(remaining))
        remaining.remove((start, current))
        loop = [start]
        while current != start:
            loop.append(current)
            edge = (current, outgoing[current][0])
            if edge not in remaining:
                raise RuntimeError("Boundary loop is open or self-intersecting")
            remaining.remove(edge)
            current = edge[1]
        loops.append(loop)
    return loops


mesh = read_tri(SOURCE)
loops = ordered_loops(directed_boundary_edges(mesh))
if sorted(map(len, loops)) != [59, 63]:
    raise RuntimeError(f"Expected nozzle loops [59, 63], got {sorted(map(len, loops))}")

vertices = list(mesh.vertices)
faces = list(mesh.faces)
components = list(mesh.components)
first_component = max(components) + 1
loop_reports = []

for loop_index, loop in enumerate(sorted(loops, key=lambda item: sum(vertices[i][1] for i in item) / len(item))):
    center_y = sum(vertices[i][1] for i in loop) / len(loop)
    center_z = sum(vertices[i][2] for i in loop) / len(loop)
    exit_x = max(vertices[i][0] for i in loop) + LENGTH_FT
    downstream = []
    for old in loop:
        _, y, z = vertices[old]
        downstream.append(len(vertices))
        vertices.append((exit_x, center_y + EXIT_SCALE * (y - center_y), center_z + EXIT_SCALE * (z - center_z)))

    component = first_component + loop_index
    for index, (start, end) in enumerate(zip(loop, loop[1:] + loop[:1])):
        new_start = downstream[index]
        new_end = downstream[(index + 1) % len(loop)]
        faces.append((end, start, new_start))
        components.append(component)
        faces.append((end, new_start, new_end))
        components.append(component)

    radii = [((vertices[i][1]-center_y)**2 + (vertices[i][2]-center_z)**2)**0.5 for i in downstream]
    loop_reports.append({
        "component": component,
        "source_vertices": len(loop),
        "downstream_x": exit_x,
        "center_y": center_y,
        "center_z": center_z,
        "downstream_radius_max_ft": max(radii),
    })

extended = TriMesh(vertices, faces, components)
topology = diagnose(extended)
scan = scan_mach_criterion(extended, 1.2)
bad = [item for item in scan if item["mach_margin"] > 0.0]
if topology["boundary_edges"] != 122 or topology["nonmanifold_edges"] or topology["inconsistent_orientation_edges"]:
    raise RuntimeError(f"Unexpected topology after wake extension: {topology}")
if bad:
    raise RuntimeError(f"Wake extension has {len(bad)} superinclined panels")

write_tri(TARGET, extended)
payload = {
    "source": str(SOURCE),
    "target": str(TARGET),
    "purpose": "move open nozzle boundaries far downstream and shrink them; exclude added components from forces",
    "length_ft": LENGTH_FT,
    "exit_scale": EXIT_SCALE,
    "force_exclusion_component_ids": [item["component"] for item in loop_reports],
    "loops": loop_reports,
    "topology": topology,
    "mach_1p2_scan": summarize_scan(scan),
}
REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(payload, ensure_ascii=False, indent=2))
