#!/usr/bin/env python3
"""Auditable geometry preparation for the certified MachLine twin.

The routines in this module never modify the OpenVSP MASTER.  They operate on
the NASCART export of the Fine MachLine twin and accept only two bounded
operations:

* removal of exact, zero-thickness duplicate face pairs when topology proves
  that both copies are a redundant terminal skin;
* replacement of a downstream axial disk by a pointed numerical closure whose
  panels satisfy the requested supersonic Mach/alpha/beta envelope.

The numerical closure is a solver device, not physical aircraft geometry.  It
therefore receives its own component number and must be excluded from force
integration by every downstream consumer.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path

from mach_repair import scan_mach_criterion, summarize_scan
from tri_mesh import TriMesh, diagnose, repair


COORDINATE_CONVENTION = {
    "source_axes": "OpenVSP_XYZ",
    "mesh_axes": "NASCART_X_Z_NEGY",
    "longitudinal_axis": "+x",
    "vertical_axis": "+y",
    "spanwise_axis": "+z",
    "vsp_to_mesh_matrix": [[1, 0, 0], [0, 0, 1], [0, -1, 0]],
}


def read_vspgeom_thin(
    path: Path,
    key_path: Path,
    *,
    component_offset: int = 0,
) -> tuple[TriMesh, dict[int, str]]:
    """Convert OpenVSP's VLM plate mesh to triangular NASCART coordinates.

    ``*.vspgeom`` v3 stores nodes, polygon loops and one part identifier per
    loop.  The converter intentionally consumes only those documented mesh
    blocks; wake and parametric-coordinate blocks are not solver geometry.
    """
    lines = [line.strip() for line in path.read_text(encoding="utf-8", errors="strict").splitlines()]
    if len(lines) < 5 or lines[0].lower() != "# vspgeom v3":
        raise ValueError("Поддерживается только OpenVSP VSPGeom v3")
    try:
        vertex_count, loop_count, _wake_edge_count = (int(value) for value in lines[2].split())
    except (ValueError, TypeError) as exc:
        raise ValueError("Некорректный заголовок VSPGeom v3") from exc
    if vertex_count <= 0 or loop_count <= 0:
        raise ValueError("VSPGeom не содержит непустую тонкую сетку")
    vertex_start = 3
    loop_count_line = vertex_start + vertex_count
    if len(lines) <= loop_count_line or int(lines[loop_count_line]) != loop_count:
        raise ValueError("VSPGeom содержит несогласованное число панелей")
    vertices = []
    for line in lines[vertex_start:loop_count_line]:
        values = line.split()
        if len(values) != 3:
            raise ValueError("Вершина VSPGeom должна содержать три координаты")
        x, y, z = (float(value) for value in values)
        vertices.append((x, z, -y))

    loop_start = loop_count_line + 1
    parameter_start = loop_start + loop_count
    if len(lines) < parameter_start + loop_count:
        raise ValueError("VSPGeom обрезан до блока идентификаторов поверхностей")
    loops: list[list[int]] = []
    for line in lines[loop_start:parameter_start]:
        values = [int(value) for value in line.split()]
        if not values or values[0] < 3 or len(values) != values[0] + 1:
            raise ValueError("Некорректная полигональная панель VSPGeom")
        indices = [value - 1 for value in values[1:]]
        if any(index < 0 or index >= vertex_count for index in indices):
            raise ValueError("Панель VSPGeom ссылается на отсутствующую вершину")
        loops.append(indices)
    parts = []
    for line in lines[parameter_start:parameter_start + loop_count]:
        values = line.split()
        if len(values) < 2:
            raise ValueError("VSPGeom не содержит part/surface для панели")
        parts.append(int(values[0]))

    part_names: dict[int, str] = {}
    if not key_path.is_file():
        raise ValueError("OpenVSP не создал VSPGeom .vkey")
    for raw in key_path.read_text(encoding="utf-8", errors="replace").splitlines():
        values = [value.strip() for value in raw.split(",")]
        if len(values) < 6 or not values[0].isdigit():
            continue
        part = int(values[0])
        name = values[3]
        thick = int(values[5])
        if thick != 0:
            raise ValueError(f"VSPGeom part {part} неожиданно помечен как толстый")
        if part in part_names:
            raise ValueError(f"Повторный VSPGeom part {part}")
        part_names[part] = name
    missing_parts = sorted(set(parts) - set(part_names))
    if missing_parts:
        raise ValueError(
            "VSPGeom содержит parts без записей .vkey: "
            + ", ".join(str(value) for value in missing_parts)
        )

    faces: list[tuple[int, int, int]] = []
    components: list[int] = []
    for indices, part in zip(loops, parts):
        # Deterministic fan triangulation; OpenVSP currently emits quads, but
        # the generic form keeps the parser fail-safe for triangular loops.
        for position in range(1, len(indices) - 1):
            faces.append((indices[0], indices[position], indices[position + 1]))
            components.append(component_offset + part)
    # A VSPGeom may deliberately share junction nodes between independent
    # VLM parts (for example Wing and VO).  MachLine must see those plates as
    # separate topological surfaces; duplicating the shared node per part
    # changes no coordinate and removes artificial nonmanifold edges.
    split_vertices: list[tuple[float, float, float]] = []
    split_faces: list[tuple[int, int, int]] = []
    split_map: dict[tuple[int, int], int] = {}
    for face, component in zip(faces, components):
        split_face = []
        for old_index in face:
            key = (old_index, component)
            if key not in split_map:
                split_map[key] = len(split_vertices)
                split_vertices.append(vertices[old_index])
            split_face.append(split_map[key])
        split_faces.append(tuple(split_face))
    names = {
        component_offset + part: name
        for part, name in sorted(part_names.items())
        if part in set(parts)
    }
    return _compact(TriMesh(split_vertices, split_faces, components)), names


def combine_meshes(meshes: list[TriMesh]) -> TriMesh:
    """Combine independent component meshes without merging their vertices."""
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    components: list[int] = []
    for mesh in meshes:
        offset = len(vertices)
        vertices.extend(mesh.vertices)
        faces.extend(tuple(index + offset for index in face) for face in mesh.faces)
        components.extend(mesh.components)
    return TriMesh(vertices, faces, components)


def nascart_freestream(alpha_deg: float, beta_deg: float) -> tuple[float, float, float]:
    """Return OpenVSP alpha/beta expressed in OpenVSP's NASCART axes."""
    alpha = math.radians(float(alpha_deg))
    beta = math.radians(float(beta_deg))
    # OpenVSP XYZ -> NASCART X,Z,-Y.
    return (
        math.cos(alpha) * math.cos(beta),
        math.sin(alpha) * math.cos(beta),
        -math.sin(beta),
    )


