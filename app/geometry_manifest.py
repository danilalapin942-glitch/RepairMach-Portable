#!/usr/bin/env python3
"""OpenVSP inventory and immutable-manifest helpers."""

from __future__ import annotations

import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from geometry_rules import canonical_json_bytes
from openvsp_runner import run_vspscript


INVENTORY_SCHEMA = "repairmach.openvsp-inventory/1.0"
_FIELD_SEPARATOR = ";"
_INVENTORY_PARAMETER_NAMES = {
    "Root_Chord", "Tip_Chord", "Span", "Tess_W", "Tess_U", "SectTess_U",
    "X_Rel_Location", "Y_Rel_Location", "Z_Rel_Location",
    "X_Location", "Y_Location", "Z_Location",
    "X_Rel_Rotation", "Y_Rel_Rotation", "Z_Rel_Rotation",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_payload(payload: object) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def canonical_vsp3_sha256(path: Path) -> str:
    """Hash VSP3 content while normalizing OpenVSP transient Fixed_Group IDs.

    OpenVSP 3.51 regenerates IDs in the VSPAERO ``Fixed_Group`` container on
    every API write.  The raw SHA remains the tamper seal for each artifact;
    this canonical hash is the repeatable geometry identity/cache key.
    """
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return sha256_file(path)
    transient: dict[str, str] = {}
    containers = [element for element in root.iter() if element.tag.rsplit("}", 1)[-1] == "ParmContainer"]
    for container in containers:
        names = [
            child for child in container.iter()
            if child.tag.rsplit("}", 1)[-1] == "Name" and (child.text or "").strip() == "Fixed_Group"
        ]
        if not names:
            continue
        for element in container.iter():
            tag = element.tag.rsplit("}", 1)[-1]
            text = (element.text or "").strip()
            if text and (tag == "ID" or tag.endswith("ID")):
                transient.setdefault(text, f"RM_FIXED_GROUP_ID_{len(transient):03d}")
    if transient:
        for element in root.iter():
            text = (element.text or "").strip()
            if text in transient:
                element.text = transient[text]
            for key, value in list(element.attrib.items()):
                if value in transient:
                    element.attrib[key] = transient[value]
            if len(element.attrib) > 1:
                ordered = sorted(element.attrib.items())
                element.attrib.clear()
                element.attrib.update(ordered)
    return hashlib.sha256(ET.tostring(root, encoding="utf-8")).hexdigest()


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )


def _vsp_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace('"', '\\"')


def generate_inventory_script(script_path: Path, model_path: Path) -> None:
    """Generate a read-only inventory script.

    Parm IDs are enumerated by OpenVSP itself.  This avoids ambiguous XML
    tags: service XSecs and physical wing sections can use the same names.
    """
    if not model_path.is_file():
        raise FileNotFoundError(f"Не найден VSP3: {model_path}")
    script_path.parent.mkdir(parents=True, exist_ok=True)
    text = f'''void main()
{{
    ClearVSPModel();
    ReadVSPFile( "{_vsp_path(model_path)}" );
    Update();
    Print( "REPAIRMACH_INVENTORY_BEGIN" );
    array<string> geoms = FindGeoms();
    for ( int i = 0; i < int( geoms.size() ); ++i )
    {{
        string gid = geoms[i];
        vec3d mn = GetGeomBBoxMin( gid, 0, true );
        vec3d mx = GetGeomBBoxMax( gid, 0, true );
        Print( "GEOM;ID=" + gid + ";NAME=" + GetGeomName( gid ) + ";TYPE=" + GetGeomTypeName( gid ) + ";BMIN_X=" + mn.x() + ";BMIN_Y=" + mn.y() + ";BMIN_Z=" + mn.z() + ";BMAX_X=" + mx.x() + ";BMAX_Y=" + mx.y() + ";BMAX_Z=" + mx.z() + ";SET_1=" + GetSetFlag( gid, 4 ) + ";SET_2=" + GetSetFlag( gid, 5 ) );

        array<string> parms = GetGeomParmIDs( gid );
        for ( int pi = 0; pi < int( parms.size() ); ++pi )
        {{
            Print( "PARM;GEOM_ID=" + gid + ";ID=" + parms[pi] + ";NAME=" + GetParmName( parms[pi] ) + ";GROUP=" + GetParmGroupName( parms[pi] ) + ";VALUE=" + GetParmVal( parms[pi] ) );
        }}
        if ( GetGeomTypeName( gid ) == "Wing" && GetNumXSecSurfs( gid ) == 1 )
        {{
            string xs = GetXSecSurf( gid, 0 );
            int nx = GetNumXSec( xs );
            if ( nx >= 2 )
            {{
                string terminal = GetXSec( xs, nx - 1 );
                string tip = GetXSecParm( terminal, "Tip_Chord" );
                string root = GetXSecParm( terminal, "Root_Chord" );
                string span = GetXSecParm( terminal, "Span" );
                if ( ValidParm( tip ) && ValidParm( root ) && ValidParm( span ) )
                    Print( "WING_TERMINAL;GEOM_ID=" + gid + ";XSEC_COUNT=" + nx + ";TIP_ID=" + tip + ";TIP=" + GetParmVal( tip ) + ";ROOT_ID=" + root + ";ROOT=" + GetParmVal( root ) + ";SPAN_ID=" + span + ";SPAN=" + GetParmVal( span ) );
            }}
        }}
    }}
    Print( "REPAIRMACH_INVENTORY_END" );
}}
'''
    script_path.write_text(text, encoding="utf-8")


