#!/usr/bin/env python3
"""Safe executor for certificate corrective actions owned by RepairMach.

The executor never edits the certified MASTER.  It either refuses with an
auditable operator queue or starts a fresh complete certification from the
same sealed inputs.  This intentionally favours a full repeat over trying to
resume ambiguous partial solver state.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path

from geometry_certificate import certificate_integrity_errors
from geometry_certification import certify_geometry
from geometry_manifest import sha256_file, sha256_payload, write_json
from geometry_remediation import corrective_action_plan_errors


EXECUTION_SCHEMA = "repairmach.geometry-corrective-execution/1.0"
SAFE_RECERTIFICATION_ACTIONS = {
    "rebuild_transformation_plan",
    "rebuild_solver_twins",
    "rebuild_mesh_ladder",
    "reexport_machline_tri",
    "rerun_machline_qualification",
    "rerun_vspaero_qualification",
    "restart_certification",
}


def resolve_hybrid_corrective_actions(
    certificate: dict,
    bundle: dict,
    *,
    workbook_verification: dict | None = None,
) -> dict:
    """Close hybrid actions only from evidence already accepted by the consumer."""
    plan = certificate.get("corrective_action_plan", {})
    coverage = bundle.get("replacement_coverage", [])
    rows = bundle.get("rows", [])
    workbook_ok = bool(
        isinstance(workbook_verification, dict)
        and workbook_verification.get("build_status") == "passed"
        and workbook_verification.get("external_links_detected") is False
        and workbook_verification.get("charts_use_internal_cells") is True
    )
    resolved = []
    for item in plan.get("items", []) if isinstance(plan, dict) else []:
        status = "not_required"
        evidence: dict = {}
        if item.get("required_before_calculation"):
            status = "pending"
            code = item.get("action_code")
            component = str(item.get("component", "")).casefold()
            if item.get("new_geometry_certificate_required"):
                status = "blocked_geometry"
            elif item.get("owner") != "repairmach":
                status = "blocked_operator"
            elif code == "BIND_MACHLINE_COMPONENT_SERIES":
                matched = [
                    entry for entry in coverage
                    if str(entry.get("component", "")).casefold() == component
                    and entry.get("satisfied_by") == "machline_pressure_wave"
                ]
                points_ok = bool(rows) and all(row.get("sources", {}).get("machline") for row in rows)
                status = "completed" if matched and points_ok else "pending_sources"
                evidence = {"coverage_records": len(matched), "all_points_bound": points_ok}
            elif code == "BIND_PARASITE_COMPONENT_SERIES":
                subsonic = [row for row in rows if float(row.get("Mach", 99.0)) < 1.0]
                matched = [
                    entry for entry in coverage
                    if str(entry.get("component", "")).casefold() == component
                    and entry.get("satisfied_by") == "parasite_drag"
                ]
                points_ok = all(row.get("sources", {}).get("parasite_drag") for row in subsonic)
                status = "completed" if (not subsonic or matched) and points_ok else "pending_sources"
                evidence = {"coverage_records": len(matched), "subsonic_points_bound": points_ok}
            elif code == "CALCULATE_SEMIEMPIRICAL_BASE_DRAG":
                applicable = [row for row in rows if float(row.get("Mach", 0.0)) >= 1.0]
                terms_ok = bool(applicable) and all(
                    any(term.get("id") == "base_drag" for term in row.get("semiempirical_terms", []))
                    for row in applicable
                )
                status = "completed" if terms_ok else "pending_method_passport"
                evidence = {"applicable_points": len(applicable), "base_drag_present": terms_ok}
            elif code == "BIND_SEMIEMPIRICAL_COMPONENT_PRESSURE_SERIES":
                matched = [
                    entry for entry in coverage
                    if str(entry.get("component", "")).casefold() == component
                    and entry.get("satisfied_by") == "semiempirical_component_pressure_wave"
                ]
                covered_points = {
                    (float(entry["Mach"]), float(entry["alpha_deg"]))
                    for entry in matched
                    if entry.get("Mach") is not None and entry.get("alpha_deg") is not None
                }
                required_points = {
                    (float(row["Mach"]), float(row["alpha_deg"])) for row in rows
                }
                points_ok = bool(required_points) and covered_points == required_points
                passports_ok = bool(matched) and all(
                    entry.get("term_fingerprint")
                    and entry.get("method_id")
                    and entry.get("equation_version")
                    for entry in matched
                )
                status = (
                    "completed"
                    if points_ok and passports_ok
                    else "pending_method_passport"
                )
                evidence = {
                    "coverage_records": len(matched),
                    "all_points_bound": points_ok,
                    "passports_sealed": passports_ok,
                }
            if status == "completed" and "rebuild_hybrid_workbook" in item.get("allowed_automatic_actions", []):
                status = "completed" if workbook_ok else "pending_workbook"
                evidence["workbook_verified"] = workbook_ok
        resolved.append({
            "id": item.get("id"),
            "action_code": item.get("action_code"),
            "component": item.get("component"),
            "status": status,
            "evidence": evidence,
        })
    pending = [item for item in resolved if item["status"] not in {"completed", "not_required"}]
    payload = {
        "schema": "repairmach.hybrid-corrective-action-execution/1.0",
        "status": "complete" if not pending else "pending",
        "certificate_id": certificate.get("certificate_id"),
        "hybrid_result_status": bundle.get("status"),
        "workbook_verified": workbook_ok,
        "items": resolved,
        "pending_action_ids": [item.get("id") for item in pending],
    }
    payload["execution_fingerprint"] = sha256_payload(payload)
    return payload


def _write_record(root: Path, payload: dict) -> Path:
    root = Path(root) / "corrective_executions"
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = root / f"{stamp}_{payload.get('source_certificate_id', 'certificate')}.json"
    candidate = path
    index = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        index += 1
    payload["execution_fingerprint"] = sha256_payload(payload)
    write_json(candidate, payload)
    return candidate


def execute_safe_recertification(
    *,
    certificate_path: Path,
    output_root: Path,
    executables: dict[str, Path | None],
    run_vspaero_probes: bool | None = None,
) -> dict:
    """Repeat a failed certificate only when every required geometry action is safe."""
    certificate_path = Path(certificate_path).resolve()
    certificate = json.loads(certificate_path.read_text(encoding="utf-8"))
    plan = certificate.get("corrective_action_plan", {})
    errors = certificate_integrity_errors(certificate) + corrective_action_plan_errors(plan)
    master_path = Path(str(certificate.get("master", {}).get("source_path", "")))
    expected_master = certificate.get("master", {}).get("sha256_before")
    if not master_path.is_file():
        errors.append("Исходный MASTER сертификата отсутствует")
    elif sha256_file(master_path) != expected_master:
        errors.append("Исходный MASTER изменён после сертификата")

    required = [
        item for item in plan.get("items", [])
        if isinstance(item, dict) and item.get("required_before_calculation")
    ] if isinstance(plan, dict) else []
    recertification_items = [
        item for item in required if item.get("new_geometry_certificate_required")
    ]
    operator_items = [
        item for item in recertification_items
        if item.get("owner") != "repairmach" or item.get("master_change_required")
    ]
    unsupported_items = []
    for item in recertification_items:
        actions = set(item.get("allowed_automatic_actions", []))
        if not actions or not actions.issubset(SAFE_RECERTIFICATION_ACTIONS):
            unsupported_items.append(item)

    status = "not_started"
    new_result = None
    if errors:
        status = "invalid_source_certificate"
    elif operator_items or unsupported_items:
        status = "blocked_by_operator"
    elif not recertification_items:
        status = "awaiting_hybrid_inputs" if required else "no_action_required"
    else:
        policy_snapshot = certificate_path.parent / "policy_snapshot.json"
        if not policy_snapshot.is_file():
            errors.append("В пакете сертификата отсутствует policy_snapshot.json")
            status = "invalid_source_certificate"
        else:
            status = "running_fresh_recertification"
            new_result = certify_geometry(
                master_path=master_path,
                output_root=Path(output_root),
                project_name=str(certificate.get("project", master_path.stem)),
                reference=deepcopy(certificate["reference"]),
                policy_path=policy_snapshot,
                executables=executables,
                tri_path=None,
                scope_override=deepcopy(certificate.get("requested_scope", certificate.get("scope"))),
                policy_overrides=None,
                run_vspaero_probes=run_vspaero_probes,
                backend_branch=certificate.get("certification_branch", "combined"),
            )
            status = (
                "completed_pass"
                if new_result["certificate"].get("verdict") != "FAIL"
                else "completed_fail"
            )

    record = {
        "schema": EXECUTION_SCHEMA,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": status,
        "source_certificate": str(certificate_path),
        "source_certificate_id": certificate.get("certificate_id"),
        "source_certificate_fingerprint": certificate.get("certificate_fingerprint"),
        "master_sha256": expected_master,
        "policy": "fresh_complete_recertification_only",
        "master_modified": False,
        "safe_action_allowlist": sorted(SAFE_RECERTIFICATION_ACTIONS),
        "required_action_ids": [item.get("id") for item in required],
        "recertification_action_ids": [item.get("id") for item in recertification_items],
        "operator_action_ids": [item.get("id") for item in operator_items],
        "unsupported_action_ids": [item.get("id") for item in unsupported_items],
        "errors": errors,
        "new_certificate": (
            {
                "id": new_result["certificate"].get("certificate_id"),
                "verdict": new_result["certificate"].get("verdict"),
                "path": str(new_result["certificate_path"].resolve()),
                "fingerprint": new_result["certificate"].get("certificate_fingerprint"),
            }
            if new_result else None
        ),
    }
    record_path = _write_record(Path(output_root), record)
    return {"status": status, "record": record, "record_path": record_path, "result": new_result}
