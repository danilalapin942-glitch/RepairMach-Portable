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
    that no automatic points are inserted in the 0.9--1.1 transonic gap.
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
            "name": "supersonic_M1p2_2p2",
            "mach_start": 1.2,
            "mach_end": 2.2,
            "mach_points": 11,
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


def _wake_iteration_guard_script(wake_num_iter: int) -> str:
    """Check the loaded OpenVSP backend before it can silently clamp the request.

    Analysis-input readback is insufficient: in OpenVSP 3.51 the input may
    retain 320 while VSPAEROMgr's WakeNumIter Parm limits the solver to 255.
    Query the backend limits, not a version-independent hard-coded maximum.
    This changes only an in-memory solver setting, never the source VSP3.
    """
    if isinstance(wake_num_iter, bool) or not isinstance(wake_num_iter, int) or wake_num_iter < 1:
        raise ValueError("Число итераций следа должно быть положительным целым")
    return f'''
    string wake_settings = FindContainer( "VSPAEROSettings", 0 );
    string wake_iter_parm = FindParm( wake_settings, "WakeNumIter", "VSPAERO" );
    if ( !ValidParm( wake_iter_parm ) )
    {{
        Print( "ERROR=WakeNumIter backend parameter is unavailable; refusing unverified controls" );
        Print( "REPAIRMACH_ABORTED_NUMERICAL_CONTROLS=1" );
        return;
    }}
    double wake_iter_min = GetParmLowerLimit( wake_iter_parm );
    double wake_iter_max = GetParmUpperLimit( wake_iter_parm );
    Print( "REPAIRMACH_WAKE_ITER_LIMITS;REQUESTED={wake_num_iter};MIN=" + wake_iter_min + ";MAX=" + wake_iter_max );
    if ( !({wake_num_iter} >= wake_iter_min && {wake_num_iter} <= wake_iter_max) )
    {{
        Print( "ERROR=Requested WakeNumIter={wake_num_iter} exceeds the loaded OpenVSP backend limits; no automatic clamping" );
        Print( "REPAIRMACH_ABORTED_NUMERICAL_CONTROLS=1" );
        return;
    }}
    SetParmVal( wake_iter_parm, {wake_num_iter} );
    double actual_wake_iter = GetParmVal( wake_iter_parm );
    if ( actual_wake_iter != {wake_num_iter} )
    {{
        Print( "ERROR=WakeNumIter backend readback does not match the request" );
        Print( "REPAIRMACH_ABORTED_NUMERICAL_CONTROLS=1" );
        return;
    }}
    Print( "REPAIRMACH_WAKE_ITER_CONFIRMED;REQUESTED={wake_num_iter};ACTUAL=" + actual_wake_iter );
'''


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
    forward_gmres_convergence_factor: float = 1.0,
    wake_num_iter: int = 8,
    num_wake_nodes: int = 24,
    wake_relax: float = 0.8,
    implicit_wake: bool | None = None,
    implicit_wake_start_iter: int = 8,
    tail_geometry_name: str | None = None,
    tail_incidence_deg: float = 0.0,
    engine_boundary: str | None = None,
    engine_geometry_aliases: list[str] | None = None,
    require_canonical_components: bool = False,
    require_nonempty_fuselage_set: bool = True,
    require_all_geometries_assigned: bool = True,
    diagnostic_excluded_components: list[str] | None = None,
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
    if forward_gmres_convergence_factor <= 0.0:
        raise ValueError("Коэффициент сходимости GMRES должен быть положительным")
    wake_iteration_guard = _wake_iteration_guard_script(wake_num_iter)
    if num_wake_nodes < 2:
        raise ValueError("Число узлов следа должно быть не меньше 2")
    if not 0.0 < wake_relax <= 1.0:
        raise ValueError("Коэффициент релаксации следа должен лежать в (0; 1]")
    if implicit_wake is not None and not isinstance(implicit_wake, bool):
        raise ValueError("implicit_wake должен быть bool или None")
    if (isinstance(implicit_wake_start_iter, bool)
            or not isinstance(implicit_wake_start_iter, int)
            or implicit_wake_start_iter < 0
            or (implicit_wake is True and implicit_wake_start_iter >= wake_num_iter)):
        raise ValueError("Начало неявного следа должно быть целым >= 0 и раньше конца итераций")
    if fuselage_user_set == wing_user_set:
        raise ValueError("Наборы фюзеляжа и крыла должны различаться")
    if tail_geometry_name is not None and not tail_geometry_name.strip():
        raise ValueError("Имя геометрии горизонтального оперения пустое")
    if not math.isfinite(float(tail_incidence_deg)):
        raise ValueError("Угол горизонтального оперения должен быть конечным числом")
    if engine_boundary not in {None, "model", "to_face"}:
        raise ValueError("engine_boundary должен быть model, to_face или None")
    exclusions = diagnostic_excluded_components
    if exclusions is None:
        exclusions = []
    if (not isinstance(exclusions, list)
            or len(exclusions) > 1
            or any(name not in ("Fuselage", "Gondola") for name in exclusions)):
        raise ValueError("Diagnostic exclusion supports exactly one Fuselage or Gondola body")
    if exclusions and (engine_boundary == "to_face"
                       or not require_all_geometries_assigned
                       or not require_nonempty_fuselage_set
                       or not require_canonical_components):
        raise ValueError("Diagnostic exclusion requires strict mixed validation and model engine boundary")
    exclusion_match = 'false' if not exclusions else f'name == "{exclusions[0]}"'
    diagnostic_validation = ""
    if exclusions:
        excluded = exclusions[0]
        diagnostic_validation = f'''
    array<string> excluded_bodies = FindGeomsWithName( "{excluded}" );
    if ( excluded_bodies.size() != 1 )
    {{
        Print( "ERROR=Diagnostic excluded body must exist exactly once;NAME={excluded}" );
        invalid_sets = true;
    }}
    else if ( GetGeomTypeName( excluded_bodies[0] ) != "Fuselage" || GetSetFlag( excluded_bodies[0], thick_set ) || GetSetFlag( excluded_bodies[0], thin_set ) )
    {{
        Print( "ERROR=Diagnostic excluded body must be Fuselage type and absent from both analysis sets;NAME={excluded}" );
        invalid_sets = true;
    }}
    else
    {{
        Print( "REPAIRMACH_DIAGNOSTIC_EXCLUSION;NAME={excluded};CONFIRMED=1;QUALIFICATION_ALLOWED=0" );
    }}
'''
    engine_geometry_aliases = list(engine_geometry_aliases or [])
    if (
        any(not isinstance(name, str) or not name.strip() or name != name.strip()
            for name in engine_geometry_aliases)
        or len(engine_geometry_aliases) != len(set(engine_geometry_aliases))
    ):
        raise ValueError("Имена alias мотогондол должны быть уникальными непустыми строками")

    thick_set = user_set_to_api_index(fuselage_user_set)
    thin_set = user_set_to_api_index(wing_user_set)
    script_path.parent.mkdir(parents=True, exist_ok=True)
    results_csv_path.parent.mkdir(parents=True, exist_ok=True)

    # Optional: preserve the original model/default when this is not requested.
    # This changes the iteration algorithm, not geometry or acceptance limits.
    implicit_setup = ""
    if implicit_wake is not None:
        implicit_setup = f'''
    array<int> implicit_wake_flag(1, {int(implicit_wake)});
    array<int> implicit_wake_start(1, {implicit_wake_start_iter});
    SetIntAnalysisInput( sweep_name, "ImplicitWake", implicit_wake_flag );
    SetIntAnalysisInput( sweep_name, "ImplicitWakeStartIteration", implicit_wake_start );
    array<int> actual_implicit = GetIntAnalysisInput( sweep_name, "ImplicitWake" );
    array<int> actual_implicit_start = GetIntAnalysisInput( sweep_name, "ImplicitWakeStartIteration" );
    if ( actual_implicit.size() != 1 || actual_implicit_start.size() != 1 )
    {{
        Print( "ERROR=Implicit wake analysis inputs are unavailable" );
        return;
    }}
    if ( actual_implicit[0] != {int(implicit_wake)} || actual_implicit_start[0] != {implicit_wake_start_iter} )
    {{
        Print( "ERROR=Implicit wake analysis inputs did not match the request" );
        return;
    }}
    Print( "REPAIRMACH_IMPLICIT_WAKE=" + actual_implicit[0] + ";START_ITER=" + actual_implicit_start[0] );
'''

    if tail_geometry_name is None:
        tail_validation = '''
    Print( "TAIL;MODE=model_default" );
'''
        tail_apply = '''
    Print( "REPAIRMACH_TAIL_SETUP;MODE=model_default;VALID=true" );
'''
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
    string tail_rotation_parm = FindParm( tail_id, "Y_Rel_Rotation", "XForm" );
    if ( !ValidParm( tail_rotation_parm ) )
    {{
        Print( "ERROR=Horizontal-tail incidence parameter is missing;ID=" + tail_id );
        Print( "REPAIRMACH_TAIL_SETUP;MODE=fixed_incidence;NAME=" + GetGeomName( tail_id ) + ";ID=" + tail_id + ";REQUESTED_ANGLE_DEG={float(tail_incidence_deg):.17g};VALID=false" );
        invalid_setup = true;
    }}
    else
    {{
        SetParmVal( tail_rotation_parm, {float(tail_incidence_deg):.17g} );
        Update();
        double actual_tail_incidence = GetParmVal( tail_rotation_parm );
        bool tail_applied = actual_tail_incidence == actual_tail_incidence && abs( actual_tail_incidence - {float(tail_incidence_deg):.17g} ) <= 1.0e-9;
        Print( "REPAIRMACH_TAIL_SETUP;MODE=fixed_incidence;NAME=" + GetGeomName( tail_id ) + ";ID=" + tail_id + ";PARM_ID=" + tail_rotation_parm + ";REQUESTED_ANGLE_DEG={float(tail_incidence_deg):.17g};ACTUAL_ANGLE_DEG=" + actual_tail_incidence + ";VALID=" + tail_applied );
        if ( !tail_applied )
        {{
            Print( "ERROR=Horizontal-tail incidence was not applied;ID=" + tail_id );
            invalid_setup = true;
        }}
        else
        {{
            Print( "REPAIRMACH_TAIL_INCIDENCE_APPLIED=" + actual_tail_incidence );
        }}
    }}
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
    else if ( {str('Fuselage' not in exclusions).lower()} && !GetSetFlag( required_fuselage[0], thick_set ) )
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

    if engine_boundary in {None, "model"}:
        engine_apply = '''
    Print( "REPAIRMACH_ENGINE_BOUNDARY=model" );
    Print( "REPAIRMACH_ENGINE_SETUP;MODE=model;VALID=true" );
'''
    else:
        engine_name_match = (
            'gondola_name == "Gondola" || '
            '( gondola_name.length() >= 8 && gondola_name.substr( 0, 8 ) == "Gondola_" )'
        )
        for alias in engine_geometry_aliases:
            engine_name_match += f' || gondola_name == "{_vsp_string(alias)}"'
        engine_apply = '''
    array<string> all_engine_geoms = FindGeoms();
    int engine_expected_count = 0;
    int engine_applied_count = 0;
    for ( int engine_index = 0; engine_index < int( all_engine_geoms.size() ); ++engine_index )
    {
        string gondola_id = all_engine_geoms[engine_index];
        string gondola_name = GetGeomName( gondola_id );
        bool is_gondola = __ENGINE_NAME_MATCH__;
        if ( !is_gondola )
        {
            continue;
        }
        engine_expected_count += 1;
        string geom_in_parm = FindParm( gondola_id, "GeomInType", "EngineModel" );
        string geom_out_parm = FindParm( gondola_id, "GeomOutType", "EngineModel" );
        string inlet_mode_parm = FindParm( gondola_id, "InletModeType", "EngineModel" );
        string outlet_mode_parm = FindParm( gondola_id, "OutletModeType", "EngineModel" );
        bool engine_parms_valid = ValidParm( geom_in_parm ) && ValidParm( geom_out_parm ) && ValidParm( inlet_mode_parm ) && ValidParm( outlet_mode_parm );
        if ( !engine_parms_valid )
        {
            Print( "ERROR=Gondola EngineModel parameters required for to_face are missing;ID=" + gondola_id );
            Print( "REPAIRMACH_ENGINE_COMPONENT;MODE=to_face;NAME=" + gondola_name + ";ID=" + gondola_id + ";VALID=false" );
            invalid_setup = true;
            continue;
        }

        SetParmVal( geom_in_parm, 3 );
        SetParmVal( geom_out_parm, 3 );
        SetParmVal( inlet_mode_parm, 4 );
        SetParmVal( outlet_mode_parm, 4 );
        Update();
        double actual_geom_in = GetParmVal( geom_in_parm );
        double actual_geom_out = GetParmVal( geom_out_parm );
        double actual_inlet_mode = GetParmVal( inlet_mode_parm );
        double actual_outlet_mode = GetParmVal( outlet_mode_parm );
        bool engine_applied = actual_geom_in == actual_geom_in && actual_geom_out == actual_geom_out && actual_inlet_mode == actual_inlet_mode && actual_outlet_mode == actual_outlet_mode && abs( actual_geom_in - 3.0 ) <= 1.0e-9 && abs( actual_geom_out - 3.0 ) <= 1.0e-9 && abs( actual_inlet_mode - 4.0 ) <= 1.0e-9 && abs( actual_outlet_mode - 4.0 ) <= 1.0e-9;
        Print( "REPAIRMACH_ENGINE_COMPONENT;MODE=to_face;NAME=" + gondola_name + ";ID=" + gondola_id + ";GEOM_IN_TYPE=" + actual_geom_in + ";GEOM_OUT_TYPE=" + actual_geom_out + ";INLET_MODE_TYPE=" + actual_inlet_mode + ";OUTLET_MODE_TYPE=" + actual_outlet_mode + ";VALID=" + engine_applied );
        if ( !engine_applied )
        {
            Print( "ERROR=Gondola to_face boundary parameters were not applied;ID=" + gondola_id );
            invalid_setup = true;
        }
        else
        {
            engine_applied_count += 1;
            Print( "REPAIRMACH_ENGINE_BOUNDARY_APPLIED=to_face;NAME=" + gondola_name + ";ID=" + gondola_id );
        }
    }
    bool engine_setup_valid = engine_expected_count > 0 && engine_applied_count == engine_expected_count && !invalid_setup;
    Print( "REPAIRMACH_ENGINE_SETUP;MODE=to_face;EXPECTED_COUNT=" + engine_expected_count + ";APPLIED_COUNT=" + engine_applied_count + ";VALID=" + engine_setup_valid );
    if ( !engine_setup_valid )
    {
        Print( "ERROR=To-Face requires every Gondola/Gondola_suffix component;EXPECTED_COUNT=" + engine_expected_count + ";APPLIED_COUNT=" + engine_applied_count );
        invalid_setup = true;
    }
'''
        engine_apply = engine_apply.replace("__ENGINE_NAME_MATCH__", engine_name_match)

    setup_write = ""
    if tail_geometry_name is not None or engine_boundary == "to_face":
        setup_write = f'''
    WriteVSPFile( "{_vsp_path(vsp3_path)}", 0 );
    int setup_write_error_count = GetNumTotalErrors();
    while ( GetNumTotalErrors() > 0 )
    {{
        ErrorObj err = PopLastError();
        Print( "OPENVSP_ERROR=" + err.GetErrorString() );
    }}
    if ( setup_write_error_count > 0 )
    {{
        Print( "REPAIRMACH_ABORTED_SETUP_WRITE_ERRORS=1" );
        return;
    }}
'''

    if require_nonempty_fuselage_set:
        thick_set_validation = f'''
    if ( thick_geoms.size() == 0 )
    {{
        Print( "ERROR=Set_{fuselage_user_set} is empty" );
        invalid_sets = true;
    }}
'''
    else:
        thick_set_validation = '''
    if ( thick_geoms.size() == 0 )
    {
        Print( "DIAGNOSTIC=Empty thick-geometry set accepted" );
    }
'''

    if require_all_geometries_assigned:
        unassigned_validation = f'''
        if ( !in_thick && !in_thin && !({exclusion_match}) )
        {{
            Print( "ERROR=Geometry is not assigned to Set_{fuselage_user_set} or Set_{wing_user_set};ID=" + gid + ";NAME=" + name );
            invalid_sets = true;
        }}
'''
    else:
        unassigned_validation = ""

    native_guard = Path(__file__).with_name("vspgeom_guard.vspscript").read_text(encoding="utf-8")
    text = native_guard + f'''\nvoid main()
{{
    Print( "REPAIRMACH_NATIVE_GUARD_REQUIRED=1" );
    ClearVSPModel();
    ReadVSPFile( "{_vsp_path(vsp3_path)}" );
    Update();
    bool invalid_setup = false;
{engine_apply}

    int thick_set = {thick_set};
    int thin_set = {thin_set};
    bool invalid_sets = false;
    array<string> all_geoms = GetGeomSetAtIndex( 0 );
    array<string> thick_geoms = GetGeomSetAtIndex( thick_set );
    array<string> thin_geoms = GetGeomSetAtIndex( thin_set );

    Print( "REPAIRMACH_SET_REPORT_BEGIN" );
    Print( "ROLE=fuselage_and_nacelles;USER_SET={fuselage_user_set};API_INDEX=" + thick_set + ";NAME=" + GetSetName( thick_set ) + ";COUNT=" + thick_geoms.size() );
    Print( "ROLE=wing_and_empennage;USER_SET={wing_user_set};API_INDEX=" + thin_set + ";NAME=" + GetSetName( thin_set ) + ";COUNT=" + thin_geoms.size() );

{thick_set_validation}
    if ( thin_geoms.size() == 0 )
    {{
        Print( "ERROR=Set_{wing_user_set} is empty" );
        invalid_sets = true;
    }}
{canonical_validation}
{diagnostic_validation}

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
{unassigned_validation}
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
    int setup_error_count = GetNumTotalErrors();
    while ( GetNumTotalErrors() > 0 )
    {{
        ErrorObj err = PopLastError();
        Print( "OPENVSP_ERROR=" + err.GetErrorString() );
    }}
    if ( invalid_setup || setup_error_count > 0 )
    {{
        Print( "REPAIRMACH_ABORTED_SETUP_VALIDATION=1" );
        return;
    }}
{setup_write}
    Print( "REPAIRMACH_SETUP_VALIDATION=OK" );
    Print( "REPAIRMACH_SET_VALIDATION=OK" );

{wake_iteration_guard}

    string compute_name = "VSPAEROComputeGeometry";
    SetAnalysisInputDefaults( compute_name );
    array<int> thick_input(1, thick_set);
    array<int> thin_input(1, thin_set);
    SetIntAnalysisInput( compute_name, "GeomSet", thick_input );
    SetIntAnalysisInput( compute_name, "ThinGeomSet", thin_input );
    string compute_result = ExecAnalysis( compute_name );
    Print( "REPAIRMACH_COMPUTE_RESULT_ID=" + compute_result );

    array<string> native_files = GetStringResults( compute_result, "VSPGeomFileName" );
    array<string> native_mesh_ids = GetStringResults( compute_result, "Mesh_GeomID" );
    bool native_ok = compute_result.length() > 0 && native_files.length() == 1 && native_mesh_ids.length() == 1;
    if ( native_ok ) native_ok = native_mesh_ids[0].length() > 0 && native_files[0] == "{_vsp_path(vsp3_path.with_suffix('.vspgeom'))}";
    if ( GetNumTotalErrors() > 0 ) native_ok = false;
    if ( native_ok ) native_ok = RMNative( native_files[0], "{_vsp_path(vsp3_path.with_suffix('.vkey'))}", thick_geoms, thin_geoms );
    if ( !native_ok )
    {{
        Print( "REPAIRMACH_ABORTED_NATIVE_MESH=1" );
        return;
    }}
    Print( "REPAIRMACH_NATIVE_MESH_VALIDATION=OK" );

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
    array<double> gmres_factor(1, {float(forward_gmres_convergence_factor):.17g});
    array<int> wake_iterations(1, {int(wake_num_iter)});
    array<int> wake_nodes(1, {int(num_wake_nodes)});
    array<double> wake_relaxation(1, {float(wake_relax):.17g});
    SetDoubleAnalysisInput( sweep_name, "ForwardGMRESConvergenceFactor", gmres_factor );
    SetIntAnalysisInput( sweep_name, "WakeNumIter", wake_iterations );
    SetIntAnalysisInput( sweep_name, "NumWakeNodes", wake_nodes );
    SetDoubleAnalysisInput( sweep_name, "WakeRelax", wake_relaxation );

{implicit_setup}

    string sweep_result = ExecAnalysis( sweep_name );
    Print( "REPAIRMACH_SWEEP_RESULT_ID=" + sweep_result );
    WriteResultsCSVFile( sweep_result, "{_vsp_path(results_csv_path)}" );
    int solver_error_count = GetNumTotalErrors();
    while ( GetNumTotalErrors() > 0 )
    {{
        ErrorObj err = PopLastError();
        Print( "OPENVSP_ERROR=" + err.GetErrorString() );
    }}
    if ( solver_error_count > 0 )
    {{
        Print( "REPAIRMACH_ABORTED_SOLVER_ERRORS=1" );
        return;
    }}
    Print( "REPAIRMACH_VSPAERO_COMPLETE=1" );
}}
'''
    script_path.write_text(text, encoding="utf-8")