def _face_geometry(mesh: TriMesh, face_index: int) -> tuple[tuple[float, float, float], float, tuple[float, float, float]]:
    a, b, c = (mesh.vertices[index] for index in mesh.faces[face_index])
    u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    v = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    cross = (
        u[1] * v[2] - u[2] * v[1],
        u[2] * v[0] - u[0] * v[2],
        u[0] * v[1] - u[1] * v[0],
    )
    twice_area = math.sqrt(sum(value * value for value in cross))
    normal = tuple(value / twice_area for value in cross) if twice_area else (0.0, 0.0, 0.0)
    center = tuple((a[axis] + b[axis] + c[axis]) / 3.0 for axis in range(3))
    return normal, 0.5 * twice_area, center


def _compact(mesh: TriMesh) -> TriMesh:
    used = sorted({vertex for face in mesh.faces for vertex in face})
    remap = {old: new for new, old in enumerate(used)}
    return TriMesh(
        [mesh.vertices[index] for index in used],
        [tuple(remap[index] for index in face) for face in mesh.faces],
        list(mesh.components),
    )


def _face_id_summary(zero_based_ids: set[int] | list[int]) -> dict:
    """Keep large repair logs auditable without embedding thousands of IDs."""
    ids = [int(value) + 1 for value in sorted(set(zero_based_ids))]
    ranges = []
    if ids:
        first = previous = ids[0]
        for value in ids[1:]:
            if value == previous + 1:
                previous = value
                continue
            ranges.append([first, previous])
            first = previous = value
        ranges.append([first, previous])
    return {
        "count": len(ids),
        "sample": ids[:100],
        "sample_truncated": len(ids) > 100,
        "ranges": ranges[:100],
        "ranges_truncated": len(ranges) > 100,
        "sha256": hashlib.sha256(json.dumps(ids, separators=(",", ":")).encode("utf-8")).hexdigest(),
    }


