#!/usr/bin/env python3
"""Automatic G0--G8 geometry certification for RepairMach 9.1.

The certificate concerns solver suitability and traceability for one exact
MASTER, policy, reference set, solver version and Mach/alpha scope.  It does
not claim aerodynamic accuracy and never mutates the MASTER file.
"""

from __future__ import annotations

import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from aero_hybrid import parse_vspaero_polar, validate_vspaero_run_outputs
from geometry_certificate import emit_certificate
from geometry_delta import compute_geometry_deltas, delta_within_policy
from geometry_manifest import (
    canonical_vsp3_sha256,
    inventory_model,
    sha256_file,
    sha256_payload,
    write_json,
)
from geometry_rules import (
    component_base,
    effective_policy,
    finding,
    load_geometry_policy,
    semantic_audit,
)
from geometry_twins import (
    apply_model_actions,
    build_transformation_plan,
    copy_passthrough,
    mesh_actions,
    verify_mesh_inventory,
    verify_plan,
)
from machline_geometry import (
    COORDINATE_CONVENTION,
    combine_meshes,
    nascart_freestream,
    prepare_machline_geometry,
    read_vspgeom_thin,
    scan_scope,
)
from machline_postprocess import masked_force_coefficients, write_masked_force_record
from mach_repair import scan_mach_criterion, summarize_scan
from openvsp_runner import run_vspscript
from tri_mesh import TriMesh, diagnose as diagnose_tri
from tri_mesh import read_tri, repair as repair_tri, write_tri
from vspaero_runner import find_generated_polar, generate_vspaero_sweep_script, parse_set_report


RUN_SCHEMA = "repairmach.geometry-certification-run/1.0"
MACHLINE_TRI_EXPORT_SCHEMA = "repairmach.machline-tri-export/1.0"


def _unique_run_dir(output_root: Path, project_name: str, model_stem: str) -> Path:
    project_token = (
        re.sub(r"[^A-Za-z0-9_.-]+", "_", project_name).strip("._") or "project"
    )
    model_token = (
        re.sub(r"[^A-Za-z0-9_.-]+", "_", model_stem).strip("._") or "model"
    )
    identity = sha256_payload({"project": project_name, "model": model_stem})[:8]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = output_root / (
        f"{timestamp}_{project_token[:12]}_{model_token[:18]}_{identity}"
    )
    longest_internal = (
        base / "probes" / "vspaero_lifting" / "M2p2_A5_to_face"
        / "ultra_fine" / "model.case.1.quad.1.dat"
    )
    if os.name == "nt" and len(str(longest_internal.resolve())) >= 240:
        raise ValueError(
            "Каталог сертификации слишком глубокий для внутренних файлов "
            "VSPAERO; выберите более короткий output_root"
        )
    candidate = base
    counter = 2
    while candidate.exists():
        candidate = Path(f"{base}_{counter}")
        counter += 1
    candidate.mkdir(parents=True)
    return candidate


def _certificate_relative_path(run_dir: Path, value: object) -> str | None:
    """Return a portable path only when ``value`` is sealed below ``run_dir``."""
    text = str(value or "").strip()
    if not text:
        return None
    root = run_dir.resolve()
    candidate = Path(text).resolve()
    try:
        return candidate.relative_to(root).as_posix()
    except ValueError:
        return None


def _validate_scope(scope: dict) -> dict:
    normalized = deepcopy(scope)
    intervals = normalized.get("mach_intervals")
    if not isinstance(intervals, list) or not intervals:
        raise ValueError("Область сертификации не содержит Mach-интервалов")
    clean_intervals = []
    for interval in intervals:
        if not isinstance(interval, (list, tuple)) or len(interval) != 2:
            raise ValueError("Каждый Mach-интервал должен иметь две границы")
        lo, hi = float(interval[0]), float(interval[1])
        if not all(math.isfinite(value) for value in (lo, hi)) or lo < 0.0 or hi < lo:
            raise ValueError("Некорректный Mach-интервал")
        clean_intervals.append([lo, hi])
    alpha = normalized.get("alpha_deg")
    if not isinstance(alpha, (list, tuple)) or len(alpha) != 2:
        raise ValueError("alpha_deg должен содержать начальную и конечную границы")
    alpha = [float(alpha[0]), float(alpha[1])]
    if not all(math.isfinite(value) for value in alpha) or alpha[1] < alpha[0]:
        raise ValueError("Некорректный диапазон alpha")
    beta = float(normalized.get("beta_deg", 0.0))
    if not math.isfinite(beta):
        raise ValueError("Некорректный beta")
    tail = normalized.get("horizontal_tail", {})
    if tail:
        if not isinstance(tail, dict) or tail.get("mode") not in {"fixed", "model_default"}:
            raise ValueError("horizontal_tail.mode должен быть fixed или model_default")
        if tail.get("mode") == "fixed":
            if not str(tail.get("geometry_name", "")).strip():
                raise ValueError("Для фиксированного ГО требуется точное имя geometry_name")
            incidence = float(tail.get("incidence_deg", math.nan))
            if not math.isfinite(incidence):
                raise ValueError("Некорректный угол установки ГО")
    normalized["mach_intervals"] = clean_intervals
    normalized["alpha_deg"] = alpha
    normalized["beta_deg"] = beta
    return normalized


def _software_manifest(inventory: dict, executables: dict[str, Path | None]) -> dict:
    solvers: dict[str, dict] = {
        "openvsp": {
            "version": inventory.get("openvsp_version"),
            "executable": str(executables.get("vspscript")) if executables.get("vspscript") else None,
            "sha256": sha256_file(executables["vspscript"]) if executables.get("vspscript") and executables["vspscript"].is_file() else None,
        }
    }
    for name in ("vspaero", "machline"):
        path = executables.get(name)
        solvers[name] = {
            "version": None,
            "executable": str(path) if path else None,
            "sha256": sha256_file(path) if path and path.is_file() else None,
        }
    return {
        "repairmach": "9.1",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "solvers": solvers,
    }


def _native_diagnostics(inventory: dict, semantic: dict) -> dict:
    components = []
    for item in inventory.get("components", []):
        bbox = item.get("bbox", {})
        lo, hi = bbox.get("min", [0, 0, 0]), bbox.get("max", [0, 0, 0])
        components.append({
            "id": item.get("id"),
            "name": item.get("name"),
            "type": item.get("type"),
            "bbox": bbox,
            "bbox_size": [float(hi[index]) - float(lo[index]) for index in range(3)],
            "main_surfaces": item.get("main_surfaces"),
            "total_surfaces": item.get("total_surfaces"),
            "terminal_chord": item.get("wing_terminal"),
            "limitations": [
                "surface area, volume, self-intersection and minimum-gap tests require exported TRI/degen geometry"
            ],
        })
    return {
        "schema": "repairmach.geometry-native-diagnostics/1.0",
        "inventory_valid": inventory.get("valid", False),
        "semantic_valid": semantic.get("valid", False),
        "components": components,
        "openvsp_errors": inventory.get("errors", []),
        "coverage": {
            "available": [
                "component inventory", "absolute bounding boxes", "surface counts",
                "Set membership", "terminal wing chord", "selected mesh and transform parameters",
            ],
            "requires_tri_or_degen": [
                "area", "volume", "centroid", "vertices", "faces", "minimum triangle",
                "boundary/nonmanifold edges", "normals", "self intersections", "intercomponent gaps",
            ],
        },
    }


def _vsp_script_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace('"', '\\"')


def _seal_machline_export_record(record: dict) -> dict:
    """Seal the in-memory export record which the certificate will embed.

    The final geometry certificate seals this record again, but the local
    fingerprint lets ``_tri_certification`` reject a substituted record before
    any topology repair is attempted.
    """
    sealed = deepcopy(record)
    sealed.pop("record_fingerprint", None)
    sealed["record_fingerprint"] = sha256_payload(sealed)
    return sealed


def _machline_export_record_errors(record: dict | None, tri_path: Path | None, policy: dict) -> list[str]:
    """Return fail-closed lineage errors for one OpenVSP NASCART export."""
    if not isinstance(record, dict):
        return ["нет запечатанной записи автоматического экспорта TRI"]
    errors: list[str] = []
    if record.get("schema") != MACHLINE_TRI_EXPORT_SCHEMA:
        errors.append("неподдерживаемая схема записи экспорта TRI")
    expected_fingerprint = record.get("record_fingerprint")
    unsealed = deepcopy(record)
    unsealed.pop("record_fingerprint", None)
    if not expected_fingerprint or expected_fingerprint != sha256_payload(unsealed):
        errors.append("нарушена целостность записи экспорта TRI")
    if record.get("status") != "passed":
        errors.append("автоматический экспорт TRI не подтверждён")
    source = record.get("source_solver_twin", {})
    source_path = Path(str(source.get("path", "")))
    if source.get("role") != "machline_fine" or source.get("mesh_level") != "fine":
        errors.append("TRI не связан с Fine-двойником MachLine")
    if not source_path.is_file():
        errors.append("исходный Fine-двойник MachLine отсутствует")
    elif source.get("sha256") != sha256_file(source_path):
        errors.append("хэш Fine-двойника MachLine изменился после экспорта")
    if not record.get("source_unchanged"):
        errors.append("Fine-двойник MachLine изменился во время экспорта")
    bindings = record.get("certification_bindings", {})
    required_bindings = (
        "master_sha256",
        "plan_sha256",
        "base_twin_sha256",
        "fine_twin_sha256",
        "fine_mesh_signature_sha256",
    )
    for key in required_bindings:
        if not str(bindings.get(key) or "").strip():
            errors.append(f"запись экспорта TRI не содержит binding {key}")
    if bindings.get("fine_twin_sha256") != source.get("sha256"):
        errors.append("binding Fine-двойника не совпадает с источником экспорта TRI")

    export = record.get("export", {})
    if export.get("format") != "OpenVSP_EXPORT_NASCART":
        errors.append("TRI создан не подтверждённым экспортёром OpenVSP NASCART")
    expected_sets = {
        "source_thick_user_set": int(policy["sets"]["thick_user_set"]),
        "source_thick_api_set": int(policy["sets"]["thick_user_set"]) + 3,
        "source_thin_user_set": int(policy["sets"]["thin_user_set"]),
        "source_thin_api_set": int(policy["sets"]["thin_user_set"]) + 3,
        "union_user_set": int(policy["sets"]["machline_export_user_set"]),
        "union_api_set": int(policy["sets"]["machline_export_user_set"]) + 3,
    }
    for key, expected in expected_sets.items():
        if export.get(key) != expected:
            errors.append(f"экспорт TRI выполнен с неверным {key}")
    if export.get("include_subsurfaces") is not True:
        errors.append("запись экспорта TRI не фиксирует включение подсекций")
    if export.get("thin_export_set") != "SET_NONE":
        errors.append("NASCART-экспорт толстых поверхностей должен использовать SET_NONE для thin")
    if export.get("mesh_representation") != "NASCART_thick_plus_VSPGeom_VLM_thin":
        errors.append("MachLine TRI не подтверждает раздельное thick/thin представление")
    component_map = export.get("component_map")
    if not isinstance(component_map, dict) or not component_map:
        errors.append("экспорт TRI не содержит покомпонентный NASCART-паспорт")
    key_path = Path(str(export.get("key_path", "")))
    if not key_path.is_file():
        errors.append("NASCART .key с именами компонентов отсутствует")
    elif export.get("key_sha256") != sha256_file(key_path):
        errors.append("хэш NASCART .key не совпадает")
    union_names = export.get("union_geometry_names")
    if not isinstance(union_names, list) or not union_names:
        errors.append("экспорт TRI не фиксирует состав полного union-набора")
    if export.get("coordinate_convention") != COORDINATE_CONVENTION:
        errors.append("экспорт TRI не фиксирует подтверждённое преобразование координат")
    component_roles = export.get("component_roles")
    if (
        not isinstance(component_roles, dict)
        or "thick" not in component_roles.values()
        or "thin" not in component_roles.values()
    ):
        errors.append("экспорт TRI не содержит роли thick/thin для компонентов")
    for path_key, hash_key, label in (
        ("thin_path", "thin_sha256", "VSPGeom тонких поверхностей"),
        ("thin_key_path", "thin_key_sha256", "VSPGeom .vkey"),
    ):
        artifact = Path(str(export.get(path_key, "")))
        if not artifact.is_file():
            errors.append(f"{label} отсутствует")
        elif export.get(hash_key) != sha256_file(artifact):
            errors.append(f"хэш {label} не совпадает")
    export_path = Path(str(export.get("path", "")))
    if tri_path is None or not export_path.is_file():
        errors.append("автоматически экспортированный TRI отсутствует")
    else:
        if export_path.resolve() != tri_path.resolve():
            errors.append("проверяемый TRI не совпадает с выходом автоматического экспорта")
        elif export.get("sha256") != sha256_file(export_path):
            errors.append("хэш автоматически экспортированного TRI не совпадает")
    return errors


def _read_openvsp_nascart(path: Path) -> TriMesh:
    """Read OpenVSP's line-oriented NASCART output.

    OpenVSP writes ``i j k component`` on every face row whereas RepairMach's
    canonical Cart3D writer stores the component block after all face rows.
    Parsing by records avoids confusing the fourth face value with the next
    panel's first vertex.
    """
    lines = [line.strip() for line in path.read_text(encoding="utf-8", errors="strict").splitlines() if line.strip()]
    if not lines:
        raise ValueError("OpenVSP создал пустой NASCART TRI")
    header = lines[0].split()
    if len(header) < 2:
        raise ValueError("NASCART TRI не содержит размеры сетки")
    try:
        vertex_count, face_count = int(header[0]), int(header[1])
    except ValueError as exc:
        raise ValueError("Некорректный заголовок NASCART TRI") from exc
    if vertex_count <= 0 or face_count <= 0:
        raise ValueError("NASCART TRI не содержит непустую сетку")
    expected_lines = 1 + vertex_count + face_count
    if len(lines) != expected_lines:
        raise ValueError(
            f"NASCART TRI имеет {len(lines)} строк вместо ожидаемых {expected_lines}"
        )
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    components: list[int] = []
    try:
        for line in lines[1:1 + vertex_count]:
            tokens = line.split()
            if len(tokens) != 3:
                raise ValueError("строка вершины NASCART должна содержать три координаты")
            vertices.append(tuple(float(value) for value in tokens))
        for line in lines[1 + vertex_count:]:
            tokens = line.split()
            if len(tokens) not in {3, 4}:
                raise ValueError("строка панели NASCART должна содержать три индекса и компонент")
            faces.append(tuple(int(value) - 1 for value in tokens[:3]))
            component = float(tokens[3]) if len(tokens) == 4 else 1.0
            if not math.isfinite(component) or not component.is_integer():
                raise ValueError("номер компонента NASCART не является целым")
            components.append(int(component))
    except ValueError as exc:
        raise ValueError(f"Некорректные данные NASCART TRI: {exc}") from exc
    return TriMesh(vertices, faces, components)


def _read_openvsp_nascart_key(path: Path) -> dict[int, str]:
    """Read the component-number mapping emitted beside a NASCART export."""
    if not path.is_file():
        raise ValueError("OpenVSP не создал NASCART .key с именами компонентов")
    mapping: dict[int, str] = {}
    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
    ):
        line = raw.strip()
        if not line or line.lower().startswith("color name"):
            continue
        match = re.match(r"^([+-]?\d+(?:\.0+)?)\s+(.+?)\s+([+-]?\d+)\s*$", line)
        if not match:
            continue
        component_value = float(match.group(1))
        if not component_value.is_integer():
            raise ValueError(f"Некорректный номер компонента в .key, строка {line_number}")
        component = int(component_value)
        name = match.group(2).strip()
        if component <= 0 or not name or component in mapping:
            raise ValueError(f"Некорректная или повторная запись .key, строка {line_number}")
        mapping[component] = name
    if not mapping:
        raise ValueError("NASCART .key не содержит компонентов")
    return mapping


def _mesh_component_subset(mesh: TriMesh, component_ids: set[int]) -> TriMesh:
    faces = []
    components = []
    for index, face in enumerate(mesh.faces):
        component = mesh.components[index] if index < len(mesh.components) else 1
        if component in component_ids:
            faces.append(face)
            components.append(component)
    used = sorted({vertex for face in faces for vertex in face})
    remap = {old: new for new, old in enumerate(used)}
    return TriMesh(
        [mesh.vertices[index] for index in used],
        [tuple(remap[index] for index in face) for face in faces],
        components,
    )


def _component_geometry_coverage(
    component_ids: set[int],
    component_map: dict[int, str],
    union_geometry_names: list[str],
) -> dict:
    """Map exported mesh component ids back to exact OpenVSP geometries.

    OpenVSP adds surface suffixes such as ``_S_Surf0`` and ``_C`` to the
    names written to NASCART/VSPGeom key files.  Replacement certification
    must nevertheless remain component-aware: a solver output may replace an
    omitted physical component only when that exact OpenVSP geometry is
    represented by the solver mesh.  Unresolved or ambiguous ids are kept as
    explicit evidence and make the coverage contract incomplete.
    """
    names = [str(name).strip() for name in union_geometry_names if str(name).strip()]
    covered: set[str] = set()
    resolved: dict[str, str] = {}
    unresolved: list[dict] = []
    ambiguous: list[dict] = []
    for component_id in sorted(component_ids):
        exported_name = str(component_map.get(component_id, "")).strip()
        matches = [
            name for name in names
            if exported_name == name or exported_name.startswith(name + "_")
        ]
        if not matches:
            unresolved.append({
                "component_id": component_id,
                "exported_name": exported_name or None,
            })
            continue
        longest = max(len(name) for name in matches)
        best = sorted(name for name in matches if len(name) == longest)
        if len(best) != 1:
            ambiguous.append({
                "component_id": component_id,
                "exported_name": exported_name,
                "matches": best,
            })
            continue
        resolved[str(component_id)] = best[0]
        covered.add(best[0])
    return {
        "coverage_kind": "exact_openvsp_components",
        "complete": bool(component_ids) and not unresolved and not ambiguous,
        "components": sorted(covered, key=str.casefold),
        "component_ids": sorted(component_ids),
        "resolved_component_ids": resolved,
        "unresolved_components": unresolved,
        "ambiguous_components": ambiguous,
    }


