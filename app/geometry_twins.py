#!/usr/bin/env python3
"""PLAN/APPLY and independent solver-twin construction."""

from __future__ import annotations

import math
import re
import shutil
from copy import deepcopy
from pathlib import Path

from geometry_manifest import canonical_vsp3_sha256, find_parameters, sha256_file, sha256_payload
from geometry_rules import component_base, finding
from openvsp_runner import run_vspscript


PLAN_SCHEMA = "repairmach.geometry-transformation-plan/1.0"


def _vsp_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace('"', '\\"')


def _first_parameter(component: dict, name: str, group: str | None = None) -> dict | None:
    matches = find_parameters(component, name)
    if group is not None:
        matches = [item for item in matches if item.get("group") == group]
    return matches[0] if len(matches) == 1 else None


def build_transformation_plan(
    *,
    master_sha256: str,
    policy_sha256: str,
    reference_sha256: str,
    scope_sha256: str,
    inventory: dict,
    semantic_audit: dict,
    reference: dict,
    policy: dict,
) -> dict:
    """Build a deterministic, sealed plan without changing any file."""
    actions: list[dict] = []
    findings = deepcopy(semantic_audit.get("findings", []))
    thick_set = int(policy["sets"]["thick_user_set"])
    thin_set = int(policy["sets"]["thin_user_set"])

    component_by_id = {item["id"]: item for item in inventory.get("components", [])}
    component_by_name = {item["name"]: item for item in inventory.get("components", [])}
    for exclusion in semantic_audit.get("declared_exclusions", []):
        component = component_by_name.get(str(exclusion.get("component", "")))
        if component is None:
            continue
        declared_backends = exclusion.get("backends", [])
        if not isinstance(declared_backends, list) or not declared_backends:
            findings.append(
                finding(
                    "GEO-EXCL-002",
                    "BLOCKER",
                    "Declared exclusion must contain an explicit non-empty backends list",
                    component=component["name"],
                    evidence={"backends": declared_backends},
                )
            )
            continue
        # Geometry-certification policy v1 has one complete exclusion
        # contract: the component is removed from both VSPAERO twins and its
        # contribution is supplied by the sealed hybrid stage.  Do not infer
        # backend-specific variants here if a caller bypassed policy
        # validation -- doing so would create an incompletely replaced model.
        if declared_backends != ["vspaero", "hybrid"]:
            findings.append(
                finding(
                    "GEO-EXCL-002",
                    "BLOCKER",
                    "Declared exclusion uses a backend contract unsupported by policy v1",
                    component=component["name"],
                    evidence={
                        "backends": declared_backends,
                        "supported_contract": ["vspaero", "hybrid"],
                    },
                )
            )
            continue
        actions.append({
            "action": "clear_sets",
            "target_twins": ["vspaero_mixed", "vspaero_lifting"],
            "geom_id": component["id"],
            "component": component["name"],
            "user_sets": [thick_set, thin_set],
            "before": deepcopy(component.get("sets", {})),
            "after": {str(thick_set): False, str(thin_set): False},
            "reason_code": "GEO-EXCL-001",
        })
    for item in findings:
        if item["code"] != "GEO-SET-001" or item["severity"] != "REPAIRABLE":
            continue
        geom_id = item.get("evidence", {}).get("geom_id")
        component = component_by_id.get(geom_id)
        if not component:
            continue
        base, _ = component_base(component["name"], policy)
        role = policy["semantics"]["roles"].get(base)
        expected = thin_set if role == "thin" else thick_set
        other = thick_set if role == "thin" else thin_set
        actions.append({
            "action": "set_assignment",
            "target_twins": ["vspaero_mixed", "vspaero_lifting", "machline"],
            "geom_id": geom_id,
            "component": component["name"],
            "expected_user_set": expected,
            "other_user_set": other,
            "before": {
                str(expected): bool(component.get("sets", {}).get(str(expected), False)),
                str(other): bool(component.get("sets", {}).get(str(other), False)),
            },
            "after": {str(expected): True, str(other): False},
            "reason_code": "GEO-SET-001",
        })

    tip_policy = policy["regularization"]["terminal_chord"]
    zero_ratio = float(tip_policy["zero_ratio"])
    floor_ratio = float(tip_policy["floor_root_ratio"])
    max_area_delta = float(tip_policy["max_area_delta_fraction"])
    sref = float(reference["area"])
    for component in semantic_audit.get("recognized_components", []):
        base = component.get("semantic_base")
        if policy["semantics"]["roles"].get(base) != "thin":
            continue
        terminal = component.get("wing_terminal")
        if not terminal or terminal["root_chord"] <= 0.0:
            continue
        ratio = terminal["tip_chord"] / terminal["root_chord"]
        if ratio > zero_ratio:
            continue
        proposed = floor_ratio * terminal["root_chord"]
        area_delta_fraction = (
            0.5 * abs(proposed - terminal["tip_chord"]) * abs(terminal["span"]) / sref
        )
        if area_delta_fraction > max_area_delta:
            findings.append(
                finding(
                    "GEO-TIP-002",
                    "BLOCKER",
                    "Регуляризация нулевой законцовки превышает бюджет изменения площади",
                    component=component["name"],
                    evidence={
                        "parm_id": terminal["tip_parm_id"],
                        "area_delta_fraction_estimate": area_delta_fraction,
                        "limit": max_area_delta,
                    },
                )
            )
            continue
        actions.append({
            "action": "set_parameter",
            "target_twins": ["vspaero_mixed", "vspaero_lifting", "machline"],
            "geom_id": component["id"],
            "component": component["name"],
            "parm_id": terminal["tip_parm_id"],
            "parm_name": "Tip_Chord",
            "parm_group": "terminal_xsec",
            "before": terminal["tip_chord"],
            "after": proposed,
            "normalized_delta": abs(proposed - terminal["tip_chord"]) / float(reference["cref"]),
            "area_delta_fraction_estimate": area_delta_fraction,
            "reason_code": "GEO-TIP-001",
        })
        findings.append(
            finding(
                "GEO-TIP-001",
                "REPAIRABLE",
                "Нулевая законцовка будет регуляризована только в расчётных двойниках",
                component=component["name"],
                evidence={
                    "before": terminal["tip_chord"],
                    "after": proposed,
                    "parm_id": terminal["tip_parm_id"],
                },
                disposition="fix_in_solver_twins",
            )
        )

    # Gaps are never guessed.  Each action must name an exact component and
    # axis in the policy or project override.
    exact_names = {item["name"]: item for item in inventory.get("components", [])}
    for request in policy.get("regularization", {}).get("explicit_offsets", []):
        name = str(request.get("component", ""))
        axis = str(request.get("axis", "Z")).upper()
        delta_cref = float(request.get("delta_cref", 0.0))
        component = exact_names.get(name)
        if component is None or axis not in {"X", "Y", "Z"} or not math.isfinite(delta_cref):
            findings.append(
                finding(
                    "GEO-INT-002",
                    "BLOCKER",
                    "Явная регуляризация зазора задана неоднозначно",
                    component=name or None,
                    evidence=request,
                )
            )
            continue
        parm = _first_parameter(component, f"{axis}_Rel_Location")
        max_delta = float(policy["regularization"]["offsets"]["max_delta_cref"])
        if parm is None or abs(delta_cref) > max_delta:
            findings.append(
                finding(
                    "GEO-INT-002",
                    "BLOCKER",
                    "Явный offset отсутствует или превышает допустимый предел",
                    component=name,
                    evidence={"axis": axis, "delta_cref": delta_cref, "limit": max_delta},
                )
            )
            continue
        after = parm["value"] + delta_cref * float(reference["cref"])
        actions.append({
            "action": "set_parameter",
            "target_twins": list(request.get("target_twins", ["vspaero_mixed", "vspaero_lifting"])),
            "geom_id": component["id"],
            "component": name,
            "parm_id": parm["id"],
            "parm_name": parm["name"],
            "parm_group": parm.get("group", ""),
            "before": parm["value"],
            "after": after,
            "normalized_delta": abs(delta_cref),
            "reason_code": "GEO-INT-001",
        })

    actions.sort(key=lambda item: (
        item["action"], item.get("component", ""), item.get("parm_group", ""), item.get("parm_id", "")
    ))
    plan = {
        "schema": PLAN_SCHEMA,
        "method_version": policy["method_version"],
        "bindings": {
            "master_sha256": master_sha256,
            "policy_sha256": policy_sha256,
            "reference_sha256": reference_sha256,
            "scope_sha256": scope_sha256,
        },
        "actions": actions,
        "findings": findings,
        "blocked": any(item["severity"] == "BLOCKER" for item in findings),
    }
    plan["plan_sha256"] = sha256_payload(plan)
    return plan


