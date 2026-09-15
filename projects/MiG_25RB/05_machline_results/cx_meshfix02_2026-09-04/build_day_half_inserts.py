"""Build a topology-preserving positive half mesh using pointed inserts."""

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
from tri_mesh import TriMesh, diagnose, read_tri, repair, write_tri  # noqa: E402


SOURCE = HERE / "mesh" / "MiG25_MeshFix02_coarse8_point_open.tri"
TARGET = HERE / "mesh" / "MiG25_MeshFix02_coarse8_day_half_inserts_M1p2_Apm1.tri"
AUDIT = TARGET.with_suffix(".audit.json")
ALPHAS = (-1.0, 0.0, 1.0)
MACH = 1.2
SAFETY_MARGIN = 0.001
LIMIT = 1.0 / MACH - SAFETY_MARGIN
OFFSETS = (0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.4, 0.8, 1.2, 2.0, 3.0, 5.0)
TOL = 1.0e-8


def flow(alpha):
    angle = math.radians(alpha)
    return math.cos(angle), 0.0, math.sin(angle)


FLOWS = [flow(alpha) for alpha in ALPHAS]


def dot(a, b):
    return sum(a[index] * b[index] for index in range(3))


def geometry(face, vertices):
    a, b, c = (vertices[index] for index in face)
    u = tuple(b[index] - a[index] for index in range(3))
    v = tuple(c[index] - a[index] for index in range(3))
    cross = (
        u[1] * v[2] - u[2] * v[1],
        u[2] * v[0] - u[0] * v[2],
        u[0] * v[1] - u[1] * v[0],
    )
    length = math.sqrt(sum(value * value for value in cross))
    return tuple(value / length for value in cross), 0.5 * length


source = read_tri(SOURCE)
crossing = [
    index for index, face in enumerate(source.faces)
    if min(source.vertices[vertex][1] for vertex in face) < -TOL
    and max(source.vertices[vertex][1] for vertex in face) > TOL
]
if crossing:
    raise RuntimeError(f"Mesh has {len(crossing)} faces crossing y=0")
kept = [
    (face, tag) for face, tag in zip(source.faces, source.components)
    if all(source.vertices[vertex][1] >= -TOL for vertex in face)
]
used = sorted({vertex for face, _ in kept for vertex in face})
remap = {old: new for new, old in enumerate(used)}
half = TriMesh(
    [source.vertices[index] for index in used],
    [tuple(remap[vertex] for vertex in face) for face, _ in kept],
    [tag for _, tag in kept],
)
half, compaction = repair(half)

offending = set()
before = {}
for alpha, current_flow in zip(ALPHAS, FLOWS):
    scan = scan_mach_criterion(half, MACH, current_flow)
    before[f"M1.2_A{alpha:+.0f}"] = summarize_scan(scan)
    for row in scan:
        if row["mach_margin"] > 0.0:
            offending.add(int(row["panel_id"]) - 1)

vertices = list(half.vertices)
faces = []
components = []
replacements = []
for face_index, face in enumerate(half.faces):
    if face_index not in offending:
        faces.append(face)
        components.append(half.components[face_index])
        continue
    original_normal, original_area = geometry(face, vertices)
    centroid = tuple(sum(vertices[vertex][axis] for vertex in face) / 3.0 for axis in range(3))
    candidates = []
    for sign in (-1.0, 1.0):
        for offset in OFFSETS:
            point = (centroid[0] + sign * offset, centroid[1], centroid[2])
            point_index = len(vertices)
            trial_vertices = vertices + [point]
            children = (
                (face[0], face[1], point_index),
                (face[1], face[2], point_index),
                (face[2], face[0], point_index),
            )
            child_geometry = [geometry(child, trial_vertices) for child in children]
            if min(dot(normal, original_normal) for normal, _ in child_geometry) <= 0.0:
                continue
            maximum = max(abs(dot(normal, current_flow)) for normal, _ in child_geometry for current_flow in FLOWS)
            if maximum <= LIMIT:
                area = sum(value for _, value in child_geometry)
                candidates.append((area, offset, sign, point, children, maximum))
    if not candidates:
        raise RuntimeError(f"No safe pointed insert found for panel {face_index + 1}")
    candidates.sort(key=lambda item: (item[0], item[1]))
    area, offset, sign, point, children, maximum = candidates[0]
    vertices.append(point)
    faces.extend(children)
    components.extend([half.components[face_index]] * 3)
    replacements.append({
        "panel_id": face_index + 1,
        "component": half.components[face_index],
        "center": centroid,
        "x_offset_ft": sign * offset,
        "original_area_ft2": original_area,
        "insert_area_ft2": area,
        "area_ratio": area / original_area,
        "maximum_projection": maximum,
    })

mesh = TriMesh(vertices, faces, components)
validation = {}
for alpha, current_flow in zip(ALPHAS, FLOWS):
    summary = summarize_scan(scan_mach_criterion(mesh, MACH, current_flow))
    validation[f"M1.2_A{alpha:+.0f}"] = summary
    if summary["bad_panels"]:
        raise RuntimeError(f"Insert mesh still has bad panels at alpha={alpha}: {summary}")
topology = diagnose(mesh)
if not topology["machline_safe_topology"]:
    raise RuntimeError(f"Insert mesh topology is unsafe: {topology}")
write_tri(TARGET, mesh)
payload = {
    "method": "positive_half_topology_preserving_pointed_inserts",
    "source": str(SOURCE), "target": str(TARGET), "mirror_about": "xz",
    "crossing_faces": len(crossing), "compaction": compaction,
    "scan_before": before, "replaced_panel_count": len(replacements),
    "added_vertex_count": len(mesh.vertices) - len(half.vertices),
    "added_face_count_net": len(mesh.faces) - len(half.faces),
    "replacements": replacements, "validation": validation, "topology": topology,
}
AUDIT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({
    "target": str(TARGET), "replaced_panel_count": len(replacements),
    "validation": validation, "topology": topology,
}, ensure_ascii=False, indent=2))