def _run_machline_geometry_qualification(
    *,
    executable: Path | None,
    mesh_path: Path,
    mesh: TriMesh,
    run_dir: Path,
    scope: dict,
    reference: dict,
    force_mask_path: Path,
    force_contract: dict,
    policy: dict,
) -> dict:
    """Probe the certified thick-body TRI at the supersonic envelope corners.

    The thin VLM plates are audited by the composite geometry but deliberately
    stay outside this solve.  MachLine's open-surface Neumann solution can have
    a small algebraic residual while producing a strongly asymmetric force
    field.  The production probe therefore solves only the watertight thick
    body and validates a component-masked per-panel force reconstruction.
    """
    settings = policy.get("qualification", {})
    if not settings.get("enabled", True):
        return {"requested": False, "valid": False, "status": "disabled", "probes": []}
    if executable is None or not executable.is_file():
        return {
            "requested": True,
            "valid": False,
            "status": "missing_executable",
            "probes": [],
            "errors": ["machline.exe отсутствует"],
        }
    if not force_mask_path.is_file():
        return {
            "requested": True,
            "valid": False,
            "status": "missing_force_mask",
            "probes": [],
            "errors": ["запечатанная маска сил отсутствует"],
        }
    mach_values = sorted({
        float(value)
        for lo, hi in scope.get("mach_intervals", [])
        for value in (lo, hi)
        if float(value) > 1.0
    })
    alpha_values = sorted({
        float(value) for value in scope.get("alpha_deg", [0.0, 0.0])
    })
    if not mach_values or not alpha_values:
        return {
            "requested": True,
            "valid": False,
            "status": "no_supersonic_scope",
            "probes": [],
            "errors": ["область не содержит сверхзвуковых угловых точек"],
        }
    beta = float(scope.get("beta_deg", 0.0))
    if abs(beta) > 1.0e-12:
        return {
            "requested": True,
            "valid": False,
            "status": "unsupported_beta",
            "probes": [],
            "errors": ["первая версия MachLine-квалификации поддерживает beta=0"],
        }

    probe_dir = run_dir / "machline" / "solver_probes"
    probe_dir.mkdir(parents=True, exist_ok=True)
    area = float(reference.get("area", 0.0))
    length = float(reference.get("cref", reference.get("length", 0.0)))
    cg_source = reference.get("CG", reference.get("center", [0.0, 0.0, 0.0]))
    if (
        area <= 0.0
        or length <= 0.0
        or not isinstance(cg_source, (list, tuple))
        or len(cg_source) != 3
    ):
        return {
            "requested": True,
            "valid": False,
            "status": "invalid_reference",
            "probes": [],
            "errors": ["для MachLine нужны положительные Sref/cref и CG X Y Z"],
        }
    cg = [float(cg_source[0]), float(cg_source[2]), -float(cg_source[1])]
    maximum_iterations = min(
        int(settings.get("max_iterations_cap", 25000)),
        max(1000, len(mesh.vertices) + int(settings.get("iteration_margin", 100))),
    )
    timeout = float(settings.get("timeout_seconds", 1800))
    residual_norm_limit = float(settings.get("max_residual_norm", 0.05))
    residual_max_limit = float(settings.get("max_residual_max", 0.01))
    lateral_limit = float(settings.get("max_abs_lateral_force", 0.005))
    records = []
    errors = []
    mesh_relative = mesh_path.resolve().relative_to(run_dir.resolve()).as_posix()
    include_ids = [int(value) for value in force_contract.get("include_component_ids", [])]
    exclude_ids = [int(value) for value in force_contract.get("exclude_component_ids", [])]

    for mach in mach_values:
        for alpha in alpha_values:
            token = f"M{mach:g}_A{alpha:g}".replace("-", "m").replace(".", "p")
            input_path = probe_dir / f"{token}.json"
            log_path = probe_dir / f"{token}.log"
            report_path = probe_dir / f"{token}_report.json"
            body_path = probe_dir / f"{token}_body.vtk"
            masked_path = probe_dir / f"{token}_masked_force.json"
            input_relative = input_path.resolve().relative_to(run_dir.resolve()).as_posix()
            report_relative = report_path.resolve().relative_to(run_dir.resolve()).as_posix()
            body_relative = body_path.resolve().relative_to(run_dir.resolve()).as_posix()
            direction = nascart_freestream(alpha, beta)
            payload = {
                "flow": {
                    "freestream_velocity": [100.0 * value for value in direction],
                    "gamma": 1.4,
                    "freestream_mach_number": mach,
                },
                "geometry": {
                    "file": mesh_relative,
                    "spanwise_axis": "+z",
                    "wake_model": {"wake_present": False, "append_wake": False},
                    "reference": {"area": area, "length": length, "CG": cg},
                },
                "solver": {
                    "formulation": "neumann-doublet-only-mass-flux",
                    "matrix_solver": "GMRES",
                    "preconditioner": "DIAG",
                    "tolerance": float(settings.get("tolerance", 1.0e-10)),
                    "max_iterations": maximum_iterations,
                    "control_point_offset": float(settings.get("control_point_offset", 0.001)),
                },
                "post_processing": {
                    "pressure_rules": {"isentropic": True},
                    "pressure_for_forces": "isentropic",
                },
                "output": {
                    "body_file": body_relative,
                    "wake_file": "none",
                    "control_point_file": "none",
                    "report_file": report_relative,
                },
            }
            write_json(input_path, payload)
            started = time.monotonic()
            try:
                process = subprocess.run(
                    [str(executable.resolve())],
                    input=input_relative + "\n",
                    text=True,
                    cwd=str(run_dir.resolve()),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=timeout,
                    env={**os.environ, "OMP_NUM_THREADS": str(settings.get("ncpu", 4))},
                )
                output = process.stdout or ""
                log_path.write_text(output, encoding="utf-8", errors="ignore")
                record = {
                    "mach": mach,
                    "alpha_deg": alpha,
                    "input": str(input_path.resolve()),
                    "input_sha256": sha256_file(input_path),
                    "log": str(log_path.resolve()),
                    "log_sha256": sha256_file(log_path),
                    "return_code": process.returncode,
                    "runtime_seconds": time.monotonic() - started,
                    "max_iterations": maximum_iterations,
                    "valid": False,
                    "errors": [],
                }
                if process.returncode != 0:
                    record["errors"].append(f"MachLine return code {process.returncode}")
                if not report_path.is_file() or not body_path.is_file():
                    record["errors"].append("MachLine не создал report/body VTK")
                else:
                    report = json.loads(report_path.read_text(encoding="utf-8"))
                    solver = report.get("solver_results", {})
                    residual = solver.get("residual", {})
                    status_code = int(solver.get("solver_status_code", -1))
                    residual_norm = float(residual.get("norm", math.nan))
                    residual_max = float(residual.get("max", math.nan))
                    if status_code != 0:
                        record["errors"].append(f"solver_status_code={status_code}")
                    if not math.isfinite(residual_norm) or residual_norm > residual_norm_limit:
                        record["errors"].append(
                            f"residual.norm={residual_norm:g} > {residual_norm_limit:g}"
                        )
                    if not math.isfinite(residual_max) or residual_max > residual_max_limit:
                        record["errors"].append(
                            f"residual.max={residual_max:g} > {residual_max_limit:g}"
                        )
                    if not re.search(r"Found\s+0\s+superinclined panels", output):
                        record["errors"].append("журнал не подтверждает 0 superinclined panels")
                    masked = masked_force_coefficients(
                        mesh=mesh,
                        body_vtk_path=body_path,
                        reference_area=area,
                        include_component_ids=include_ids,
                        exclude_component_ids=exclude_ids,
                        report_total_forces=report.get("total_forces", {}),
                        freestream_velocity=payload["flow"]["freestream_velocity"],
                        spanwise_axis="+z",
                    )
                    lateral = abs(float(masked["masked_wind_axes"]["cy_span"]))
                    if lateral > lateral_limit:
                        record["errors"].append(
                            f"симметричный beta=0 дал |CYspan|={lateral:g} > {lateral_limit:g}"
                        )
                    write_masked_force_record(
                        masked_path,
                        mesh_path=mesh_path,
                        body_vtk_path=body_path,
                        report_path=report_path,
                        force_mask_path=force_mask_path,
                        result=masked,
                    )
                    record.update({
                        "report": str(report_path.resolve()),
                        "report_sha256": sha256_file(report_path),
                        "body_vtk": str(body_path.resolve()),
                        "body_vtk_sha256": sha256_file(body_path),
                        "solver_status_code": status_code,
                        "iterations": solver.get("iterations"),
                        "residual_norm": residual_norm,
                        "residual_max": residual_max,
                        "masked_force": str(masked_path.resolve()),
                        "masked_force_sha256": sha256_file(masked_path),
                        "masked_wind_axes": masked["masked_wind_axes"],
                    })
                record["valid"] = not record["errors"]
            except subprocess.TimeoutExpired:
                record = {
                    "mach": mach,
                    "alpha_deg": alpha,
                    "valid": False,
                    "errors": [f"MachLine timeout {timeout:g} s"],
                    "runtime_seconds": time.monotonic() - started,
                }
            except Exception as exc:
                record = {
                    "mach": mach,
                    "alpha_deg": alpha,
                    "valid": False,
                    "errors": [str(exc)],
                    "runtime_seconds": time.monotonic() - started,
                }
            records.append(record)
            if not record["valid"]:
                errors.extend(
                    f"M={mach:g}, alpha={alpha:g}: {message}"
                    for message in record["errors"]
                )

    result = {
        "requested": True,
        "valid": not errors and len(records) == len(mach_values) * len(alpha_values),
        "status": "passed" if not errors else "failed",
        "formulation": "neumann-doublet-only-mass-flux",
        "matrix_solver": "GMRES",
        "mesh_role": "watertight_thick_body_with_tagged_numerical_closure",
        "thin_surface_contract": "audited_by_composite_mesh_solved_by_vspaero",
        "force_output": "component_masked_pressure_wave_without_base_drag",
        "limits": {
            "residual_norm": residual_norm_limit,
            "residual_max": residual_max_limit,
            "abs_lateral_force": lateral_limit,
        },
        "probes": records,
        "errors": errors,
    }
    write_json(probe_dir / "qualification.json", result)
    return result


def _export_machline_tri(
    *,
    solver_twin_record: dict,
    run_dir: Path,
    policy: dict,
    vspscript_executable: Path,
    certification_bindings: dict,
    legacy_tri_hint: Path | None = None,
) -> dict:
    """Export and seal the only TRI eligible for the MachLine backend.

    A user-supplied TRI is retained only as an audit hint.  It is never used as
    solver geometry and therefore cannot inherit the certificate of a VSP3
    twin merely by being passed on the command line.
    """
    export_dir = run_dir / "machline" / "export"
    export_dir.mkdir(parents=True, exist_ok=True)
    source_path = Path(str(solver_twin_record.get("path", "")))
    raw_path = export_dir / "openvsp_nascart_raw.tri"
    key_path = raw_path.with_suffix(".key")
    local_model_path = export_dir / "openvsp_thin_model.vsp3"
    thin_path = local_model_path.with_suffix(".vspgeom")
    thin_key_path = local_model_path.with_suffix(".vkey")
    output_path = export_dir / "machline_fine_export.tri"
    script_path = export_dir / "export_machline_tri.vspscript"
    log_path = export_dir / "export_machline_tri.log"
    manifest_path = export_dir / "export_manifest.json"
    thick_user_set = int(policy["sets"]["thick_user_set"])
    thin_user_set = int(policy["sets"]["thin_user_set"])
    thick_api_set = thick_user_set + 3
    thin_api_set = thin_user_set + 3
    empty_thick_api_set = int(policy["sets"]["empty_thick_user_set"]) + 3
    union_user_set = int(policy["sets"]["machline_export_user_set"])
    union_api_set = union_user_set + 3
    source_before = sha256_file(source_path) if source_path.is_file() else None
    bindings = deepcopy(certification_bindings)
    legacy_hint = None
    if legacy_tri_hint is not None:
        legacy_hint = {
            "path": str(legacy_tri_hint.resolve()),
            "sha256": sha256_file(legacy_tri_hint) if legacy_tri_hint.is_file() else None,
            "used_as_solver_geometry": False,
            "reason": "legacy input is audit-only; certified TRI is exported from machline/fine VSP3",
        }
    errors: list[str] = []
    return_code = None
    component_map: dict[int, str] = {}
    component_face_counts: dict[int, int] = {}
    component_roles: dict[int, str] = {}
    union_geometry_names: list[str] = []
    if not source_path.is_file():
        errors.append("Fine-двойник MachLine отсутствует")
    required_bindings = (
        "master_sha256",
        "plan_sha256",
        "base_twin_sha256",
        "fine_twin_sha256",
        "fine_mesh_signature_sha256",
    )
    for key in required_bindings:
        if not str(bindings.get(key) or "").strip():
            errors.append(f"не задан binding {key} для экспорта TRI")
    if source_before and bindings.get("fine_twin_sha256") != source_before:
        errors.append("Fine-двойник экспорта не совпадает с запечатанным mesh record")
    if not errors:
        shutil.copy2(source_path, local_model_path)
        script_text = f'''void main()
{{
    ClearVSPModel();
    ReadVSPFile( "{_vsp_script_path(local_model_path)}" );
    Update();
    array<string> all_geoms = FindGeoms();
    int union_count = 0;
    for ( int gi = 0; gi < int(all_geoms.length()); gi++ )
    {{
        bool selected = GetSetFlag( all_geoms[gi], {thick_api_set} ) || GetSetFlag( all_geoms[gi], {thin_api_set} );
        SetSetFlag( all_geoms[gi], {union_api_set}, selected );
        if ( selected )
        {{
            union_count++;
            Print( "REPAIRMACH_MACHLINE_UNION_GEOM=" + GetGeomName( all_geoms[gi] ) );
        }}
    }}
    SetSetName( {union_api_set}, "RM_CERT_MACHLINE_ALL" );
    Print( "REPAIRMACH_MACHLINE_UNION_COUNT=" + union_count );
    string export_id = ExportFile( "{_vsp_script_path(raw_path)}", {thick_api_set}, EXPORT_NASCART, 1, SET_NONE );
    Print( "REPAIRMACH_MACHLINE_TRI_EXPORT_ID=" + export_id );
    string compute_name = "VSPAEROComputeGeometry";
    SetAnalysisInputDefaults( compute_name );
    array<int> empty_thick_input(1, {empty_thick_api_set});
    array<int> thin_input(1, {thin_api_set});
    SetIntAnalysisInput( compute_name, "GeomSet", empty_thick_input );
    SetIntAnalysisInput( compute_name, "ThinGeomSet", thin_input );
    string compute_id = ExecAnalysis( compute_name );
    Print( "REPAIRMACH_MACHLINE_THIN_COMPUTE_ID=" + compute_id );
    Print( "REPAIRMACH_MACHLINE_TRI_EXPORT_OK=1" );
    while ( GetNumTotalErrors() > 0 )
    {{
        ErrorObj err = PopLastError();
        Print( "OPENVSP_ERROR=" + err.GetErrorString() );
    }}
}}
'''
        script_path.write_text(script_text, encoding="utf-8")
        try:
            return_code = run_vspscript(
                vspscript_executable,
                script_path,
                log_path,
                working_dir=export_dir,
                timeout_seconds=float(policy["probes"]["openvsp_load"].get("timeout_seconds", 180)),
            )
            log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
            union_geometry_names = [
                item.strip()
                for item in re.findall(r"REPAIRMACH_MACHLINE_UNION_GEOM=([^\r\n]+)", log_text)
                if item.strip()
            ]
            if return_code not in (0, 1) or "REPAIRMACH_MACHLINE_TRI_EXPORT_OK=1" not in log_text:
                errors.append("OpenVSP не подтвердил NASCART-экспорт TRI")
            elif not raw_path.is_file():
                errors.append("OpenVSP подтвердил экспорт, но TRI-файл отсутствует")
            else:
                thick_mesh = _read_openvsp_nascart(raw_path)
                thick_names = _read_openvsp_nascart_key(key_path)
                unknown_components = sorted(set(thick_mesh.components) - set(thick_names))
                if unknown_components:
                    raise ValueError(
                        "NASCART TRI содержит компоненты без записей .key: "
                        + ", ".join(str(value) for value in unknown_components)
                    )
                component_offset = max(thick_names or {0: ""})
                thin_mesh, thin_names = read_vspgeom_thin(
                    thin_path,
                    thin_key_path,
                    component_offset=component_offset,
                )
                component_map = {**thick_names, **thin_names}
                missing_geometries = [
                    name for name in union_geometry_names
                    if not any(
                        component_name == name
                        or component_name.startswith(name + "_")
                        for component_name in component_map.values()
                    )
                ]
                if missing_geometries:
                    raise ValueError(
                        "NASCART-экспорт потерял выбранные геометрии: "
                        + ", ".join(missing_geometries)
                    )
                exported_mesh = combine_meshes([thick_mesh, thin_mesh])
                component_roles = {
                    **{component: "thick" for component in thick_names},
                    **{component: "thin" for component in thin_names},
                }
                for component in exported_mesh.components:
                    component_face_counts[component] = component_face_counts.get(component, 0) + 1
                write_tri(output_path, exported_mesh)
        except Exception as exc:
            errors.append(f"сбой автоматического экспорта TRI: {exc}")
    source_after = sha256_file(source_path) if source_path.is_file() else None
    source_unchanged = bool(source_before and source_before == source_after)
    if source_before and not source_unchanged:
        errors.append("Fine-двойник MachLine изменился во время экспорта")
    if not output_path.is_file() and not errors:
        errors.append("канонический TRI после экспорта отсутствует")

    record = {
        "schema": MACHLINE_TRI_EXPORT_SCHEMA,
        "status": "passed" if not errors else "failed",
        "source_solver_twin": {
            "role": "machline_fine",
            "mesh_level": "fine",
            "path": str(source_path.resolve()),
            "sha256": source_before,
            "canonical_sha256": solver_twin_record.get("canonical_sha256"),
        },
        "certification_bindings": bindings,
        "source_unchanged": source_unchanged,
        "export": {
            "format": "OpenVSP_EXPORT_NASCART",
            "source_thick_user_set": thick_user_set,
            "source_thick_api_set": thick_api_set,
            "source_thin_user_set": thin_user_set,
            "source_thin_api_set": thin_api_set,
            "union_user_set": union_user_set,
            "union_api_set": union_api_set,
            "thin_export_set": "SET_NONE",
            "include_subsurfaces": True,
            "coordinate_convention": COORDINATE_CONVENTION,
            "mesh_representation": "NASCART_thick_plus_VSPGeom_VLM_thin",
            "raw_path": str(raw_path.resolve()),
            "raw_sha256": sha256_file(raw_path) if raw_path.is_file() else None,
            "key_path": str(key_path.resolve()),
            "key_sha256": sha256_file(key_path) if key_path.is_file() else None,
            "thin_path": str(thin_path.resolve()),
            "thin_sha256": sha256_file(thin_path) if thin_path.is_file() else None,
            "thin_key_path": str(thin_key_path.resolve()),
            "thin_key_sha256": sha256_file(thin_key_path) if thin_key_path.is_file() else None,
            "component_map": {str(key): value for key, value in sorted(component_map.items())},
            "component_roles": {str(key): value for key, value in sorted(component_roles.items())},
            "component_face_counts": {
                str(key): value for key, value in sorted(component_face_counts.items())
            },
            "union_geometry_names": union_geometry_names,
            "path": str(output_path.resolve()),
            "sha256": sha256_file(output_path) if output_path.is_file() else None,
        },
        "tool": {
            "vspscript_executable": str(vspscript_executable.resolve()),
            "vspscript_sha256": sha256_file(vspscript_executable) if vspscript_executable.is_file() else None,
            "script": str(script_path.resolve()),
            "script_sha256": sha256_file(script_path) if script_path.is_file() else None,
            "log": str(log_path.resolve()),
            "log_sha256": sha256_file(log_path) if log_path.is_file() else None,
            "return_code": return_code,
        },
        "legacy_tri_hint": legacy_hint,
        "errors": errors,
        "manifest_path": str(manifest_path.resolve()),
    }
    sealed = _seal_machline_export_record(record)
    write_json(manifest_path, sealed)
    return sealed


