#!/usr/bin/env python3
"""Geometry certificate serialization, human report and validity checks."""

from __future__ import annotations

import json
import math
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from geometry_manifest import canonical_vsp3_sha256, sha256_file, sha256_payload, write_json
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

    backends = certificate.get("backends", {})
    if not isinstance(backends, dict):
        errors.append("Раздел backends отсутствует или повреждён")
        backends = {}

    # Recompute the minimum cross-field eligibility invariants rather than
    # trusting a single persisted boolean from the producer.
    vspaero_item = backends.get("vspaero")
    vspaero_eligible = isinstance(vspaero_item, dict) and vspaero_item.get("eligible") is True
    solver_eligible = isinstance(flags, dict) and flags.get("solver_eligible") is True
    if not isinstance(vspaero_item, dict):
        errors.append("Положительный сертификат не содержит backend VSPAERO")
    if solver_eligible != vspaero_eligible:
        errors.append("Флаг solver_eligible не согласован с backend VSPAERO")
    if not vspaero_eligible:
        errors.append("Положительный сертификат не подтверждает пригодность VSPAERO")
    hybrid_item = backends.get("hybrid")
    if (
        isinstance(hybrid_item, dict)
        and hybrid_item.get("eligible") is True
        and not vspaero_eligible
    ):
        errors.append("Backend hybrid не может быть пригоден без VSPAERO")

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
    lines.extend(["", "## Преобразования", ""])
    actions = certificate.get("transformations", {}).get("actions", [])
    if not actions:
        lines.append("Преобразования геометрии не потребовались (`PASS_NATIVE`).")
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
    lines.extend([
        "",
        "## Артефакты",
        "",
        f"- Семантический аудит: `{certificate.get('artifacts', {}).get('semantic_audit')}`",
        f"- Исходная диагностика: `{certificate.get('artifacts', {}).get('native_diagnostics')}`",
        f"- План: `{certificate.get('artifacts', {}).get('transformation_plan')}`",
        f"- Отклонения: `{certificate.get('artifacts', {}).get('geometry_deltas')}`",
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
