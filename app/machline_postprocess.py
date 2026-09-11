#!/usr/bin/env python3
"""Fail-closed component force masking for certified MachLine results.

MachLine writes the dimensional-free force contribution of every TRI panel to
the ``dC_f`` VTK cell vector.  A certified numerical closure must participate
in the potential solution but must never participate in the physical force
sum.  This module recomputes the total from the per-cell vectors, proves their
ordering against MachLine's own report and then applies the sealed component
mask.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from geometry_manifest import sha256_file, write_json
from tri_mesh import TriMesh


def _finite_vector(values: object, *, label: str) -> tuple[float, float, float]:
    if not isinstance(values, (list, tuple)) or len(values) != 3:
        raise ValueError(f"{label} должен содержать три компоненты")
    result = tuple(float(value) for value in values)
    if not all(math.isfinite(value) for value in result):
        raise ValueError(f"{label} содержит нечисловое значение")
    return result


def read_vtk_cell_vectors(
    path: Path,
    name: str,
    *,
    expected_count: int,
) -> list[tuple[float, float, float]]:
    """Read one legacy ASCII VTK ``VECTORS`` cell array."""
    if expected_count <= 0:
        raise ValueError("Ожидаемое число VTK-ячеек должно быть положительным")
    lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    cell_count = None
    vector_line = None
    for index, raw in enumerate(lines):
        values = raw.split()
        if len(values) >= 2 and values[0].upper() == "CELL_DATA":
            cell_count = int(values[1])
        if (
            len(values) >= 3
            and values[0].upper() == "VECTORS"
            and values[1] == name
        ):
            vector_line = index
            break
    if cell_count != expected_count:
        raise ValueError(
            f"VTK CELL_DATA={cell_count}, а сертифицированный TRI содержит "
            f"{expected_count} панелей"
        )
    if vector_line is None:
        raise ValueError(f"VTK не содержит обязательный cell-vector {name}")
    vectors = []
    for raw in lines[vector_line + 1:]:
        values = raw.split()
        if not values:
            continue
        if len(values) != 3:
            break
        try:
            vector = tuple(float(value) for value in values)
        except ValueError:
            break
        if not all(math.isfinite(value) for value in vector):
            raise ValueError(f"VTK {name} содержит нечисловой вектор")
        vectors.append(vector)
        if len(vectors) == expected_count:
            break
    if len(vectors) != expected_count:
        raise ValueError(
            f"VTK {name}: прочитано {len(vectors)} из {expected_count} векторов"
        )
    return vectors


def _sum_coefficients(
    vectors: list[tuple[float, float, float]],
    indices: list[int],
    reference_area: float,
) -> tuple[float, float, float]:
    return tuple(
        sum(vectors[index][axis] for index in indices) / reference_area
        for axis in range(3)
    )


def _wind_axes(
    coefficients: tuple[float, float, float],
    freestream_velocity: object,
    *,
    spanwise_axis: str,
) -> dict[str, float]:
    velocity = _finite_vector(freestream_velocity, label="freestream_velocity")
    magnitude = math.sqrt(sum(value * value for value in velocity))
    if magnitude <= 0.0:
        raise ValueError("freestream_velocity не может быть нулевым")
    drag = tuple(value / magnitude for value in velocity)
    axes = {
        "+x": (1.0, 0.0, 0.0), "-x": (-1.0, 0.0, 0.0),
        "+y": (0.0, 1.0, 0.0), "-y": (0.0, -1.0, 0.0),
        "+z": (0.0, 0.0, 1.0), "-z": (0.0, 0.0, -1.0),
    }
    if spanwise_axis not in axes:
        raise ValueError(f"Неподдерживаемая поперечная ось MachLine: {spanwise_axis}")
    span = axes[spanwise_axis]
    span_projection = sum(span[i] * drag[i] for i in range(3))
    if abs(span_projection) > 1.0e-8:
        raise ValueError("Поперечная ось не перпендикулярна скорости")
    lift = (
        span[1] * drag[2] - span[2] * drag[1],
        span[2] * drag[0] - span[0] * drag[2],
        span[0] * drag[1] - span[1] * drag[0],
    )
    return {
        "cd": sum(coefficients[i] * drag[i] for i in range(3)),
        "cl": sum(coefficients[i] * lift[i] for i in range(3)),
        "cy_span": sum(coefficients[i] * span[i] for i in range(3)),
    }


def masked_force_coefficients(
    *,
    mesh: TriMesh,
    body_vtk_path: Path,
    reference_area: float,
    include_component_ids: list[int],
    exclude_component_ids: list[int],
    report_total_forces: dict,
    freestream_velocity: object,
    spanwise_axis: str = "+z",
    alignment_tolerance: float = 1.0e-8,
) -> dict:
    """Apply a sealed force mask only after proving VTK/TRI face alignment."""
    area = float(reference_area)
    if not math.isfinite(area) or area <= 0.0:
        raise ValueError("Sref для интегрирования сил должна быть положительной")
    include = {int(value) for value in include_component_ids}
    exclude = {int(value) for value in exclude_component_ids}
    if not include or include & exclude:
        raise ValueError("Маска сил пуста или содержит пересекающиеся include/exclude")
    known = set(mesh.components)
    if not include <= known or not exclude <= known:
        raise ValueError("Маска сил ссылается на отсутствующий компонент TRI")
    if include | exclude != known:
        raise ValueError("Маска сил не классифицирует все компоненты TRI")

    vectors = read_vtk_cell_vectors(
        body_vtk_path, "dC_f", expected_count=len(mesh.faces)
    )
    all_indices = list(range(len(mesh.faces)))
    raw = _sum_coefficients(vectors, all_indices, area)
    reported = _finite_vector(
        [report_total_forces.get(key) for key in ("Cx", "Cy", "Cz")],
        label="MachLine total_forces",
    )
    mismatch = max(abs(raw[index] - reported[index]) for index in range(3))
    if mismatch > float(alignment_tolerance):
        raise ValueError(
            "Порядок VTK-ячеек не подтверждён: сумма dC_f не совпадает с "
            f"MachLine total_forces (max delta={mismatch:.6g})"
        )
    kept_indices = [
        index for index, component in enumerate(mesh.components)
        if component in include
    ]
    masked = _sum_coefficients(vectors, kept_indices, area)
    excluded = tuple(raw[index] - masked[index] for index in range(3))
    return {
        "raw_mesh_axes": dict(zip(("Cx", "Cy", "Cz"), raw)),
        "masked_mesh_axes": dict(zip(("Cx", "Cy", "Cz"), masked)),
        "excluded_mesh_axes": dict(zip(("Cx", "Cy", "Cz"), excluded)),
        "masked_wind_axes": _wind_axes(
            masked, freestream_velocity, spanwise_axis=spanwise_axis
        ),
        "alignment": {
            "verified": True,
            "max_force_delta": mismatch,
            "tolerance": float(alignment_tolerance),
            "vtk_cell_count": len(vectors),
            "tri_face_count": len(mesh.faces),
        },
        "included_panels": len(kept_indices),
        "excluded_panels": len(mesh.faces) - len(kept_indices),
    }


def write_masked_force_record(
    path: Path,
    *,
    mesh_path: Path,
    body_vtk_path: Path,
    report_path: Path,
    force_mask_path: Path,
    result: dict,
) -> dict:
    record = {
        "schema": "repairmach.machline-masked-force/1.0",
        "inputs": {
            "tri": {"path": str(mesh_path.resolve()), "sha256": sha256_file(mesh_path)},
            "body_vtk": {"path": str(body_vtk_path.resolve()), "sha256": sha256_file(body_vtk_path)},
            "report": {"path": str(report_path.resolve()), "sha256": sha256_file(report_path)},
            "force_mask": {"path": str(force_mask_path.resolve()), "sha256": sha256_file(force_mask_path)},
        },
        "result": result,
    }
    write_json(path, record)
    return record
