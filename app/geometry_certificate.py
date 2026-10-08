#!/usr/bin/env python3
"""Geometry certificate serialization, human report and validity checks."""

from __future__ import annotations

import json
import math
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from geometry_manifest import canonical_vsp3_sha256, sha256_file, sha256_payload, write_json
from geometry_remediation import corrective_action_plan_errors
from geometry_rules import VERDICTS


CERTIFICATE_SCHEMA = "repairmach.geometry-certificate/1.1"

BACKEND_SOLVERS = {
    "vspaero": ("openvsp", "vspaero"),
    "parasite_drag": ("openvsp",),
    "machline": ("machline",),
}

PASS_VERDICTS = {
    "PASS_NATIVE",
    "PASS_REGULARIZED",
    "PASS_WITH_DECLARED_EXCLUSIONS",
}


def _finding_blocks_backend(item: dict, backend: str | None) -> bool:
    """Return whether a BLOCKER is global or applies to ``backend``.

    Backend-scoped findings must not revoke an independently certified solver,
    while findings from the MASTER/plan/run stages are global.  Keep the
    parasite aliases together because certificates historically used both.
    """
    if not isinstance(item, dict) or item.get("severity") != "BLOCKER":
        return False
    scope = str(item.get("scope", "master")).strip().lower()
    aliases = {
        "vspaero": "vspaero",
        "machline": "machline",
        "parasite_drag": "parasite_drag",
        "parasite": "parasite_drag",
        "hybrid": "hybrid",
    }
    scoped_backend = None
    for prefix in ("parasite_drag", "vspaero", "machline", "parasite", "hybrid"):
        if scope == prefix or scope.startswith(prefix + "_"):
            scoped_backend = aliases[prefix]
            break
    if scoped_backend is None:
        return True
    return backend is not None and scoped_backend == aliases.get(backend, backend)


def seal_certificate(payload: dict) -> dict:
    result = deepcopy(payload)
    result.pop("certificate_id", None)
    result.pop("certificate_fingerprint", None)
    fingerprint = sha256_payload(result)
    result["certificate_id"] = f"RMC-{fingerprint[:16].upper()}"
    result["certificate_fingerprint"] = fingerprint
    return result


def certificate_integrity_errors(certificate: dict) -> list[str]:
    errors: list[str] = []
    if certificate.get("schema") != CERTIFICATE_SCHEMA:
        errors.append("Неизвестная схема сертификата")
    if certificate.get("verdict") not in VERDICTS:
        errors.append("Неизвестный вердикт сертификата")
    expected = certificate.get("certificate_fingerprint")
    unsealed = deepcopy(certificate)
    unsealed.pop("certificate_id", None)
    unsealed.pop("certificate_fingerprint", None)
    actual = sha256_payload(unsealed)
    if expected != actual:
        errors.append("Нарушена цифровая целостность certificate.json")
    expected_id = f"RMC-{actual[:16].upper()}"
    if certificate.get("certificate_id") != expected_id:
        errors.append("ID сертификата не соответствует его содержимому")
    return errors


def _point_matches(point: dict, mach: float | None, alpha_deg: float | None) -> bool:
    tolerance = 1.0e-8
    if any(not math.isfinite(float(point.get(key, math.nan))) for key in ("mach", "alpha_deg")):
        return False
    if any(value is not None and not math.isfinite(float(value)) for value in (mach, alpha_deg)):
        return False
    if mach is not None and abs(float(point.get("mach", math.nan)) - float(mach)) > tolerance:
        return False
    if alpha_deg is not None and abs(float(point.get("alpha_deg", math.nan)) - float(alpha_deg)) > tolerance:
        return False
    return True


def _scope_contains(scope: dict, mach: float | None, alpha_deg: float | None) -> bool:
    """Return whether a requested condition is inside the *qualified* scope.

    ``exact_points`` deliberately permits no interpolation.  ``anchor_envelope``
    is issued only after every policy anchor has passed and therefore permits
    interpolation inside the requested Mach intervals and alpha range.  The
    legacy range-only representation is retained for old 9.1 certificates.
    """
    coverage_kind = scope.get("coverage_kind")
    if coverage_kind == "exact_points":
        points = scope.get("points", [])
        if mach is None and alpha_deg is None:
            return bool(points)
        return any(_point_matches(point, mach, alpha_deg) for point in points)
    if coverage_kind == "none":
        return mach is None and alpha_deg is None

    if mach is not None:
        if not math.isfinite(float(mach)):
            return False
        intervals = scope.get("mach_intervals", [])
        if not any(float(lo) <= float(mach) <= float(hi) for lo, hi in intervals):
            return False
    if alpha_deg is not None:
        alpha_range = scope.get("alpha_deg", [])
        if len(alpha_range) != 2 or not float(alpha_range[0]) <= float(alpha_deg) <= float(alpha_range[1]):
            return False
    return True


def _normalise_sha256(value: object) -> str | None:
    text = str(value or "").strip().lower()
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        return None
    return text


