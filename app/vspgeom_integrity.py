"""Read-only completeness gate for OpenVSP v3 single-level native meshes.

Checks both representations, not CAD validity or aerodynamic qualification.
No triangulation is generated or repaired here. Unsupported formats fail closed.
The caller supplies thick surface IDs from an independently verified inventory;
names, consecutive IDs, and thin-wing boundaries must not imply a closed body.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
from math import isfinite
from pathlib import Path


class MeshFormatError(ValueError):
    pass


def _require(condition, message):
    if not condition:
        raise MeshFormatError(message)


class _Lines:
    def __init__(self, text):
        self.lines = [line.strip() for line in text.splitlines() if line.strip()]
        self.pos = 0

    def row(self, cast=str, count=None):
        _require(self.pos < len(self.lines), "Truncated VSPGEOM")
        line = self.lines[self.pos]
        self.pos += 1
        try:
            row = [cast(v) for v in line.split()]
        except (ValueError, OverflowError) as exc:
            raise MeshFormatError(f"Invalid token at line {self.pos}") from exc
        _require(count is None or len(row) == count, f"Invalid record length at line {self.pos}")
        return row


def _parse(data):
    reader = _Lines(data.decode("utf-8-sig"))
    _require(reader.row() == ["#", "vspgeom", "v3"], "Unsupported VSPGEOM version")
    _require(reader.row(int, 1) == [1], "Unsupported VSPGEOM refinement levels")
    nn, nf, nw = reader.row(int, 3)
    _require(nn > 0 and nf > 0 and nw >= 0, "Invalid mesh counts")
    _require(nn + 4 * nf <= len(reader.lines), "Mesh counts exceed available records")
    points = [reader.row(float, 3) for _ in range(nn)]
    _require(all(isfinite(v) for p in points for v in p), "Nonfinite mesh coordinates")
    _require(reader.row(int, 1) == [nf], "Polygon count mismatch")
    polygons = []
    for _ in range(nf):
        row = reader.row(int)
        _require(bool(row) and row[0] >= 3 and len(row) == row[0] + 1, "Invalid polygon record")
        nodes = row[1:]
        _require(len(set(nodes)) == len(nodes), "Repeated polygon vertex")
        _require(all(1 <= v <= nn for v in nodes), "Polygon node out of range")
        polygons.append(nodes)
    tags = []
    for poly in polygons:
        row = reader.row()
        _require(len(row) == 2 + 2 * len(poly), "Invalid polygon UV record")
        try:
            sid, patch = map(int, row[:2])
            uv = list(map(float, row[2:]))
        except ValueError as exc:
            raise MeshFormatError("Invalid polygon metadata") from exc
        _require(sid > 0 and patch > 0 and all(map(isfinite, uv)), "Invalid polygon metadata values")
        tags.append((sid, patch))
    for i in range(1, nf + 1):
        _require(reader.row(int, 2) == [i, i], "Invalid single-level refinement transfer")
    _require(reader.row(int, 1) == [nw], "Wake count mismatch")
    for _ in range(nw):
        row = reader.row(int)
        _require(bool(row) and row[0] > 0, "Invalid wake record")
        n = row[0]
        while len(row) < n + 2:
            row += reader.row(int)
        _require(len(row) == n + 2, "Invalid wake record length")
        _require(all(1 <= v <= nn for v in row[2:]), "Wake node out of range")
    triangles = []
    for i in range(1, nf + 1):
        row = reader.row(int)
        _require(len(row) >= 2 and row[0] == i and row[1] >= 0, "Invalid triangle owner/count")
        _require(len(row) == 2 + 3 * row[1], "Invalid triangle record length")
        children = [row[j:j + 3] for j in range(2, len(row), 3)]
        _require(all(1 <= v <= nn for tri in children for v in tri), "Triangle node out of range")
        triangles.append(children)
    # V3 contains triangle-UV records following the supplied triangles.
    for i, children in enumerate(triangles, 1):
        row = reader.row()
        _require(len(row) == 3 + 6 * len(children), "Invalid triangle UV record length")
        try:
            head = list(map(int, row[:3]))
            uv = list(map(float, row[3:]))
        except ValueError as exc:
            raise MeshFormatError("Invalid triangle UV metadata") from exc
        _require(head == [i, *tags[i - 1]] and all(map(isfinite, uv)), "Triangle UV metadata mismatch")
    _require(reader.pos == len(reader.lines), "Unexpected trailing mesh records")
    return points, polygons, tags, triangles


def _edges(faces):
    directed = Counter((a, b) for face in faces for a, b in zip(face, face[1:] + face[:1]))
    edges = Counter()
    for (a, b), count in directed.items():
        edges[tuple(sorted((a, b)))] += count
    boundary = Counter({edge: count for edge, count in edges.items() if count == 1})
    bad_orientation = sum(count == 2 and directed[(a, b)] != directed[(b, a)]
                          for (a, b), count in edges.items())
    return directed, edges, boundary, bad_orientation


def _area_vector(nodes, points):
    # Translation about one vertex avoids catastrophic cancellation far from zero.
    origin = points[nodes[0] - 1]
    p = [[points[n - 1][j] - origin[j] for j in range(3)] for n in nodes]
    vector = [0., 0., 0.]
    for a, b in zip(p, p[1:] + p[:1]):
        vector[0] += (a[1] * b[2] - a[2] * b[1]) / 2
        vector[1] += (a[2] * b[0] - a[0] * b[2]) / 2
        vector[2] += (a[0] * b[1] - a[1] * b[0]) / 2
    return vector


def validate_vspgeom(path: Path, *, thick_surface_ids=()):
    """Fail closed, returning JSON-safe evidence without modifying the input.

    Closure is enforced only for explicitly supplied thick surfaces. Every face
    is checked for complete, oriented triangulation, including thin surfaces.
    Small nonzero faces are not discarded with a dimensional tolerance.
    """
    result = {"schema": "repairmach.vspgeom-integrity/1", "valid": False,
              "qualification_granted": False, "path": str(Path(path).resolve()),
              "errors": [], "face_errors": [], "thick_topology": []}
    try:
        ids = list(thick_surface_ids)
        _require(all(type(i) is int and i > 0 for i in ids) and len(ids) == len(set(ids)),
                 "Thick surface IDs must be unique positive integers")
        data = Path(path).read_bytes()
        result["sha256"] = sha256(data).hexdigest()
        points, polygons, tags, children = _parse(data)
        result.update(nodes=len(points), polygons=len(polygons), triangles=sum(map(len, children)))
        surface_ids = {sid for sid, _ in tags}
        _require(set(ids) <= surface_ids, "Requested thick surface is absent")
        for i, (poly, tris) in enumerate(zip(polygons, children), 1):
            faults = []
            if len(tris) != len(poly) - 2:
                faults.append("triangle_count")
            if any(len(set(tri)) != 3 or not set(tri) <= set(poly) for tri in tris):
                faults.append("invalid_triangle_vertices")
            if len({tuple(sorted(tri)) for tri in tris}) != len(tris):
                faults.append("duplicate_triangle")
            pd, pe, _, _ = _edges([poly])
            td, te, boundary, orientation = _edges(tris)
            if boundary != pe or any(n > 2 for n in te.values()):
                faults.append("boundary_or_manifold")
            if orientation or any(td[e] != 1 or td[e[::-1]] != 0 for e in pd):
                faults.append("orientation")
            pa = _area_vector(poly, points)
            if sum(v * v for v in pa) == 0:
                faults.append("zero_polygon_area")
            for tri in tris:
                ta = _area_vector(tri, points)
                if sum(v * v for v in ta) == 0:
                    faults.append("zero_triangle_area")
                    break
                if sum(a * b for a, b in zip(pa, ta)) <= 0:
                    faults.append("folded_or_reversed_triangle")
                    break
            if faults:
                result["face_errors"].append({"face": i, "surface_id": tags[i - 1][0],
                    "errors": faults, "vertices": len(poly), "triangles": len(tris)})
        for sid in ids:
            faces = [poly for poly, tag in zip(polygons, tags) if tag[0] == sid]
            tris = [tri for items, tag in zip(children, tags) if tag[0] == sid for tri in items]
            summary = {"surface_id": sid}
            for label, group in (("polygon", faces), ("triangle", tris)):
                _, edges, boundary, orientation = _edges(group)
                summary[label] = {"boundary_edges": len(boundary),
                                  "nonmanifold_edges": sum(n > 2 for n in edges.values()),
                                  "inconsistent_edges": orientation}
            result["thick_topology"].append(summary)
        if result["face_errors"]:
            result["errors"].append("Incomplete or invalid per-polygon triangulation")
        if any(any(v for v in item[label].values()) for item in result["thick_topology"]
               for label in ("polygon", "triangle")):
            result["errors"].append("Thick surface is not closed and consistently oriented in both representations")
        result["valid"] = not result["errors"]
    except (OSError, UnicodeError, ValueError, TypeError, OverflowError) as exc:
        result["errors"].append(str(exc))
    return result