def remove_redundant_duplicate_skins(mesh: TriMesh) -> tuple[TriMesh, dict]:
    """Remove both copies of exact duplicate faces only when topology improves.

    Keeping one face from a duplicated zero-thickness end skin can leave a
    nonmanifold edge.  We trial-removal of every exact duplicate group at once
    and accept it only if it reduces nonmanifold edges without opening the
    surface or introducing a new invalid condition.
    """
    groups: dict[tuple[int, int, int], list[int]] = defaultdict(list)
    for index, face in enumerate(mesh.faces):
        if len(set(face)) == 3:
            groups[tuple(sorted(face))].append(index)
    removed = sorted(index for values in groups.values() if len(values) > 1 for index in values)
    before = diagnose(mesh)
    if not removed:
        return mesh, {
            "attempted": False,
            "accepted": False,
            "removed_face_ids": [],
            "reason": "exact duplicate groups absent",
        }
    removed_set = set(removed)
    candidate = _compact(TriMesh(
        list(mesh.vertices),
        [face for index, face in enumerate(mesh.faces) if index not in removed_set],
        [
            mesh.components[index] if index < len(mesh.components) else 1
            for index in range(len(mesh.faces)) if index not in removed_set
        ],
    ))
    after = diagnose(candidate)
    accepted = bool(
        after["invalid_index_faces"] == 0
        and after["repeated_vertex_faces"] == 0
        and after["degenerate_faces"] == 0
        and after["duplicate_faces"] == 0
        and after["nonmanifold_edges"] < before["nonmanifold_edges"]
        and after["boundary_edges"] <= before["boundary_edges"]
    )
    return (candidate if accepted else mesh), {
        "attempted": True,
        "accepted": accepted,
        "removed_face_ids": [index + 1 for index in removed] if accepted else [],
        "removed_faces": len(removed) if accepted else 0,
        "before": {
            "boundary_edges": before["boundary_edges"],
            "nonmanifold_edges": before["nonmanifold_edges"],
        },
        "after": {
            "boundary_edges": after["boundary_edges"],
            "nonmanifold_edges": after["nonmanifold_edges"],
        },
        "reason": (
            "exact duplicate terminal skin removed and topology improved"
            if accepted else
            "trial removal did not prove a topology improvement"
        ),
    }


def scan_scope(mesh: TriMesh, scope: dict, component_names: dict[int, str] | None = None) -> list[dict]:
    """Scan Mach criterion at every supersonic Mach/alpha envelope corner."""
    names = component_names or {}
    mach_values = sorted({
        float(value)
        for lo, hi in scope.get("mach_intervals", [])
        for value in (lo, hi)
        if float(value) > 1.0
    })
    alpha_values = sorted({float(value) for value in scope.get("alpha_deg", [0.0, 0.0])})
    beta = float(scope.get("beta_deg", 0.0))
    results = []
    for mach in mach_values:
        for alpha in alpha_values:
            flow = nascart_freestream(alpha, beta)
            detailed = scan_mach_criterion(mesh, mach, flow)
            summary = summarize_scan(detailed)
            by_component: dict[int, dict] = {}
            for item in detailed:
                if item["mach_margin"] <= 0.0:
                    continue
                face_index = int(item["panel_id"]) - 1
                component = mesh.components[face_index] if face_index < len(mesh.components) else 1
                entry = by_component.setdefault(component, {
                    "component_id": component,
                    "component_name": names.get(component, f"component_{component}"),
                    "bad_panels": 0,
                    "bad_area": 0.0,
                    "bad_panel_ids": [],
                })
                entry["bad_panels"] += 1
                entry["bad_area"] += float(item["area"])
                if len(entry["bad_panel_ids"]) < 200:
                    entry["bad_panel_ids"].append(item["panel_id"])
            summary.update({
                "mach": mach,
                "alpha_deg": alpha,
                "beta_deg": beta,
                "freestream_mesh_axes": list(flow),
                "bad_by_component": list(by_component.values()),
            })
            results.append(summary)
    return results