def _parse_fields(line: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for part in line.split(_FIELD_SEPARATOR):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        fields[key.strip()] = value.strip()
    return fields


def _finite_float(value: str, field: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"OpenVSP вернул нечисловое значение {field}: {value!r}")
    return result


def parse_inventory_log(log_text: str) -> dict:
    inside = False
    vsp_version = None
    sets: list[dict] = []
    components: list[dict] = []
    by_id: dict[str, dict] = {}
    errors: list[str] = []
    for raw in log_text.splitlines():
        line = raw.strip()
        if line == "REPAIRMACH_INVENTORY_BEGIN":
            inside = True
            continue
        if line == "REPAIRMACH_INVENTORY_END":
            inside = False
            continue
        if line.startswith("OPENVSP_ERROR="):
            errors.append(line.split("=", 1)[1])
            continue
        if not inside:
            continue
        if line.startswith("VSP_VERSION="):
            vsp_version = line.split("=", 1)[1]
        elif line.startswith("SET;"):
            item = _parse_fields(line)
            sets.append({
                "index": int(item["INDEX"]),
                "name": item.get("NAME", ""),
                "count": int(item["COUNT"]),
            })
        elif line.startswith("GEOM;"):
            item = _parse_fields(line)
            component = {
                "id": item["ID"],
                "name": item.get("NAME", ""),
                "type": item.get("TYPE", ""),
                "parent_id": None,
                "main_surfaces": None,
                "total_surfaces": None,
                "bbox": {
                    "min": [_finite_float(item[f"BMIN_{axis}"], f"BMIN_{axis}") for axis in "XYZ"],
                    "max": [_finite_float(item[f"BMAX_{axis}"], f"BMAX_{axis}") for axis in "XYZ"],
                },
                "sets": {
                    "1": item.get("SET_1", "false").lower() in {"1", "true"},
                    "2": item.get("SET_2", "false").lower() in {"1", "true"},
                },
                "parameters": [],
            }
            components.append(component)
            by_id[component["id"]] = component
        elif line.startswith("PARM;"):
            item = _parse_fields(line)
            geom_id = item["GEOM_ID"]
            if geom_id not in by_id:
                errors.append(f"Параметр {item.get('ID')} ссылается на неизвестную геометрию {geom_id}")
                continue
            if item.get("NAME") not in _INVENTORY_PARAMETER_NAMES:
                continue
            by_id[geom_id]["parameters"].append({
                "id": item["ID"],
                "name": item["NAME"],
                "group": item.get("GROUP", ""),
                "display_group": item.get("DISPLAY_GROUP", ""),
                "value": _finite_float(item["VALUE"], "VALUE"),
            })
        elif line.startswith("WING_TERMINAL;"):
            item = _parse_fields(line)
            geom_id = item["GEOM_ID"]
            if geom_id not in by_id:
                errors.append(f"Законцовка ссылается на неизвестную геометрию {geom_id}")
                continue
            by_id[geom_id]["wing_terminal"] = {
                "xsec_count": int(item["XSEC_COUNT"]),
                "tip_parm_id": item["TIP_ID"],
                "tip_chord": _finite_float(item["TIP"], "TIP"),
                "root_parm_id": item["ROOT_ID"],
                "root_chord": _finite_float(item["ROOT"], "ROOT"),
                "span_parm_id": item["SPAN_ID"],
                "span": _finite_float(item["SPAN"], "SPAN"),
            }

    if not components:
        errors.append("OpenVSP не вернул ни одного компонента")
    return {
        "schema": INVENTORY_SCHEMA,
        "openvsp_version": vsp_version,
        "sets": sets,
        "components": components,
        "errors": errors,
        "valid": not errors,
    }


def inventory_model(
    model_path: Path,
    *,
    vspscript_executable: Path,
    work_dir: Path,
    timeout_seconds: float = 180.0,
) -> tuple[dict, Path, Path]:
    script = work_dir / "inventory.vspscript"
    log = work_dir / "inventory.log"
    generate_inventory_script(script, model_path)
    code = run_vspscript(
        vspscript_executable,
        script,
        log,
        working_dir=work_dir,
        timeout_seconds=timeout_seconds,
    )
    log_text = log.read_text(encoding="utf-8", errors="replace")
    inventory = parse_inventory_log(log_text)
    if not inventory.get("openvsp_version"):
        match = re.search(r"(?i)OpenVSP[-_ ]([0-9]+(?:\.[0-9]+){1,3})", str(vspscript_executable))
        inventory["openvsp_version"] = match.group(1) if match else "bound_by_executable_sha256"
    inventory["vspscript_return_code"] = code
    inventory["source_path"] = str(model_path.resolve())
    inventory["source_sha256"] = sha256_file(model_path)
    inventory["source_canonical_sha256"] = canonical_vsp3_sha256(model_path)
    if code not in (0, 1):
        inventory["errors"].append(f"vspscript завершился с кодом {code}")
        inventory["valid"] = False
    if re.search(r"(?i)segmentation fault|access violation|fatal error", log_text):
        inventory["errors"].append("OpenVSP сообщил аварийное завершение")
        inventory["valid"] = False
    return inventory, script, log


def inventory_component_map(inventory: dict) -> dict[str, dict]:
    return {item["id"]: item for item in inventory.get("components", [])}


def find_parameters(component: dict, name: str) -> list[dict]:
    return [item for item in component.get("parameters", []) if item.get("name") == name]