def _tri_certification(
    tri_path: Path | None,
    run_dir: Path,
    policy: dict,
    scope: dict,
    *,
    export_record: dict | None = None,
    reference: dict | None = None,
    machline_executable: Path | None = None,
) -> tuple[dict, list[dict]]:
    if tri_path is None:
        if export_record is not None:
            errors = _machline_export_record_errors(export_record, None, policy)
            return {
                "requested": True,
                "eligible": False,
                "status": "blocked_lineage",
                "export_record": export_record,
                "errors": errors,
            }, [finding(
                "GEO-TRI-LINEAGE-001", "BLOCKER",
                "MachLine TRI не имеет проверяемого происхождения от Fine-двойника",
                scope="machline", evidence={"errors": errors},
            )]
        return {
            "requested": False,
            "eligible": False,
            "status": "not_requested",
            "reason": "TRI не передан; сертификат MachLine не выдавался",
        }, []
    findings: list[dict] = []
    lineage_errors = _machline_export_record_errors(export_record, tri_path, policy)
    if lineage_errors:
        findings.append(finding(
            "GEO-TRI-LINEAGE-001", "BLOCKER",
            "MachLine TRI не имеет проверяемого происхождения от Fine-двойника",
            scope="machline", evidence={"errors": lineage_errors},
        ))
        return {
            "requested": True,
            "eligible": False,
            "status": "blocked_lineage",
            "source": str(tri_path.resolve()),
            "source_sha256": sha256_file(tri_path) if tri_path.is_file() else None,
            "export_record": export_record,
            "errors": lineage_errors,
        }, findings
    tri_dir = run_dir / "machline"
    tri_dir.mkdir(parents=True, exist_ok=True)
    source_copy = tri_dir / "source_mesh.tri"
    shutil.copy2(tri_path, source_copy)
    try:
        mesh = read_tri(source_copy)
        initial = diagnose_tri(mesh)
    except Exception as exc:
        findings.append(finding("GEO-TRI-001", "BLOCKER", f"TRI не читается: {exc}", scope="machline"))
        return {
            "requested": True,
            "eligible": False,
            "status": "unreadable",
            "export_record": export_record,
        }, findings
    write_json(tri_dir / "native_diagnostics.json", initial)
    blockers = initial["invalid_index_faces"] + initial["repeated_vertex_faces"]
    max_count = min(
        int(policy["machline"]["max_changed_panels"]),
        max(0, math.floor(float(policy["machline"]["max_changed_fraction"]) * max(1, initial["faces"]))),
    )
    if blockers:
        findings.append(finding(
            "GEO-TRI-001", "BLOCKER",
            "TRI содержит invalid-index или repeated-index дефекты",
            scope="machline", evidence={"diagnostics": initial},
        ))
        return {
            "requested": True, "eligible": False, "status": "blocked_topology",
            "source": str(source_copy), "source_sha256": sha256_file(source_copy),
            "initial": initial, "repair_budget": max_count, "export_record": export_record,
        }, findings

    component_map = {
        int(key): str(value)
        for key, value in (export_record or {}).get("export", {}).get("component_map", {}).items()
    }
    prepared, preparation_log = prepare_machline_geometry(
        mesh,
        scope=scope,
        component_names=component_map,
        policy=policy["machline"],
        reference=reference or {"area": 1.0, "cref": 1.0},
    )
    duplicate_log = preparation_log.get("duplicate_skin_repair", {})
    repair_logs = [
        preparation_log.get("generic_repair", {}),
        preparation_log.get("final_repair", {}),
    ]
    repair_count = int(duplicate_log.get("removed_faces", 0))
    for repair_log in repair_logs:
        repair_count += sum(int(repair_log.get(key, 0)) for key in (
            "removed_invalid_faces",
            "removed_degenerate_faces",
            "removed_duplicate_faces",
            "flipped_faces_for_consistency",
        ))
    repair_budget_ok = repair_count <= max_count
    if not repair_budget_ok:
        findings.append(finding(
            "GEO-TRI-002", "BLOCKER", "Безопасный TRI-ремонт превышает бюджет",
            scope="machline", evidence={"required": repair_count, "limit": max_count},
        ))

    audit_path = tri_dir / "certified_audit_mesh.tri"
    write_tri(audit_path, prepared)
    audit_final = diagnose_tri(prepared)
    write_json(tri_dir / "audit_diagnostics.json", audit_final)
    write_json(tri_dir / "preparation_log.json", preparation_log)
    component_roles = {
        int(key): str(value)
        for key, value in (export_record or {}).get("export", {}).get("component_roles", {}).items()
    }
    closure = preparation_log.get("downstream_axial_closure", {})
    for component in closure.get("surrogate_component_ids", []):
        component_map[int(component)] = "RM_NUMERICAL_AFT_CLOSURE"
    thick_component_ids = {
        component for component, role in component_roles.items() if role == "thick"
    }
    union_geometry_names = [
        str(name)
        for name in (export_record or {}).get("export", {}).get(
            "union_geometry_names", []
        )
    ]
    pressure_wave_coverage = _component_geometry_coverage(
        thick_component_ids,
        component_map,
        union_geometry_names,
    )
    delegated_thin_coverage = _component_geometry_coverage(
        {
            component
            for component, role in component_roles.items()
            if role == "thin"
        },
        component_map,
        union_geometry_names,
    )
    surrogate_component_ids = {
        int(value) for value in closure.get("surrogate_component_ids", [])
    }
    solver_component_ids = thick_component_ids | surrogate_component_ids
    solver_mesh = _mesh_component_subset(prepared, solver_component_ids)
    final_path = tri_dir / "certified_mesh.tri"
    write_tri(final_path, solver_mesh)
    final = diagnose_tri(solver_mesh)
    write_json(tri_dir / "final_diagnostics.json", final)
    thick_watertight = bool(
        final["machline_safe_topology"] and final["watertight"]
    )
    topology_contract = {
        "audit_mesh": str(audit_path.resolve()),
        "audit_mesh_safe_topology": audit_final["machline_safe_topology"],
        "audit_mesh_boundary_edges": audit_final["boundary_edges"],
        "thin_component_ids": sorted(
            component for component, role in component_roles.items() if role == "thin"
        ),
        "physical_thick_component_ids": sorted(thick_component_ids),
        "surrogate_component_ids": sorted(surrogate_component_ids),
        "solver_component_ids": sorted(solver_component_ids),
        "solver_mesh_diagnostics": final,
        "intentional_open_boundaries_allowed_only_on_thin_components": True,
        "solver_mesh_is_watertight_thick_body": thick_watertight,
        "thin_surfaces_audited_but_not_solved_by_machline": True,
        "pressure_wave_component_coverage": pressure_wave_coverage,
        "delegated_thin_component_coverage": delegated_thin_coverage,
    }
    write_json(tri_dir / "topology_contract.json", topology_contract)
    topology_ok = bool(
        audit_final["machline_safe_topology"]
        and final["machline_safe_topology"]
        and thick_watertight
    )
    if not topology_ok:
        findings.append(finding(
            "GEO-TRI-003", "BLOCKER",
            "После ограниченного ремонта TRI не удовлетворяет топологии MachLine",
            scope="machline", evidence={
                "audit_diagnostics": audit_final,
                "solver_diagnostics": final,
            },
        ))
    coverage_ok = bool(
        pressure_wave_coverage["complete"]
        and delegated_thin_coverage["complete"]
    )
    if not coverage_ok:
        findings.append(finding(
            "GEO-MACH-COVERAGE-001", "BLOCKER",
            "Покомпонентное покрытие MachLine не удалось однозначно связать с OpenVSP",
            scope="machline", evidence={
                "pressure_wave": pressure_wave_coverage,
                "delegated_thin": delegated_thin_coverage,
            },
        ))

    mach_scans = scan_scope(prepared, scope, component_map)
    mach_ok = all(not scan["bad_panels"] for scan in mach_scans)
    write_json(tri_dir / "mach_criterion.json", {
        "coordinate_convention": COORDINATE_CONVENTION,
        "scope": scope,
        "scans": mach_scans,
    })
    if not mach_ok:
        findings.append(finding(
            "GEO-MACH-001", "BLOCKER",
            "MachLine-критерий не выполнен во всём диапазоне Mach и alpha; "
            "автоматическое абсолютное смещение вершин запрещено",
            scope="machline", evidence={"scans": mach_scans},
        ))

    force_contract = {
        "required": True,
        "include_component_ids": sorted(thick_component_ids),
        "exclude_component_ids": sorted(surrogate_component_ids),
        "base_drag_replacement_required": bool(closure.get("accepted")),
        "thin_component_ids_delegated_to_vspaero": topology_contract["thin_component_ids"],
        "pressure_wave_components": pressure_wave_coverage["components"],
        "delegated_thin_components": delegated_thin_coverage["components"],
        "component_coverage_complete": bool(
            pressure_wave_coverage["complete"]
            and delegated_thin_coverage["complete"]
        ),
        "output_kind": "masked_thick_body_pressure_wave",
    }
    force_mask_path = tri_dir / "force_integration_mask.json"
    write_json(force_mask_path, {
        "schema": "repairmach.machline-force-mask/1.0",
        "certified_tri": str(final_path.resolve()),
        "certified_tri_sha256": sha256_file(final_path),
        "audit_tri": str(audit_path.resolve()),
        "audit_tri_sha256": sha256_file(audit_path),
        "coordinate_convention": COORDINATE_CONVENTION,
        **force_contract,
    })
    if closure.get("accepted"):
        findings.append(finding(
            "GEO-MACH-SURROGATE-001", "INFO",
            "Плоский кормовой срез заменён ограниченным численным замыканием; "
            "MASTER и физические панели перед срезом не изменены",
            scope="machline", evidence={"closures": closure.get("closures", [])},
        ))

    geometry_eligible = bool(
        topology_ok and coverage_ok and mach_ok and repair_budget_ok
    )
    qualification = {
        "requested": False,
        "valid": False,
        "status": "geometry_not_eligible",
        "probes": [],
    }
    if geometry_eligible:
        qualification = _run_machline_geometry_qualification(
            executable=machline_executable,
            mesh_path=final_path,
            mesh=solver_mesh,
            run_dir=run_dir,
            scope=scope,
            reference=reference or {"area": 1.0, "cref": 1.0, "CG": [0.0, 0.0, 0.0]},
            force_mask_path=force_mask_path,
            force_contract=force_contract,
            policy=policy["machline"],
        )
    masked_output_available = bool(
        policy["machline"].get("solver_surrogate", {})
        .get("masked_force_postprocessor_available", False)
    )
    force_contract_ok = bool(masked_output_available and qualification.get("valid"))
    if not masked_output_available:
        findings.append(finding(
            "GEO-MACH-SURROGATE-002", "BLOCKER",
            "Допуск сил закрыт: автоматический покомпонентный постпроцессор отключён",
            scope="machline", evidence={
                "force_mask": str(force_mask_path.resolve()),
                "contract": force_contract,
            },
        ))
    elif not qualification.get("valid"):
        findings.append(finding(
            "GEO-MACH-SOLVER-001", "BLOCKER",
            "Подготовленная толстотельная сетка не прошла реальные угловые "
            "пробы MachLine и проверку маскированных сил",
            scope="machline", evidence={
                "status": qualification.get("status"),
                "errors": qualification.get("errors", []),
            },
        ))
    else:
        findings.append(finding(
            "GEO-MACH-SOLVER-002", "INFO",
            "Толстотельный двойник прошёл реальные угловые пробы MachLine, "
            "контроль невязки, симметрии и покомпонентной маски сил",
            scope="machline", evidence={
                "probe_count": len(qualification.get("probes", [])),
                "limits": qualification.get("limits", {}),
            },
        ))

    eligible = bool(geometry_eligible and force_contract_ok)
    if eligible:
        status = "passed"
    elif not repair_budget_ok:
        status = "repair_budget_exceeded"
    elif geometry_eligible and not force_contract_ok:
        status = (
            "solver_qualification_failed"
            if masked_output_available
            else "prepared_requires_masked_force_integration"
        )
    else:
        status = "failed"
    return {
        "requested": True,
        "eligible": eligible,
        "geometry_eligible": geometry_eligible,
        "force_output_eligible": force_contract_ok,
        "standalone_total_force_eligible": bool(
            force_contract_ok and not force_contract["base_drag_replacement_required"]
        ),
        "hybrid_pressure_wave_eligible": force_contract_ok,
        "status": status,
        "source": str(source_copy.resolve()),
        "source_sha256": sha256_file(source_copy),
        "certified_tri": str(final_path.resolve()),
        "certified_tri_sha256": sha256_file(final_path),
        "audit_tri": str(audit_path.resolve()),
        "audit_tri_sha256": sha256_file(audit_path),
        "initial": initial,
        "final": final,
        "audit_final": audit_final,
        "repairs": preparation_log,
        "repair_budget": max_count,
        "repair_count": repair_count,
        "mach_scans": mach_scans,
        "good_panels_degraded": 0,
        "coordinate_convention": COORDINATE_CONVENTION,
        "topology_contract": topology_contract,
        "component_coverage": {
            "pressure_wave": pressure_wave_coverage,
            "delegated_to_vspaero": delegated_thin_coverage,
        },
        "force_integration_mask": (
            str(force_mask_path.resolve()) if force_mask_path.is_file() else None
        ),
        "machline_qualification": qualification,
        "export_record": export_record,
    }, findings


def parse_vspaero_health(log_text: str) -> list[str]:
    patterns = {
        "timeout": r"REPAIRMACH_VSPSCRIPT_TIMEOUT",
        # Infinite optional ratios (notably E/LoD at alpha=0) are normal
        # VSPAERO diagnostics.  Mandatory CL/CD fields are checked as finite
        # values by validate_vspaero_run_outputs.
        "nan_or_inf": r"(?i)(?:^|[^A-Za-z])nan(?:\([^)]*\))?(?:[^A-Za-z]|$)|floating point exception",
        "not_convex": r"(?i)not\s+convex",
        "upwind_loop": r"(?i)upwind.*loop",
        "zero_magnitude": r"(?i)(?:zero\s+magnitude|Mag:\s*0(?:\.0*)?(?:\s|$))",
        "fatal": r"(?i)(?:segmentation fault|access violation|fatal error)",
    }
    return [name for name, pattern in patterns.items() if re.search(pattern, log_text)]


_VSPAERO_FATAL_HEALTH_SIGNALS = frozenset({
    "timeout",
    "nan_or_inf",
    "upwind_loop",
    "fatal",
})


