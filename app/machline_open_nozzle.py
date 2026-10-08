"""Declared outlet boundaries, not a global exemption from mesh closure.

Coordinates are exported NASCART coordinates.  This first version removes only
axial, planar terminal disks; it never guesses an outlet from a body name.
"""
from collections import defaultdict
import math

from tri_mesh import TriMesh, diagnose
from machline_geometry import nascart_freestream


def _boundaries(mesh):
    edges = defaultdict(list)
    for face, component in zip(mesh.faces, mesh.components):
        for a, b in zip(face, face[1:] + face[:1]):
            edges[tuple(sorted((a, b)))].append(component)
    return {edge: owners[0] for edge, owners in edges.items() if len(owners) == 1}


def _single_loop(edges):
    adjacency = defaultdict(set)
    for a, b in edges:
        adjacency[a].add(b)
        adjacency[b].add(a)
    if len(adjacency) < 3 or any(len(n) != 2 for n in adjacency.values()):
        return False
    seen, stack = set(), [next(iter(adjacency))]
    while stack:
        vertex = stack.pop()
        if vertex not in seen:
            seen.add(vertex)
            stack.extend(adjacency[vertex] - seen)
    return len(seen) == len(adjacency)


def open_declared_nozzles(mesh, *, declarations, component_names, reference,
                         scope, context_mesh=None):
    """Return a new mesh plus audit. On any ambiguity return the original mesh."""
    audit = {"mode": "open_nozzle", "valid": False, "outlets": [],
             "removed_face_ids": [], "errors": [],
             "source": "https://github.com/usuaero/MachLine/wiki/Modeling"}
    try:
        if not isinstance(declarations, list) or not declarations:
            raise ValueError("Нужно явно задать machline.open_nozzles: component_name и plane_x")
        if len(mesh.components) != len(mesh.faces) or not diagnose(mesh)["machline_safe_topology"]:
            raise ValueError("Исходная толстотельная сетка имеет дефекты топологии")
        if any(not all(math.isfinite(x) for x in v) for v in mesh.vertices):
            raise ValueError("Неконечные координаты сетки")
        cref, sref = float(reference["cref"]), float(reference["area"])
        if not math.isfinite(cref) or not math.isfinite(sref) or min(cref, sref) <= 0:
            raise ValueError("Нужны положительные конечные cref/Sref")
        tolerance = max(1e-7 * cref, 1e-10)
        remove, used_components = set(), set()
        for declaration in declarations:
            if not isinstance(declaration, dict):
                raise ValueError("Описание выхода должно быть объектом")
            name, plane = declaration.get("component_name"), float(declaration["plane_x"])
            matches = [int(k) for k, v in component_names.items()
                       if v == name and int(k) in mesh.components]
            if len(matches) != 1 or not math.isfinite(plane):
                raise ValueError("Выход должен однозначно ссылаться на точное имя экспортированной поверхности")
            component = matches[0]
            if component in used_components:
                raise ValueError("Повторное объявление выхода одной поверхности")
            used_components.add(component)
            indices = [i for i, c in enumerate(mesh.components) if c == component]
            vertices = {v for i in indices for v in mesh.faces[i]}
            if abs(max(mesh.vertices[v][0] for v in vertices) - plane) > tolerance:
                raise ValueError("Выход не совпадает с терминальным X-сечением указанной поверхности")
            cap, area = [], 0.0
            for i in indices:
                points = [mesh.vertices[v] for v in mesh.faces[i]]
                if not all(abs(p[0] - plane) <= tolerance for p in points):
                    continue
                a, b, c = points
                u, v = [b[j]-a[j] for j in range(3)], [c[j]-a[j] for j in range(3)]
                normal = (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])
                norm = math.sqrt(sum(x*x for x in normal))
                if norm == 0 or normal[0] / norm < 0.995:
                    raise ValueError("Кандидат содержит неосевые или неверно ориентированные панели")
                area += norm / 2
                cap.append(i)
            if len(cap) == len(indices) or area > 0.05 * sref:
                raise ValueError("Удаление выхода превышает допустимую площадь либо удаляет всю поверхность")
            remove.update(cap)
            audit["outlets"].append({"component_name": name, "component_id": component,
                                     "plane_x": plane, "removed_cap_area": area,
                                     "removed_face_ids": [i+1 for i in cap]})
        if sum(item["removed_cap_area"] for item in audit["outlets"]) > 0.05 * sref:
            raise ValueError("Суммарная площадь удаляемых крышек превышает 5% Sref")
        faces = [f for i, f in enumerate(mesh.faces) if i not in remove]
        # Cap-centre vertices must not survive as disconnected unknowns.
        used = sorted({v for face in faces for v in face})
        mapping = {old: new for new, old in enumerate(used)}
        candidate = TriMesh([mesh.vertices[v] for v in used],
                            [tuple(mapping[v] for v in f) for f in faces],
                            [c for i, c in enumerate(mesh.components) if i not in remove])
        remaining = _boundaries(candidate)
        claimed = set()
        for outlet in audit["outlets"]:
            edges = [e for e, c in remaining.items() if c == outlet["component_id"]
                     and all(abs(candidate.vertices[v][0]-outlet["plane_x"]) <= tolerance for v in e)]
            if not _single_loop(edges):
                raise ValueError("Выход не образует единственный замкнутый контур граничных рёбер")
            claimed.update(edges)
            outlet["boundary_edges"] = [list(e) for e in sorted(edges)]
        if claimed != set(remaining):
            raise ValueError("Обнаружены незаявленные отверстия вне сопел")
        if not diagnose(candidate)["machline_safe_topology"]:
            raise ValueError("Открытие сопла нарушило топологию")
        # The documented open-nozzle condition is restricted to supersonic
        # flow. Check the entire aircraft, not just the isolated nacelle.
        context = context_mesh or mesh
        if any(float(hi) > 1 for lo, hi in scope["mach_intervals"]):
            for alpha in set(scope["alpha_deg"]):
                direction = nascart_freestream(alpha, scope.get("beta_deg", 0))
                dot = lambda p: sum(x*y for x, y in zip(p, direction))
                for outlet in audit["outlets"]:
                    ids = {v for e in outlet["boundary_edges"] for v in e}
                    lip_min = min(dot(candidate.vertices[v]) for v in ids)
                    # Exclude only this outlet disk, not unrelated geometry
                    # that happens to share its X station.
                    context_vertices = {v for face, cid in zip(context.faces, context.components)
                                        if not (cid == outlet["component_id"] and all(
                                            abs(context.vertices[j][0]-outlet["plane_x"]) <= tolerance
                                            for j in face)) for v in face}
                    ring_points = {candidate.vertices[v] for v in ids}
                    others = [dot(context.vertices[v]) for v in context_vertices
                              if context.vertices[v] not in ring_points]
                    if others and max(others) > lip_min + tolerance:
                        raise ValueError("Сопло не находится позади остальной геометрии по направлению потока")
        audit.update(valid=True, removed_face_ids=[i+1 for i in sorted(remove)],
                     boundary_edges=len(remaining), topology=diagnose(candidate),
                     original_vertex_ids=[v+1 for v in used],
                     removed_vertex_count=len(mesh.vertices)-len(used),
                     subsonic_solver_qualified=False)
        return candidate, audit
    except (ValueError, KeyError, TypeError, IndexError) as error:
        audit["errors"].append(str(error))
        return mesh, audit
