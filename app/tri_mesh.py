#!/usr/bin/env python3
"""Dependency-free diagnostics and conservative repair for Cart3D TRI meshes."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from math import sqrt
from pathlib import Path


@dataclass
class TriMesh:
    vertices: list[tuple[float, float, float]]
    faces: list[tuple[int, int, int]]  # zero-based
    components: list[int]


def read_tri(path: Path) -> TriMesh:
    tokens = path.read_text(encoding="utf-8", errors="strict").split()
    if len(tokens) < 2:
        raise ValueError("TRI-файл пуст или не содержит заголовок")
    try:
        vertex_count, face_count = int(tokens[0]), int(tokens[1])
    except ValueError as exc:
        raise ValueError("Первые два значения TRI должны быть целыми числами") from exc
    if vertex_count < 0 or face_count < 0:
        raise ValueError("Количество вершин и панелей не может быть отрицательным")

    geometry_tokens = 2 + 3 * vertex_count + 3 * face_count
    if len(tokens) < geometry_tokens:
        raise ValueError(
            f"TRI-файл обрезан: ожидалось не менее {geometry_tokens} значений, "
            f"получено {len(tokens)}"
        )

    cursor = 2
    try:
        vertices = []
        for _ in range(vertex_count):
            vertices.append(tuple(float(v) for v in tokens[cursor:cursor + 3]))
            cursor += 3
        faces = []
        for _ in range(face_count):
            faces.append(tuple(int(v) - 1 for v in tokens[cursor:cursor + 3]))
            cursor += 3
    except ValueError as exc:
        raise ValueError("TRI содержит некорректное число в блоке геометрии") from exc

    components = [1] * face_count
    available = min(face_count, len(tokens) - cursor)
    try:
        for index in range(available):
            components[index] = int(tokens[cursor + index])
    except ValueError as exc:
        raise ValueError("TRI содержит некорректный номер компонента") from exc
    return TriMesh(vertices, faces, components)


def write_tri(path: Path, mesh: TriMesh) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{len(mesh.vertices)} {len(mesh.faces)}"]
    lines.extend(f"{x:.12g} {y:.12g} {z:.12g}" for x, y, z in mesh.vertices)
    lines.extend(f"{a + 1} {b + 1} {c + 1}" for a, b, c in mesh.faces)
    lines.extend(str(component) for component in mesh.components)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _bbox_diagonal(vertices: list[tuple[float, float, float]]) -> float:
    if not vertices:
        return 0.0
    mins = [min(v[axis] for v in vertices) for axis in range(3)]
    maxs = [max(v[axis] for v in vertices) for axis in range(3)]
    return sqrt(sum((maxs[axis] - mins[axis]) ** 2 for axis in range(3)))


def _area_twice_sq(face: tuple[int, int, int], vertices: list[tuple[float, float, float]]) -> float:
    a, b, c = (vertices[index] for index in face)
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    cross = (uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx)
    return sum(value * value for value in cross)


def _edge_map(faces: list[tuple[int, int, int]]) -> dict[tuple[int, int], list[tuple[int, int]]]:
    edges: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    for face_index, (a, b, c) in enumerate(faces):
        for start, end in ((a, b), (b, c), (c, a)):
            edge = (start, end) if start < end else (end, start)
            direction = 1 if start < end else -1
            edges[edge].append((face_index, direction))
    return edges


def _connected_face_components(face_count: int, edges: dict) -> int:
    adjacency = [[] for _ in range(face_count)]
    for entries in edges.values():
        if len(entries) < 2:
            continue
        root = entries[0][0]
        for face_index, _ in entries[1:]:
            adjacency[root].append(face_index)
            adjacency[face_index].append(root)
    seen = set()
    count = 0
    for start in range(face_count):
        if start in seen:
            continue
        count += 1
        queue = [start]
        seen.add(start)
        while queue:
            current = queue.pop()
            for neighbor in adjacency[current]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append(neighbor)
    return count


def _duplicate_vertex_map(vertices: list[tuple[float, float, float]], tolerance: float) -> tuple[list[int], int]:
    if not vertices:
        return [], 0
    cells: dict[tuple[int, int, int], list[int]] = defaultdict(list)
    remap = list(range(len(vertices)))
    duplicates = 0
    tolerance_sq = tolerance * tolerance
    for index, vertex in enumerate(vertices):
        cell = tuple(int(round(value / tolerance)) for value in vertex)
        match = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for candidate in cells.get((cell[0] + dx, cell[1] + dy, cell[2] + dz), []):
                        other = vertices[candidate]
                        if sum((vertex[axis] - other[axis]) ** 2 for axis in range(3)) <= tolerance_sq:
                            match = candidate
                            break
                    if match is not None:
                        break
                if match is not None:
                    break
            if match is not None:
                break
        if match is None:
            cells[cell].append(index)
        else:
            remap[index] = remap[match]
            duplicates += 1
    return remap, duplicates


def diagnose(mesh: TriMesh) -> dict:
    vertex_count = len(mesh.vertices)
    diagonal = _bbox_diagonal(mesh.vertices)
    merge_tolerance = max(diagonal * 1.0e-10, 1.0e-12)
    area_sq_tolerance = max((diagonal * diagonal * 1.0e-14) ** 2, 1.0e-30)
    invalid_indices = []
    repeated_vertex_faces = []
    degenerate_faces = []
    valid_faces = []
    valid_original_indices = []
    for face_index, face in enumerate(mesh.faces):
        if any(index < 0 or index >= vertex_count for index in face):
            invalid_indices.append(face_index + 1)
            continue
        if len(set(face)) < 3:
            repeated_vertex_faces.append(face_index + 1)
            continue
        if _area_twice_sq(face, mesh.vertices) <= area_sq_tolerance:
            degenerate_faces.append(face_index + 1)
            continue
        valid_faces.append(face)
        valid_original_indices.append(face_index + 1)

    duplicate_faces = []
    face_keys = {}
    for local_index, face in enumerate(valid_faces):
        key = tuple(sorted(face))
        if key in face_keys:
            duplicate_faces.append(valid_original_indices[local_index])
        else:
            face_keys[key] = local_index

    edges = _edge_map(valid_faces)
    boundary_edges = sum(len(entries) == 1 for entries in edges.values())
    nonmanifold_edges = sum(len(entries) > 2 for entries in edges.values())
    inconsistent_edges = sum(
        len(entries) == 2 and entries[0][1] == entries[1][1]
        for entries in edges.values()
    )
    referenced = {index for face in valid_faces for index in face}
    unreferenced_vertices = vertex_count - len(referenced)
    _, duplicate_vertices = _duplicate_vertex_map(mesh.vertices, merge_tolerance)
    connected_components = _connected_face_components(len(valid_faces), edges) if valid_faces else 0

    safe_repairs = (
        len(invalid_indices) + len(repeated_vertex_faces) + len(degenerate_faces)
        + len(duplicate_faces) + duplicate_vertices + unreferenced_vertices + inconsistent_edges
    )
    return {
        "vertices": vertex_count,
        "faces": len(mesh.faces),
        "connected_components": connected_components,
        "bbox_diagonal": diagonal,
        "merge_tolerance": merge_tolerance,
        "invalid_index_faces": len(invalid_indices),
        "invalid_index_face_ids": invalid_indices[:100],
        "repeated_vertex_faces": len(repeated_vertex_faces),
        "repeated_vertex_face_ids": repeated_vertex_faces[:100],
        "degenerate_faces": len(degenerate_faces),
        "degenerate_face_ids": degenerate_faces[:100],
        "duplicate_faces": len(duplicate_faces),
        "duplicate_face_ids": duplicate_faces[:100],
        "duplicate_vertices": duplicate_vertices,
        "unreferenced_vertices": unreferenced_vertices,
        "boundary_edges": boundary_edges,
        "nonmanifold_edges": nonmanifold_edges,
        "inconsistent_orientation_edges": inconsistent_edges,
        "safe_repairs_available": safe_repairs,
        "watertight": boundary_edges == 0,
        "machline_safe_topology": (
            not invalid_indices and not repeated_vertex_faces and not degenerate_faces
            and not duplicate_faces and nonmanifold_edges == 0 and inconsistent_edges == 0
        ),
    }


def _orient_faces(faces: list[tuple[int, int, int]]) -> tuple[list[tuple[int, int, int]], int, int]:
    edges = _edge_map(faces)
    adjacency: list[list[tuple[int, int]]] = [[] for _ in faces]
    for entries in edges.values():
        if len(entries) != 2:
            continue
        (first, first_dir), (second, second_dir) = entries
        required_xor = 1 if first_dir == second_dir else 0
        adjacency[first].append((second, required_xor))
        adjacency[second].append((first, required_xor))

    parity: list[int | None] = [None] * len(faces)
    conflicts = 0
    for start in range(len(faces)):
        if parity[start] is not None:
            continue
        parity[start] = 0
        queue = deque([start])
        while queue:
            current = queue.popleft()
            for neighbor, required_xor in adjacency[current]:
                expected = parity[current] ^ required_xor
                if parity[neighbor] is None:
                    parity[neighbor] = expected
                    queue.append(neighbor)
                elif parity[neighbor] != expected:
                    conflicts += 1
    oriented = [
        (face[0], face[2], face[1]) if parity[index] else face
        for index, face in enumerate(faces)
    ]
    return oriented, sum(bool(value) for value in parity), conflicts // 2


def repair(mesh: TriMesh, *, merge_duplicate_vertices: bool = True) -> tuple[TriMesh, dict]:
    diagonal = _bbox_diagonal(mesh.vertices)
    merge_tolerance = max(diagonal * 1.0e-10, 1.0e-12)
    area_sq_tolerance = max((diagonal * diagonal * 1.0e-14) ** 2, 1.0e-30)
    if merge_duplicate_vertices:
        vertex_remap, merged_vertices = _duplicate_vertex_map(mesh.vertices, merge_tolerance)
    else:
        vertex_remap, merged_vertices = list(range(len(mesh.vertices))), 0

    accepted_faces = []
    accepted_components = []
    removed_invalid = removed_degenerate = removed_duplicate = 0
    seen_faces = set()
    for face_index, face in enumerate(mesh.faces):
        if any(index < 0 or index >= len(mesh.vertices) for index in face):
            removed_invalid += 1
            continue
        remapped = tuple(vertex_remap[index] for index in face)
        if len(set(remapped)) < 3 or _area_twice_sq(remapped, mesh.vertices) <= area_sq_tolerance:
            removed_degenerate += 1
            continue
        key = tuple(sorted(remapped))
        if key in seen_faces:
            removed_duplicate += 1
            continue
        seen_faces.add(key)
        accepted_faces.append(remapped)
        accepted_components.append(mesh.components[face_index] if face_index < len(mesh.components) else 1)

    accepted_faces, flipped_faces, orientation_conflicts = _orient_faces(accepted_faces)
    used_vertices = sorted({index for face in accepted_faces for index in face})
    compact_map = {old: new for new, old in enumerate(used_vertices)}
    compact_vertices = [mesh.vertices[index] for index in used_vertices]
    compact_faces = [tuple(compact_map[index] for index in face) for face in accepted_faces]
    repaired = TriMesh(compact_vertices, compact_faces, accepted_components)
    summary = {
        "merged_duplicate_vertices": merged_vertices,
        "duplicate_vertex_merge_enabled": merge_duplicate_vertices,
        "removed_invalid_faces": removed_invalid,
        "removed_degenerate_faces": removed_degenerate,
        "removed_duplicate_faces": removed_duplicate,
        "removed_unreferenced_vertices": len(mesh.vertices) - merged_vertices - len(compact_vertices),
        "flipped_faces_for_consistency": flipped_faces,
        "orientation_conflicts": orientation_conflicts,
    }
    return repaired, summary


def format_diagnostics(report: dict) -> list[str]:
    return [
        f"Вершины                         : {report['vertices']}",
        f"Панели                          : {report['faces']}",
        f"Связные компоненты              : {report['connected_components']}",
        f"Некорректные индексы            : {report['invalid_index_faces']}",
        f"Панели с повтором вершины       : {report['repeated_vertex_faces']}",
        f"Вырожденные панели              : {report['degenerate_faces']}",
        f"Дубликаты панелей               : {report['duplicate_faces']}",
        f"Совпадающие вершины             : {report['duplicate_vertices']}",
        f"Неиспользуемые вершины          : {report['unreferenced_vertices']}",
        f"Граничные рёбра                 : {report['boundary_edges']}",
        f"Неманифолдные рёбра             : {report['nonmanifold_edges']}",
        f"Несогласованные соседние панели : {report['inconsistent_orientation_edges']}",
        f"Замкнутая поверхность           : {'да' if report['watertight'] else 'нет'}",
    ]
