#!/usr/bin/env python3
"""Unified fail-closed readiness gate for a sealed blind calculation."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path

from geometry_certificate import verify_certificate
from geometry_manifest import sha256_file, sha256_payload, write_json
from geometry_remediation import corrective_action_plan_errors


READINESS_SCHEMA = "repairmach.blind-readiness/1.0"
REFERENCE_ROLE_TOKENS = ("reference", "benchmark", "book", "tunnel", "эталон", "книг", "продув")


def _declared_replacements(method_declaration: dict) -> set[tuple[str, str]]:
    result: set[tuple[str, str]] = set()
    for item in method_declaration.get("replacement_methods", []):
        if not isinstance(item, dict):
            continue
        component = str(item.get("component", "")).strip().casefold()
        method = str(item.get("method", "")).strip().casefold()
        if component and method:
            result.add((component, method))
    return result


def assess_blind_readiness(
    *,
    geometry_path: Path,
    certificate_path: Path,
    selected_scenario: dict,
    method_declaration: dict,
    additional_inputs: list[tuple[str, Path]] | None = None,
    runtime_executables: dict[str, Path] | None = None,
) -> dict:
    """Require certified geometry, frozen method and no unresolved geometry work."""
    geometry_path = Path(geometry_path).resolve()
    certificate_path = Path(certificate_path).resolve()
    errors: list[str] = []
    warnings: list[str] = []
    checks: list[dict] = []

    verification = verify_certificate(
        certificate_path,
        master_path=geometry_path,
        backend="vspaero",
        scenario_id=str(selected_scenario.get("id", "")),
        scenario=selected_scenario,
        runtime_executables=runtime_executables,
    )
    checks.append({
        "id": "geometry_certificate",
        "valid": bool(verification["valid"]),
        "errors": list(verification["errors"]),
    })
    errors.extend(verification["errors"])

    certificate = json.loads(certificate_path.read_text(encoding="utf-8"))
    plan = certificate.get("corrective_action_plan", {})
    plan_errors = corrective_action_plan_errors(plan)
    errors.extend(plan_errors)
    unresolved_geometry = [
        item for item in plan.get("items", [])
        if item.get("required_before_calculation")
        and item.get("new_geometry_certificate_required")
    ] if isinstance(plan, dict) else []
    if unresolved_geometry:
        errors.append(
            "Сертификат содержит незакрытые действия, требующие новой сертификации: "
            + ", ".join(str(item.get("id", "?")) for item in unresolved_geometry)
        )
    checks.append({
        "id": "corrective_actions",
        "valid": not plan_errors and not unresolved_geometry,
        "unresolved_geometry_action_ids": [item.get("id") for item in unresolved_geometry],
    })

    if method_declaration.get("pointwise_tuning") is not False:
        errors.append("Поточечная подстройка не запрещена до раскрытия эталона")
    if method_declaration.get("geometry_adjustment_after_seal") is not False:
        errors.append("Изменение геометрии после печати не запрещено")
    if not str(method_declaration.get("method_version", "")).strip():
        errors.append("Версия методики не зафиксирована")

    replacement_contract = (
        certificate.get("backends", {}).get("hybrid", {}).get("replacement_contract", {})
    )
    requirements = (
        replacement_contract.get("requirements", [])
        if isinstance(replacement_contract, dict) else []
    )
    declared = _declared_replacements(method_declaration)
    undeclared = []
    unavailable = []
    for item in requirements:
        key = (
            str(item.get("component", "")).strip().casefold(),
            str(item.get("method", "")).strip().casefold(),
        )
        if key not in declared:
            undeclared.append({"component": item.get("component"), "method": item.get("method")})
        if item.get("backend_capability_available") is not True:
            unavailable.append({"component": item.get("component"), "method": item.get("method")})
    if undeclared:
        errors.append("Не все гибридные замены объявлены до расчёта")
    if unavailable:
        errors.append("Сертификат не подтверждает backend-покрытие всех гибридных замен")
    roles = [str(role).strip().lower() for role, _path in additional_inputs or []]
    if requirements and not any("hybrid_method_policy" == role for role in roles):
        errors.append("Для гибридного слепого расчёта не приложена замороженная политика метода")
    contamination = [
        role for role in roles if any(token in role for token in REFERENCE_ROLE_TOKENS)
    ]
    if contamination:
        errors.append("До расчёта обнаружены роли эталонных данных: " + ", ".join(contamination))
    checks.append({
        "id": "hybrid_method",
        "valid": not undeclared and not unavailable and (not requirements or "hybrid_method_policy" in roles),
        "required": bool(requirements),
        "requirements": deepcopy(requirements),
        "undeclared": undeclared,
        "backend_unavailable": unavailable,
    })
    checks.append({
        "id": "reference_contamination",
        "valid": not contamination,
        "detected_roles": contamination,
    })
    if runtime_executables is None:
        warnings.append("Исполняемые файлы не сверялись с сертификатом на этапе готовности")

    payload = {
        "schema": READINESS_SCHEMA,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": "READY_TO_SEAL" if not errors else "BLOCKED",
        "valid": not errors,
        "geometry": {
            "path": str(geometry_path),
            "sha256": sha256_file(geometry_path) if geometry_path.is_file() else None,
        },
        "certificate": {
            "path": str(certificate_path),
            "sha256": sha256_file(certificate_path),
            "id": certificate.get("certificate_id"),
            "fingerprint": certificate.get("certificate_fingerprint"),
            "verification": verification,
        },
        "scenario": {
            "id": selected_scenario.get("id"),
            "sha256": sha256_payload(selected_scenario),
        },
        "method_declaration_sha256": sha256_payload(method_declaration),
        "checks": checks,
        "errors": errors,
        "warnings": warnings,
    }
    payload["readiness_fingerprint"] = sha256_payload(payload)
    return payload


def write_blind_readiness(path: Path, payload: dict) -> None:
    write_json(Path(path), payload)


def verify_blind_readiness(payload: dict) -> list[str]:
    errors: list[str] = []
    if payload.get("schema") != READINESS_SCHEMA:
        errors.append("Неизвестная схема допуска слепого расчёта")
    expected = payload.get("readiness_fingerprint")
    unsealed = deepcopy(payload)
    unsealed.pop("readiness_fingerprint", None)
    if expected != sha256_payload(unsealed):
        errors.append("Нарушена цифровая целостность допуска слепого расчёта")
    if payload.get("valid") is not True or payload.get("status") != "READY_TO_SEAL":
        errors.append("Допуск слепого расчёта не положительный")
    return errors
