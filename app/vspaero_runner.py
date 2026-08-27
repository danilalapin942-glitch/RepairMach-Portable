#!/usr/bin/env python3
"""Automatic VSP3 import validation and VSPAERO sweep preparation."""

from __future__ import annotations

import math
import re
from pathlib import Path


OPENVSP_USER_SET_OFFSET = 3


def standard_vspaero_cases(mode: str) -> list[dict]:
    """Return the version-9 standard sweeps.

    Mach spacing is 0.1. The combined mode stays split into two analyses so
    that no automatic points are inserted in the 0.9--1.0 transonic gap.
    Alpha is always 0--5 degrees in one-degree increments.
    """
    key = mode.strip().lower()
    aliases = {
        "1": "subsonic",
        "subsonic": "subsonic",
        "дозвук": "subsonic",
        "2": "supersonic",
        "supersonic": "supersonic",
        "сверхзвук": "supersonic",
        "3": "combined",
        "combined": "combined",
        "all": "combined",
        "все": "combined",
        "всё": "combined",
    }
    selected = aliases.get(key)
    if selected is None:
        raise ValueError("Режим должен быть: 1 — дозвук, 2 — сверхзвук, 3 — всё вместе")
    presets = {
        "subsonic": {
            "name": "subsonic_M0p0_0p8",
            "mach_start": 0.0,
            "mach_end": 0.8,
            "mach_points": 9,
            "alpha_start": 0.0,
            "alpha_end": 5.0,
            "alpha_points": 6,
        },
        "supersonic": {
            "name": "supersonic_M1p1_2p2",
            "mach_start": 1.1,
            "mach_end": 2.2,
            "mach_points": 12,
            "alpha_start": 0.0,
            "alpha_end": 5.0,
            "alpha_points": 6,
        },
    }
    if selected == "combined":
        return [dict(presets["subsonic"]), dict(presets["supersonic"])]
    return [dict(presets[selected])]


def user_set_to_api_index(user_set_number: int) -> int:
    """Translate UI ``Set_N`` into OpenVSP's API index.

    API indices 0, 1 and 2 are reserved for All, Shown and Not_Shown.
    Therefore UI Set_1 is API index 4 and UI Set_2 is API index 5.
    """
    if user_set_number < 0:
        raise ValueError("Номер пользовательского Set не может быть отрицательным")
    return OPENVSP_USER_SET_OFFSET + int(user_set_number)


def _vsp_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace('"', '\\"')


def _vsp_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _check_range(start: float, end: float, points: int, label: str) -> None:
    if points < 1:
        raise ValueError(f"Число точек {label} должно быть не меньше 1")
    if points == 1 and abs(end - start) > 1.0e-12:
        raise ValueError(f"Для одной точки {label} начало и конец должны совпадать")