def parse_set_report(log_text: str) -> dict:
    """Parse the machine-readable set inventory printed by the generated script."""
    roles: dict[str, dict] = {}
    geometries: list[dict] = []
    errors: list[str] = []
    tail: dict = {}
    tail_setup: dict = {}
    engine_setup: dict = {}
    engine_components: list[dict] = []
    inside = False
    for raw_line in log_text.splitlines():
        line = raw_line.strip()
        if line.startswith("ERROR="):
            errors.append(line.removeprefix("ERROR="))
            continue
        if line.startswith("OPENVSP_ERROR="):
            errors.append(line)
            continue
        if line.startswith("REPAIRMACH_TAIL_SETUP;"):
            tail_setup = _parse_fields(line)
            continue
        if line.startswith("REPAIRMACH_ENGINE_SETUP;"):
            engine_setup = _parse_fields(line)
            continue
        if line.startswith("REPAIRMACH_ENGINE_COMPONENT;"):
            engine_components.append(_parse_fields(line))
            continue
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

    for setup_name, setup in (("ГО", tail_setup), ("Engine boundary", engine_setup)):
        if not setup:
            errors.append(f"Отсутствует машинное подтверждение настройки: {setup_name}")
            continue
        setup["VALID"] = str(setup.get("VALID", "false")).lower() in {"1", "true"}
        if not setup["VALID"]:
            errors.append(f"Настройка не подтверждена: {setup_name}")

    if tail_setup.get("MODE") == "fixed_incidence" and tail_setup.get("VALID"):
        try:
            requested = float(tail_setup["REQUESTED_ANGLE_DEG"])
            actual = float(tail_setup["ACTUAL_ANGLE_DEG"])
            tail_setup["REQUESTED_ANGLE_DEG"] = requested
            tail_setup["ACTUAL_ANGLE_DEG"] = actual
            if not all(math.isfinite(value) for value in (requested, actual)) or abs(actual - requested) > 1.0e-9:
                errors.append("Фактический угол ГО не совпадает с заданным")
        except (KeyError, TypeError, ValueError):
            errors.append("Машинное подтверждение угла ГО неполно")

    if engine_setup.get("MODE") == "to_face" and engine_setup.get("VALID"):
        try:
            expected_count = int(engine_setup["EXPECTED_COUNT"])
            applied_count = int(engine_setup["APPLIED_COUNT"])
            engine_setup["EXPECTED_COUNT"] = expected_count
            engine_setup["APPLIED_COUNT"] = applied_count
            if expected_count < 1 or applied_count != expected_count:
                errors.append(
                    "Engine to_face: число подтверждённых мотогондол не совпадает с ожидаемым"
                )
            if len(engine_components) != expected_count:
                errors.append(
                    "Engine to_face: неполный набор машинных подтверждений мотогондол"
                )
        except (KeyError, TypeError, ValueError):
            errors.append("Engine to_face: итоговое подтверждение количества неполно")

        expected_engine = {
            "GEOM_IN_TYPE": 3.0,
            "GEOM_OUT_TYPE": 3.0,
            "INLET_MODE_TYPE": 4.0,
            "OUTLET_MODE_TYPE": 4.0,
        }
        seen_ids: set[str] = set()
        for component in engine_components:
            component["VALID"] = str(component.get("VALID", "false")).lower() in {
                "1", "true"
            }
            geom_id = str(component.get("ID", ""))
            if not geom_id or geom_id in seen_ids:
                errors.append("Engine to_face: отсутствует или повторяется ID мотогондолы")
            seen_ids.add(geom_id)
            if not component["VALID"]:
                errors.append(
                    f"Engine to_face: настройка мотогондолы {component.get('NAME', geom_id)} не подтверждена"
                )
            for key, expected in expected_engine.items():
                try:
                    actual = float(component[key])
                    component[key] = actual
                    if not math.isfinite(actual) or abs(actual - expected) > 1.0e-9:
                        errors.append(
                            f"Engine to_face: {component.get('NAME', geom_id)} "
                            f"{key}={actual:g}, ожидалось {expected:g}"
                        )
                except (KeyError, TypeError, ValueError):
                    errors.append(
                        f"Engine to_face: у {component.get('NAME', geom_id)} "
                        f"отсутствует фактическое значение {key}"
                    )

    if "REPAIRMACH_ABORTED_NUMERICAL_CONTROLS=1" in log_text:
        errors.append("Фактические численные настройки OpenVSP не подтверждены")
    # Legacy logs remain readable, but cannot claim to have passed this gate.
    markers = {line.strip() for line in log_text.splitlines()}
    native_required = "REPAIRMACH_NATIVE_GUARD_REQUIRED=1" in markers
    native_valid = "REPAIRMACH_NATIVE_MESH_VALIDATION=OK" in markers
    native_aborted = "REPAIRMACH_ABORTED_NATIVE_MESH=1" in markers
    if native_aborted or (native_required and not native_valid):
        errors.append("Полнота фактической сетки VSPGEOM не подтверждена; Sweep запрещён")
    setup_valid = "REPAIRMACH_SETUP_VALIDATION=OK" in log_text and not any(
        marker in log_text for marker in (
            "REPAIRMACH_ABORTED_SETUP_VALIDATION=1",
            "REPAIRMACH_ABORTED_SETUP_WRITE_ERRORS=1",
        )
    )
    if not setup_valid:
        errors.append("Настройка VSPAERO не получила финальный маркер SETUP_VALIDATION")
    return {
        "roles": roles,
        "geometries": geometries,
        "errors": errors,
        "tail": tail,
        "tail_setup": tail_setup,
        "engine_setup": engine_setup,
        "engine_components": engine_components,
        "setup_valid": setup_valid,
        "native_mesh_guard_required": native_required,
        "native_mesh_validated": native_required and native_valid and not native_aborted,
        "valid": not errors and "REPAIRMACH_SET_VALIDATION=OK" in log_text and setup_valid,
        "calculation_complete": (
            not errors
            and setup_valid
            and "REPAIRMACH_VSPAERO_COMPLETE=1" in log_text
            and "REPAIRMACH_ABORTED_SOLVER_ERRORS=1" not in log_text
            and "REPAIRMACH_ABORTED_NUMERICAL_CONTROLS=1" not in log_text
        ),
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