def verify_plan(
    plan: dict,
    *,
    master_sha256: str,
    policy_sha256: str,
    reference_sha256: str,
    scope_sha256: str,
) -> list[str]:
    errors: list[str] = []
    expected_hash = plan.get("plan_sha256")
    unsealed = deepcopy(plan)
    unsealed.pop("plan_sha256", None)
    if expected_hash != sha256_payload(unsealed):
        errors.append("Хэш плана преобразований не совпадает")
    expected = {
        "master_sha256": master_sha256,
        "policy_sha256": policy_sha256,
        "reference_sha256": reference_sha256,
        "scope_sha256": scope_sha256,
    }
    for key, value in expected.items():
        if plan.get("bindings", {}).get(key) != value:
            errors.append(f"План не соответствует текущему {key}")
    if plan.get("blocked"):
        errors.append("План содержит блокирующие дефекты")
    return errors


def _action_script(action: dict, twin_name: str) -> str:
    if twin_name not in action.get("target_twins", []):
        return ""
    if action["action"] == "set_assignment":
        expected_api = int(action["expected_user_set"]) + 3
        other_api = int(action["other_user_set"]) + 3
        gid = action["geom_id"]
        return f'''
    SetSetFlag( "{gid}", {expected_api}, true );
    SetSetFlag( "{gid}", {other_api}, false );
    if ( !GetSetFlag( "{gid}", {expected_api} ) || GetSetFlag( "{gid}", {other_api} ) )
    {{
        Print( "REPAIRMACH_APPLY_ABORT=SET_ASSIGNMENT_NOT_APPLIED;GEOM_ID={gid}" );
        return;
    }}
    Print( "APPLIED;ACTION=set_assignment;GEOM_ID={gid};SET={expected_api}" );
'''
    if action["action"] == "clear_sets":
        gid = action["geom_id"]
        statements = "\n".join(
            f'    SetSetFlag( "{gid}", {int(user_set) + 3}, false );'
            for user_set in action["user_sets"]
        )
        checks = "\n".join(
            f'''    if ( GetSetFlag( "{gid}", {int(user_set) + 3} ) )
    {{
        Print( "REPAIRMACH_APPLY_ABORT=SET_CLEAR_NOT_APPLIED;GEOM_ID={gid};SET={int(user_set) + 3}" );
        return;
    }}'''
            for user_set in action["user_sets"]
        )
        return f'''\n{statements}
{checks}
    Print( "APPLIED;ACTION=clear_sets;GEOM_ID={gid}" );
'''
    if action["action"] == "set_parameter":
        pid = action["parm_id"]
        before = float(action["before"])
        after = float(action["after"])
        before_tolerance = 1.0e-10 * max(1.0, abs(before))
        after_tolerance = 1.0e-10 * max(1.0, abs(after))
        return f'''
    if ( !ValidParm( "{pid}" ) || abs( GetParmVal( "{pid}" ) - {before:.17g} ) > {before_tolerance:.17g} )
    {{
        Print( "REPAIRMACH_APPLY_ABORT=PARAMETER_BINDING;PARM_ID={pid}" );
        return;
    }}
    SetParmVal( "{pid}", {after:.17g} );
    double actual_{re.sub(r'[^A-Za-z0-9_]', '_', pid)} = GetParmVal( "{pid}" );
    if ( actual_{re.sub(r'[^A-Za-z0-9_]', '_', pid)} != actual_{re.sub(r'[^A-Za-z0-9_]', '_', pid)} || abs( actual_{re.sub(r'[^A-Za-z0-9_]', '_', pid)} - {after:.17g} ) > {after_tolerance:.17g} )
    {{
        Print( "REPAIRMACH_APPLY_ABORT=PARAMETER_NOT_APPLIED;PARM_ID={pid};REQUESTED={after:.17g};ACTUAL=" + actual_{re.sub(r'[^A-Za-z0-9_]', '_', pid)} );
        return;
    }}
    Print( "APPLIED;ACTION=set_parameter;PARM_ID={pid};VALUE=" + actual_{re.sub(r'[^A-Za-z0-9_]', '_', pid)} );
'''
    raise ValueError(f"Неизвестное действие плана: {action['action']}")