def _edge_entries(mesh: TriMesh, face_ids: set[int] | None = None) -> dict[tuple[int, int], list[tuple[int, int, int]]]:
    entries: dict[tuple[int, int], list[tuple[int, int, int]]] = defaultdict(list)
    indices = range(len(mesh.faces)) if face_ids is None else sorted(face_ids)
    for face_index in indices:
        a, b, c = mesh.faces[face_index]
        for start, end in ((a, b), (b, c), (c, a)):
            key = (start, end) if start < end else (end, start)
            entries[key].append((face_index, start, end))
    return entries


def _one_boundary_loop(mesh: TriMesh, kept_face_ids: set[int], component: int) -> list[tuple[int, int]] | None:
    component_faces = {
        index for index in kept_face_ids
        if (mesh.components[index] if index < len(mesh.components) else 1) == component
    }
    edges = _edge_entries(mesh, component_faces)
    directed = [(items[0][1], items[0][2]) for items in edges.values() if len(items) == 1]
    if len(directed) < 3:
        return None
    adjacency: dict[int, list[int]] = defaultdict(list)
    for start, end in directed:
        adjacency[start].append(end)
        adjacency[end].append(start)
    if any(len(values) != 2 for values in adjacency.values()):
        return None
    loop = []
    start = directed[0][0]
    previous = None
    current = start
    for _ in range(len(directed)):
        candidates = [value for value in adjacency[current] if value != previous]
        if not candidates:
            return None
        following = candidates[0]
        loop.append((current, following))
        previous, current = current, following
        if current == start:
            break
    if len(loop) != len(directed) or current != start:
        return None
    # Orient every shared edge opposite to its surviving physical face.
    oriented = []
    for first, second in loop:
        key = (first, second) if first < second else (second, first)
        surviving = edges[key][0]
        oriented.append((surviving[2], surviving[1]))
    # The independently reversed edges must also form a cycle.  The triangle
    # construction itself needs only the edge directions, not their ordering.
    return oriented


def _candidate_axial_caps(
    mesh: TriMesh,
    component_names: dict[int, str],
    policy: dict,
    reference: dict,
) -> list[dict]:
    prefixes = tuple(str(value) for value in policy.get("component_name_prefixes", ["Fuselage", "Gondola"]))
    cref = float(reference.get("cref", reference.get("length", 1.0)))
    tolerance = max(float(policy.get("plane_tolerance_cref", 1.0e-7)) * cref, 1.0e-10)
    axial_min = float(policy.get("axial_normal_min", 0.995))
    component_faces: dict[int, list[int]] = defaultdict(list)
    for index, component in enumerate(mesh.components):
        component_faces[component].append(index)
    candidates = []
    for component, face_ids in sorted(component_faces.items()):
        name = component_names.get(component, f"component_{component}")
        if not name.startswith(prefixes):
            continue
        vertex_ids = {vertex for face_id in face_ids for vertex in mesh.faces[face_id]}
        maximum_x = max(mesh.vertices[index][0] for index in vertex_ids)
        cap_ids = []
        area = 0.0
        for face_id in face_ids:
            face = mesh.faces[face_id]
            if max(abs(mesh.vertices[index][0] - maximum_x) for index in face) > tolerance:
                continue
            normal, face_area, _ = _face_geometry(mesh, face_id)
            if abs(normal[0]) < axial_min:
                continue
            cap_ids.append(face_id)
            area += face_area
        if cap_ids:
            candidates.append({
                "component_id": component,
                "component_name": name,
                "plane_x": maximum_x,
                "face_ids": cap_ids,
                "area": area,
            })
    return candidates