def classify_vspaero_health(signals: list[str]) -> tuple[list[str], list[str]]:
    """Split solver health signals into invalidating errors and retained warnings.

    VSPAERO can report zero-magnitude loops and non-convex panels while still
    producing a complete, finite and mesh-converged solution.  Those messages
    remain visible in the certificate, but are not equivalent to a timeout,
    non-finite solution, upwind-loop failure, or fatal process error.
    """
    errors = [item for item in signals if item in _VSPAERO_FATAL_HEALTH_SIGNALS]
    warnings = [item for item in signals if item not in _VSPAERO_FATAL_HEALTH_SIGNALS]
    return errors, warnings


def _probe_setup_errors(
    set_report: dict,
    *,
    requested_engine_boundary: str,
    tail_geometry_name: str | None,
    tail_incidence_deg: float,
) -> list[str]:
    """Compare machine readback with the exact qualification request."""
    errors: list[str] = []
    actual_engine = str(
        set_report.get("engine_setup", {}).get("MODE", "")
    ).strip().lower().replace("-", "_").replace(" ", "_")
    expected_engine = str(requested_engine_boundary).strip().lower().replace(
        "-", "_"
    ).replace(" ", "_")
    engine_aliases = {
        "none": "model",
        "default": "model",
        "model_default": "model",
        "toface": "to_face",
    }
    actual_engine = engine_aliases.get(actual_engine, actual_engine)
    expected_engine = engine_aliases.get(expected_engine, expected_engine)
    if not actual_engine or actual_engine != expected_engine:
        errors.append(
            "Фактический Engine boundary из лога не совпадает с квалификационной постановкой"
        )

    actual_tail = set_report.get("tail_setup", {})
    actual_tail_mode = str(actual_tail.get("MODE", "")).strip().lower()
    if tail_geometry_name:
        if actual_tail_mode != "fixed_incidence":
            errors.append("Фактический режим ГО из лога не является fixed_incidence")
        if str(actual_tail.get("NAME", "")).strip() != str(tail_geometry_name).strip():
            errors.append("Фактическое имя ГО из лога не совпадает с постановкой")
        try:
            actual_incidence = float(actual_tail["ACTUAL_ANGLE_DEG"])
        except (KeyError, TypeError, ValueError):
            actual_incidence = math.nan
        if (
            not math.isfinite(actual_incidence)
            or abs(actual_incidence - float(tail_incidence_deg)) > 1.0e-9
        ):
            errors.append("Фактический угол ГО из лога не совпадает с постановкой")
    elif actual_tail_mode != "model_default":
        errors.append("Фактический режим ГО из лога не является model_default")
    return errors