def _final_state_checks(
    actions: list[dict], twin_name: str, phase: str,
    empty_thick_user_set: int | None,
) -> str:
    """Check the final requested state, not the transient SetParmVal result."""
    parameters: dict[str, float] = {}
    assignments: dict[tuple[str, int], bool] = {}
    for action in actions:
        if twin_name not in action.get("target_twins", []):
            continue
        if action["action"] == "set_parameter":
            parameters[action["parm_id"]] = float(action["after"])
        elif action["action"] == "set_assignment":
            gid = action["geom_id"]
            assignments[gid, int(action["expected_user_set"]) + 3] = True
            assignments[gid, int(action["other_user_set"]) + 3] = False
        elif action["action"] == "clear_sets":
            for user_set in action["user_sets"]:
                assignments[action["geom_id"], int(user_set) + 3] = False
    empty_api = None if empty_thick_user_set is None else int(empty_thick_user_set) + 3
    fragments = []
    for index, (pid, expected) in enumerate(parameters.items()):
        tolerance = 1.0e-10 * max(1.0, abs(expected))
        var = f"check_{phase}_{index}"
        fragments.append(f'''
    if ( !ValidParm( "{pid}" ) )
    {{
        Print( "REPAIRMACH_APPLY_ABORT={phase}_PARAMETER_MISSING;PARM_ID={pid}" );
        return;
    }}
    double {var} = GetParmVal( "{pid}" );
    if ( {var} != {var} || abs( {var} - {expected:.17g} ) > {tolerance:.17g} )
    {{
        Print( "REPAIRMACH_APPLY_ABORT={phase}_PARAMETER_MISMATCH;PARM_ID={pid};REQUESTED={expected:.17g};ACTUAL=" + {var} );
        return;
    }}
''')
    for (gid, api), expected in assignments.items():
        # Clearing the deliberately empty set is the last operation.
        expected = False if api == empty_api else expected
        fragments.append(f'''
    if ( GetSetFlag( "{gid}", {api} ) != {str(expected).lower()} )
    {{
        Print( "REPAIRMACH_APPLY_ABORT={phase}_SET_MISMATCH;GEOM_ID={gid};SET={api}" );
        return;
    }}
''')
    if empty_api is not None:
        fragments.append(f'''
    array<string> empty_geoms_{phase} = FindGeoms();
    for ( int gi = 0; gi < int( empty_geoms_{phase}.size() ); ++gi )
    {{
        if ( GetSetFlag( empty_geoms_{phase}[gi], {empty_api} ) )
        {{
            Print( "REPAIRMACH_APPLY_ABORT={phase}_EMPTY_SET_MISMATCH" );
            return;
        }}
    }}
''')
    return "".join(fragments)