def resolve_certificate_artifact(
    certificate_path: Path,
    record: dict,
    *,
    label: str = "Артефакт сертификата",
) -> Path:
    """Resolve a schema-1.1 artifact without trusting its captured host path.

    Absolute ``path`` values remain provenance-only metadata.  Every sealed
    artifact consumed by a 1.1 verifier must name a location below the
    certificate directory through ``relative_path``.  Resolving before the
    containment check also blocks ``..`` and symlink escapes.
    """
    relative_text = record.get("relative_path") if isinstance(record, dict) else None
    if not isinstance(relative_text, str) or not relative_text.strip():
        raise ValueError(f"{label}: отсутствует обязательный relative_path")
    relative = Path(relative_text.strip())
    if relative.is_absolute() or relative.anchor or relative.drive:
        raise ValueError(f"{label}: relative_path не является относительным")
    root = certificate_path.resolve().parent
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise ValueError(f"{label}: relative_path выходит за каталог сертификата")
    return candidate


def _verify_portable_artifact(
    certificate_path: Path,
    record: dict,
    *,
    label: str,
    errors: list[str],
) -> None:
    if not isinstance(record, dict):
        errors.append(f"{label}: запись отсутствует или повреждена")
        return
    expected = _normalise_sha256(record.get("sha256"))
    if expected is None:
        errors.append(f"{label}: отсутствует корректный sha256")
        return
    try:
        candidate = resolve_certificate_artifact(
            certificate_path, record, label=label
        )
    except ValueError as exc:
        errors.append(str(exc))
        return
    if not candidate.is_file() or sha256_file(candidate) != expected:
        errors.append(f"{label}: файл был изменён, удалён или не соответствует sha256")


