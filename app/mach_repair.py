#!/usr/bin/env python3
"""Mach-criterion diagnostics and conservative vertex repair for TRI meshes."""

from __future__ import annotations

import csv
import math
from pathlib import Path

from tri_mesh import TriMesh


def _length(vector):
    return math.sqrt(sum(value * value for value in vector))


def _normalize(vector):
    length = _length(vector)
    return tuple(value / length for value in vector) if length else (0.0, 0.0, 0.0)


def _dot(first, second):
    return sum(a * b for a, b in zip(first, second))


def _normal_area_center(face, vertices):
    a, b, c = (vertices[index] for index in face)
    u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    v = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    cross = (
        u[1] * v[2] - u[2] * v[1],
        u[2] * v[0] - u[0] * v[2],
        u[0] * v[1] - u[1] * v[0],
    )
    center = tuple((a[axis] + b[axis] + c[axis]) / 3.0 for axis in range(3))
    return _normalize(cross), _length(cross) / 2.0, center


def build_adjacency(mesh: TriMesh):
    vertex_faces = [[] for _ in mesh.vertices]
    for face_index, face in enumerate(mesh.faces):
        for vertex_index in face:
            vertex_faces[vertex_index].append(face_index)
    neighbors = []
    for face_index, face in enumerate(mesh.faces):
        candidates = set()
        for vertex_index in face:
            candidates.update(vertex_faces[vertex_index])
        neighbors.append(sorted(
            candidate for candidate in candidates
            if candidate != face_index and len(set(face) & set(mesh.faces[candidate])) >= 2
        ))
    return vertex_faces, neighbors


def panel_diagnostic(mesh: TriMesh, face_index: int, neighbors, mach: float, flow):
    face = mesh.faces[face_index]
    normal, area, center = _normal_area_center(face, mesh.vertices)
    neighbor_normals = [
        _normal_area_center(mesh.faces[index], mesh.vertices)[0]
        for index in neighbors[face_index]
    ]
    if neighbor_normals:
        average = _normalize(tuple(
            sum(item[axis] for item in neighbor_normals) / len(neighbor_normals)
            for axis in range(3)
        ))
    else:
        average = normal
    cosine = max(-1.0, min(1.0, _dot(normal, average)))
    neighbor_angle = math.degrees(math.acos(cosine)) if _length(normal) and _length(average) else 180.0
    mach_value = abs(_dot(normal, _normalize(flow)))
    return {
        "panel_id": face_index + 1,
        "vertices": [index + 1 for index in face],
        "center": center,
        "normal": normal,
        "area": area,
        "neighbor_count": len(neighbors[face_index]),
        "neighbor_angle": neighbor_angle,
        "mach_value": mach_value,
        "mach_margin": mach_value - 1.0 / mach,
    }


def scan_mach_criterion(mesh: TriMesh, mach: float, flow=(1.0, 0.0, 0.0)):
    if mach <= 1.0:
        raise ValueError("Mach-диагностика применяется только при M > 1")
    if _length(flow) == 0.0:
        raise ValueError("Направление потока не может быть нулевым")
    _, neighbors = build_adjacency(mesh)
    items = [panel_diagnostic(mesh, index, neighbors, mach, flow) for index in range(len(mesh.faces))]
    items.sort(key=lambda item: item["mach_margin"], reverse=True)
    return items


def summarize_scan(items, near_margin=0.01):
    bad = [item for item in items if item["mach_margin"] > 0.0]
    near = [item for item in items if -near_margin < item["mach_margin"] <= 0.0]
    return {
        "panels": len(items),
        "bad_panels": len(bad),
        "near_limit_panels": len(near),
        "maximum_margin": items[0]["mach_margin"] if items else 0.0,
        "maximum_value": items[0]["mach_value"] if items else 0.0,
        "bad_panel_ids": [item["panel_id"] for item in bad[:200]],
    }


def write_scan_csv(path: Path, diagnostics) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow([
            "panel_id", "v1", "v2", "v3", "center_x", "center_y", "center_z",
            "normal_x", "normal_y", "normal_z", "area", "neighbor_count",
            "neighbor_angle_deg", "mach_value_abs_n_dot_flow", "mach_margin_value_minus_1_over_M",
        ])
        for item in diagnostics:
            writer.writerow([
                item["panel_id"], *item["vertices"], *item["center"], *item["normal"],
                item["area"], item["neighbor_count"], item["neighbor_angle"],
                item["mach_value"], item["mach_margin"],
            ])