def generate_apply_script(
    script_path: Path,
    source_path: Path,
    output_path: Path,
    actions: list[dict],
    *,
    twin_name: str,
    empty_thick_user_set: int | None = None,
) -> None:
    script_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fragments = "".join(_action_script(action, twin_name) for action in actions)
    post_update_checks = _final_state_checks(actions, twin_name, "POST_UPDATE", empty_thick_user_set)
    persisted_checks = _final_state_checks(actions, twin_name, "PERSISTED", empty_thick_user_set)
    empty_fragment = ""
    if empty_thick_user_set is not None:
        api_index = int(empty_thick_user_set) + 3
        empty_fragment = f'''
    array<string> all_geoms = FindGeoms();
    for ( int gi = 0; gi < int( all_geoms.size() ); ++gi )
    {{
        SetSetFlag( all_geoms[gi], {api_index}, false );
    }}
    SetSetName( {api_index}, "RM_CERT_EMPTY_THICK" );
    for ( int gi = 0; gi < int( all_geoms.size() ); ++gi )
    {{
        if ( GetSetFlag( all_geoms[gi], {api_index} ) )
        {{
            Print( "REPAIRMACH_APPLY_ABORT=EMPTY_SET_NOT_APPLIED;GEOM_ID=" + all_geoms[gi] );
            return;
        }}
    }}
    Print( "APPLIED;ACTION=clear_empty_thick_set;SET={api_index}" );
'''
    text = f'''void main()
{{
    ClearVSPModel();
    ReadVSPFile( "{_vsp_path(source_path)}" );
    Update();
    array<string> source_geoms = FindGeoms();
{fragments}{empty_fragment}
    Update();
{post_update_checks}
    int apply_error_count = GetNumTotalErrors();
    while ( GetNumTotalErrors() > 0 )
    {{
        ErrorObj err = PopLastError();
        Print( "OPENVSP_ERROR=" + err.GetErrorString() );
    }}
    if ( apply_error_count > 0 )
    {{
        Print( "REPAIRMACH_APPLY_ABORT=OPENVSP_ERRORS_BEFORE_WRITE" );
        return;
    }}
    WriteVSPFile( "{_vsp_path(output_path)}", 0 );
    int write_error_count = GetNumTotalErrors();
    while ( GetNumTotalErrors() > 0 )
    {{
        ErrorObj err = PopLastError();
        Print( "OPENVSP_ERROR=" + err.GetErrorString() );
    }}
    if ( write_error_count > 0 )
    {{
        Print( "REPAIRMACH_APPLY_ABORT=OPENVSP_ERRORS_DURING_WRITE" );
        return;
    }}
    // A successful write is insufficient: re-read the saved calculation copy.
    ClearVSPModel();
    ReadVSPFile( "{_vsp_path(output_path)}" );
    Update();
    array<string> persisted_geoms = FindGeoms();
    if ( persisted_geoms.size() != source_geoms.size() )
    {{
        Print( "REPAIRMACH_APPLY_ABORT=PERSISTED_GEOMETRY_COUNT" );
        return;
    }}
    for ( int si = 0; si < int( source_geoms.size() ); ++si )
    {{
        bool found = false;
        for ( int pi = 0; pi < int( persisted_geoms.size() ); ++pi )
        {{
            if ( source_geoms[si] == persisted_geoms[pi] ) found = true;
        }}
        if ( !found )
        {{
            Print( "REPAIRMACH_APPLY_ABORT=PERSISTED_GEOMETRY_ID" );
            return;
        }}
    }}
{persisted_checks}
    int readback_error_count = GetNumTotalErrors();
    while ( GetNumTotalErrors() > 0 )
    {{
        ErrorObj err = PopLastError();
        Print( "OPENVSP_ERROR=" + err.GetErrorString() );
    }}
    if ( readback_error_count > 0 )
    {{
        Print( "REPAIRMACH_APPLY_ABORT=OPENVSP_ERRORS_DURING_READBACK" );
        return;
    }}
    Print( "REPAIRMACH_APPLY_OK=1" );
}}
'''
    script_path.write_text(text, encoding="utf-8")