def verify_certificate(
    certificate_path: Path,
    *,
    master_path: Path | None = None,
    backend: str | None = None,
    solver_versions: dict | None = None,
    runtime_executables: dict[str, Path] | None = None,
    mach: float | None = None,
    alpha_deg: float | None = None,
    scenario_id: str | None = None,
    scenario: dict | None = None,
    scenario_fingerprint: str | None = None,
) -> dict:
    certificate = json.loads(certificate_path.read_text(encoding="utf-8"))
    errors = certificate_integrity_errors(certificate)

    if certificate.get("run_status") != "complete":
        errors.append("Сертификационный прогон не имеет статуса complete")
    if certificate.get("verdict") not in PASS_VERDICTS:
        errors.append("Сертификат не содержит положительный итоговый вердикт")

    master_record = certificate.get("master", {})
    flags = certificate.get("flags", {})
    master_before = (
        _normalise_sha256(master_record.get("sha256_before"))
        if isinstance(master_record, dict) else None
    )
    master_after = (
        _normalise_sha256(master_record.get("sha256_after"))
        if isinstance(master_record, dict) else None
    )
    if not isinstance(master_record, dict) or master_record.get("unchanged") is not True:
        errors.append("Сертификат не подтверждает неизменность MASTER")
    if not isinstance(flags, dict) or flags.get("master_unchanged") is not True:
        errors.append("Флаг неизменности MASTER отсутствует или сброшен")
    if not master_before or not master_after:
        errors.append("Сертификат не содержит корректные SHA-256 MASTER до и после")
    elif master_before != master_after:
        errors.append("Хэши MASTER до и после сертификации не совпадают")

    if master_path is not None:
        if not master_path.is_file():
            errors.append("Указанный MASTER отсутствует")
        elif sha256_file(master_path) != master_before:
            errors.append("MASTER не соответствует сертификату")

    findings = certificate.get("findings", [])
    if not isinstance(findings, list):
        errors.append("Список диагностических findings повреждён")
        findings = []
    global_blockers = [item for item in findings if _finding_blocks_backend(item, None)]
    if global_blockers:
        errors.append("Сертификат содержит глобальные блокирующие замечания")

    corrective_plan = certificate.get("corrective_action_plan")
    if corrective_plan is not None:
        errors.extend(corrective_action_plan_errors(corrective_plan))

    backends = certificate.get("backends", {})
    if not isinstance(backends, dict):
        errors.append("Раздел backends отсутствует или повреждён")
        backends = {}

    # Recompute the minimum cross-field eligibility invariants rather than
    # trusting a single persisted boolean from the producer.
    vspaero_item = backends.get("vspaero")
    vspaero_eligible = isinstance(vspaero_item, dict) and vspaero_item.get("eligible") is True
    solver_eligible = isinstance(flags, dict) and flags.get("solver_eligible") is True
    branch = certificate.get("certification_branch", "combined")
    primary = certificate.get("primary_backend", "vspaero")
    if branch not in {"combined", "vspaero", "machline_open_nozzle"}:
        errors.append("Неизвестная ветка сертификации")
    if primary != ("machline" if branch == "machline_open_nozzle" else "vspaero"):
        errors.append("Основной решатель не согласован с веткой сертификации")
    if branch == "machline_open_nozzle":
        ml = backends.get("machline", {})
        if not isinstance(ml, dict):
            ml = {}
        if not solver_eligible or ml.get("eligible") is not True:
            errors.append("Независимая ветка не подтверждает пригодность MachLine")
        if any(isinstance(backends.get(name), dict) and backends[name].get("eligible") is True
               for name in ("vspaero", "hybrid", "parasite_drag")):
            errors.append("Допуск независимой ветки MachLine нельзя переносить на другие backends")
        contract = ml.get("output_contract") or {}
        outlets = contract.get("declared_open_nozzles") or {}
        if (contract.get("topology_mode") != "open_nozzle" or outlets.get("valid") is not True
                or not outlets.get("outlets") or outlets.get("errors")
                or contract.get("standalone_total_force_eligible") is not False):
            errors.append("Нет проверенного ограниченного контракта открытых сопел")
        qualification = ml.get("qualification") or {}
        probes = qualification.get("probes") or []
        limits = qualification.get("limits") or {}
        valid_probes = bool(probes) and qualification.get("valid") is True
        try:
            for p in probes:
                valid_probes &= p.get("valid") is True and not p.get("errors") and p.get("solver_status_code") == 0
                valid_probes &= p.get("superinclined_panels") == 0
                valid_probes &= math.isfinite(float(p["mach"])) and float(p["mach"]) > 1
                valid_probes &= math.isfinite(float(p["alpha_deg"]))
                for key in ("residual_norm", "residual_max"):
                    value, limit = float(p[key]), float(limits[key])
                    valid_probes &= math.isfinite(value) and math.isfinite(limit) and 0 <= value <= limit
            qualified = ml.get("qualified_scope") or {}
            actual = {(float(p["mach"]), float(p["alpha_deg"])) for p in probes}
            declared = {(float(p["mach"]), float(p["alpha_deg"])) for p in qualified.get("points", [])}
            valid_probes &= qualified.get("coverage_kind") == "exact_points" and actual == declared
        except (ValueError, TypeError, KeyError):
            valid_probes = False
        if not valid_probes:
            errors.append("Открытое сопло не имеет достоверных проб в заявленных точках")
        for name in ("force_integration_mask", "audit_geometry"):
            _verify_portable_artifact(certificate_path, ml.get(name), label=name, errors=errors)
    else:
        if not isinstance(vspaero_item, dict):
            errors.append("Положительный сертификат не содержит backend VSPAERO")
        if solver_eligible != vspaero_eligible:
            errors.append("Флаг solver_eligible не согласован с backend VSPAERO")
        if not vspaero_eligible:
            errors.append("Положительный сертификат не подтверждает пригодность VSPAERO")
        if branch == "vspaero" and backends.get("machline", {}).get("eligible") is True:
            errors.append("Ветка VSPAERO не может выдавать допуск MachLine")
    hybrid_item = backends.get("hybrid")
    if (
        isinstance(hybrid_item, dict)
        and hybrid_item.get("eligible") is True
        and not vspaero_eligible
    ):
        errors.append("Backend hybrid не может быть пригоден без VSPAERO")
    substitution_required = bool(
        isinstance(flags, dict) and flags.get("hybrid_substitution_required")
    )
    if substitution_required:
        replacement_contract = (
            hybrid_item.get("replacement_contract", {})
            if isinstance(hybrid_item, dict) else {}
        )
        if (
            not isinstance(replacement_contract, dict)
            or replacement_contract.get("schema")
            != "repairmach.replacement-contract/1.0"
            or replacement_contract.get("required") is not True
            or not isinstance(replacement_contract.get("requirements"), list)
            or not replacement_contract.get("requirements")
        ):
            errors.append(
                "Требуемые гибридные замены не имеют проверяемого покомпонентного контракта"
            )
        if isinstance(hybrid_item, dict) and hybrid_item.get("eligible") is True:
            errors.append(
                "Backend hybrid не может быть безусловно пригоден при обязательных заменах"
            )
    machline_item = backends.get("machline", {})
    base_drag_required = bool(
        isinstance(machline_item, dict)
        and machline_item.get("output_contract", {}).get(
            "base_drag_replacement_required"
        )
    )
    if (
        base_drag_required
        and isinstance(hybrid_item, dict)
        and hybrid_item.get("eligible") is True
    ):
        errors.append(
            "Backend hybrid ошибочно допущен без обязательного донного сопротивления"
        )

    for backend_name, item in backends.items():
        if not isinstance(item, dict) or item.get("eligible") is not True:
            continue
        blockers = item.get("blockers")
        if not isinstance(blockers, list):
            errors.append(f"Backend {backend_name} не фиксирует список blockers")
        elif blockers:
            errors.append(f"Backend {backend_name} помечен пригодным при наличии blockers")
        if any(_finding_blocks_backend(finding, backend_name) for finding in findings):
            errors.append(f"Backend {backend_name} помечен пригодным при блокирующей диагностике")

    backend_item = None
    if backend is not None:
        backend_item = backends.get(backend)
        if not isinstance(backend_item, dict) or backend_item.get("eligible") is not True:
            errors.append(f"Backend {backend} не сертифицирован")
        elif not isinstance(backend_item.get("blockers"), list):
            errors.append(f"Backend {backend} не содержит проверяемый список blockers")
        elif backend_item.get("blockers"):
            errors.append(f"Backend {backend} содержит блокирующие замечания")
        if any(_finding_blocks_backend(finding, backend) for finding in findings):
            errors.append(f"Backend {backend} содержит блокирующую диагностику")
    certified_solvers = certificate.get("software", {}).get("solvers", {})
    required_solvers: set[str] = set(BACKEND_SOLVERS.get(backend, ()))
    if backend is None:
        for backend_name, item in backends.items():
            if isinstance(item, dict) and item.get("eligible") is True:
                required_solvers.update(BACKEND_SOLVERS.get(backend_name, ()))
    if not isinstance(certified_solvers, dict):
        errors.append("Раздел software.solvers отсутствует или повреждён")
        certified_solvers = {}
    if runtime_executables is not None and not isinstance(runtime_executables, dict):
        errors.append("runtime_executables должен быть словарём путей решателей")
        runtime_executables = {}
    checked_runtime_executables: list[str] = []
    for solver_name in sorted(required_solvers):
        solver = certified_solvers.get(solver_name)
        expected_hash = (
            _normalise_sha256(solver.get("sha256")) if isinstance(solver, dict) else None
        )
        if expected_hash is None:
            errors.append(f"Сертификат не содержит полный отпечаток решателя {solver_name}")
            continue
        # In schema 1.1 the captured absolute executable path is provenance,
        # not a runtime authority.  A caller that is about to run a solver
        # supplies the actual executable explicitly and it is checked here.
        if runtime_executables is not None:
            runtime_value = runtime_executables.get(solver_name)
            runtime_path = Path(runtime_value) if runtime_value else None
            if runtime_path is None or not runtime_path.is_file():
                errors.append(f"Исполняемый файл {solver_name} для текущего запуска отсутствует")
            elif sha256_file(runtime_path) != expected_hash:
                errors.append(f"Исполняемый файл {solver_name} для текущего запуска изменён")
            else:
                checked_runtime_executables.append(solver_name)
    if solver_versions:
        certified_versions = certificate.get("software", {}).get("solvers", {})
        for name, version in solver_versions.items():
            certified = certified_versions.get(name)
            certified_version = certified.get("version") if isinstance(certified, dict) else certified
            if certified_version != version:
                errors.append(f"Версия {name} изменилась")
    qualified_scope = (
        backend_item.get("qualified_scope", {})
        if isinstance(backend_item, dict)
        else certificate.get("qualified_scope", certificate.get("scope", {}))
    )
    if not _scope_contains(qualified_scope, mach, alpha_deg):
        errors.append("Запрошенный режим находится вне области сертификата")
    current_scenario_fingerprint = None
    if scenario is not None:
        if not isinstance(scenario, dict):
            errors.append("Текущий сценарий должен быть словарём")
        else:
            embedded_id = str(scenario.get("id", "")).strip()
            if not embedded_id:
                errors.append("Текущий сценарий не содержит id")
            elif scenario_id is None:
                scenario_id = embedded_id
            elif embedded_id != scenario_id:
                errors.append(
                    f"ID текущего сценария {embedded_id} не совпадает с запрошенным {scenario_id}"
                )
            current_scenario_fingerprint = sha256_payload(scenario)
    if scenario_fingerprint is not None:
        supplied_fingerprint = _normalise_sha256(scenario_fingerprint)
        if supplied_fingerprint is None:
            errors.append("Передан некорректный отпечаток текущего сценария")
        elif (
            current_scenario_fingerprint is not None
            and supplied_fingerprint != current_scenario_fingerprint
        ):
            errors.append("Содержимое сценария не соответствует переданному отпечатку")
        else:
            current_scenario_fingerprint = supplied_fingerprint
    if scenario_id is not None:
        scenario_map = certificate.get("scenario_eligibility", {})
        scenario_item = scenario_map.get(scenario_id) if isinstance(scenario_map, dict) else None
        eligible = certificate.get("eligible_scenarios", [])
        if isinstance(scenario_item, dict):
            scenario_ok = bool(scenario_item.get("eligible", False))
        else:
            scenario_ok = scenario_id in eligible
        if not scenario_ok:
            errors.append(f"Сценарий {scenario_id} не разрешён сертификатом")
        certified_scenario_fingerprint = (
            _normalise_sha256(scenario_item.get("scenario_sha256"))
            if isinstance(scenario_item, dict) else None
        )
        if certified_scenario_fingerprint is None:
            errors.append(
                f"Сертификат не содержит корректный отпечаток сценария {scenario_id}"
            )
        if current_scenario_fingerprint is None:
            errors.append(
                f"Для проверки сценария {scenario_id} не передано его текущее содержимое или отпечаток"
            )
        elif (
            certified_scenario_fingerprint is not None
            and current_scenario_fingerprint != certified_scenario_fingerprint
        ):
            errors.append(
                f"Сценарий {scenario_id} изменён после сертификации"
            )

    evidence_files = certificate.get("evidence_files", [])
    if not isinstance(evidence_files, list):
        errors.append("Раздел evidence_files отсутствует или повреждён")
        evidence_files = []
    for index, evidence in enumerate(evidence_files):
        role = evidence.get("role", f"evidence_{index}") if isinstance(evidence, dict) else f"evidence_{index}"
        _verify_portable_artifact(
            certificate_path,
            evidence,
            label=f"Файл доказательства {role}",
            errors=errors,
        )

    twins = certificate.get("twins", {})
    if not isinstance(twins, dict):
        errors.append("Раздел twins отсутствует или повреждён")
        twins = {}
    for name, item in twins.items():
        _verify_portable_artifact(
            certificate_path,
            item,
            label=f"Расчётный двойник {name}",
            errors=errors,
        )

    # Solver geometry is a separate sealed binding: a twin may be valid while
    # an eligible backend accidentally points at another mesh level or TRI.
    for backend_name, item in backends.items():
        if (
            backend_name not in BACKEND_SOLVERS
            or not isinstance(item, dict)
            or item.get("eligible") is not True
        ):
            continue
        _verify_portable_artifact(
            certificate_path,
            item.get("solver_geometry"),
            label=f"Расчётная геометрия backend {backend_name}",
            errors=errors,
        )
    return {
        "valid": not errors,
        "certificate_id": certificate.get("certificate_id"),
        "verdict": certificate.get("verdict"),
        "runtime_executables_checked": checked_runtime_executables,
        "errors": errors,
    }