def replace_downstream_axial_caps(
    mesh: TriMesh,
    *,
    scope: dict,
    component_names: dict[int, str],
    policy: dict,
    reference: dict,
) -> tuple[TriMesh, dict]:
    """Replace proven downstream disks by tagged supersonic closures."""
    if not policy.get("enabled", False):
        return mesh, {"attempted": False, "accepted": False, "closures": []}
    candidates = _candidate_axial_caps(mesh, component_names, policy, reference)
    if not candidates:
        return mesh, {
            "attempted": True,
            "accepted": False,
            "closures": [],
            "reason": "no bounded downstream axial cap matched policy",
        }
    sref = float(reference.get("area", 0.0))
    cref = float(reference.get("cref", reference.get("length", 0.0)))
    if sref <= 0.0 or cref <= 0.0:
        return mesh, {
            "attempted": True,
            "accepted": False,
            "closures": [],
            "reason": "positive Sref and cref are required",
        }
    mach_values = [
        float(value)
        for lo, hi in scope.get("mach_intervals", [])
        for value in (lo, hi)
        if float(value) > 1.0
    ]
    if not mach_values:
        return mesh, {"attempted": False, "accepted": False, "closures": [],
                      "reason": "not_applicable_to_subsonic_scope"}
    mach_max = max(mach_values)
    alpha_max = max(abs(float(value)) for value in scope.get("alpha_deg", [0.0, 0.0]))
    beta = abs(float(scope.get("beta_deg", 0.0)))
    flow_angle = math.degrees(math.acos(
        max(-1.0, min(1.0, math.cos(math.radians(alpha_max)) * math.cos(math.radians(beta))))
    ))
    safety = float(policy.get("safety_angle_deg", 2.0))
    limit_angle = math.degrees(math.asin(1.0 / mach_max)) - flow_angle - safety
    if limit_angle <= 0.0:
        return mesh, {
            "attempted": True,
            "accepted": False,
            "closures": [],
            "reason": "requested flow envelope leaves no positive closure angle",
        }

    working = TriMesh(list(mesh.vertices), list(mesh.faces), list(mesh.components))
    baseline_boundary_edges = diagnose(working)["boundary_edges"]
    closures = []
    surrogate_components = []
    removed_all: set[int] = set()
    next_component = max(working.components or [0]) + 1
    for candidate in candidates:
        face_ids = set(candidate["face_ids"])
        if face_ids & removed_all:
            continue
        if candidate["area"] / sref > float(policy.get("max_removed_area_over_sref", 0.05)):
            continue
        kept = set(range(len(working.faces))) - face_ids
        boundary = _one_boundary_loop(working, kept, int(candidate["component_id"]))
        if boundary is None or len(boundary) < int(policy.get("min_boundary_edges", 8)):
            continue
        ring_vertices = sorted({vertex for edge in boundary for vertex in edge})
        center_y = sum(working.vertices[index][1] for index in ring_vertices) / len(ring_vertices)
        center_z = sum(working.vertices[index][2] for index in ring_vertices) / len(ring_vertices)
        radius = max(math.hypot(
            working.vertices[index][1] - center_y,
            working.vertices[index][2] - center_z,
        ) for index in ring_vertices)
        extension = radius / math.tan(math.radians(limit_angle)) * 1.02
        maximum_extension = float(policy.get("max_extension_over_cref", 0.5)) * cref
        accepted = None
        for _attempt in range(12):
            if extension > maximum_extension:
                break
            vertices = list(working.vertices)
            apex = len(vertices)
            vertices.append((candidate["plane_x"] + extension, center_y, center_z))
            faces = [face for index, face in enumerate(working.faces) if index not in face_ids]
            components = [
                working.components[index] if index < len(working.components) else 1
                for index in range(len(working.faces)) if index not in face_ids
            ]
            first_new_face = len(faces)
            for start, end in boundary:
                faces.append((start, end, apex))
                components.append(next_component)
            trial = _compact(TriMesh(vertices, faces, components))
            trial_scans = scan_scope(trial, scope, {**component_names, next_component: "RM_NUMERICAL_AFT_CLOSURE"})
            surrogate_bad = any(
                any(item["component_id"] == next_component for item in scan["bad_by_component"])
                for scan in trial_scans
            )
            topology = diagnose(trial)
            if (
                not surrogate_bad
                and topology["machline_safe_topology"]
                and topology["boundary_edges"] <= baseline_boundary_edges
            ):
                accepted = (trial, trial_scans, first_new_face)
                break
            extension *= 1.15
        if accepted is None:
            continue
        working, _trial_scans, first_new_face = accepted
        removed_all.update(face_ids)
        surrogate_components.append(next_component)
        closures.append({
            "source_component_id": candidate["component_id"],
            "source_component_name": candidate["component_name"],
            "surrogate_component_id": next_component,
            "removed_cap_faces": len(face_ids),
            "removed_cap_face_ids": _face_id_summary(face_ids),
            "removed_cap_area": candidate["area"],
            "removed_cap_area_over_sref": candidate["area"] / sref,
            "boundary_edges": len(boundary),
            "radius": radius,
            "extension": extension,
            "extension_over_cref": extension / cref,
            "limit_half_angle_deg": limit_angle,
            "new_closure_faces": len(boundary),
            "first_new_face_before_compaction": first_new_face + 1,
        })
        next_component += 1
        # Indexes from further candidates refer to the original mesh.  The
        # first accepted closure changes indexing, so version 1 intentionally
        # permits exactly one axial closure and fails closed on other caps.
        break

    accepted = bool(closures)
    return working, {
        "attempted": True,
        "accepted": accepted,
        "closures": closures,
        "surrogate_component_ids": surrogate_components,
        "force_integration_contract": {
            "required": accepted,
            "include_component_ids": sorted(set(mesh.components)),
            "exclude_component_ids": surrogate_components,
            "base_drag_replacement_required": accepted,
        },
        "reason": (
            "bounded downstream numerical closure created"
            if accepted else
            "candidate cap could not be closed inside policy bounds"
        ),
    }