def _candidate_breaks_good_panels(mesh, affected, neighbors, mach, flow, old_margins):
    return any(
        old_margins[index] <= 0.0
        and panel_diagnostic(mesh, index, neighbors, mach, flow)["mach_margin"] > 0.0
        for index in affected
    )


def _find_candidate(mesh, face_index, vertex_faces, neighbors, mach, flow, safety_margin):
    target = 1.0 / mach - safety_margin
    directions = (
        ("+X", 1.0, 0.0, 0.0), ("-X", -1.0, 0.0, 0.0),
        ("+Y", 0.0, 1.0, 0.0), ("-Y", 0.0, -1.0, 0.0),
        ("+Z", 0.0, 0.0, 1.0), ("-Z", 0.0, 0.0, -1.0),
    )
    steps = (1e-6, 1e-5, 1e-4, 5e-4, 1e-3, 2e-3, 5e-3, 1e-2, 2e-2, 5e-2)
    old_margins = {
        index: panel_diagnostic(mesh, index, neighbors, mach, flow)["mach_margin"]
        for vertex_index in mesh.faces[face_index]
        for index in vertex_faces[vertex_index]
    }
    passing = []
    for vertex_index in mesh.faces[face_index]:
        original = mesh.vertices[vertex_index]
        affected = vertex_faces[vertex_index]
        for direction_name, dx, dy, dz in directions:
            for step in steps:
                mesh.vertices[vertex_index] = (
                    original[0] + dx * step,
                    original[1] + dy * step,
                    original[2] + dz * step,
                )
                result = panel_diagnostic(mesh, face_index, neighbors, mach, flow)
                breaks_good = _candidate_breaks_good_panels(
                    mesh, affected, neighbors, mach, flow, old_margins
                )
                if result["mach_value"] <= target and not breaks_good:
                    passing.append({
                        "vertex_index": vertex_index,
                        "direction": direction_name,
                        "step": step,
                        "coordinates": mesh.vertices[vertex_index],
                        "mach_value": result["mach_value"],
                        "mach_margin": result["mach_margin"],
                        "neighbor_angle": result["neighbor_angle"],
                    })
                mesh.vertices[vertex_index] = original
    if not passing:
        return None
    passing.sort(key=lambda item: (item["step"], item["neighbor_angle"], abs(item["mach_value"] - target)))
    return passing[0]


def repair_mach_criterion(
    source: TriMesh,
    mach: float,
    flow=(1.0, 0.0, 0.0),
    safety_margin=0.001,
    max_repairs=100,
):
    mesh = TriMesh(list(source.vertices), list(source.faces), list(source.components))
    vertex_faces, neighbors = build_adjacency(mesh)
    initial = [panel_diagnostic(mesh, index, neighbors, mach, flow) for index in range(len(mesh.faces))]
    order = sorted(range(len(mesh.faces)), key=lambda index: initial[index]["mach_margin"], reverse=True)
    log = []
    repaired_count = 0
    for face_index in order:
        if repaired_count >= max_repairs:
            break
        before = panel_diagnostic(mesh, face_index, neighbors, mach, flow)
        if before["mach_margin"] <= 0.0:
            continue
        candidate = _find_candidate(mesh, face_index, vertex_faces, neighbors, mach, flow, safety_margin)
        if candidate is None:
            log.append({"panel_id": face_index + 1, "status": "NO_SAFE_CANDIDATE", "before": before})
            continue
        mesh.vertices[candidate["vertex_index"]] = candidate["coordinates"]
        after = panel_diagnostic(mesh, face_index, neighbors, mach, flow)
        repaired_count += 1
        log.append({
            "panel_id": face_index + 1,
            "status": "REPAIRED",
            "vertex_id": candidate["vertex_index"] + 1,
            "direction": candidate["direction"],
            "step": candidate["step"],
            "before_margin": before["mach_margin"],
            "after_margin": after["mach_margin"],
        })
    final_scan = scan_mach_criterion(mesh, mach, flow)
    return mesh, log, final_scan
