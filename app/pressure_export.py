"""Self-contained per-point ParaView output, without re-solving or smoothing Cp.

ADB v3 layout: OpenVSP_3.51.0/src/vsp_aero/Solver/VSP_Solver.C,
WriteOutAerothermalDatabase{Header,Geometry,Solution}; angles in ADB are radians.
Unknown layouts fail explicitly. Export success is NOT solver acceptance.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import shutil
import struct
from xml.sax.saxutils import escape
from aero_hybrid import is_vspaero_zero_mach_regularization


def _sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


class _Binary:
    def __init__(self, data):
        self.data, self.pos = data, 0
        self.endian = "<"
        if len(data) < 4:
            raise ValueError("Truncated ADB header")
        if struct.unpack_from("<i", data)[0] != -123789453:
            self.endian = ">"
        if self.read("i")[0] != -123789453:
            raise ValueError("Only OpenVSP ADB version 3 is supported")

    def read(self, fmt):
        size = struct.calcsize(self.endian + fmt)
        if self.pos + size > len(self.data):
            raise ValueError(f"Truncated ADB at byte {self.pos}")
        result = struct.unpack_from(self.endian + fmt, self.data, self.pos)
        self.pos += size
        return result

    def count(self):
        n = self.read("i")[0]
        if not 0 <= n <= 100_000_000:
            raise ValueError(f"Invalid ADB count: {n}")
        return n

    def skip(self, n):
        if n < 0 or self.pos + n > len(self.data):
            raise ValueError(f"Truncated ADB block at byte {self.pos}")
        self.pos += n

    def records(self, fmt, n):
        start = self.pos
        self.skip(struct.calcsize(self.endian + fmt) * n)
        return list(struct.iter_unpack(self.endian + fmt, memoryview(self.data)[start:self.pos]))


def read_adb_v3(path):
    """Read steady surface solutions; retain original vertex order/axes/Cp values."""
    reader = _Binary(Path(path).read_bytes())
    model_type, symmetry, unsteady = reader.read("3i")
    if unsteady:
        raise ValueError("Unsteady ADB needs a time-aware exporter; no steady-point substitution")
    loops, nodes, tris, edges = (reader.count() for _ in range(4))
    if nodes < 3 or tris < 1:
        raise ValueError("ADB contains no surface mesh")
    reference = reader.read("6f")
    surfaces = {}
    for _ in range(reader.count()):
        sid = reader.read("i")[0]
        name = reader.read("100s")[0].split(b"\0", 1)[0].decode("utf-8", errors="replace")
        surfaces[sid] = {"name": name, "component_id": reader.read("i")[0]}
    header = {"model_type": model_type, "symmetry": symmetry,
              "reference_s_c_b_xyz": reference, "surfaces": surfaces}
    cases = []
    while reader.pos < len(reader.data):
        triangles = reader.records("6if", tris)
        points = reader.records("3f", nodes)
        if any(not 1 <= v <= nodes for t in triangles for v in t[:3]):
            raise ValueError("ADB triangle vertex index out of range")
        if any(not math.isfinite(x) for p in points for x in p):
            raise ValueError("Non-finite ADB coordinates")
        rotors, nozzles = reader.count(), reader.count()
        # RotorDisk.C: 11 doubles; EngineFace.C: 7 doubles. Geometry metadata,
        # not pressure-bearing surface elements; no fabricated disk Cp.
        reader.skip(88 * rotors + 56 * nozzles)
        for _ in range(reader.count()):
            coarse_nodes, coarse_edges = reader.count(), reader.count()
            reader.skip(12 * coarse_nodes + 16 * coarse_edges)
        reader.skip(4 * reader.count())  # Kutta edges
        reader.skip(4 * reader.count())  # Kutta nodes
        controls = reader.count()
        for _ in range(controls):
            n = reader.count()
            reader.skip(12 * n + 36)  # optional polygon, hinge points and direction
            reader.skip(4 * reader.count())
        mach, alpha_rad, beta_rad, cp_min, cp_max = reader.read("5f")
        condition = {"mach": mach, "alpha_deg": math.degrees(alpha_rad), "beta_deg": math.degrees(beta_rad)}
        if any(not math.isfinite(x) for x in condition.values()):
            raise ValueError("Non-finite ADB condition")
        reader.skip(40 * loops + 24 * edges)  # gamma/dCp, forces, velocities
        fields = reader.records("3f", tris)  # Cp (or DeltaCp), Cp_unsteady, gamma
        if any(not math.isfinite(x) for row in fields for x in row):
            raise ValueError("Non-finite pressure field; native ADB retained for diagnosis")
        for _ in range(reader.count()):
            reader.skip(12)  # TE node (int) and span coordinate (double)
            reader.skip(24 * reader.count())
        reader.skip(4 * controls)
        cases.append({"condition": condition, "points": points, "triangles": triangles,
                      "fields": fields, "reported_cp_range": [cp_min, cp_max]})
    if not cases:
        raise ValueError("ADB has no complete solution")
    return header, cases


def _array(name, values, kind="Float64", components=1):
    data = " ".join(str(x) for x in values)
    return (f'<DataArray type="{kind}" Name="{escape(name)}" '
            f'NumberOfComponents="{components}" format="ascii">{data}</DataArray>')


def write_pressure_vtu(path, case, accepted):
    points, triangles, fields = case["points"], case["triangles"], case["fields"]
    n = len(triangles)
    content = ['<?xml version="1.0"?>', '<VTKFile type="UnstructuredGrid" version="0.1" byte_order="LittleEndian">',
               '<UnstructuredGrid>', f'<Piece NumberOfPoints="{len(points)}" NumberOfCells="{n}">',
               '<Points>', _array("Points", (x for p in points for x in p), components=3), '</Points><Cells>',
               _array("connectivity", (v - 1 for t in triangles for v in t[:3]), "Int64"),
               _array("offsets", range(3, 3 * n + 1, 3), "Int64"),
               _array("types", [5] * n, "UInt8"),
               '</Cells><CellData Scalars="' + ('Gamma' if case.get('kind') == 'wake' else 'Cp_or_DeltaCp') + '">',
               _array("Cp_or_DeltaCp", (f[0] for f in fields)),
               _array("Cp_unsteady", (f[1] for f in fields)),
               _array("Gamma", (f[2] for f in fields)),
               _array("ComponentID", (t[3] for t in triangles), "Int32"),
               _array("SurfaceID", (t[4] for t in triangles), "Int32"),
               _array("OriginalCellID", case.get("original_cells", range(n)), "Int64"),
               _array("SolverAccepted", [int(accepted)] * n, "UInt8"),
               '</CellData></Piece></UnstructuredGrid></VTKFile>']
    Path(path).write_text("\n".join(content), encoding="utf-8")


def _subset(case, indices, kind):
    """Split by explicit solver IDs, never by geometric distance or Cp magnitude."""
    used = sorted({v - 1 for i in indices for v in case["triangles"][i][:3]})
    mapping = {old + 1: new + 1 for new, old in enumerate(used)}
    triangles = [tuple(mapping[v] for v in case["triangles"][i][:3]) + case["triangles"][i][3:] for i in indices]
    return {**case, "kind": kind, "points": [case["points"][i] for i in used],
            "triangles": triangles, "fields": [case["fields"][i] for i in indices], "original_cells": indices}


def _condition_match(actual, expected, quality):
    if any(abs(actual[k] - float(expected[k])) > 1e-5 for k in ("alpha_deg", "beta_deg")):
        return False
    if abs(actual["mach"] - float(expected["mach"])) <= 1e-5:
        return True
    if not is_vspaero_zero_mach_regularization(expected["mach"], actual["mach"]):
        return False
    # Require recorded nominal/effective linkage from the native Solving block.
    # An arbitrary ADB at M=.001 must not silently stand in for a requested zero.
    return any(p.get("mach_mapping") == "zero_to_0.001"
               and p.get("Mach") == 0.0
               and p.get("solver_mach") is not None
               and abs(p["solver_mach"] - actual["mach"]) <= 1e-8
               and abs(p.get("alpha_deg", math.inf) - actual["alpha_deg"]) <= 1e-5
               and abs(p.get("Beta", math.inf) - actual["beta_deg"]) <= 1e-5
               for p in (quality or {}).get("points", []))


def export_vspaero_pressure(adb_path, output_dir, *, accepted=False, quality=None, expected_conditions=None, mode=None):
    """Export all steady points or record an explicit error; never alter solver validity.

    Output directory must be fresh so stale data cannot masquerade as a new run.
    A rejected run may still be viewed, but every point is labelled diagnostic.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    adb_path = Path(adb_path)
    result = {"schema": "repairmach.pressure-export/1.0", "backend": "vspaero",
              "status": "failed", "solver_accepted": bool(accepted), "quality": quality or {},
              "source": str(adb_path.resolve()), "points": [], "mode": mode,
              "axes": "OpenVSP XYZ (unchanged)", "pressure_units": "dimensionless",
              "pressure_meaning": "Native Cp on thick panels; pressure jump DeltaCp on thin lifting surfaces. Not absolute pressure.",
              "source_format": "ADB v3, steady", "errors": []}
    try:
        result["source_sha256"] = _sha(adb_path)
        header, cases = read_adb_v3(adb_path)
        if expected_conditions is not None:
            remaining = list(expected_conditions)
            for case in cases:
                match = next((i for i, expected in enumerate(remaining)
                              if _condition_match(case["condition"], expected, quality)), None)
                if match is None:
                    raise ValueError("ADB conditions do not match POLAR/requested points")
                case["requested_condition"] = dict(remaining.pop(match))
            if remaining:
                raise ValueError("ADB missing requested pressure points")
        result["header"] = header
        for i, case in enumerate(cases, 1):
            body_indices = [j for j, t in enumerate(case["triangles"]) if t[4] in header["surfaces"]]
            wake_indices = [j for j, t in enumerate(case["triangles"]) if t[4] == 0 and t[3] == 0]
            if not body_indices or len(body_indices) + len(wake_indices) != len(case["triangles"]):
                raise ValueError("ADB has unknown surface IDs or no physical surface")
            body = _subset(case, body_indices, "surface")
            c = case["condition"]
            token = f"{i:04d}_M{c['mach']:.6g}_A{c['alpha_deg']:.6g}_B{c['beta_deg']:.6g}".replace("-", "m").replace(".", "p")
            folder = output_dir / token
            folder.mkdir()
            surface = folder / "pressure.vtu"
            write_pressure_vtu(surface, body, bool(accepted))
            record = {"condition": c, "solver_accepted": bool(accepted),
                      "requested_condition": case.get("requested_condition"),
                      "mach_mapping": "zero_to_0.001" if case.get("requested_condition") and is_vspaero_zero_mach_regularization(case["requested_condition"]["mach"], c["mach"]) else "identity_or_unverified",
                      "status": "accepted_solver_point" if accepted else "diagnostic_not_accepted",
                      "file": str(surface.relative_to(output_dir)), "sha256": _sha(surface),
                      "nodes": len(body["points"]), "triangles": len(body["triangles"]),
                      "field_min": min(f[0] for f in body["fields"]),
                      "field_max": max(f[0] for f in body["fields"]),
                      "pressure_meaning": result["pressure_meaning"],
                      "qualification": "Solver gate only; not an aircraft ADH certificate",
                      "source_sha256": result["source_sha256"], "quality": quality or {}}
            if wake_indices:
                wake = folder / "wake.vtu"
                write_pressure_vtu(wake, _subset(case, wake_indices, "wake"), bool(accepted))
                record["wake"] = {"file": str(wake.relative_to(output_dir)), "sha256": _sha(wake),
                                  "triangles": len(wake_indices), "surface_id": 0, "physical_pressure": False}
            _json(folder / "point.json", record)
            result["points"].append(record)
        result["status"] = "exported"
    except (OSError, ValueError, struct.error) as exc:
        result["errors"].append(str(exc))
    _json(output_dir / "manifest.json", result)
    (output_dir / "README.md").write_text(
        "# ParaView: распределение давления\n\n"
        "Для каждой точки: File → Open → pressure.vtu → Apply. Выберите Cell Data / Cp_or_DeltaCp.\n"
        "Значения взяты из ADB без сглаживания. На тонких поверхностях это перепад ΔCp, "
        "на толстых — Cp. Это НЕ давление в Па. Полуэмпирические добавки сюда не распределяются.\n\n"
        "След (SurfaceID=ComponentID=0) отделён в wake.vtu, цвет Gamma; его нулевой Cp не является давлением на самолёте.\n\n"
        "Смотрите point.json: SolverAccepted=0 означает диагностический, непринятый расчёт. "
        "Успешный экспорт не подтверждает сходимость, физическую достоверность или сертификацию.\n"
        "Координаты сохранены в осях и единицах исходного решателя. VTK содержит все данные, "
        "исходный ADB для открытия не нужен. manifest.json перечисляет точки, ошибки и hashes.\n",
        encoding="utf-8")
    result["manifest"] = str((output_dir / "manifest.json").resolve())
    return result