def write_certificate_report(path: Path, certificate: dict) -> None:
    findings = certificate.get("findings", [])
    flags = certificate.get("flags", {})
    lines = [
        "# Сертификат геометрии RepairMach",
        "",
        f"- ID: `{certificate.get('certificate_id')}`",
        f"- Методика: `{certificate.get('method_version')}`",
        f"- Вердикт: **{certificate.get('verdict')}**",
        f"- Создан: {certificate.get('created_at')}",
        f"- MASTER: `{certificate.get('master', {}).get('sha256_before')}`",
        f"- MASTER неизменён: **{'да' if flags.get('master_unchanged') else 'нет'}**",
        f"- Ветка: `{certificate.get('certification_branch', 'combined')}`; основной решатель: `{certificate.get('primary_backend', 'vspaero')}`",
        f"- Пригодность для решателя: **{'да' if flags.get('solver_eligible') else 'нет'}**",
        f"- Представлена полная геометрия: **{'да' if flags.get('full_geometry_represented') else 'нет'}**",
        f"- Требуется гибридная замена: **{'да' if flags.get('hybrid_substitution_required') else 'нет'}**",
        "",
        "Сертификат подтверждает прослеживаемость и пригодность геометрии для указанной постановки. "
        "Он не подтверждает аэродинамическую точность и не заменяет сеточную или экспериментальную верификацию.",
        "",
        "## Область действия",
        "",
        f"- Запрошенный Mach: `{certificate.get('requested_scope', certificate.get('scope', {})).get('mach_intervals')}`",
        f"- Запрошенный alpha: `{certificate.get('requested_scope', certificate.get('scope', {})).get('alpha_deg')}` град",
        f"- Квалификационный профиль: `{certificate.get('qualification', {}).get('profile')}`",
        f"- Подтверждённых опорных точек: `{len(certificate.get('qualification', {}).get('anchors', []))}`",
        "",
        "## Backends",
        "",
        "| Backend | Пригоден | Постановка | Подтверждённая область |",
        "|---|---:|---|---|",
    ]
    for name, item in certificate.get("backends", {}).items():
        lines.append(
            f"| {name} | {'да' if item.get('eligible') else 'нет'} | {item.get('mode', '')} | "
            f"{item.get('qualified_scope', {}).get('coverage_kind', 'none')} |"
        )
    replacement_contract = (
        certificate.get("backends", {}).get("hybrid", {})
        .get("replacement_contract", {})
    )
    if isinstance(replacement_contract, dict) and replacement_contract.get("required"):
        lines.extend([
            "",
            "## Контракт замещающих вкладов",
            "",
            "Сертификатор различает наличие расчётного файла и реальное "
            "покомпонентное покрытие. Источник может заменить исключённую деталь "
            "только когда эта деталь присутствует в запечатанной геометрии backend.",
            "",
            "| Компонент | Обязательный метод | Возможность backend | Основание |",
            "|---|---|---:|---|",
        ])
        for requirement in replacement_contract.get("requirements", []):
            lines.append(
                f"| {requirement.get('component', '')} | "
                f"{requirement.get('method', '')} | "
                f"{'да' if requirement.get('backend_capability_available') else 'нет'} | "
                f"{requirement.get('reason', '')} |"
            )
        if replacement_contract.get("unavailable_requirements"):
            lines.extend([
                "",
                "Гибрид нельзя считать полным, пока отсутствующие покомпонентные "
                "вклады не будут получены отдельным сертифицированным источником.",
            ])
    tri = certificate.get("tri", {})
    if isinstance(tri, dict) and tri.get("requested"):
        final = tri.get("final", {})
        audit_final = tri.get("audit_final", {})
        lines.extend([
            "",
            "## Численный двойник MachLine",
            "",
            "Полная составная сетка используется для аудита всей геометрии. "
            "В решатель передаётся только водонепроницаемая толстотельная часть; "
            "тонкие Wing/GO/VO остаются в контуре VSPAERO.",
            "",
            f"- Геометрический допуск: **{'да' if tri.get('geometry_eligible') else 'нет'}**",
            f"- Маскированное давление/волна для гибрида: **{'да' if tri.get('hybrid_pressure_wave_eligible') else 'нет'}**",
            f"- Самостоятельная полная сила без донной поправки: **{'да' if tri.get('standalone_total_force_eligible') else 'нет'}**",
            f"- Полная аудиторская сетка: `{audit_final.get('faces', 0)}` панелей",
            f"- Сетка решателя: `{final.get('faces', 0)}` панелей; "
            f"watertight=`{final.get('watertight')}`",
            f"- Ограниченный ремонт: `{tri.get('repair_count', 0)}` из "
            f"`{tri.get('repair_budget', 0)}` разрешённых панелей",
        ])
        closure_records = (
            tri.get("repairs", {}).get("downstream_axial_closure", {}).get("closures", [])
        )
        if closure_records:
            lines.extend([
                "",
                "### Численное кормовое замыкание",
                "",
                "| Исходный компонент | Удалено панелей | Площадь/Sref | Длина/cref | Новый компонент |",
                "|---|---:|---:|---:|---:|",
            ])
            for closure in closure_records:
                lines.append(
                    f"| {closure.get('source_component_name', '')} | "
                    f"{closure.get('removed_cap_faces', 0)} | "
                    f"{100.0 * float(closure.get('removed_cap_area_over_sref', 0.0)):.3f}% | "
                    f"{float(closure.get('extension_over_cref', 0.0)):.4f} | "
                    f"{closure.get('surrogate_component_id', '')} |"
                )
            lines.extend([
                "",
                "Служебный компонент участвует в решении потенциала, но исключается "
                "из покомпонентной суммы сил. Донное сопротивление должно добавляться "
                "отдельным полуэмпирическим слагаемым.",
            ])
        scans = tri.get("mach_scans", [])
        if scans:
            lines.extend([
                "",
                "### Геометрический Mach-критерий",
                "",
                "| M | alpha, град | Недопустимых панелей | Минимальный запас |",
                "|---:|---:|---:|---:|",
            ])
            for scan in scans:
                margin = scan.get("maximum_margin")
                lines.append(
                    f"| {scan.get('mach', '')} | {scan.get('alpha_deg', '')} | "
                    f"{scan.get('bad_panels', '')} | "
                    f"{'' if margin is None else f'{float(margin):.6g}'} |"
                )
        qualification = tri.get("machline_qualification", {})
        qualification_probes = qualification.get("probes", []) if isinstance(qualification, dict) else []
        if qualification_probes:
            limits = qualification.get("limits", {})
            lines.extend([
                "",
                "### Реальные пробы MachLine",
                "",
                f"Допуски: residual.norm ≤ `{limits.get('residual_norm')}`, "
                f"residual.max ≤ `{limits.get('residual_max')}`, "
                f"|CYspan| ≤ `{limits.get('abs_lateral_force')}`.",
                "",
                "| M | alpha, град | Результат | Итерации | residual.norm | residual.max | Cx pressure/wave | Cy pressure/wave | CYspan |",
                "|---:|---:|---|---:|---:|---:|---:|---:|---:|",
            ])
            for probe in qualification_probes:
                wind = probe.get("masked_wind_axes", {})
                lines.append(
                    f"| {probe.get('mach', '')} | {probe.get('alpha_deg', '')} | "
                    f"{'PASS' if probe.get('valid') else 'FAIL'} | "
                    f"{probe.get('iterations', '')} | "
                    f"{probe.get('residual_norm', '')} | {probe.get('residual_max', '')} | "
                    f"{wind.get('cd', '')} | {wind.get('cl', '')} | "
                    f"{wind.get('cy_span', '')} |"
                )
    lines.extend(["", "## Преобразования", ""])
    actions = certificate.get("transformations", {}).get("actions", [])
    if not actions:
        lines.append("Преобразования геометрии не потребовались.")
    else:
        for action in actions:
            lines.append(
                f"- `{action.get('reason_code')}` — {action.get('component')}: "
                f"{action.get('action')} ({action.get('before')} → {action.get('after')})"
            )
    lines.extend(["", "## Исключения", ""])
    exclusions = certificate.get("exclusions", [])
    if exclusions:
        for item in exclusions:
            lines.append(
                f"- {item.get('component', item.get('role', 'компонент'))}: "
                f"{item.get('reason', item.get('code', 'declared exclusion'))}"
            )
    else:
        lines.append("Нет.")
    lines.extend(["", "## Диагностика", ""])
    if findings:
        lines.extend([
            "| Код | Уровень | Компонент | Сообщение |",
            "|---|---|---|---|",
        ])
        for item in findings:
            message = str(item.get("message", "")).replace("|", "\\|")
            lines.append(
                f"| `{item.get('code')}` | {item.get('severity')} | "
                f"{item.get('component', '')} | {message} |"
            )
    else:
        lines.append("Замечаний нет.")

    corrective_plan = certificate.get("corrective_action_plan", {})
    if isinstance(corrective_plan, dict) and corrective_plan.get("schema"):
        summary = corrective_plan.get("summary", {})
        minimum_retest = corrective_plan.get("minimum_retest", {})
        backend_action_free = corrective_plan.get("backend_action_free", {})
        lines.extend([
            "",
            "## План дальнейших действий",
            "",
            f"- Состояние: **{corrective_plan.get('status', '')}**",
            f"- Можно выпускать расчёт без дополнительных действий: "
            f"**{'да' if corrective_plan.get('calculation_release_ready') else 'нет'}**",
            f"- Обязательных действий: `{summary.get('required_count', 0)}`; "
            f"блокирующих: `{summary.get('blocker_count', 0)}`; "
            f"автоматизируемых: `{summary.get('automatic_action_count', 0)}`",
            f"- Минимально повторить этапы: `{', '.join(minimum_retest.get('stages', [])) or 'не требуется'}`",
            f"- Повторно проверить backends: `{', '.join(minimum_retest.get('backends', [])) or 'не требуется'}`",
            "- Ветви без незакрытых действий: `"
            + ", ".join(
                f"{name}={'да' if ready else 'нет'}"
                for name, ready in backend_action_free.items()
            )
            + "`",
            f"- Новый сертификат геометрии: "
            f"**{'да' if minimum_retest.get('new_geometry_certificate_required') else 'нет'}**",
            "",
        ])
        items = corrective_plan.get("items", [])
        if items:
            lines.extend([
                "| Приоритет | ID | Владелец | Компонент | Причина | Маршрут | Действие | Минимальный повтор |",
                "|---:|---|---|---|---|---|---|---|",
            ])
            for item in items:
                action_text = str(item.get("action", "")).replace("|", "\\|")
                lines.append(
                    f"| {item.get('priority', '')} | `{item.get('id', '')}` | "
                    f"{item.get('owner', '')} | {item.get('component') or ''} | "
                    f"`{item.get('trigger', {}).get('code', '')}` | "
                    f"`{item.get('action_code', '')}` | {action_text} | "
                    f"{', '.join(item.get('stages', []))} |"
                )
        else:
            lines.append("Дополнительные действия не требуются.")

    failure_diagnostics = []
    probes = certificate.get("probes", {})
    if isinstance(probes, dict):
        for mode_name, qualification in probes.items():
            if not isinstance(qualification, dict):
                continue
            for anchor in qualification.get("anchors", []):
                diagnostics = anchor.get("failure_diagnostics")
                if isinstance(diagnostics, dict) and diagnostics.get("attempted"):
                    failure_diagnostics.append((mode_name, anchor, diagnostics))
    if failure_diagnostics:
        lines.extend([
            "",
            "## Автоматическая локализация нечислового отказа",
            "",
            "Диагностические двойники не являются допущенной расчётной геометрией.",
            "",
            "| Постановка | M | alpha | Сетка | Исключённая группа | Результат | CLtot | CDtot |",
            "|---|---:|---:|---|---|---|---:|---:|",
        ])
        for mode_name, anchor, diagnostics in failure_diagnostics:
            condition = diagnostics.get("condition", {})
            for variant in diagnostics.get("variants", []):
                probe = variant.get("probe") or {}
                values = probe.get("values") or {}
                restored = bool(variant.get("restored_finite_solution"))
                lines.append(
                    f"| {mode_name} | {condition.get('mach', anchor.get('mach'))} | "
                    f"{condition.get('alpha_deg', anchor.get('alpha_deg'))} | "
                    f"{condition.get('mesh_level', '')} | "
                    f"{variant.get('excluded_semantic_group', '')} | "
                    f"{'конечное решение восстановлено' if restored else 'отказ сохранился'} | "
                    f"{values.get('CLtot', '')} | {values.get('CDtot', '')} |"
                )
        restored_groups = sorted({
            str(group)
            for _mode_name, _anchor, diagnostics in failure_diagnostics
            for group in diagnostics.get(
                "groups_whose_exclusion_restored_solution", []
            )
        })
        if restored_groups:
            lines.extend([
                "",
                "Локализованные группы: "
                + ", ".join(f"`{group}`" for group in restored_groups)
                + ". Следует исправить их форму, Sets или сопряжения в MASTER либо "
                "объявить физически обоснованную гибридную замену и выпустить новый сертификат.",
                "",
                "Нельзя выбирать единственную сетку, на которой отказ случайно исчез: "
                "допуск выдаётся только по полной сеточной лестнице.",
            ])
    plateau_anchors = []
    if isinstance(probes, dict):
        for mode_name, qualification in probes.items():
            if not isinstance(qualification, dict):
                continue
            for anchor in qualification.get("anchors", []):
                convergence = anchor.get("convergence", {})
                if convergence.get("local_span_plateau_attempted"):
                    plateau_anchors.append((mode_name, anchor, convergence))
    if plateau_anchors:
        lines.extend([
            "",
            "## Локальная сеточная полка после отказа плотной сетки",
            "",
            "Плотная сетка с нечисловым результатом сохранена как отклонённое "
            "доказательство. Допуск основан только на трёх валидных, повторно "
            "прочитанных уровнях непосредственно ниже границы устойчивости; "
            "численный допуск не изменялся.",
            "",
            "| Постановка | M | alpha | Принятые уровни | CL, последнее изменение | CD, последнее изменение |",
            "|---|---:|---:|---|---:|---:|",
        ])
        for mode_name, anchor, convergence in plateau_anchors:
            quantities = convergence.get("quantities", {})
            cl_change = quantities.get("CLtot", {}).get("medium_fine_relative")
            cd_change = quantities.get("CDtot", {}).get("medium_fine_relative")
            lines.append(
                f"| {mode_name} | {anchor.get('mach')} | {anchor.get('alpha_deg')} | "
                f"{', '.join(convergence.get('level_sequence', []))} | "
                f"{'' if cl_change is None else f'{100.0 * float(cl_change):.3f}%'} | "
                f"{'' if cd_change is None else f'{100.0 * float(cd_change):.3f}%'} |"
            )
    recovered_probes = []
    if isinstance(probes, dict):
        for mode_name, qualification in probes.items():
            if not isinstance(qualification, dict):
                continue
            for anchor in qualification.get("anchors", []):
                for probe in anchor.get("probes", []):
                    recovery = probe.get("numerical_recovery", {})
                    if isinstance(recovery, dict) and recovery.get("attempted"):
                        recovered_probes.append((mode_name, anchor, probe, recovery))
    if recovered_probes:
        lines.extend([
            "",
            "## Усиленное подтверждение численной повторяемости VSPAERO",
            "",
            "Исходный прогон с неприемлемой локальной невязкой сохранён как "
            "отклонённое доказательство. Сертификатор выполнил два независимых "
            "однопоточных повтора с усиленными настройками и не менял допуск "
            "сеточной сходимости.",
            "",
            "| Постановка | M | alpha | Сетка | Повторов | Повторяемость | CL, расхождение | CD, расхождение |",
            "|---|---:|---:|---|---:|---|---:|---:|",
        ])
        for mode_name, anchor, probe, recovery in recovered_probes:
            repeatability = recovery.get("repeatability", {})
            quantities = repeatability.get("quantities", {})
            cl_change = quantities.get("CLtot", {}).get("relative_delta")
            cd_change = quantities.get("CDtot", {}).get("relative_delta")
            lines.append(
                f"| {mode_name} | {anchor.get('mach')} | {anchor.get('alpha_deg')} | "
                f"{probe.get('level', '')} | {len(recovery.get('repeat_probes', []))} | "
                f"{'подтверждена' if recovery.get('valid') else 'не подтверждена'} | "
                f"{'' if cl_change is None else f'{100.0 * float(cl_change):.4f}%'} | "
                f"{'' if cd_change is None else f'{100.0 * float(cd_change):.4f}%'} |"
            )
    lines.extend([
        "",
        "## Артефакты",
        "",
        f"- Семантический аудит: `{certificate.get('artifacts', {}).get('semantic_audit')}`",
        f"- Исходная диагностика: `{certificate.get('artifacts', {}).get('native_diagnostics')}`",
        f"- План: `{certificate.get('artifacts', {}).get('transformation_plan')}`",
        f"- Отклонения: `{certificate.get('artifacts', {}).get('geometry_deltas')}`",
        f"- План дальнейших действий: `{certificate.get('artifacts', {}).get('corrective_action_plan')}`",
        "",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def emit_certificate(run_dir: Path, payload: dict) -> tuple[dict, Path, Path]:
    payload = deepcopy(payload)
    payload.setdefault("schema", CERTIFICATE_SCHEMA)
    payload.setdefault("created_at", datetime.now().astimezone().isoformat(timespec="seconds"))
    certificate = seal_certificate(payload)
    json_path = run_dir / "certificate.json"
    markdown_path = run_dir / "certificate.md"
    write_json(json_path, certificate)
    write_certificate_report(markdown_path, certificate)
    return certificate, json_path, markdown_path
