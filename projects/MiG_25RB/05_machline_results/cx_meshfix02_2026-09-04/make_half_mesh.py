from collections import Counter, defaultdict, deque
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "mesh" / "MiG25_MeshFix02_coarse8_open_gondola_pad020_M1p2.tri"
TARGET = ROOT / "mesh" / "MiG25_MeshFix02_coarse8_open_gondola_pad020_M1p2_half_ypos.tri"
REPORT = TARGET.with_suffix(".half_mesh.json")
TOL = 1.0e-8


def read_tri(path):
    with path.open("r", encoding="ascii") as stream:
        n_vertices, n_faces = map(int, stream.readline().split())
        vertices = [tuple(map(float, stream.readline().split())) for _ in range(n_vertices)]
        faces = [tuple(int(value) - 1 for value in stream.readline().split()[:3]) for _ in range(n_faces)]
        tags = [int(stream.readline()) for _ in range(n_faces)]
    return vertices, faces, tags


def boundary_components(faces):
    edge_counts = Counter()
    for a, b, c in faces:
        for edge in ((a, b), (b, c), (c, a)):
            edge_counts[tuple(sorted(edge))] += 1
    boundary_edges = [edge for edge, count in edge_counts.items() if count == 1]
    graph = defaultdict(set)
    for a, b in boundary_edges:
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
    return boundary_edges, components


vertices, faces, tags = read_tri(SOURCE)
crossing = [
    index for index, face in enumerate(faces)
    if min(vertices[vertex][1] for vertex in face) < -TOL
    and max(vertices[vertex][1] for vertex in face) > TOL
]
if crossing:
    raise RuntimeError(f"Mesh has {len(crossing)} faces crossing y=0; refusing ragged half cut")

kept = [
    (face, tag) for face, tag in zip(faces, tags)
    if all(vertices[vertex][1] >= -TOL for vertex in face)
]
used = sorted({vertex for face, _ in kept for vertex in face})
remap = {old: new for new, old in enumerate(used)}
half_vertices = [vertices[index] for index in used]
half_faces = [tuple(remap[vertex] for vertex in face) for face, _ in kept]
half_tags = [tag for _, tag in kept]

boundary_edges, components = boundary_components(half_faces)
plane_edges = [
    edge for edge in boundary_edges
    if all(abs(half_vertices[vertex][1]) <= TOL for vertex in edge)
]
open_edges = [edge for edge in boundary_edges if edge not in set(plane_edges)]

with TARGET.open("w", encoding="ascii", newline="\n") as stream:
    stream.write(f"{len(half_vertices)} {len(half_faces)}\n")
    for x, y, z in half_vertices:
        stream.write(f"{x:.10g} {y:.10g} {z:.10g}\n")
    for a, b, c in half_faces:
        stream.write(f"{a + 1} {b + 1} {c + 1}\n")
    for tag in half_tags:
        stream.write(f"{tag}\n")

payload = {
    "source": str(SOURCE),
    "target": str(TARGET),
    "side": "y >= 0",
    "tolerance": TOL,
    "source_vertices": len(vertices),
    "source_faces": len(faces),
    "crossing_faces": len(crossing),
    "half_vertices": len(half_vertices),
    "half_faces": len(half_faces),
    "boundary_edges": len(boundary_edges),
    "symmetry_plane_boundary_edges": len(plane_edges),
    "physical_open_boundary_edges": len(open_edges),
    "boundary_component_vertex_counts": sorted(len(component) for component in components),
    "component_tags": dict(sorted(Counter(half_tags).items())),
}
REPORT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
print(json.dumps(payload, indent=2))