def apply_model_actions(
    *,
    source_path: Path,
    output_path: Path,
    actions: list[dict],
    twin_name: str,
    vspscript_executable: Path,
    work_dir: Path,
    empty_thick_user_set: int | None = None,
    timeout_seconds: float = 180.0,
) -> dict:
    if source_path.resolve() == output_path.resolve():
        raise ValueError("Расчётный двойник нельзя записывать поверх исходной модели")
    script = work_dir / f"apply_{twin_name}.vspscript"
    log = work_dir / f"apply_{twin_name}.log"
    # Remove any prior artifact before even generating the new APPLY script;
    # a preparation failure must not leave a stale twin looking current.
    output_path.unlink(missing_ok=True)
    generate_apply_script(
        script,
        source_path,
        output_path,
        actions,
        twin_name=twin_name,
        empty_thick_user_set=empty_thick_user_set,
    )
    code = run_vspscript(
        vspscript_executable,
        script,
        log,
        working_dir=work_dir,
        timeout_seconds=timeout_seconds,
    )
    log_text = log.read_text(encoding="utf-8", errors="replace")
    expected_applied = sum(
        1 for action in actions if twin_name in action.get("target_twins", [])
    ) + (1 if empty_thick_user_set is not None else 0)
    applied = sum(1 for line in log_text.splitlines() if line.strip().startswith("APPLIED;"))
    ok_markers = sum(
        1 for line in log_text.splitlines() if line.strip() == "REPAIRMACH_APPLY_OK=1"
    )
    aborts = [
        line.strip() for line in log_text.splitlines()
        if "REPAIRMACH_APPLY_ABORT=" in line
    ]
    openvsp_errors = [
        line.strip() for line in log_text.splitlines()
        if "OPENVSP_ERROR=" in line
    ]
    errors: list[str] = []
    if code not in (0, 1):
        errors.append(f"vspscript завершился с кодом {code}")
    if ok_markers != 1:
        errors.append("OpenVSP не выдал единственный финальный маркер APPLY_OK")
    if aborts:
        errors.append("Применение плана прервано: " + " | ".join(aborts))
    if openvsp_errors:
        errors.append("OpenVSP сообщил ошибки: " + " | ".join(openvsp_errors))
    if applied != expected_applied:
        errors.append(
            f"Подтверждено действий {applied}, ожидалось {expected_applied}"
        )
    if not output_path.is_file():
        errors.append("OpenVSP не создал расчётный двойник")
    valid = not errors
    return {
        "name": twin_name,
        "path": str(output_path.resolve()),
        "sha256": sha256_file(output_path) if output_path.is_file() else None,
        "canonical_sha256": canonical_vsp3_sha256(output_path) if output_path.is_file() else None,
        "valid": valid,
        "return_code": code,
        "expected_applied_actions": expected_applied,
        "confirmed_applied_actions": applied,
        "script": str(script.resolve()),
        "log": str(log.resolve()),
        "errors": errors,
    }