def export_machline_pressure(body_path, output_dir, *, condition, quality, extras=()):
    """Package native VTK fields and provenance per MachLine point, without conversion."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    result = {"schema": "repairmach.pressure-export/1.0", "backend": "machline",
              "condition": condition, "quality": quality, "status": "failed", "files": [],
              "axes": "Native MachLine mesh axes; no implicit axis rotation", "errors": []}
    try:
        body_path = Path(body_path)
        if not body_path.is_file() or "SCALARS" not in body_path.read_text(encoding="utf-8", errors="replace"):
            raise ValueError("MachLine body VTK missing or contains no scalar fields")
        for source in (body_path, *extras):
            source = Path(source)
            if not source.is_file():
                continue
            target = output_dir / source.name
            shutil.copy2(source, target)
            result["files"].append({"file": target.name, "sha256": _sha(target), "source": str(source.resolve())})
        result["status"] = "exported"
    except (OSError, ValueError) as exc:
        result["errors"].append(str(exc))
    _json(output_dir / "point.json", result)
    result["manifest"] = str((output_dir / "point.json").resolve())
    return result


def refresh_review_pressure_exports(status_path):
    """Postprocess a completed native anchor review; numerical results stay intact."""
    status_path = Path(status_path).resolve()
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    if payload.get("schema") != "repairmach.vspaero-anchor-review/1.0" or payload.get("state") != "complete":
        raise ValueError("Only a completed native anchor review can be re-exported")
    exports = {}
    def visit(value):
        if isinstance(value, list):
            for child in value:
                visit(child)
        if not isinstance(value, dict):
            return
        if "log" in value and "requested_condition" in value and "numerical_controls" in value:
            folder = Path(value["log"]).resolve().parent
            if not folder.is_relative_to(status_path.parent):
                raise ValueError("Probe directory outside the selected review")
            key = str(folder)
            if key not in exports:
                target = folder / "paraview_surface"
                result = export_vspaero_pressure(
                    folder / "model.adb", target, accepted=value.get("valid") is True,
                    quality=value.get("output_quality"), mode=value.get("mode"),
                    expected_conditions=[value["requested_condition"]],
                )
                if result["status"] != "exported":
                    raise ValueError(str(result["errors"]))
                exports[key] = result
            value["pressure_export"] = exports[key]
        for name, child in list(value.items()):
            if name != "pressure_export":
                visit(child)
    visit(payload)
    backup = status_path.with_name("status_before_pressure_refresh.json")
    if backup.exists():
        raise FileExistsError(backup)
    shutil.copy2(status_path, backup)
    _json(status_path, payload)
    return {"reexported_probes": len(exports), "backup": str(backup)}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Re-export pressure from a completed native anchor review, without solving")
    parser.add_argument("--review", type=Path, required=True, help="Native status.json")
    args = parser.parse_args()
    print(json.dumps(refresh_review_pressure_exports(args.review), ensure_ascii=False))