def generate_vspaero_sweep_script(
    script_path: Path,
    vsp3_path: Path,
    results_csv_path: Path,
    *,
    mach_start: float,
    mach_end: float,
    mach_points: int,
    alpha_start: float,
    alpha_end: float,
    alpha_points: int,
    beta_deg: float,
    reference_area: float,
    reference_chord: float,
    reference_span: float,
    center: tuple[float, float, float] | list[float],
    fuselage_user_set: int = 1,
    wing_user_set: int = 2,
    ncpu: int = 4,
    tail_geometry_name: str | None = None,
    tail_incidence_deg: float = 0.0,
    require_canonical_components: bool = False,
) -> None:
    """Generate a self-validating mixed thick/thin VSPAERO sweep script."""
    if not vsp3_path.is_file():
        raise FileNotFoundError(f"Не найден VSP3: {vsp3_path}")
    _check_range(mach_start, mach_end, mach_points, "Mach")
    _check_range(alpha_start, alpha_end, alpha_points, "alpha")
    if min(reference_area, reference_chord, reference_span) <= 0.0:
        raise ValueError("Sref, cref и bref должны быть положительными")
    if len(center) != 3:
        raise ValueError("Центр масс должен содержать X, Y, Z")
    if ncpu < 1:
        raise ValueError("Число потоков VSPAERO должно быть не меньше 1")
    if fuselage_user_set == wing_user_set:
        raise ValueError("Наборы фюзеляжа и крыла должны различаться")
    if tail_geometry_name is not None and not tail_geometry_name.strip():
        raise ValueError("Имя геометрии горизонтального оперения пустое")
    if not math.isfinite(float(tail_incidence_deg)):
        raise ValueError("Угол горизонтального оперения должен быть конечным числом")

    thick_set = user_set_to_api_index(fuselage_user_set)
    thin_set = user_set_to_api_index(wing_user_set)
    script_path.parent.mkdir(parents=True, exist_ok=True)
    results_csv_path.parent.mkdir(parents=True, exist_ok=True)

    if tail_geometry_name is None:
        tail_validation = '''
    Print( "TAIL;MODE=model_default" );
'''
        tail_apply = ""
    else:
        tail_name = _vsp_string(tail_geometry_name.strip())
        tail_validation = f'''
    string tail_id = "";
    array<string> tail_candidates = FindGeomsWithName( "{tail_name}" );
    if ( tail_candidates.size() != 1 )
    {{
        Print( "ERROR=Expected exactly one horizontal-tail geometry named {tail_name};COUNT=" + tail_candidates.size() );
        invalid_sets = true;
    }}
    else
    {{
        tail_id = tail_candidates[0];
        Print( "TAIL;MODE=fixed_incidence;NAME=" + GetGeomName( tail_id ) + ";ID=" + tail_id + ";TYPE=" + GetGeomTypeName( tail_id ) + ";ANGLE_DEG={float(tail_incidence_deg):.17g}" );
        if ( !GetSetFlag( tail_id, thin_set ) )
        {{
            Print( "ERROR=Horizontal tail is not assigned to Set_{wing_user_set};ID=" + tail_id + ";NAME=" + GetGeomName( tail_id ) );
            invalid_sets = true;
        }}
        if ( GetGeomTypeName( tail_id ) != "Wing" )
        {{
            Print( "ERROR=Horizontal-tail geometry must have Wing type;ID=" + tail_id + ";TYPE=" + GetGeomTypeName( tail_id ) );
            invalid_sets = true;
        }}
    }}
'''
        tail_apply = f'''
    SetParmVal( tail_id, "Y_Rel_Rotation", "XForm", {float(tail_incidence_deg):.17g} );
    Update();
    WriteVSPFile( "{_vsp_path(vsp3_path)}", 0 );
    Print( "REPAIRMACH_TAIL_INCIDENCE_APPLIED={float(tail_incidence_deg):.17g}" );
'''

    canonical_validation = ""
    if require_canonical_components:
        canonical_validation = f'''
    array<string> required_fuselage = FindGeomsWithName( "Fuselage" );
    array<string> required_wing = FindGeomsWithName( "Wing" );
    Print( "COMPONENT_NAME;BASE=Fuselage;COUNT=" + required_fuselage.size() );
    Print( "COMPONENT_NAME;BASE=Wing;COUNT=" + required_wing.size() );
    if ( required_fuselage.size() != 1 )
    {{
        Print( "ERROR=Expected exactly one primary geometry named Fuselage;COUNT=" + required_fuselage.size() );
        invalid_sets = true;
    }}
    else if ( !GetSetFlag( required_fuselage[0], thick_set ) )
    {{
        Print( "ERROR=Fuselage must be assigned to Set_{fuselage_user_set}" );
        invalid_sets = true;
    }}
    if ( required_wing.size() != 1 )
    {{
        Print( "ERROR=Expected exactly one primary geometry named Wing;COUNT=" + required_wing.size() );
        invalid_sets = true;
    }}
    else if ( !GetSetFlag( required_wing[0], thin_set ) )
    {{
        Print( "ERROR=Wing must be assigned to Set_{wing_user_set}" );
        invalid_sets = true;
    }}
'''

    text = f'''void main()
{{
    ClearVSPModel();
    ReadVSPFile( "{_vsp_path(vsp3_path)}" );
    Update();

    int thick_set = {thick_set};
    int thin_set = {thin_set};
    bool invalid_sets = false;
    array<string> all_geoms = GetGeomSetAtIndex( 0 );
    array<string> thick_geoms = GetGeomSetAtIndex( thick_set );
    array<string> thin_geoms = GetGeomSetAtIndex( thin_set );

    Print( "REPAIRMACH_SET_REPORT_BEGIN" );
    Print( "ROLE=fuselage_and_nacelles;USER_SET={fuselage_user_set};API_INDEX=" + thick_set + ";NAME=" + GetSetName( thick_set ) + ";COUNT=" + thick_geoms.size() );
    Print( "ROLE=wing_and_empennage;USER_SET={wing_user_set};API_INDEX=" + thin_set + ";NAME=" + GetSetName( thin_set ) + ";COUNT=" + thin_geoms.size() );

    if ( thick_geoms.size() == 0 )
    {{
        Print( "ERROR=Set_{fuselage_user_set} is empty" );
        invalid_sets = true;
    }}
    if ( thin_geoms.size() == 0 )
    {{
        Print( "ERROR=Set_{wing_user_set} is empty" );
        invalid_sets = true;
    }}
{canonical_validation}

    for ( int i = 0; i < int( all_geoms.size() ); ++i )
    {{
        string gid = all_geoms[i];
        string name = GetGeomName( gid );
        string geom_type = GetGeomTypeName( gid );
        bool in_thick = GetSetFlag( gid, thick_set );
        bool in_thin = GetSetFlag( gid, thin_set );
        Print( "GEOM;ID=" + gid + ";NAME=" + name + ";TYPE=" + geom_type + ";IN_SET_{fuselage_user_set}=" + in_thick + ";IN_SET_{wing_user_set}=" + in_thin );
        if ( in_thick && in_thin )
        {{
            Print( "ERROR=Geometry belongs to both sets;ID=" + gid + ";NAME=" + name );
            invalid_sets = true;
        }}
        if ( !in_thick && !in_thin )
        {{
            Print( "ERROR=Geometry is not assigned to Set_{fuselage_user_set} or Set_{wing_user_set};ID=" + gid + ";NAME=" + name );
            invalid_sets = true;
        }}
        if ( in_thick && geom_type == "Wing" )
        {{
            Print( "ERROR=Wing geometry is assigned to fuselage Set_{fuselage_user_set};ID=" + gid + ";NAME=" + name );
            invalid_sets = true;
        }}
        if ( in_thin && geom_type != "Wing" )
        {{
            Print( "ERROR=Non-wing geometry is assigned to wing Set_{wing_user_set};ID=" + gid + ";NAME=" + name + ";TYPE=" + geom_type );
            invalid_sets = true;
        }}
    }}
{tail_validation}
    Print( "REPAIRMACH_SET_REPORT_END" );

    if ( invalid_sets )
    {{
        Print( "REPAIRMACH_ABORTED_SET_VALIDATION=1" );
        return;
    }}
{tail_apply}
    Print( "REPAIRMACH_SET_VALIDATION=OK" );

    string compute_name = "VSPAEROComputeGeometry";
    SetAnalysisInputDefaults( compute_name );
    array<int> thick_input(1, thick_set);
    array<int> thin_input(1, thin_set);
    SetIntAnalysisInput( compute_name, "GeomSet", thick_input );
    SetIntAnalysisInput( compute_name, "ThinGeomSet", thin_input );
    string compute_result = ExecAnalysis( compute_name );
    Print( "REPAIRMACH_COMPUTE_RESULT_ID=" + compute_result );

    string sweep_name = "VSPAEROSweep";
    SetAnalysisInputDefaults( sweep_name );
    SetIntAnalysisInput( sweep_name, "GeomSet", thick_input );
    SetIntAnalysisInput( sweep_name, "ThinGeomSet", thin_input );

    array<int> ref_flag(1, 0);
    SetIntAnalysisInput( sweep_name, "RefFlag", ref_flag );
    array<double> sref(1, {reference_area:.17g});
    array<double> cref(1, {reference_chord:.17g});
    array<double> bref(1, {reference_span:.17g});
    SetDoubleAnalysisInput( sweep_name, "Sref", sref );
    SetDoubleAnalysisInput( sweep_name, "cref", cref );
    SetDoubleAnalysisInput( sweep_name, "bref", bref );

    array<double> xcg(1, {float(center[0]):.17g});
    array<double> ycg(1, {float(center[1]):.17g});
    array<double> zcg(1, {float(center[2]):.17g});
    SetDoubleAnalysisInput( sweep_name, "Xcg", xcg );
    SetDoubleAnalysisInput( sweep_name, "Ycg", ycg );
    SetDoubleAnalysisInput( sweep_name, "Zcg", zcg );

    array<double> mach_start(1, {mach_start:.17g});
    array<double> mach_end(1, {mach_end:.17g});
    array<int> mach_points(1, {int(mach_points)});
    SetDoubleAnalysisInput( sweep_name, "MachStart", mach_start );
    SetDoubleAnalysisInput( sweep_name, "MachEnd", mach_end );
    SetIntAnalysisInput( sweep_name, "MachNpts", mach_points );

    array<double> alpha_start(1, {alpha_start:.17g});
    array<double> alpha_end(1, {alpha_end:.17g});
    array<int> alpha_points(1, {int(alpha_points)});
    SetDoubleAnalysisInput( sweep_name, "AlphaStart", alpha_start );
    SetDoubleAnalysisInput( sweep_name, "AlphaEnd", alpha_end );
    SetIntAnalysisInput( sweep_name, "AlphaNpts", alpha_points );

    array<double> beta(1, {beta_deg:.17g});
    array<int> beta_points(1, 1);
    SetDoubleAnalysisInput( sweep_name, "BetaStart", beta );
    SetDoubleAnalysisInput( sweep_name, "BetaEnd", beta );
    SetIntAnalysisInput( sweep_name, "BetaNpts", beta_points );
    array<int> cpu_count(1, {int(ncpu)});
    SetIntAnalysisInput( sweep_name, "NCPU", cpu_count );

    string sweep_result = ExecAnalysis( sweep_name );
    Print( "REPAIRMACH_SWEEP_RESULT_ID=" + sweep_result );
    WriteResultsCSVFile( sweep_result, "{_vsp_path(results_csv_path)}" );
    Print( "REPAIRMACH_VSPAERO_COMPLETE=1" );

    while ( GetNumTotalErrors() > 0 )
    {{
        ErrorObj err = PopLastError();
        Print( "OPENVSP_ERROR=" + err.GetErrorString() );
    }}
}}
'''
    script_path.write_text(text, encoding="utf-8")