def prepare_machline_geometry(
    mesh: TriMesh,
    *,
    scope: dict,
    component_names: dict[int, str],
    policy: dict,
    reference: dict,
) -> tuple[TriMesh, dict]:
    """Create a deterministic MachLine mesh and a sealed-ready audit record."""
    deduplicated, duplicate_log = remove_redundant_duplicate_skins(mesh)
    repaired, generic_log = repair(deduplicated, merge_duplicate_vertices=False)
    closure_policy = policy.get("solver_surrogate", {}).get("downstream_axial_closure", {})
    if policy.get("topology_mode", "closed_body") == "open_nozzle":
        closure_policy = {**closure_policy, "enabled": False}
    prepared, closure_log = replace_downstream_axial_caps(
        repaired,
        scope=scope,
        component_names=component_names,
        policy=closure_policy,
        reference=reference,
    )
    final, final_repair_log = repair(prepared, merge_duplicate_vertices=False)
    record = {
        "coordinate_convention": COORDINATE_CONVENTION,
        "duplicate_skin_repair": duplicate_log,
        "generic_repair": generic_log,
        "downstream_axial_closure": closure_log,
        "final_repair": final_repair_log,
    }
    record["transformation_sha256"] = hashlib.sha256(json.dumps(
        record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")).hexdigest()
    return final, record