def mesh_actions(inventory: dict, semantic_audit: dict, level: dict, twin_name: str) -> list[dict]:
    recognized = {item["id"]: item for item in semantic_audit.get("recognized_components", [])}
    actions: list[dict] = []
    for component in inventory.get("components", []):
        audited = recognized.get(component["id"])
        if audited is None:
            continue
        role = audited.get("semantic_base")
        # semantic_base is converted to thin/thick by the caller's audit data
        semantic_role = audited.get("semantic_role")
        if semantic_role not in {"thin", "thick"}:
            semantic_role = "thin" if component.get("type") == "Wing" else "thick"
        values = {
            "Tess_W": int(level[f"{semantic_role}_tess_w"]),
            "SectTess_U": int(level[f"{semantic_role}_sect_tess_u"]),
            "Tess_U": int(level[f"{semantic_role}_tess_u"]),
        }
        for parm in component.get("parameters", []):
            if parm["name"] not in values:
                continue
            after = values[parm["name"]]
            if abs(parm["value"] - after) < 1.0e-12:
                continue
            actions.append({
                "action": "set_parameter",
                "target_twins": [twin_name],
                "geom_id": component["id"],
                "component": component["name"],
                "parm_id": parm["id"],
                "parm_name": parm["name"],
                "parm_group": parm.get("group", ""),
                "before": parm["value"],
                "after": after,
                "reason_code": "GEO-MESH-001",
            })
    return actions