def parse_set_report(log_text: str) -> dict:
    """Parse the machine-readable set inventory printed by the generated script."""
    roles: dict[str, dict] = {}
    geometries: list[dict] = []
    errors: list[str] = []
    tail: dict = {}
    inside = False
    for raw_line in log_text.splitlines():
        line = raw_line.strip()
        if line == "REPAIRMACH_SET_REPORT_BEGIN":
            inside = True
            continue
        if line == "REPAIRMACH_SET_REPORT_END":
            inside = False
            continue
        if not inside:
            continue
        if line.startswith("ROLE="):
            fields = _parse_fields(line)
            role = fields.pop("ROLE")
            for key in ("USER_SET", "API_INDEX", "COUNT"):
                if key in fields:
                    fields[key] = int(fields[key])
            roles[role] = fields
        elif line.startswith("GEOM;"):
            fields = _parse_fields(line)
            for key in tuple(fields):
                if key.startswith("IN_SET_"):
                    fields[key] = fields[key].lower() in ("1", "true")
            geometries.append(fields)
        elif line.startswith("TAIL;"):
            tail = _parse_fields(line)
            if "ANGLE_DEG" in tail:
                tail["ANGLE_DEG"] = float(tail["ANGLE_DEG"])
        elif line.startswith("ERROR="):
            errors.append(line.removeprefix("ERROR="))
    return {
        "roles": roles,
        "geometries": geometries,
        "errors": errors,
        "tail": tail,
        "valid": not errors and "REPAIRMACH_SET_VALIDATION=OK" in log_text,
        "calculation_complete": "REPAIRMACH_VSPAERO_COMPLETE=1" in log_text,
    }


def _parse_fields(line: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for part in line.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        fields[key.strip()] = value.strip()
    return fields


def find_generated_polar(run_dir: Path, model_stem: str) -> Path | None:
    expected = run_dir / f"{model_stem}.polar"
    if expected.is_file():
        return expected
    matches = sorted(run_dir.glob("*.polar"), key=lambda item: item.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def safe_vsp3_name(name: str) -> str:
    """Return a filesystem-friendly VSP3 stem while keeping Cyrillic supported."""
    stem = Path(name).stem.strip()
    stem = re.sub(r'[<>:"/\\|?*]+', "_", stem)
    return stem or "imported_model"