def _run_one_vspaero_probe(
    *,
    model: Path,
    probe_dir: Path,
    mode: str,
    level: str,
    reference: dict,
    policy: dict,
    vspscript_executable: Path,
    mach: float | None = None,
    alpha_deg: float | None = None,
    engine_boundary: str | None = None,
    diagnostic_ignore_fixed_tail: bool = False,
    numerical_overrides: dict | None = None,
) -> dict:
    probe_dir.mkdir(parents=True, exist_ok=True)
    probe_model = probe_dir / "model.vsp3"
    shutil.copy2(model, probe_model)
    script = probe_dir / "probe.vspscript"
    results_csv = probe_dir / "probe.csv"
    log = probe_dir / "probe.log"
    settings = deepcopy(policy["probes"]["vspaero"])
    if numerical_overrides:
        allowed_controls = {
            "ncpu",
            "forward_gmres_convergence_factor",
            "wake_num_iter",
            "num_wake_nodes",
            "wake_relax",
        }
        unknown_controls = sorted(set(numerical_overrides) - allowed_controls)
        if unknown_controls:
            raise ValueError(
                "Неизвестные численные настройки VSPAERO: "
                + ", ".join(unknown_controls)
            )
        settings.update(numerical_overrides)
    requested_mach = float(settings["mach"] if mach is None else mach)
    requested_alpha = float(settings["alpha_deg"] if alpha_deg is None else alpha_deg)
    requested_beta = float(policy["scope"].get("beta_deg", 0.0))
    requested_boundary = str(engine_boundary or "model")
    tail_scope = policy.get("scope", {}).get("horizontal_tail", {})
    tail_name = None
    tail_incidence = 0.0
    if tail_scope.get("mode") == "fixed" and not diagnostic_ignore_fixed_tail:
        tail_name = str(tail_scope.get("geometry_name", "")).strip() or None
        tail_incidence = float(tail_scope.get("incidence_deg", 0.0))
    thick_user_set = (
        int(policy["sets"]["empty_thick_user_set"])
        if mode == "lifting"
        else int(policy["sets"]["thick_user_set"])
    )
    generate_vspaero_sweep_script(
        script,
        probe_model,
        results_csv,
        mach_start=requested_mach,
        mach_end=requested_mach,
        mach_points=1,
        alpha_start=requested_alpha,
        alpha_end=requested_alpha,
        alpha_points=1,
        beta_deg=requested_beta,
        reference_area=float(reference["area"]),
        reference_chord=float(reference["cref"]),
        reference_span=float(reference["bref"]),
        center=list(reference["center"]),
        fuselage_user_set=thick_user_set,
        wing_user_set=int(policy["sets"]["thin_user_set"]),
        ncpu=int(settings.get("ncpu", 4)),
        forward_gmres_convergence_factor=float(
            settings.get("forward_gmres_convergence_factor", 1.0)
        ),
        wake_num_iter=int(settings.get("wake_num_iter", 8)),
        num_wake_nodes=int(settings.get("num_wake_nodes", 24)),
        wake_relax=float(settings.get("wake_relax", 0.8)),
        tail_geometry_name=tail_name,
        tail_incidence_deg=tail_incidence,
        engine_boundary=requested_boundary,
        engine_geometry_aliases=[
            str(name)
            for name, base in policy.get("semantics", {}).get("aliases", {}).items()
            if base == "Gondola"
        ],
        require_canonical_components=(mode == "mixed"),
        require_nonempty_fuselage_set=(mode == "mixed"),
        require_all_geometries_assigned=(mode == "mixed"),
    )
    started = time.monotonic()
    code = run_vspscript(
        vspscript_executable,
        script,
        log,
        working_dir=probe_dir,
        timeout_seconds=float(settings.get("timeout_seconds", 600)),
    )
    elapsed = time.monotonic() - started
    log_text = log.read_text(encoding="utf-8", errors="replace")
    set_report = parse_set_report(log_text)
    polar = find_generated_polar(probe_dir, probe_model.stem)
    rows = []
    parse_error = None
    if polar is not None:
        try:
            rows = parse_vspaero_polar(polar)
        except Exception as exc:
            parse_error = str(exc)
    finite_rows = []
    for row in rows:
        alpha_key = "Alpha" if "Alpha" in row else "AoA" if "AoA" in row else None
        required = ["Beta", "Mach", "CLtot", "CDtot"]
        if alpha_key is None or any(key not in row for key in required):
            continue
        if all(math.isfinite(float(row[key])) for key in [*required, alpha_key]):
            finite_rows.append(row)
    condition_errors = _probe_setup_errors(
        set_report,
        requested_engine_boundary=requested_boundary,
        tail_geometry_name=tail_name,
        tail_incidence_deg=tail_incidence,
    )
    actual_betas = []
    for row in rows:
        try:
            actual_betas.append(float(row["Beta"]))
        except (KeyError, TypeError, ValueError):
            actual_betas.append(math.nan)
    if not actual_betas or any(
        not math.isfinite(value) or abs(value - requested_beta) > 1.0e-9
        for value in actual_betas
    ):
        condition_errors.append(
            "Фактический Beta каждой строки POLAR должен совпадать с квалификационной постановкой"
        )
    health_signals = parse_vspaero_health(log_text)
    health_errors, health_warnings = classify_vspaero_health(health_signals)
    output_quality = {
        "valid": False,
        "expected_points": 1,
        "actual_points": len(rows),
        "errors": ["Строгая проверка результата VSPAERO не выполнялась"],
    }
    output_quality_error = None
    output_quality_failure_kind = None
    if polar is not None and results_csv.is_file():
        try:
            output_quality = validate_vspaero_run_outputs(
                polar,
                log,
                mach_start=requested_mach,
                mach_end=requested_mach,
                mach_points=1,
                alpha_start=requested_alpha,
                alpha_end=requested_alpha,
                alpha_points=1,
                max_log10_l2_residual=float(
                    settings["max_log10_l2_residual"]
                ),
                max_log10_max_residual=(
                    float(settings["max_log10_max_residual"])
                    if settings.get("max_log10_max_residual") is not None
                    else None
                ),
                allow_zero_mach_without_logged_residual=bool(
                    settings.get("allow_zero_mach_without_logged_residual", False)
                ),
                allow_zero_rhs_normalization_nan=bool(
                    mode == "lifting"
                    and abs(requested_alpha) <= 1.0e-12
                    and abs(requested_beta) <= 1.0e-12
                ),
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            output_quality_error = str(exc)
            if "невязк" in output_quality_error.lower():
                output_quality_failure_kind = "residual"
            output_quality = {
                "valid": False,
                "expected_points": 1,
                "actual_points": len(rows),
                "max_log10_l2_residual": settings.get("max_log10_l2_residual"),
                "max_log10_max_residual": settings.get("max_log10_max_residual"),
                "errors": [output_quality_error],
            }
    if output_quality.get("benign_zero_rhs_normalization_nan"):
        health_errors = [item for item in health_errors if item != "nan_or_inf"]
        if "zero_rhs_normalization_nan" not in health_warnings:
            health_warnings.append("zero_rhs_normalization_nan")
    valid = (
        code in (0, 1)
        and set_report["valid"]
        and set_report["calculation_complete"]
        and results_csv.is_file()
        and polar is not None
        and len(finite_rows) == 1
        and output_quality.get("valid") is True
        and not health_errors
        and not condition_errors
        and parse_error is None
    )
    values = None
    if finite_rows:
        row = finite_rows[-1]
        alpha_key = "Alpha" if "Alpha" in row else "AoA"
        values = {
            "Mach": float(row["Mach"]),
            "Alpha": float(row[alpha_key]),
            "Beta": float(row["Beta"]),
            "CLtot": float(row["CLtot"]),
            "CDtot": float(row["CDtot"]),
        }
        if abs(values["Mach"] - requested_mach) > 1.0e-6 or abs(values["Alpha"] - requested_alpha) > 1.0e-5:
            valid = False
            parse_error = (
                f"VSPAERO вернул M={values['Mach']:g}, alpha={values['Alpha']:g} вместо "
                f"M={requested_mach:g}, alpha={requested_alpha:g}"
            )
    return {
        "mode": mode,
        "level": level,
        "valid": valid,
        "return_code": code,
        "elapsed_seconds": round(elapsed, 3),
        "set_validation": set_report,
        "health_signals": health_signals,
        "health_errors": health_errors,
        "health_warnings": health_warnings,
        "parse_error": parse_error,
        "condition_errors": condition_errors,
        "output_quality": output_quality,
        "output_quality_error": output_quality_error,
        "output_quality_failure_kind": output_quality_failure_kind,
        "finite_rows": len(finite_rows),
        "values": values,
        "requested_condition": {
            "mach": requested_mach,
            "alpha_deg": requested_alpha,
            "beta_deg": requested_beta,
            "engine_boundary": requested_boundary,
        },
        "numerical_controls": {
            "ncpu": int(settings.get("ncpu", 4)),
            "forward_gmres_convergence_factor": float(
                settings.get("forward_gmres_convergence_factor", 1.0)
            ),
            "wake_num_iter": int(settings.get("wake_num_iter", 8)),
            "num_wake_nodes": int(settings.get("num_wake_nodes", 24)),
            "wake_relax": float(settings.get("wake_relax", 0.8)),
        },
        "diagnostic_overrides": {
            "ignore_fixed_tail": bool(diagnostic_ignore_fixed_tail),
        },
        "model": str(model.resolve()),
        "model_sha256": sha256_file(model),
        "polar": str(polar.resolve()) if polar else None,
        "results_csv": str(results_csv.resolve()) if results_csv.is_file() else None,
        "log": str(log.resolve()),
    }


def _run_vspaero_component_isolation(
    *,
    failed_probe: dict,
    model: Path,
    diagnostic_root: Path,
    mode: str,
    level: str,
    reference: dict,
    policy: dict,
    executable: Path,
    mach: float,
    alpha_deg: float,
    engine_boundary: str,
) -> dict:
    """Localize a non-finite lifting failure without modifying the MASTER.

    Diagnostic twins are never eligible solver geometries.  Each run removes
    one semantic group (except the main Wing) from the thin Set and repeats
    only the failed condition.  A restored solution implicates that group or
    its junctions; it does not authorize excluding it from production.
    """
    if mode != "lifting" or failed_probe.get("valid"):
        return {"attempted": False, "reason": "not_an_invalid_lifting_probe"}
    settings = policy.get("probes", {}).get("vspaero", {})
    enabled = bool(
        settings.get("failure_diagnostics", {}).get(
            "component_group_isolation", False
        )
    )
    if not enabled:
        return {"attempted": False, "reason": "disabled_by_policy"}

    thin_user_set = int(policy["sets"]["thin_user_set"])
    set_key = f"IN_SET_{thin_user_set}"
    groups: dict[str, list[dict]] = {}
    for geom in failed_probe.get("set_validation", {}).get("geometries", []):
        if not geom.get(set_key):
            continue
        base, _ = component_base(str(geom.get("NAME", "")), policy)
        if policy.get("semantics", {}).get("roles", {}).get(base) != "thin":
            continue
        groups.setdefault(base, []).append(geom)

    variants = []
    for base in sorted(groups):
        if base == "Wing":
            continue
        token = (re.sub(r"[^A-Za-z0-9]+", "_", base).strip("_") or "group")[:10]
        variant_root = diagnostic_root / f"no_{token}"
        output_model = variant_root / "model.vsp3"
        actions = [
            {
                "action": "clear_sets",
                "target_twins": ["diagnostic_isolation"],
                "geom_id": str(item["ID"]),
                "component": str(item["NAME"]),
                "user_sets": [thin_user_set],
            }
            for item in groups[base]
        ]
        applied = apply_model_actions(
            source_path=model,
            output_path=output_model,
            actions=actions,
            twin_name="diagnostic_isolation",
            vspscript_executable=executable,
            work_dir=variant_root / "apply",
            empty_thick_user_set=int(policy["sets"]["empty_thick_user_set"]),
            timeout_seconds=float(settings.get("timeout_seconds", 600)),
        )
        diagnostic_probe = None
        if applied.get("valid"):
            diagnostic_probe = _run_one_vspaero_probe(
                model=output_model,
                probe_dir=variant_root / "probe",
                mode=mode,
                level=level,
                reference=reference,
                policy=policy,
                vspscript_executable=executable,
                mach=mach,
                alpha_deg=alpha_deg,
                engine_boundary=engine_boundary,
                diagnostic_ignore_fixed_tail=(base == "GO"),
            )
        variants.append(
            {
                "excluded_semantic_group": base,
                "excluded_components": [str(item["NAME"]) for item in groups[base]],
                "apply": applied,
                "probe": diagnostic_probe,
                "restored_finite_solution": bool(
                    diagnostic_probe and diagnostic_probe.get("valid")
                ),
            }
        )
    restored = [
        item["excluded_semantic_group"]
        for item in variants
        if item["restored_finite_solution"]
    ]
    return {
        "attempted": bool(variants),
        "diagnostic_only": True,
        "condition": {
            "mach": mach,
            "alpha_deg": alpha_deg,
            "engine_boundary": engine_boundary,
            "mesh_level": level,
        },
        "variants": variants,
        "groups_whose_exclusion_restored_solution": restored,
        "interpretation": (
            "Указанные группы или их сопряжения вызывают нечисловой VLM-режим; "
            "диагностическое исключение не разрешено переносить в расчётную модель"
            if restored
            else "Изоляция отдельных семантических групп не восстановила решение"
        ),
    }


def mesh_convergence(
    probes: list[dict],
    policy: dict,
    levels: tuple[str, str, str] = ("coarse", "medium", "fine"),
) -> dict:
    by_level = {item["level"]: item for item in probes if item.get("valid")}
    if len(levels) != 3 or len(set(levels)) != 3:
        raise ValueError("Для оценки сходимости нужны три разных уровня сетки")
    if any(level not in by_level for level in levels):
        return {
            "valid": False,
            "converged": False,
            "level_sequence": list(levels),
            "errors": ["Не все три сетки дали валидный результат"],
        }
    low_name, middle_name, high_name = levels
    floor = float(policy["convergence"]["absolute_floor"])
    tolerance = float(policy["convergence"]["relative_tolerance"])
    absolute_tolerances = policy["convergence"].get("absolute_tolerances", {})
    oscillation_fraction = float(
        policy["convergence"].get("oscillation_significance_fraction", 0.10)
    )
    quantities = {}
    converged = True
    oscillating_quantities: list[str] = []
    for key in policy["convergence"]["quantities"]:
        coarse = float(by_level[low_name]["values"][key])
        medium = float(by_level[middle_name]["values"][key])
        fine = float(by_level[high_name]["values"][key])
        coarse_medium_delta = medium - coarse
        medium_fine_delta = fine - medium
        cm_absolute = abs(coarse_medium_delta)
        mf_absolute = abs(medium_fine_delta)
        cm = cm_absolute / max(abs(medium), abs(coarse), floor)
        mf = mf_absolute / max(abs(fine), abs(medium), floor)
        absolute_tolerance = float(absolute_tolerances.get(key, 0.0))
        oscillation_significance = max(
            floor,
            absolute_tolerance * oscillation_fraction,
        )
        oscillating = bool(
            cm_absolute > oscillation_significance
            and mf_absolute > oscillation_significance
            and coarse_medium_delta * medium_fine_delta < 0.0
        )
        monotonic = not oscillating
        if cm_absolute > floor:
            contraction_ratio = mf_absolute / cm_absolute
        elif mf_absolute <= floor:
            contraction_ratio = 0.0
        else:
            # There is no meaningful denominator; keep JSON finite and make
            # the undefined ratio explicit rather than emitting infinity.
            contraction_ratio = None
        local_ok = (
            all(math.isfinite(value) for value in (coarse, medium, fine, cm, mf, mf_absolute))
            and not oscillating
            and (mf <= tolerance or (absolute_tolerance > 0.0 and mf_absolute <= absolute_tolerance))
        )
        if oscillating:
            oscillating_quantities.append(key)
        quantities[key] = {
            low_name: coarse,
            middle_name: medium,
            high_name: fine,
            "level_sequence": list(levels),
            "comparison_levels": [middle_name, high_name],
            "coarse_medium_delta": coarse_medium_delta,
            "medium_fine_delta": medium_fine_delta,
            "coarse_medium_relative": cm,
            "medium_fine_relative": mf,
            "medium_fine_absolute": mf_absolute,
            "absolute_tolerance": absolute_tolerance,
            "oscillation_significance": oscillation_significance,
            "monotonic": monotonic,
            "oscillating": oscillating,
            "contraction_ratio": contraction_ratio,
            "converged": local_ok,
        }
        converged = converged and local_ok
    errors = []
    if oscillating_quantities:
        errors.append(
            "Осцилляция Coarse→Medium→Fine обнаружена для: "
            + ", ".join(oscillating_quantities)
        )
    if any(
        not item["converged"] and not item["oscillating"]
        for item in quantities.values()
    ):
        errors.append("Изменение Medium→Fine превышает допуск")
    return {
        "valid": True,
        "converged": converged,
        "level_sequence": list(levels),
        "comparison_levels": [middle_name, high_name],
        "adaptive": high_name != "fine",
        "monotonic": not oscillating_quantities,
        "oscillating": bool(oscillating_quantities),
        "oscillating_quantities": oscillating_quantities,
        "relative_tolerance": tolerance,
        "quantities": quantities,
        "errors": errors,
    }


def _is_nonfinite_solver_failure(probe: dict) -> bool:
    """Return True only for a numerical solver breakdown, not a setup error."""
    if probe.get("valid"):
        return False
    health_errors = {str(item) for item in probe.get("health_errors", [])}
    output_errors = " ".join(
        str(item) for item in probe.get("output_quality", {}).get("errors", [])
    ).lower()
    return "nan_or_inf" in health_errors and "nonfinite_solver" in output_errors


def _build_local_span_plateau_meshes(
    *,
    mode: str,
    meshes: dict[str, dict],
    run_dir: Path,
    condition_id: str,
    reference: dict,
    policy: dict,
    executable: Path,
) -> dict:
    """Build two verified meshes immediately below Fine in thin Tess_W.

    These meshes are not a looser fallback.  Together with Fine they form a
    three-level local convergence ladder immediately below a denser mesh that
    failed numerically.  Every persisted Tess_* value is read back and checked.
    """
    settings = (
        policy.get("convergence", {})
        .get("adaptive_refinement", {})
        .get("local_span_plateau", {})
    )
    offsets = [int(item) for item in settings.get("backoff_offsets", [8, 4])]
    if len(offsets) != 2 or not offsets[0] > offsets[1] > 0:
        return {
            "valid": False,
            "levels": [],
            "meshes": {},
            "errors": ["Некорректные backoff_offsets локальной сеточной полки"],
        }
    fine_record = meshes.get("fine", {})
    fine_policy = deepcopy(fine_record.get("mesh_policy", {}))
    inventory_path = Path(str(fine_record.get("post_inventory", "")))
    if not fine_policy or not inventory_path.is_file():
        return {
            "valid": False,
            "levels": [],
            "meshes": {},
            "errors": ["Для локальной полки отсутствует проверенная сетка Fine"],
        }
    try:
        fine_inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        fine_audit = semantic_audit(fine_inventory, reference, policy)
        fine_tess_w = int(fine_policy["thin_tess_w"])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return {
            "valid": False,
            "levels": [],
            "meshes": {},
            "errors": [f"Не удалось прочитать параметры Fine для локальной полки: {exc}"],
        }

    twin_name = "vspaero_lifting" if mode == "lifting" else "vspaero_mixed"
    mode_token = "vl" if mode == "lifting" else "vm"
    result_meshes: dict[str, dict] = {}
    levels: list[str] = []
    errors: list[str] = []
    for offset in offsets:
        target_tess_w = fine_tess_w - offset
        level_name = f"local_span_m{offset}"
        levels.append(level_name)
        if target_tess_w < 2:
            errors.append(
                f"Локальный уровень {level_name} даёт недопустимый thin_tess_w={target_tess_w}"
            )
            continue
        level_policy = deepcopy(fine_policy)
        level_policy["thin_tess_w"] = target_tess_w
        mesh_root = run_dir / "am" / mode_token / condition_id / level_name
        destination = mesh_root / "model.vsp3"
        actions = mesh_actions(fine_inventory, fine_audit, level_policy, twin_name)
        record = apply_model_actions(
            source_path=Path(fine_record["path"]),
            output_path=destination,
            actions=actions,
            twin_name=twin_name,
            vspscript_executable=executable,
            work_dir=mesh_root / "script",
            empty_thick_user_set=(
                int(policy["sets"]["empty_thick_user_set"])
                if mode == "lifting"
                else None
            ),
        )
        record["level"] = level_name
        record["mesh_policy"] = deepcopy(level_policy)
        verification = {
            "valid": False,
            "errors": ["Локальный сеточный двойник не создан"],
        }
        inventory_output = mesh_root / "diag" / "inventory.json"
        if destination.is_file():
            try:
                inventory, _, _ = inventory_model(
                    destination,
                    vspscript_executable=executable,
                    work_dir=inventory_output.parent,
                )
                write_json(inventory_output, inventory)
                audit = semantic_audit(inventory, reference, policy)
                verification = verify_mesh_inventory(inventory, audit, level_policy)
            except Exception as exc:
                verification = {
                    "valid": False,
                    "errors": [f"Сбой проверки локального сеточного двойника: {exc}"],
                }
        record["post_inventory"] = (
            str(inventory_output.resolve()) if inventory_output.is_file() else None
        )
        record["mesh_verification"] = verification
        record["valid"] = bool(record.get("valid") and verification.get("valid"))
        if not record["valid"]:
            record.setdefault("errors", []).extend(verification.get("errors", []))
            errors.append(f"Локальный уровень {level_name} не прошёл проверку Tess_*")
        result_meshes[level_name] = record
    levels.append("fine")
    return {
        "valid": not errors and len(result_meshes) == 2,
        "levels": levels,
        "meshes": result_meshes,
        "errors": errors,
        "axis": "thin_tess_w",
        "fine_value": fine_tess_w,
        "backoff_offsets": offsets,
        "acceptance_tolerance": float(policy["convergence"]["relative_tolerance"]),
    }


def _run_vspaero_ladder(
    *,
    mode: str,
    meshes: dict[str, dict],
    run_dir: Path,
    reference: dict,
    policy: dict,
    executable: Path,
    point: dict | None = None,
) -> dict:
    settings = policy["probes"]["vspaero"]
    point = point or {"mach": settings["mach"], "alpha_deg": settings["alpha_deg"]}
    mach = float(point["mach"])
    alpha = float(point["alpha_deg"])
    boundary = str(point.get("engine_boundary", "model"))
    condition_id = f"M{mach:g}_A{alpha:g}_{boundary}".replace("-", "m").replace(".", "p")
    invalid_meshes = [
        level for level in ("coarse", "medium", "fine")
        if level not in meshes
        or not meshes[level].get("valid")
        or not meshes[level].get("mesh_verification", {}).get("valid")
    ]
    if invalid_meshes:
        errors = [
            "Сеточная лестница не прошла проверку записанных Tess_*: "
            + ", ".join(invalid_meshes)
        ]
        return {
            "mode": mode,
            "id": condition_id,
            "mach": mach,
            "alpha_deg": alpha,
            "beta_deg": float(policy["scope"].get("beta_deg", 0.0)),
            "engine_boundary": boundary,
            "valid": False,
            "converged": False,
            "probes": [],
            "convergence": {"valid": False, "converged": False, "errors": errors},
        }
    probes = []
    failure_diagnostics = None
    for level in ("coarse", "medium", "fine"):
        probe = _run_vspaero_probe_with_recovery(
            model=Path(meshes[level]["path"]),
            probe_dir=run_dir / "probes" / f"vspaero_{mode}" / condition_id / level,
            mode=mode,
            level=level,
            reference=reference,
            policy=policy,
            executable=executable,
            mach=mach,
            alpha_deg=alpha,
            engine_boundary=boundary,
        )
        probes.append(probe)
        # A three-level convergence statement is impossible after any invalid
        # solver point.  Stop this ladder immediately instead of spending the
        # remaining timeout budget on evidence that cannot make it pass.
        if not probe.get("valid"):
            failure_diagnostics = _run_vspaero_component_isolation(
                failed_probe=probe,
                model=Path(meshes[level]["path"]),
                diagnostic_root=run_dir / "iso" / condition_id / level,
                mode=mode,
                level=level,
                reference=reference,
                policy=policy,
                executable=executable,
                mach=mach,
                alpha_deg=alpha,
                engine_boundary=boundary,
            )
            break
    convergence = mesh_convergence(probes, policy)
    initial_convergence = deepcopy(convergence)
    adaptive = policy.get("convergence", {}).get("adaptive_refinement", {})
    retry_level = str(adaptive.get("level", "extra_fine"))
    failed_relative_changes = [
        float(item.get("medium_fine_relative", math.inf))
        for item in convergence.get("quantities", {}).values()
        if not item.get("converged") and not item.get("oscillating")
    ]
    oscillating_items = [
        item
        for item in convergence.get("quantities", {}).values()
        if item.get("oscillating")
    ]
    # A sign reversal remains a failed three-grid result. It may be resolved
    # by the verification-only ExtraFine level when the latest oscillating
    # step is already inside that quantity's absolute tolerance. This gathers
    # more evidence without accepting the point or relaxing either limit.
    recoverable_oscillation = bool(oscillating_items) and all(
        float(item.get("absolute_tolerance", 0.0)) > 0.0
        and float(item.get("medium_fine_absolute", math.inf))
        <= float(item.get("absolute_tolerance", 0.0))
        for item in oscillating_items
    )
    retry_limit = float(adaptive.get("max_initial_relative_for_retry", 0.10))
    should_retry = bool(
        adaptive.get("enabled", False)
        and convergence.get("valid")
        and not convergence.get("converged")
        and (not convergence.get("oscillating") or recoverable_oscillation)
        and failed_relative_changes
        and max(failed_relative_changes) <= retry_limit
    )
    retry_probe = None
    if should_retry:
        retry_mesh = meshes.get(retry_level, {})
        retry_mesh_valid = bool(
            retry_mesh.get("valid")
            and retry_mesh.get("mesh_verification", {}).get("valid")
        )
        if retry_mesh_valid:
            retry_probe = _run_vspaero_probe_with_recovery(
                model=Path(retry_mesh["path"]),
                probe_dir=(
                    run_dir / "probes" / f"vspaero_{mode}" / condition_id / retry_level
                ),
                mode=mode,
                level=retry_level,
                reference=reference,
                policy=policy,
                executable=executable,
                mach=mach,
                alpha_deg=alpha,
                engine_boundary=boundary,
            )
            probes.append(retry_probe)
            if retry_probe.get("valid"):
                convergence = mesh_convergence(
                    probes,
                    policy,
                    levels=("medium", "fine", retry_level),
                )
            else:
                convergence = {
                    "valid": False,
                    "converged": False,
                    "level_sequence": ["medium", "fine", retry_level],
                    "errors": ["Адаптивный ExtraFine-уровень не дал валидный результат"],
                }
        else:
            convergence = {
                "valid": False,
                "converged": False,
                "level_sequence": ["medium", "fine", retry_level],
                "errors": ["Адаптивный ExtraFine-уровень отсутствует или не прошёл проверку Tess_*"],
            }
        convergence["adaptive_refinement_attempted"] = True
        convergence["adaptive_recoverable_oscillation"] = recoverable_oscillation
        convergence["initial_convergence"] = initial_convergence
    else:
        convergence["adaptive_refinement_attempted"] = False
        convergence["adaptive_recoverable_oscillation"] = recoverable_oscillation

    # A denser grid can cross a numerical stability boundary of VSPAERO even
    # when a locally converged family exists immediately below it.  In that
    # narrowly defined case, construct two independently persisted Tess_W
    # levels below Fine and require the same three-grid tolerance.  The failed
    # denser probe remains rejection evidence and is never used as a result.
    plateau_settings = adaptive.get("local_span_plateau", {})
    should_try_plateau = bool(
        plateau_settings.get("enabled", False)
        and should_retry
        and retry_probe is not None
        and _is_nonfinite_solver_failure(retry_probe)
        and not convergence.get("converged")
    )
    if should_try_plateau:
        failed_refinement = deepcopy(convergence)
        plateau = _build_local_span_plateau_meshes(
            mode=mode,
            meshes=meshes,
            run_dir=run_dir,
            condition_id=condition_id,
            reference=reference,
            policy=policy,
            executable=executable,
        )
        plateau_probes: list[dict] = []
        if plateau.get("valid"):
            for level_name in plateau["levels"][:-1]:
                mesh_record = plateau["meshes"][level_name]
                local_probe = _run_vspaero_probe_with_recovery(
                    model=Path(mesh_record["path"]),
                    probe_dir=(
                        run_dir
                        / "probes"
                        / f"vspaero_{mode}"
                        / condition_id
                        / level_name
                    ),
                    mode=mode,
                    level=level_name,
                    reference=reference,
                    policy=policy,
                    executable=executable,
                    mach=mach,
                    alpha_deg=alpha,
                    engine_boundary=boundary,
                )
                probes.append(local_probe)
                plateau_probes.append(local_probe)
                if not local_probe.get("valid"):
                    break
            fine_probe = next(
                (item for item in probes if item.get("level") == "fine"), None
            )
            if len(plateau_probes) == 2 and fine_probe is not None:
                convergence = mesh_convergence(
                    [*plateau_probes, fine_probe],
                    policy,
                    levels=tuple(plateau["levels"]),
                )
            else:
                convergence = {
                    "valid": False,
                    "converged": False,
                    "level_sequence": list(plateau.get("levels", [])),
                    "errors": ["Локальная полка не дала три валидных уровня"],
                }
        else:
            convergence = {
                "valid": False,
                "converged": False,
                "level_sequence": list(plateau.get("levels", [])),
                "errors": list(plateau.get("errors", [])),
            }
        convergence["local_span_plateau_attempted"] = True
        convergence["local_span_plateau"] = plateau
        convergence["rejected_dense_refinement"] = retry_probe
        convergence["pre_plateau_convergence"] = failed_refinement
        convergence["initial_convergence"] = initial_convergence
    else:
        convergence["local_span_plateau_attempted"] = False

    # If ExtraFine brought every latest change inside the unchanged numerical
    # limits but the three-level direction still oscillates, one final
    # verification level may arbitrate the sequence.  The result is evaluated
    # on Fine/ExtraFine/UltraFine; Fine remains the production mesh.
    first_adaptive_convergence = deepcopy(convergence)
    resolution_level = str(
        adaptive.get("oscillation_resolution_level", "ultra_fine")
    )
    unresolved = [
        item
        for item in convergence.get("quantities", {}).values()
        if not item.get("converged")
    ]
    unresolved_inside_limits = bool(unresolved) and all(
        item.get("oscillating")
        and (
            float(item.get("medium_fine_relative", math.inf))
            <= float(convergence.get("relative_tolerance", math.inf))
            or (
                float(item.get("absolute_tolerance", 0.0)) > 0.0
                and float(item.get("medium_fine_absolute", math.inf))
                <= float(item.get("absolute_tolerance", 0.0))
            )
        )
        for item in unresolved
    )
    should_resolve_oscillation = bool(
        convergence.get("adaptive_refinement_attempted")
        and convergence.get("valid")
        and not convergence.get("converged")
        and convergence.get("oscillating")
        and unresolved_inside_limits
        and resolution_level != retry_level
    )
    if should_resolve_oscillation:
        resolution_mesh = meshes.get(resolution_level, {})
        resolution_mesh_valid = bool(
            resolution_mesh.get("valid")
            and resolution_mesh.get("mesh_verification", {}).get("valid")
        )
        if resolution_mesh_valid:
            resolution_probe = _run_vspaero_probe_with_recovery(
                model=Path(resolution_mesh["path"]),
                probe_dir=(
                    run_dir
                    / "probes"
                    / f"vspaero_{mode}"
                    / condition_id
                    / resolution_level
                ),
                mode=mode,
                level=resolution_level,
                reference=reference,
                policy=policy,
                executable=executable,
                mach=mach,
                alpha_deg=alpha,
                engine_boundary=boundary,
            )
            probes.append(resolution_probe)
            if resolution_probe.get("valid"):
                convergence = mesh_convergence(
                    probes,
                    policy,
                    levels=("fine", retry_level, resolution_level),
                )
            else:
                convergence = {
                    "valid": False,
                    "converged": False,
                    "level_sequence": ["fine", retry_level, resolution_level],
                    "errors": ["Арбитражный UltraFine-уровень не дал валидный результат"],
                }
        else:
            convergence = {
                "valid": False,
                "converged": False,
                "level_sequence": ["fine", retry_level, resolution_level],
                "errors": ["Арбитражный UltraFine-уровень отсутствует или не прошёл проверку Tess_*"],
            }
        convergence["oscillation_resolution_attempted"] = True
        convergence["oscillation_resolution_level"] = resolution_level
        convergence["first_adaptive_convergence"] = first_adaptive_convergence
        convergence["initial_convergence"] = initial_convergence
    else:
        convergence["oscillation_resolution_attempted"] = False
    probe_by_level = {item.get("level"): item for item in probes}
    selected_levels = list(convergence.get("level_sequence", []))
    selected_probes_valid = bool(selected_levels) and all(
        level in probe_by_level and probe_by_level[level].get("valid")
        for level in selected_levels
    )
    return {
        "mode": mode,
        "id": condition_id,
        "mach": mach,
        "alpha_deg": alpha,
        "beta_deg": float(policy["scope"].get("beta_deg", 0.0)),
        "engine_boundary": boundary,
        "valid": bool(convergence.get("valid") and selected_probes_valid),
        "converged": convergence["converged"],
        "probes": probes,
        "convergence": convergence,
        "failure_diagnostics": failure_diagnostics,
    }


def _qualification_profile(policy: dict) -> tuple[str, dict]:
    settings = policy["probes"]["vspaero"]
    profiles = settings.get("qualification_profiles", {})
    name = str(settings.get("qualification_profile", "screening_exact"))
    if profiles:
        return name, deepcopy(profiles[name])
    return "legacy_single_anchor", {
        "coverage_kind": "exact_points",
        "points": [{"mach": float(settings["mach"]), "alpha_deg": float(settings["alpha_deg"])}],
    }


def _run_vspaero_qualification(
    *,
    mode: str,
    meshes: dict[str, dict],
    run_dir: Path,
    reference: dict,
    policy: dict,
    executable: Path,
    has_gondola: bool = False,
) -> dict:
    profile_name, profile = _qualification_profile(policy)
    anchors = []
    expected_anchor_count = len(profile["points"])
    for point in profile["points"]:
        point = deepcopy(point)
        if point.get("engine_boundary") == "auto":
            point["engine_boundary"] = "to_face" if has_gondola else "model"
        ladder = _run_vspaero_ladder(
            mode=mode,
            meshes=meshes,
            run_dir=run_dir,
            reference=reference,
            policy=policy,
            executable=executable,
            point=point,
        )
        ladder.setdefault("mach", float(point["mach"]))
        ladder.setdefault("alpha_deg", float(point["alpha_deg"]))
        ladder.setdefault("beta_deg", float(policy["scope"].get("beta_deg", 0.0)))
        ladder.setdefault("engine_boundary", str(point.get("engine_boundary", "model")))
        anchors.append(ladder)
        # Envelope qualification is conjunctive: one failed anchor makes the
        # whole mode ineligible.  Fail fast so the alternate solver twin can
        # be tried without waiting through every remaining anchor.
        if not (ladder.get("valid") and ladder.get("converged")):
            break
    valid = bool(anchors) and all(item.get("valid") for item in anchors)
    converged = valid and all(item.get("converged") for item in anchors)
    return {
        "mode": mode,
        "profile": profile_name,
        "coverage_kind": profile.get("coverage_kind", "exact_points"),
        "valid": valid,
        "converged": converged,
        "anchors": anchors,
        "probes": [probe for anchor in anchors for probe in anchor.get("probes", [])],
        "convergence": {
            "valid": valid,
            "converged": converged,
            "anchor_count": len(anchors),
            "expected_anchor_count": expected_anchor_count,
            "aborted_after_failure": len(anchors) < expected_anchor_count,
            "failed_anchors": [
                {"mach": item.get("mach"), "alpha_deg": item.get("alpha_deg")}
                for item in anchors if not (item.get("valid") and item.get("converged"))
            ],
            "errors": [] if converged else ["Не все опорные точки дали валидную сеточно сходящуюся тройку"],
        },
    }


def _write_transformation_log(path: Path, plan: dict, twins: dict) -> None:
    lines = []
    for action in plan.get("actions", []):
        targets = [name for name in action.get("target_twins", []) if name in twins]
        lines.append(json.dumps({"event": "planned_action", "targets": targets, "action": action}, ensure_ascii=False))
    for name, item in twins.items():
        lines.append(json.dumps({
            "event": "twin_created", "name": name, "path": item.get("path"),
            "sha256": item.get("sha256"), "canonical_sha256": item.get("canonical_sha256"),
            "valid": item.get("valid"),
        }, ensure_ascii=False))
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _exclusion_applies(item: dict, backend: str) -> bool:
    declared = item.get("backends")
    if isinstance(declared, list):
        return backend in {str(value).lower() for value in declared}
    declared = item.get("backend")
    if declared is not None:
        return str(declared).lower() == backend
    return True


def _backend_exclusions(exclusions: list[dict], backend: str) -> list[dict]:
    return [deepcopy(item) for item in exclusions if _exclusion_applies(item, backend)]


def _build_replacement_contract(
    exclusions: list[dict],
    *,
    machline_eligible: bool,
    machline_components: list[str],
    parasite_eligible: bool,
    parasite_components: list[str],
    base_drag_required: bool,
) -> dict:
    """Describe which declared omissions can actually be replaced.

    This is a geometry/backend capability contract, not a claim that a future
    calculation has supplied every required point.  Producer manifests and
    per-condition coverage are still checked by the hybrid consumer.
    """
    machline_names = {str(name).strip().casefold() for name in machline_components}
    parasite_names = {str(name).strip().casefold() for name in parasite_components}
    requirements = []
    for exclusion in _backend_exclusions(exclusions, "vspaero"):
        component = str(exclusion.get("component", exclusion.get("role", ""))).strip()
        declared = exclusion.get("replacement_required", [])
        if isinstance(declared, str):
            declared = [declared]
        for method in declared:
            method = str(method).strip().lower()
            if method == "machline_pressure_wave_all_points":
                available = bool(
                    machline_eligible and component.casefold() in machline_names
                )
                reason = (
                    "component_present_in_certified_machline_pressure_mesh"
                    if available else "component_absent_from_certified_machline_pressure_mesh"
                )
            elif method == "parasite_drag_subsonic":
                available = bool(
                    parasite_eligible and component.casefold() in parasite_names
                )
                reason = (
                    "component_present_in_certified_parasite_geometry"
                    if available else "component_absent_from_certified_parasite_geometry"
                )
            elif method == "not_physical":
                available = True
                reason = "explicit_not_physical_declaration"
            else:
                available = False
                reason = "unsupported_replacement_method"
            requirements.append({
                "component": component,
                "method": method,
                "backend_capability_available": available,
                "reason": reason,
            })
    if base_drag_required:
        requirements.append({
            "component": "RM_NUMERICAL_AFT_CLOSURE",
            "method": "base_drag_semiempirical",
            "backend_capability_available": True,
            "source_required_at_hybrid_build": True,
            "reason": "numerical_aft_closure_force_is_masked",
        })
    unavailable = [
        item for item in requirements
        if item.get("backend_capability_available") is not True
    ]
    return {
        "schema": "repairmach.replacement-contract/1.0",
        "required": bool(requirements),
        "backend_capabilities_complete": not unavailable,
        "sealed_sources_still_required": bool(requirements),
        "requirements": requirements,
        "unavailable_requirements": unavailable,
    }


def _is_backend_blocker(item: dict, backend: str) -> bool:
    if item.get("severity") != "BLOCKER":
        return False
    scope = str(item.get("scope", "master")).lower()
    backend_scopes = {"vspaero", "machline", "parasite", "parasite_drag", "hybrid"}
    if scope in backend_scopes or scope.startswith("vspaero_") or scope.startswith("machline_"):
        aliases = {backend}
        if backend == "parasite_drag":
            aliases.add("parasite")
        return any(scope == name or scope.startswith(name + "_") for name in aliases)
    return True


def _backend_eligible(
    *,
    local_eligible: bool,
    blockers: list[dict],
    master_unchanged: bool,
    run_complete: bool,
    verdict: str,
    dependency_eligible: bool = True,
) -> bool:
    """Apply the common fail-closed gate to a backend capability."""
    return bool(
        local_eligible
        and dependency_eligible
        and master_unchanged
        and run_complete
        and verdict != "FAIL"
        and not blockers
    )


def _is_residual_quality_failure(probe: dict) -> bool:
    """Identify a numerically poor solve whose geometry/setup still passed."""
    set_report = probe.get("set_validation", {})
    return bool(
        not probe.get("valid")
        and probe.get("output_quality_failure_kind") == "residual"
        and probe.get("finite_rows") == 1
        and not probe.get("health_errors")
        and not probe.get("condition_errors")
        and set_report.get("valid")
        and set_report.get("calculation_complete")
    )


def _probe_repeatability(probes: list[dict], policy: dict) -> dict:
    """Require independent stabilized repeats to agree without relaxing limits."""
    settings = (
        policy.get("probes", {})
        .get("vspaero", {})
        .get("numerical_recovery", {})
    )
    relative_tolerance = float(settings.get("relative_tolerance", 0.005))
    floor = float(policy.get("convergence", {}).get("absolute_floor", 1.0e-6))
    absolute_tolerances = settings.get("absolute_tolerances", {})
    quantities = {}
    valid = len(probes) >= 2 and all(item.get("valid") for item in probes)
    if valid:
        first, second = probes[-2:]
        for key in policy["convergence"]["quantities"]:
            value_a = float(first["values"][key])
            value_b = float(second["values"][key])
            absolute = abs(value_b - value_a)
            relative = absolute / max(abs(value_a), abs(value_b), floor)
            absolute_tolerance = float(absolute_tolerances.get(key, 0.0))
            quantity_valid = bool(
                math.isfinite(relative)
                and (
                    relative <= relative_tolerance
                    or (absolute_tolerance > 0.0 and absolute <= absolute_tolerance)
                )
            )
            quantities[key] = {
                "first": value_a,
                "second": value_b,
                "absolute_delta": absolute,
                "relative_delta": relative,
                "relative_tolerance": relative_tolerance,
                "absolute_tolerance": absolute_tolerance,
                "valid": quantity_valid,
            }
            valid = valid and quantity_valid
    return {
        "valid": bool(valid),
        "attempts": len(probes),
        "quantities": quantities,
        "errors": [] if valid else ["Усиленные повторы VSPAERO не подтвердили воспроизводимость"],
    }


def _recover_vspaero_residual_failure(
    *,
    failed_probe: dict,
    model: Path,
    probe_root: Path,
    mode: str,
    level: str,
    reference: dict,
    policy: dict,
    executable: Path,
    mach: float,
    alpha_deg: float,
    engine_boundary: str,
) -> dict:
    """Repeat one residual-only failure using deterministic stricter controls."""
    settings = (
        policy.get("probes", {})
        .get("vspaero", {})
        .get("numerical_recovery", {})
    )
    record = {
        "attempted": False,
        "valid": False,
        "reason": "residual_quality_failure",
        "rejected_probe": deepcopy(failed_probe),
        "repeat_probes": [],
        "repeatability": {"valid": False, "errors": ["Повторы не выполнялись"]},
    }
    if not settings.get("enabled", False) or not _is_residual_quality_failure(failed_probe):
        return record
    controls = deepcopy(settings.get("controls", {}))
    attempts = max(2, int(settings.get("attempts", 2)))
    record["attempted"] = True
    record["controls"] = controls
    for attempt in range(1, attempts + 1):
        repeat_level = f"{level}_stable_repeat_{attempt}"
        probe = _run_one_vspaero_probe(
            model=model,
            probe_dir=probe_root / repeat_level,
            mode=mode,
            level=repeat_level,
            reference=reference,
            policy=policy,
            vspscript_executable=executable,
            mach=mach,
            alpha_deg=alpha_deg,
            engine_boundary=engine_boundary,
            numerical_overrides=controls,
        )
        record["repeat_probes"].append(probe)
        if not probe.get("valid"):
            break
    record["repeatability"] = _probe_repeatability(record["repeat_probes"], policy)
    record["valid"] = bool(record["repeatability"].get("valid"))
    if not record["valid"]:
        return record
    selected = deepcopy(record["repeat_probes"][-1])
    selected["level"] = level
    selected["numerical_recovery"] = record
    record["selected_probe"] = {
        "level": level,
        "source_repeat_level": record["repeat_probes"][-1].get("level"),
        "values": deepcopy(selected.get("values")),
    }
    return {**record, "selected": selected}


def _run_vspaero_probe_with_recovery(
    *,
    model: Path,
    probe_dir: Path,
    mode: str,
    level: str,
    reference: dict,
    policy: dict,
    executable: Path,
    mach: float,
    alpha_deg: float,
    engine_boundary: str,
) -> dict:
    """Run any qualification level through the same residual recovery gate."""
    probe = _run_one_vspaero_probe(
        model=model,
        probe_dir=probe_dir,
        mode=mode,
        level=level,
        reference=reference,
        policy=policy,
        vspscript_executable=executable,
        mach=mach,
        alpha_deg=alpha_deg,
        engine_boundary=engine_boundary,
    )
    if not _is_residual_quality_failure(probe):
        return probe
    recovery = _recover_vspaero_residual_failure(
        failed_probe=probe,
        model=model,
        probe_root=probe_dir / "numerical_recovery",
        mode=mode,
        level=level,
        reference=reference,
        policy=policy,
        executable=executable,
        mach=mach,
        alpha_deg=alpha_deg,
        engine_boundary=engine_boundary,
    )
    if recovery.get("valid"):
        return recovery["selected"]
    probe["numerical_recovery"] = recovery
    return probe


def _qualified_scope(
    *,
    requested_scope: dict,
    profile_name: str,
    profile: dict,
    qualification: dict | None,
    eligible: bool,
) -> dict:
    if not eligible or not qualification:
        return {"coverage_kind": "none", "points": []}
    points = [
        {
            "mach": float(anchor["mach"]),
            "alpha_deg": float(anchor["alpha_deg"]),
            "beta_deg": float(requested_scope.get("beta_deg", 0.0)),
            "engine_boundary": str(anchor.get("engine_boundary", "model")),
        }
        for anchor in qualification.get("anchors", [])
        if anchor.get("valid") and anchor.get("converged")
    ]
    coverage_kind = str(profile.get("coverage_kind", "exact_points"))
    result = {
        "coverage_kind": coverage_kind,
        "profile": profile_name,
        "points": points,
        "engine_boundaries": sorted({point["engine_boundary"] for point in points}),
        "horizontal_tail": deepcopy(requested_scope.get("horizontal_tail", {})),
    }
    if coverage_kind == "anchor_envelope" and len(points) == len(profile.get("points", [])):
        boundary_regions = []
        region_complete = True
        alpha_lo, alpha_hi = (float(value) for value in requested_scope["alpha_deg"])
        for lo, hi in requested_scope["mach_intervals"]:
            interval_points = [point for point in points if float(lo) <= point["mach"] <= float(hi)]
            boundaries = {point["engine_boundary"] for point in interval_points}
            corners_present = all(
                any(
                    abs(point["mach"] - mach_edge) <= 1.0e-8
                    and abs(point["alpha_deg"] - alpha_edge) <= 1.0e-8
                    for point in interval_points
                )
                for mach_edge in (float(lo), float(hi))
                for alpha_edge in (alpha_lo, alpha_hi)
            )
            if len(boundaries) != 1 or not corners_present:
                region_complete = False
                break
            boundary_regions.append({
                "mach_interval": [float(lo), float(hi)],
                "engine_boundary": next(iter(boundaries)),
            })
        if not region_complete:
            result["coverage_kind"] = "exact_points"
            return result
        result.update({
            "mach_intervals": deepcopy(requested_scope["mach_intervals"]),
            "alpha_deg": deepcopy(requested_scope["alpha_deg"]),
            "beta_deg": float(requested_scope.get("beta_deg", 0.0)),
            "interpolation_only": True,
            "extrapolation_allowed": False,
            "boundary_regions": boundary_regions,
        })
    else:
        result["coverage_kind"] = "exact_points"
    return result


def _qualified_scope_contains(scope: dict, mach: float, alpha: float, engine_boundary: str = "model") -> bool:
    tolerance = 1.0e-8
    if scope.get("coverage_kind") == "exact_points":
        return any(
            abs(float(point["mach"]) - mach) <= tolerance
            and abs(float(point["alpha_deg"]) - alpha) <= tolerance
            and str(point.get("engine_boundary", "model")) == engine_boundary
            for point in scope.get("points", [])
        )
    if scope.get("coverage_kind") != "anchor_envelope":
        return False
    in_boundary_region = any(
        float(region["mach_interval"][0]) <= mach <= float(region["mach_interval"][1])
        and str(region.get("engine_boundary", "model")) == engine_boundary
        for region in scope.get("boundary_regions", [])
    )
    return (
        any(float(lo) <= mach <= float(hi) for lo, hi in scope.get("mach_intervals", []))
        and len(scope.get("alpha_deg", [])) == 2
        and float(scope["alpha_deg"][0]) <= alpha <= float(scope["alpha_deg"][1])
        and in_boundary_region
    )


def _scenario_points(case: dict) -> list[tuple[float, float]]:
    mach_count = max(1, int(case.get("mach_points", 1)))
    alpha_count = max(1, int(case.get("alpha_points", 1)))
    mach_start, mach_end = float(case["mach_start"]), float(case["mach_end"])
    alpha_start, alpha_end = float(case["alpha_start"]), float(case["alpha_end"])
    machs = [
        mach_start if mach_count == 1 else mach_start + index * (mach_end - mach_start) / (mach_count - 1)
        for index in range(mach_count)
    ]
    alphas = [
        alpha_start if alpha_count == 1 else alpha_start + index * (alpha_end - alpha_start) / (alpha_count - 1)
        for index in range(alpha_count)
    ]
    return [(mach, alpha) for mach in machs for alpha in alphas]


def _derive_scenario_eligibility(qualified_scope: dict, requested_scope: dict) -> dict:
    catalog_path = Path(__file__).resolve().parents[1] / "config" / "calculation_scenarios.json"
    if not catalog_path.is_file():
        return {}
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    tail = requested_scope.get("horizontal_tail", {})
    result = {}
    for scenario in catalog.get("scenarios", []):
        if scenario.get("execution") != "vspaero_study" or scenario.get("availability") != "ready":
            continue
        scenario_id = str(scenario.get("id"))
        points = [point for case in scenario.get("vspaero_cases", []) for point in _scenario_points(case)]
        coverage_ok = bool(points) and all(
            _qualified_scope_contains(
                qualified_scope,
                mach,
                alpha,
                str(case.get("engine_boundary", "model")),
            )
            for case in scenario.get("vspaero_cases", [])
            for mach, alpha in _scenario_points(case)
        )
        tail_mode = scenario.get("tail_incidence")
        tail_ok = (
            tail.get("mode") == "fixed"
            and tail_mode == "fixed"
            and str(scenario.get("tail_geometry_name", "")) == str(tail.get("geometry_name", ""))
            and abs(float(scenario.get("tail_incidence_deg", math.nan)) - float(tail.get("incidence_deg", math.nan))) <= 1.0e-8
        )
        requested_beta = float(requested_scope.get("beta_deg", math.nan))
        scenario_beta = float(scenario.get("beta_deg", 0.0))
        beta_ok = math.isfinite(requested_beta) and abs(requested_beta - scenario_beta) <= 1.0e-8
        eligible = bool(coverage_ok and tail_ok and beta_ok)
        if eligible:
            reason = "qualified_scope_tail_and_beta_match"
        elif not coverage_ok:
            reason = "scenario_points_outside_qualified_scope"
        elif not tail_ok:
            reason = "horizontal_tail_condition_not_sealed"
        else:
            reason = "beta_condition_not_sealed"
        result[scenario_id] = {
            "eligible": eligible,
            "backend": "vspaero",
            "coverage_kind": qualified_scope.get("coverage_kind", "none"),
            "beta_deg": requested_beta if math.isfinite(requested_beta) else None,
            "scenario_sha256": sha256_payload(scenario),
            "reason": reason,
        }
    return result


def _collect_evidence_files(run_dir: Path) -> list[dict]:
    excluded_names = {".certification.lock", "source_manifest.json", "certificate.json", "certificate.md"}
    evidence = []
    for path in sorted(item for item in run_dir.rglob("*") if item.is_file()):
        if path.name in excluded_names:
            continue
        relative = path.relative_to(run_dir).as_posix()
        if relative.startswith("probes/"):
            role = "vspaero_probe_" + path.suffix.lower().lstrip(".")
        elif relative.startswith("machline/"):
            role = "machline_evidence"
        elif relative.startswith("inventory/") or relative.startswith("post_diagnostics/"):
            role = "geometry_inventory"
        elif path.suffix.lower() == ".vsp3":
            role = "sealed_geometry"
        else:
            role = "certification_evidence"
        evidence.append({
            "role": role,
            "relative_path": relative,
            "path": str(path.resolve()),
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        })
    return evidence


def certify_geometry(
    *,
    master_path: Path,
    output_root: Path,
    project_name: str,
    reference: dict,
    policy_path: Path,
    executables: dict[str, Path | None],
    tri_path: Path | None = None,
    scope_override: dict | None = None,
    policy_overrides: dict | None = None,
    run_vspaero_probes: bool | None = None,
) -> dict:
    """Run G0--G8 and return paths plus the final certificate."""
    master_path = master_path.resolve()
    if not master_path.is_file() or master_path.suffix.lower() != ".vsp3":
        raise FileNotFoundError(f"Не найден MASTER VSP3: {master_path}")
    executable = executables.get("vspscript")
    if executable is None or not executable.is_file():
        raise FileNotFoundError("Для сертификации требуется vspscript.exe")
    executable_sha256_before = {
        name: sha256_file(path)
        for name, path in executables.items()
        if path is not None and path.is_file()
    }
    base_policy = load_geometry_policy(policy_path)
    policy = effective_policy(base_policy, policy_overrides)
    scope = _validate_scope(scope_override or policy["scope"])
    policy["scope"] = deepcopy(scope)
    reference = {
        "area": float(reference["area"]),
        "cref": float(reference.get("cref", reference.get("longitudinal_length"))),
        "bref": float(reference.get("bref", reference.get("lateral_length"))),
        "center": [float(value) for value in reference["center"]],
    }
    run_dir = _unique_run_dir(output_root, project_name, master_path.stem)
    lock_path = run_dir / ".certification.lock"
    lock_path.write_text(str(os.getpid()), encoding="ascii")

    master_before = sha256_file(master_path)
    master_canonical = canonical_vsp3_sha256(master_path)
    frozen = run_dir / "frozen_master.vsp3"
    shutil.copyfile(master_path, frozen)
    if sha256_file(frozen) != master_before:
        raise RuntimeError("Замороженная копия MASTER не совпадает с источником")
    policy_snapshot = run_dir / "policy_snapshot.json"
    write_json(policy_snapshot, policy)
    policy_sha = sha256_payload(policy)
    reference_sha = sha256_payload(reference)
    scope_sha = sha256_payload(scope)
    source_manifest_path = run_dir / "source_manifest.json"
    source_manifest = {
        "schema": RUN_SCHEMA,
        "method_version": policy["method_version"],
        "project": project_name,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "master": {
            "path": str(master_path),
            "sha256": master_before,
            "canonical_sha256": master_canonical,
            "size_bytes": master_path.stat().st_size,
            "frozen_snapshot": str(frozen.resolve()),
        },
        "bindings": {
            "policy_sha256": policy_sha,
            "reference_sha256": reference_sha,
            "scope_sha256": scope_sha,
        },
        "reference": reference,
        "scope": scope,
        "status": "running",
    }
    write_json(source_manifest_path, source_manifest)

    inventory_path = run_dir / "inventory" / "master_inventory.json"
    semantic_path = run_dir / "semantic_audit.json"
    diagnostics_path = run_dir / "native_diagnostics.json"
    plan_path = run_dir / "transformation_plan.json"
    transformation_log = run_dir / "transformation_log.jsonl"
    delta_path = run_dir / "geometry_deltas.json"
    twins: dict[str, dict] = {}
    mesh_records: dict[str, dict[str, dict]] = {}
    post_inventories: dict[str, dict] = {}
    probes: dict = {"requested": False, "status": "not_run"}
    tri_result: dict = {"requested": False, "eligible": False, "status": "not_run"}
    machline_export_record: dict | None = None
    findings: list[dict] = []
    error_message = None

    try:
        # G1/G2 -- OpenVSP is the authority for components, Sets and physical XSecs.
        inventory, inventory_script, inventory_log = inventory_model(
            frozen,
            vspscript_executable=executable,
            work_dir=run_dir / "inventory",
            timeout_seconds=float(policy["probes"]["openvsp_load"].get("timeout_seconds", 180)),
        )
        write_json(inventory_path, inventory)
        semantic = semantic_audit(inventory, reference, policy)
        write_json(semantic_path, semantic)
        native = _native_diagnostics(inventory, semantic)
        write_json(diagnostics_path, native)
        findings.extend(semantic["findings"])
        if not inventory.get("valid"):
            findings.append(finding(
                "GEO-FILE-001", "BLOCKER", "OpenVSP не смог полностью инвентаризировать MASTER",
                evidence={"errors": inventory.get("errors", [])},
            ))

        # G3 -- a sealed plan.  APPLY is impossible if any binding changed.
        pre_plan_findings = deepcopy(findings)
        plan = build_transformation_plan(
            master_sha256=master_before,
            policy_sha256=policy_sha,
            reference_sha256=reference_sha,
            scope_sha256=scope_sha,
            inventory=inventory,
            semantic_audit=semantic,
            reference=reference,
            policy=policy,
        )
        write_json(plan_path, plan)
        findings = pre_plan_findings
        for item in deepcopy(plan["findings"]):
            if item not in findings:
                findings.append(item)
        plan_errors = verify_plan(
            plan,
            master_sha256=sha256_file(master_path),
            policy_sha256=sha256_payload(policy),
            reference_sha256=sha256_payload(reference),
            scope_sha256=sha256_payload(scope),
        )
        if plan_errors:
            for error in plan_errors:
                findings.append(finding("GEO-PLAN-001", "BLOCKER", error, scope="plan"))
            raise RuntimeError("PLAN не допущен к APPLY")

        # G4 -- independent copies. No solver-specific mutation crosses backends.
        twin_specs = {
            "vspaero_mixed": (run_dir / "vspaero" / "mixed" / "model.vsp3", None),
            "vspaero_lifting": (
                run_dir / "vspaero" / "lifting" / "model.vsp3",
                int(policy["sets"]["empty_thick_user_set"]),
            ),
            "machline": (run_dir / "machline" / "source_model.vsp3", None),
        }
        for twin_name, (destination, empty_set) in twin_specs.items():
            twins[twin_name] = apply_model_actions(
                source_path=frozen,
                output_path=destination,
                actions=plan["actions"],
                twin_name=twin_name,
                vspscript_executable=executable,
                work_dir=run_dir / "scripts",
                empty_thick_user_set=empty_set,
            )
        twins["parasite"] = copy_passthrough(
            frozen, run_dir / "parasite" / "model.vsp3", "parasite"
        )
        if not all(item["valid"] for item in twins.values()):
            findings.append(finding(
                "GEO-TWIN-001", "BLOCKER", "Не все расчётные двойники созданы корректно",
                scope="twins", evidence={name: item["valid"] for name, item in twins.items()},
            ))
            raise RuntimeError("Сбой создания расчётного двойника")

        # G6 -- read every twin back through OpenVSP, then quantify changes.
        for name, twin in twins.items():
            inv, _, _ = inventory_model(
                Path(twin["path"]),
                vspscript_executable=executable,
                work_dir=run_dir / "post_diagnostics" / name,
            )
            post_inventories[name] = inv
            write_json(run_dir / "post_diagnostics" / name / "inventory.json", inv)
            if not inv["valid"]:
                findings.append(finding(
                    "GEO-TWIN-002", "BLOCKER", f"Повторное чтение двойника {name} не прошло",
                    scope=name, evidence={"errors": inv.get("errors", [])},
                ))

        exclusions = deepcopy(semantic.get("declared_exclusions", []))
        vspaero_exclusions = _backend_exclusions(exclusions, "vspaero")
        deltas = compute_geometry_deltas(
            inventory, post_inventories, plan["actions"], reference, exclusions
        )
        delta_ok, delta_errors = delta_within_policy(deltas, policy)
        write_json(delta_path, deltas)
        if not delta_ok:
            for error in delta_errors:
                findings.append(finding("GEO-DELTA-001", "BLOCKER", error, scope="twins"))

        # G7 -- create all three repeatable mesh levels for both VSPAERO modes and MachLine export.
        for twin_name in ("vspaero_mixed", "vspaero_lifting", "machline"):
            mesh_records[twin_name] = {}
            base_inventory = post_inventories[twin_name]
            base_audit = semantic_audit(base_inventory, reference, policy)
            # Declared exclusions are intentionally absent from solver Sets; they remain declared in the certificate.
            for level_name, level_values in policy["mesh_levels"].items():
                destination = Path(twins[twin_name]["path"]).parent / level_name / "model.vsp3"
                actions = mesh_actions(base_inventory, base_audit, level_values, twin_name)
                record = apply_model_actions(
                    source_path=Path(twins[twin_name]["path"]),
                    output_path=destination,
                    actions=actions,
                    twin_name=twin_name,
                    vspscript_executable=executable,
                    # Short directory tokens avoid MAX_PATH failures on the
                    # deeply nested desktop-project checkout.
                    work_dir=(
                        run_dir / "ms" / ({
                            "vspaero_mixed": "vm",
                            "vspaero_lifting": "vl",
                            "machline": "ml",
                        }[twin_name] + {
                            "coarse": "c",
                            "medium": "m",
                            "fine": "f",
                            "extra_fine": "x",
                            "ultra_fine": "u",
                        }.get(level_name, level_name[:1]))
                    ),
                    empty_thick_user_set=(
                        int(policy["sets"]["empty_thick_user_set"])
                        if twin_name == "vspaero_lifting" else None
                    ),
                )
                record["level"] = level_name
                record["mesh_policy"] = deepcopy(level_values)
                # Keep these paths deliberately short: certification runs can
                # already sit near the legacy Windows MAX_PATH limit.
                twin_token = {
                    "vspaero_mixed": "vm",
                    "vspaero_lifting": "vl",
                    "machline": "ml",
                }[twin_name]
                level_token = {
                    "coarse": "c",
                    "medium": "m",
                    "fine": "f",
                    "extra_fine": "x",
                    "ultra_fine": "u",
                }.get(level_name, level_name[:1])
                mesh_inventory_path = (
                    run_dir / "mesh_diag" / f"{twin_token}_{level_token}" / "inventory.json"
                )
                mesh_verification = {
                    "valid": False,
                    "components": [],
                    "parameter_signature": [],
                    "signature_sha256": None,
                    "errors": ["Сеточный двойник отсутствует и не может быть перечитан"],
                }
                if destination.is_file():
                    try:
                        mesh_inventory, _, _ = inventory_model(
                            destination,
                            vspscript_executable=executable,
                            work_dir=mesh_inventory_path.parent,
                        )
                        write_json(mesh_inventory_path, mesh_inventory)
                        mesh_audit = semantic_audit(mesh_inventory, reference, policy)
                        mesh_verification = verify_mesh_inventory(
                            mesh_inventory, mesh_audit, level_values
                        )
                    except Exception as exc:
                        mesh_verification["errors"] = [
                            f"Сбой повторного чтения сеточного двойника: {exc}"
                        ]
                record["post_inventory"] = (
                    str(mesh_inventory_path.resolve()) if mesh_inventory_path.is_file() else None
                )
                record["mesh_verification"] = mesh_verification
                record["valid"] = bool(record.get("valid") and mesh_verification["valid"])
                if not mesh_verification["valid"]:
                    record.setdefault("errors", []).extend(mesh_verification["errors"])
                mesh_records[twin_name][level_name] = record
                if not record["valid"]:
                    findings.append(finding(
                        "GEO-MESH-001", "BLOCKER",
                        f"Сетка {twin_name}/{level_name} не создана или не соответствует mesh policy",
                        scope=twin_name, evidence={"errors": record.get("errors", [])},
                    ))

            signatures: dict[str, list[str]] = {}
            for level_name, record in mesh_records[twin_name].items():
                signature = record.get("mesh_verification", {}).get("signature_sha256")
                if signature:
                    signatures.setdefault(signature, []).append(level_name)
            duplicate_levels = [levels for levels in signatures.values() if len(levels) > 1]
            if duplicate_levels:
                for levels in duplicate_levels:
                    for level_name in levels:
                        record = mesh_records[twin_name][level_name]
                        record["valid"] = False
                        record.setdefault("errors", []).append(
                            "Фактические Tess_* совпали с другим уровнем: " + ", ".join(levels)
                        )
                findings.append(finding(
                    "GEO-MESH-002", "BLOCKER",
                    f"Сеточные уровни {twin_name} не являются независимыми",
                    scope=twin_name, evidence={"duplicate_levels": duplicate_levels},
                ))

        machline_executable = executables.get("machline")
        machline_requested = bool(
            tri_path is not None
            or (machline_executable is not None and machline_executable.is_file())
        )
        if machline_requested:
            fine_machline_record = mesh_records.get("machline", {}).get("fine", {})
            machline_export_record = _export_machline_tri(
                solver_twin_record=fine_machline_record,
                run_dir=run_dir,
                policy=policy,
                vspscript_executable=executable,
                certification_bindings={
                    "master_sha256": master_before,
                    "plan_sha256": plan.get("plan_sha256"),
                    "base_twin_sha256": twins.get("machline", {}).get("sha256"),
                    "fine_twin_sha256": fine_machline_record.get("sha256"),
                    "fine_mesh_signature_sha256": (
                        fine_machline_record.get("mesh_verification", {})
                        .get("signature_sha256")
                    ),
                },
                legacy_tri_hint=tri_path,
            )
            exported_path = Path(str(machline_export_record.get("export", {}).get("path", "")))
            tri_result, tri_findings = _tri_certification(
                exported_path if exported_path.is_file() else None,
                run_dir,
                policy,
                scope,
                export_record=machline_export_record,
                reference=reference,
                machline_executable=machline_executable,
            )
        else:
            tri_result, tri_findings = _tri_certification(None, run_dir, policy, scope)
        findings.extend(tri_findings)

        enabled = bool(policy["probes"]["vspaero"].get("enabled", True))
        if run_vspaero_probes is not None:
            enabled = bool(run_vspaero_probes)
        if enabled:
            probes["requested"] = True
            has_gondola = any(
                component.get("semantic_base") == "Gondola"
                for component in semantic.get("recognized_components", [])
            )
            mixed = None
            if not vspaero_exclusions:
                mixed = _run_vspaero_qualification(
                    mode="mixed",
                    meshes=mesh_records["vspaero_mixed"],
                    run_dir=run_dir,
                    reference=reference,
                    policy=policy,
                    executable=executable,
                    has_gondola=has_gondola,
                )
            probes["vspaero_mixed"] = mixed
            mixed_ok = bool(mixed and mixed["valid"] and mixed["converged"])
            lifting = None
            if not mixed_ok and policy["probes"]["vspaero"].get("fallback_to_lifting", True):
                lifting = _run_vspaero_qualification(
                    mode="lifting",
                    meshes=mesh_records["vspaero_lifting"],
                    run_dir=run_dir,
                    reference=reference,
                    policy=policy,
                    executable=executable,
                    has_gondola=has_gondola,
                )
            lifting_ok = bool(lifting and lifting["valid"] and lifting["converged"])
            probes["vspaero_lifting"] = lifting
            probes["status"] = "completed"
            for ladder_name, ladder in (("mixed", mixed), ("lifting", lifting)):
                if not ladder:
                    continue
                warning_evidence = [
                    {
                        "level": item.get("level"),
                        "signals": item.get("health_warnings", []),
                        "log": item.get("log"),
                    }
                    for item in ladder.get("probes", [])
                    if item.get("health_warnings")
                ]
                if warning_evidence:
                    findings.append(finding(
                        "GEO-VSP-001",
                        "WARNING",
                        f"VSPAERO ({ladder_name}) сообщил диагностические особенности сетки; "
                        "результаты допускаются только по конечности и сеточной сходимости",
                        scope=f"vspaero_{ladder_name}",
                        evidence={"probes": warning_evidence},
                    ))
                localized_failures = []
                for anchor in ladder.get("anchors", []):
                    diagnostics = anchor.get("failure_diagnostics")
                    if not isinstance(diagnostics, dict):
                        continue
                    restored_groups = diagnostics.get(
                        "groups_whose_exclusion_restored_solution", []
                    )
                    if not restored_groups:
                        continue
                    localized_failures.append({
                        "mach": anchor.get("mach"),
                        "alpha_deg": anchor.get("alpha_deg"),
                        "mesh_level": diagnostics.get("condition", {}).get("mesh_level"),
                        "groups": list(restored_groups),
                    })
                if localized_failures:
                    findings.append(finding(
                        "GEO-VSP-004",
                        "WARNING",
                        "Нечисловой отказ локализован по компонентам; подбор одной "
                        "проходящей сетки запрещён, требуется исправление MASTER или "
                        "явный физически обоснованный гибридный контракт",
                        scope=f"vspaero_{ladder_name}",
                        evidence={"localized_failures": localized_failures},
                    ))
            if not mixed_ok and lifting and lifting.get("valid") and lifting.get("converged"):
                findings.append(finding(
                    "GEO-VSP-002",
                    "WARNING",
                    "Смешанная постановка VSPAERO не прошла квалификацию; "
                    "принят отдельный несущий двойник с явной гибридной заменой толстых тел",
                    scope="vspaero",
                ))
            elif not mixed_ok and not lifting_ok:
                findings.append(finding(
                    "GEO-VSP-003",
                    "BLOCKER",
                    "Ни одна постановка VSPAERO не дала полного конечного сеточно сходящегося результата",
                    scope="vspaero",
                    evidence={
                        "mixed": mixed.get("convergence") if mixed else None,
                        "lifting": lifting.get("convergence") if lifting else None,
                    },
                ))
        else:
            probes = {
                "requested": False,
                "status": "skipped",
                "reason": "Backend probes disabled: no solver-eligible certificate can be issued",
            }
        _write_transformation_log(transformation_log, plan, twins)

    except Exception as exc:
        error_message = str(exc)
        if not any(item["severity"] == "BLOCKER" for item in findings):
            findings.append(finding("GEO-RUN-001", "BLOCKER", error_message, scope="run"))
    finally:
        master_after = sha256_file(master_path) if master_path.is_file() else None
        master_unchanged = master_after == master_before
        if not master_unchanged:
            findings.append(finding(
                "GEO-MASTER-001", "BLOCKER", "MASTER изменился во время сертификации",
                evidence={"before": master_before, "after": master_after},
            ))

        def executable_unchanged(name: str) -> bool:
            path = executables.get(name)
            before = executable_sha256_before.get(name)
            return bool(
                before
                and path is not None
                and path.is_file()
                and sha256_file(path) == before
            )

        if not executable_unchanged("vspscript"):
            findings.append(finding(
                "GEO-SOLVER-001",
                "BLOCKER",
                "vspscript.exe отсутствует, изменился или не имеет фиксируемого SHA-256",
                scope="run",
            ))

        semantic_exclusions = []
        if "semantic" in locals():
            semantic_exclusions = deepcopy(semantic.get("declared_exclusions", []))
        mixed = probes.get("vspaero_mixed") if isinstance(probes, dict) else None
        lifting = probes.get("vspaero_lifting") if isinstance(probes, dict) else None
        mixed_ok = bool(mixed and mixed.get("valid") and mixed.get("converged"))
        lifting_ok = bool(lifting and lifting.get("valid") and lifting.get("converged"))
        if (mixed_ok or lifting_ok) and not executable_unchanged("vspaero"):
            findings.append(finding(
                "GEO-SOLVER-002",
                "BLOCKER",
                "VSPAERO не может быть допущен без неизменного vspaero.exe и его SHA-256",
                scope="vspaero",
            ))
        if tri_result.get("eligible") and not executable_unchanged("machline"):
            findings.append(finding(
                "GEO-SOLVER-003",
                "BLOCKER",
                "MachLine не может быть допущен без неизменного machline.exe и его SHA-256",
                scope="machline",
            ))
        exclusions = semantic_exclusions
        vspaero_exclusions = _backend_exclusions(exclusions, "vspaero")
        if not mixed_ok and lifting_ok and "inventory" in locals():
            known = {item.get("component") for item in vspaero_exclusions}
            for component in inventory.get("components", []):
                base_role = next(
                    (
                        item.get("semantic_role") for item in semantic.get("recognized_components", [])
                        if item.get("id") == component.get("id")
                    ),
                    None,
                )
                if base_role == "thick" and component.get("name") not in known:
                    exclusions.append({
                        "component": component.get("name"),
                        "backends": ["vspaero", "hybrid"],
                        "code": "GEO-INT-001",
                        "reason": "mixed VSPAERO probe rejected; lifting-only twin accepted",
                        "replacement_required": [
                            "machline_pressure_wave_all_points",
                            "parasite_drag_subsonic",
                        ],
                    })
            vspaero_exclusions = _backend_exclusions(exclusions, "vspaero")
        vspaero_blockers = [item for item in findings if _is_backend_blocker(item, "vspaero")]
        transformations = locals().get("plan", {}).get("actions", [])
        delta_ok_final = locals().get("delta_ok", False)
        if error_message is not None or not master_unchanged or vspaero_blockers or not delta_ok_final:
            verdict = "FAIL"
            vspaero_eligible = False
            selected_mode = None
        elif mixed_ok and not vspaero_exclusions:
            verdict = "PASS_REGULARIZED" if transformations else "PASS_NATIVE"
            vspaero_eligible = True
            selected_mode = "mixed"
        elif lifting_ok:
            verdict = "PASS_WITH_DECLARED_EXCLUSIONS"
            vspaero_eligible = True
            selected_mode = "lifting"
        else:
            verdict = "FAIL"
            vspaero_eligible = False
            selected_mode = None

        run_complete = error_message is None
        machline_blockers = [
            item for item in findings if _is_backend_blocker(item, "machline")
        ]
        parasite_blockers = [
            item for item in findings if _is_backend_blocker(item, "parasite_drag")
        ]
        hybrid_blockers = [
            item for item in findings if _is_backend_blocker(item, "hybrid")
        ]
        vspaero_eligible = _backend_eligible(
            local_eligible=vspaero_eligible,
            blockers=vspaero_blockers,
            master_unchanged=master_unchanged,
            run_complete=run_complete,
            verdict=verdict,
        )
        if not vspaero_eligible:
            selected_mode = None
        machline_eligible = _backend_eligible(
            local_eligible=bool(tri_result.get("eligible")),
            blockers=machline_blockers,
            master_unchanged=master_unchanged,
            run_complete=run_complete,
            verdict=verdict,
        )
        parasite_eligible = _backend_eligible(
            local_eligible=bool(twins.get("parasite", {}).get("valid")),
            blockers=parasite_blockers,
            master_unchanged=master_unchanged,
            run_complete=run_complete,
            verdict=verdict,
        )
        base_drag_required = bool(
            tri_result.get("repairs", {})
            .get("downstream_axial_closure", {})
            .get("accepted")
        )
        hybrid_replacements_required = bool(
            vspaero_exclusions or base_drag_required
        )
        hybrid_eligible = _backend_eligible(
            local_eligible=not hybrid_replacements_required,
            blockers=hybrid_blockers,
            master_unchanged=master_unchanged,
            run_complete=run_complete,
            verdict=verdict,
            dependency_eligible=vspaero_eligible,
        )
        hybrid_conditional = _backend_eligible(
            local_eligible=hybrid_replacements_required,
            blockers=hybrid_blockers,
            master_unchanged=master_unchanged,
            run_complete=run_complete,
            verdict=verdict,
            dependency_eligible=vspaero_eligible,
        )

        profile_name, profile = _qualification_profile(policy)
        selected_qualification = mixed if selected_mode == "mixed" else lifting if selected_mode == "lifting" else None
        selected_mesh_record = (
            mesh_records.get(f"vspaero_{selected_mode}", {}).get("fine", {})
            if selected_mode else {}
        )
        qualified_scope = _qualified_scope(
            requested_scope=scope,
            profile_name=profile_name,
            profile=profile,
            qualification=selected_qualification,
            eligible=vspaero_eligible,
        )
        scenario_eligibility = _derive_scenario_eligibility(qualified_scope, scope)
        eligible_scenarios = [
            name for name, item in scenario_eligibility.items() if item.get("eligible")
        ]

        # A failed optional MachLine request does not revoke an independently valid VSPAERO certificate.
        flags = {
            "solver_eligible": vspaero_eligible,
            "full_geometry_represented": bool(vspaero_eligible and selected_mode == "mixed" and not vspaero_exclusions),
            "hybrid_substitution_required": bool(
                vspaero_eligible and hybrid_replacements_required
            ),
            "master_unchanged": master_unchanged,
        }
        certificate_twins = deepcopy(twins)
        for twin in certificate_twins.values():
            if isinstance(twin, dict):
                twin["relative_path"] = _certificate_relative_path(
                    run_dir, twin.get("path")
                )
        inventory_for_payload = locals().get("inventory", {})
        inventory_component_names = sorted(
            {
                str(component.get("name"))
                for component in inventory_for_payload.get("components", [])
                if str(component.get("name", "")).strip()
            },
            key=str.casefold,
        )
        machline_component_coverage = (
            tri_result.get("component_coverage", {})
            .get("pressure_wave", {})
            .get("components", [])
        )
        parasite_component_coverage = (
            inventory_component_names if parasite_eligible else []
        )
        replacement_contract = _build_replacement_contract(
            exclusions,
            machline_eligible=machline_eligible,
            machline_components=machline_component_coverage,
            parasite_eligible=parasite_eligible,
            parasite_components=parasite_component_coverage,
            base_drag_required=bool(
                base_drag_required
            ),
        )

        payload = {
            "schema": "repairmach.geometry-certificate/1.1",
            "method_version": policy["method_version"],
            "run_status": "complete" if run_complete else "failed",
            "verdict": verdict,
            "flags": flags,
            "project": project_name,
            "master": {
                "source_path": str(master_path),
                "snapshot_path": str(frozen.resolve()),
                "sha256_before": master_before,
                "sha256_after": master_after,
                "canonical_sha256": master_canonical,
                "unchanged": master_unchanged,
            },
            "bindings": {
                "policy_sha256": policy_sha,
                "reference_sha256": reference_sha,
                "scope_sha256": scope_sha,
                "plan_sha256": locals().get("plan", {}).get("plan_sha256"),
            },
            "policy": {"path": str(policy_path.resolve()), "snapshot": str(policy_snapshot.resolve())},
            "reference": reference,
            "scope": scope,
            "requested_scope": scope,
            "qualified_scope": qualified_scope,
            "qualification": {
                "profile": profile_name,
                "coverage_kind": profile.get("coverage_kind", "exact_points"),
                "selected_mode": selected_mode,
                "anchors": selected_qualification.get("anchors", []) if selected_qualification else [],
                "requested_anchor_count": len(profile.get("points", [])),
                "qualified_anchor_count": len(qualified_scope.get("points", [])),
            },
            "software": _software_manifest(locals().get("inventory", {}), executables),
            "findings": findings,
            "transformations": {"actions": transformations},
            "twins": certificate_twins,
            "mesh_levels": mesh_records,
            "probes": probes,
            "exclusions": exclusions,
            "backends": {
                "vspaero": {
                    "eligible": vspaero_eligible,
                    "mode": selected_mode,
                    "qualified_scope": qualified_scope,
                    "solver_geometry": {
                        "path": selected_mesh_record.get("path"),
                        "relative_path": _certificate_relative_path(
                            run_dir, selected_mesh_record.get("path")
                        ),
                        "sha256": selected_mesh_record.get("sha256"),
                        "canonical_sha256": selected_mesh_record.get("canonical_sha256"),
                        "mesh_level": "fine" if selected_mesh_record else None,
                    },
                    "blockers": vspaero_blockers,
                },
                "machline": {
                    "eligible": machline_eligible,
                    "mode": "certified_thick_body_pressure_wave" if machline_eligible else None,
                    "solver_geometry": {
                        "path": tri_result.get("certified_tri"),
                        "relative_path": _certificate_relative_path(
                            run_dir, tri_result.get("certified_tri")
                        ),
                        "sha256": tri_result.get("certified_tri_sha256"),
                        "source_vsp3_path": (
                            (tri_result.get("export_record") or {})
                            .get("source_solver_twin", {})
                            .get("path")
                        ),
                        "source_vsp3_sha256": (
                            (tri_result.get("export_record") or {})
                            .get("source_solver_twin", {})
                            .get("sha256")
                        ),
                        "export_manifest": (
                            (tri_result.get("export_record") or {}).get("manifest_path")
                        ),
                        "export_record_fingerprint": (
                            (tri_result.get("export_record") or {}).get("record_fingerprint")
                        ),
                    },
                    "audit_geometry": {
                        "path": tri_result.get("audit_tri"),
                        "relative_path": _certificate_relative_path(
                            run_dir, tri_result.get("audit_tri")
                        ),
                        "sha256": tri_result.get("audit_tri_sha256"),
                    },
                    "force_integration_mask": {
                        "path": tri_result.get("force_integration_mask"),
                        "relative_path": _certificate_relative_path(
                            run_dir, tri_result.get("force_integration_mask")
                        ),
                        "sha256": (
                            sha256_file(Path(tri_result["force_integration_mask"]))
                            if tri_result.get("force_integration_mask")
                            and Path(tri_result["force_integration_mask"]).is_file()
                            else None
                        ),
                    },
                    "output_contract": {
                        "hybrid_pressure_wave_eligible": tri_result.get(
                            "hybrid_pressure_wave_eligible", False
                        ),
                        "standalone_total_force_eligible": tri_result.get(
                            "standalone_total_force_eligible", False
                        ),
                        "base_drag_replacement_required": bool(
                            tri_result.get("repairs", {})
                            .get("downstream_axial_closure", {})
                            .get("accepted")
                        ),
                        "thin_surfaces_delegated_to_vspaero": True,
                        "component_coverage": deepcopy(
                            tri_result.get("component_coverage", {})
                        ),
                    },
                    "qualification": tri_result.get("machline_qualification"),
                    "qualified_scope": {
                        "coverage_kind": "solver_envelope" if machline_eligible else "none",
                        "mach_intervals": deepcopy(scope["mach_intervals"]) if machline_eligible else [],
                        "alpha_deg": deepcopy(scope["alpha_deg"]) if machline_eligible else [],
                        "beta_deg": scope.get("beta_deg") if machline_eligible else None,
                    },
                    "blockers": machline_blockers,
                },
                "parasite_drag": {
                    "eligible": parasite_eligible,
                    "mode": "full_geometry" if parasite_eligible else None,
                    "geometry_set": 0,
                    "reference_area": reference["area"],
                    "solver_geometry": {
                        "path": twins.get("parasite", {}).get("path"),
                        "relative_path": _certificate_relative_path(
                            run_dir, twins.get("parasite", {}).get("path")
                        ),
                        "sha256": twins.get("parasite", {}).get("sha256"),
                        "canonical_sha256": twins.get("parasite", {}).get("canonical_sha256"),
                    },
                    "qualified_scope": {
                        "coverage_kind": "geometry_only" if parasite_eligible else "none"
                    },
                    "component_coverage": {
                        "coverage_kind": (
                            "exact_openvsp_components" if parasite_eligible else "none"
                        ),
                        "complete": parasite_eligible,
                        "components": (
                            inventory_component_names
                            if parasite_eligible else []
                        ),
                    },
                    "blockers": parasite_blockers,
                },
                "hybrid": {
                    "eligible": hybrid_eligible,
                    "conditional": hybrid_conditional,
                    "mode": (
                        "declared_replacements_required"
                        if hybrid_replacements_required else "direct_or_optional"
                    ),
                    "qualified_scope": qualified_scope,
                    "replacement_contract": replacement_contract,
                    "blockers": hybrid_blockers,
                },
            },
            "tri": tri_result,
            "scenario_eligibility": scenario_eligibility,
            "eligible_scenarios": eligible_scenarios,
            "artifacts": {
                "run_directory": str(run_dir.resolve()),
                "source_manifest": str(source_manifest_path.resolve()),
                "inventory": str(inventory_path.resolve()),
                "semantic_audit": str(semantic_path.resolve()),
                "native_diagnostics": str(diagnostics_path.resolve()),
                "transformation_plan": str(plan_path.resolve()),
                "transformation_log": str(transformation_log.resolve()),
                "geometry_deltas": str(delta_path.resolve()),
            },
            "error": error_message,
        }
        payload["evidence_files"] = _collect_evidence_files(run_dir)
        certificate, certificate_path, markdown_path = emit_certificate(run_dir, payload)
        source_manifest["status"] = "complete" if verdict != "FAIL" else "failed"
        source_manifest["certificate"] = str(certificate_path.resolve())
        source_manifest["certificate_id"] = certificate["certificate_id"]
        source_manifest["verdict"] = verdict
        write_json(source_manifest_path, source_manifest)
        lock_path.unlink(missing_ok=True)

    return {
        "run_directory": run_dir,
        "certificate": certificate,
        "certificate_path": certificate_path,
        "report_path": markdown_path,
    }