def verify_mesh_inventory(inventory: dict, semantic_audit: dict, level: dict) -> dict:
    """Verify the persisted Tess_* values of a re-opened mesh twin.

    The APPLY log is only execution evidence.  Certification is based on the
    values read back from the written VSP3 so a clamped/ignored SetParmVal can
    never make two nominal mesh levels look converged.
    """
    errors: list[str] = []
    components: list[dict] = []
    signature_entries: list[dict] = []
    recognized = {
        item["id"]: item for item in semantic_audit.get("recognized_components", [])
    }
    if not inventory.get("valid"):
        errors.append("Повторное чтение VSP3 сеточного двойника не прошло")
    if not semantic_audit.get("valid"):
        errors.append("Семантика повторно прочитанного сеточного двойника повреждена")

    for component in inventory.get("components", []):
        audited = recognized.get(component.get("id"))
        if audited is None:
            continue
        semantic_role = audited.get("semantic_role")
        if semantic_role not in {"thin", "thick"}:
            semantic_role = "thin" if component.get("type") == "Wing" else "thick"
        expected = {
            "Tess_W": int(level[f"{semantic_role}_tess_w"]),
            "SectTess_U": int(level[f"{semantic_role}_sect_tess_u"]),
            "Tess_U": int(level[f"{semantic_role}_tess_u"]),
        }
        actual_by_name: dict[str, list[dict]] = {name: [] for name in expected}
        for parm in component.get("parameters", []):
            if parm.get("name") in actual_by_name:
                actual_by_name[parm["name"]].append(parm)

        component_errors: list[str] = []
        actual_summary: dict[str, list[float]] = {}
        for parm_name, expected_value in expected.items():
            parameters = actual_by_name[parm_name]
            if not parameters:
                component_errors.append(f"{parm_name}: параметр отсутствует")
                continue
            actual_values = [float(parm["value"]) for parm in parameters]
            actual_summary[parm_name] = actual_values
            for parm, actual in zip(parameters, actual_values):
                if not math.isfinite(actual) or abs(actual - expected_value) > 1.0e-9:
                    component_errors.append(
                        f"{parm_name}={actual:g}, ожидалось {expected_value}"
                    )
                signature_entries.append({
                    "geom_id": component.get("id"),
                    "parm_id": parm.get("id"),
                    "name": parm_name,
                    "group": parm.get("group", ""),
                    "value": actual,
                })
        if component_errors:
            errors.extend(
                f"{component.get('name', component.get('id'))}: {message}"
                for message in component_errors
            )
        components.append({
            "id": component.get("id"),
            "name": component.get("name"),
            "semantic_role": semantic_role,
            "expected": expected,
            "actual": actual_summary,
            "valid": not component_errors,
            "errors": component_errors,
        })

    if not components:
        errors.append("В сеточном двойнике нет распознанных компонентов для проверки Tess_*")
    signature_entries.sort(
        key=lambda item: (str(item["geom_id"]), str(item["name"]), str(item["parm_id"]))
    )
    return {
        "valid": not errors,
        "components": components,
        "parameter_signature": signature_entries,
        "signature_sha256": sha256_payload(signature_entries) if signature_entries else None,
        "errors": errors,
    }


def copy_passthrough(source: Path, destination: Path, name: str) -> dict:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return {
        "name": name,
        "path": str(destination.resolve()),
        "sha256": sha256_file(destination),
        "canonical_sha256": canonical_vsp3_sha256(destination),
        "valid": True,
        "return_code": None,
        "script": None,
        "log": None,
        "errors": [],
    }
