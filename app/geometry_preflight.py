#!/usr/bin/env python3
"""Fast, fail-closed geometry preflight before expensive solver certification.

The preflight opens an immutable snapshot through OpenVSP, audits semantic
names/Sets/reference values and seals the exact transformation plan.  It does
not create solver twins, meshes or aerodynamic solutions and never writes to
the MASTER model.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import re
import shutil
from pathlib import Path

from geometry_certification import _validate_scope
from geometry_manifest import (
    canonical_vsp3_sha256,
    inventory_model,
    sha256_file,
    sha256_payload,
    write_json,
)
from geometry_remediation import (
    build_corrective_action_plan,
    corrective_action_plan_errors,
)
from geometry_rules import effective_policy, load_geometry_policy, semantic_audit
from geometry_twins import build_transformation_plan, verify_plan


PREFLIGHT_SCHEMA = "repairmach.geometry-preflight/1.0"


def _normalise_reference(reference: dict) -> dict:
    return {
        "area": float(reference["area"]),
        "cref": float(reference.get("cref", reference.get("longitudinal_length"))),
        "bref": float(reference.get("bref", reference.get("lateral_length"))),
        "center": [float(value) for value in reference["center"]],
    }


def _unique_run_dir(output_root: Path, project_name: str, model_stem: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    project = re.sub(r"[^A-Za-z0-9_.-]+", "_", project_name).strip("._") or "project"
    model = re.sub(r"[^A-Za-z0-9_.-]+", "_", model_stem).strip("._") or "model"
    token = sha256_payload({"project": project_name, "model": model_stem})[:8]
    base = Path(output_root) / f"{timestamp}_{project[:12]}_{model[:18]}_{token}"
    candidate = base
    index = 2
    while candidate.exists():
        candidate = Path(f"{base}_{index}")
        index += 1
    candidate.mkdir(parents=True)
    return candidate


def _seal(payload: dict) -> dict:
    sealed = deepcopy(payload)
    sealed.pop("preflight_fingerprint", None)
    sealed["preflight_fingerprint"] = sha256_payload(sealed)
    return sealed


def _write_report(path: Path, payload: dict) -> None:
    summary = payload["summary"]
    plan = payload["corrective_action_plan"]
    lines = [
        "# Быстрый предполётный допуск геометрии RepairMach",
        "",
        f"- Статус: **{payload['status']}**",
        f"- Полная сертификация разрешена: **{'да' if payload['full_certification_allowed'] else 'нет'}**",
        f"- MASTER неизменён: **{'да' if payload['master']['unchanged'] else 'нет'}**",
        f"- MASTER SHA-256: `{payload['master']['sha256']}`",
        f"- Компонентов: `{summary['component_count']}`",
        f"- Блокирующих дефектов: `{summary['blocker_count']}`",
        f"- Безопасных преобразований двойников: `{summary['planned_twin_actions']}`",
        "",
        "Проверка не запускала VSPAERO, MachLine, сеточную лестницу или расчёт АДХ. "
        "Она предназначена для раннего отказа до дорогой серии.",
        "",
        "## Компоненты",
        "",
        "| Имя | Тип | Семантика | Set 1 | Set 2 |",
        "|---|---|---|---:|---:|",
    ]
    recognized = {
        item.get("id"): item for item in payload.get("semantic_audit", {}).get("recognized_components", [])
    }
    excluded_names = {
        str(item.get("component", ""))
        for item in payload.get("semantic_audit", {}).get("declared_exclusions", [])
    }
    for component in payload.get("inventory", {}).get("components", []):
        semantic = recognized.get(component.get("id"), {})
        sets = component.get("sets", {})
        lines.append(
            f"| {component.get('name', '')} | {component.get('type', '')} | "
            f"{semantic.get('semantic_base', 'declared exclusion' if component.get('name') in excluded_names else '')} | "
            f"{bool(sets.get('1'))} | {bool(sets.get('2'))} |"
        )
    lines.extend(["", "## План действий", ""])
    if not plan.get("items"):
        lines.append("Дополнительные действия не требуются; можно запускать полную сертификацию.")
    else:
        lines.extend([
            "| Приоритет | ID | Владелец | Компонент | Действие |",
            "|---:|---|---|---|---|",
        ])
        for item in plan["items"]:
            action = str(item.get("action", "")).replace("|", "\\|")
            lines.append(
                f"| {item.get('priority', '')} | `{item.get('id', '')}` | "
                f"{item.get('owner', '')} | {item.get('component') or ''} | {action} |"
            )
    lines.extend([
        "",
        "## Следующий шаг",
        "",
        payload["next_step"],
        "",
        f"Отпечаток предполётной проверки: `{payload['preflight_fingerprint']}`",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def run_geometry_preflight(
    *,
    master_path: Path,
    output_root: Path,
    project_name: str,
    reference: dict,
    policy_path: Path,
    vspscript_executable: Path,
    scope_override: dict | None = None,
    policy_overrides: dict | None = None,
) -> dict:
    """Run the fast G0--G3 gate and return sealed JSON/Markdown artifacts."""
    master_path = Path(master_path).resolve()
    policy_path = Path(policy_path).resolve()
    vspscript_executable = Path(vspscript_executable).resolve()
    if not master_path.is_file() or master_path.suffix.lower() != ".vsp3":
        raise FileNotFoundError(f"Не найден MASTER VSP3: {master_path}")
    if not vspscript_executable.is_file():
        raise FileNotFoundError(f"Не найден vspscript: {vspscript_executable}")
    policy = effective_policy(load_geometry_policy(policy_path), policy_overrides)
    scope = _validate_scope(scope_override or policy["scope"])
    policy["scope"] = deepcopy(scope)
    reference = _normalise_reference(reference)

    run_dir = _unique_run_dir(output_root, project_name, master_path.stem)
    frozen = run_dir / "frozen_master.vsp3"
    shutil.copyfile(master_path, frozen)
    master_sha = sha256_file(master_path)
    if sha256_file(frozen) != master_sha:
        raise RuntimeError("Замороженная копия MASTER не совпадает с источником")

    policy_sha = sha256_payload(policy)
    reference_sha = sha256_payload(reference)
    scope_sha = sha256_payload(scope)
    write_json(run_dir / "policy_snapshot.json", policy)
    write_json(run_dir / "reference_snapshot.json", reference)
    write_json(run_dir / "scope_snapshot.json", scope)

    inventory, script_path, log_path = inventory_model(
        frozen,
        vspscript_executable=vspscript_executable,
        work_dir=run_dir / "inventory",
        timeout_seconds=float(policy["probes"]["openvsp_load"].get("timeout_seconds", 180)),
    )
    audit = semantic_audit(inventory, reference, policy)
    plan = build_transformation_plan(
        master_sha256=master_sha,
        policy_sha256=policy_sha,
        reference_sha256=reference_sha,
        scope_sha256=scope_sha,
        inventory=inventory,
        semantic_audit=audit,
        reference=reference,
        policy=policy,
    )
    plan_errors = verify_plan(
        plan,
        master_sha256=master_sha,
        policy_sha256=policy_sha,
        reference_sha256=reference_sha,
        scope_sha256=scope_sha,
    )
    findings = deepcopy(plan.get("findings", audit.get("findings", [])))
    blockers = [item for item in findings if item.get("severity") == "BLOCKER"]
    if not inventory.get("valid") and not any(item.get("code") == "GEO-FILE-001" for item in blockers):
        blockers.append({
            "code": "GEO-FILE-001",
            "severity": "BLOCKER",
            "scope": "master",
            "message": "OpenVSP не смог полностью инвентаризировать MASTER",
            "evidence": {"errors": inventory.get("errors", [])},
        })
        findings.append(blockers[-1])
    corrective = build_corrective_action_plan(findings=findings, verdict=None)
    plan_validation_errors = [
        error for error in plan_errors if error != "План содержит блокирующие дефекты"
    ]
    internal_errors = plan_validation_errors + corrective_action_plan_errors(corrective)
    allowed = bool(not blockers and not internal_errors and inventory.get("valid"))
    status = "READY_FOR_FULL_CERTIFICATION" if allowed else "OPERATOR_ACTION_REQUIRED"
    next_step = (
        "Запустите полную сертификацию G0–G8 на этом же MASTER и с теми же опорными величинами."
        if allowed
        else "Исправьте только перечисленные операторские причины в новом варианте MASTER или политике, затем повторите предполётную проверку."
    )
    payload = {
        "schema": PREFLIGHT_SCHEMA,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "project": project_name,
        "status": status,
        "full_certification_allowed": allowed,
        "master": {
            "source_path": str(master_path),
            "snapshot_path": str(frozen.resolve()),
            "snapshot_relative_path": "frozen_master.vsp3",
            "sha256": master_sha,
            "canonical_sha256": canonical_vsp3_sha256(master_path),
            "unchanged": sha256_file(master_path) == master_sha,
        },
        "bindings": {
            "policy_sha256": policy_sha,
            "reference_sha256": reference_sha,
            "scope_sha256": scope_sha,
            "plan_sha256": plan.get("plan_sha256"),
            "vspscript_sha256": sha256_file(vspscript_executable),
        },
        "reference": reference,
        "scope": scope,
        "inventory": inventory,
        "semantic_audit": audit,
        "transformation_plan": plan,
        "corrective_action_plan": corrective,
        "internal_errors": internal_errors,
        "summary": {
            "component_count": len(inventory.get("components", [])),
            "finding_count": len(findings),
            "blocker_count": len(blockers),
            "repairable_count": sum(1 for item in findings if item.get("severity") == "REPAIRABLE"),
            "planned_twin_actions": len(plan.get("actions", [])),
            "solver_runs_avoided": ["VSPAERO", "MachLine", "OpenVSP Parasite Drag"],
        },
        "artifacts": {
            "run_directory": str(run_dir.resolve()),
            "inventory_script": str(script_path.resolve()),
            "inventory_log": str(log_path.resolve()),
            "policy_snapshot": str((run_dir / "policy_snapshot.json").resolve()),
            "reference_snapshot": str((run_dir / "reference_snapshot.json").resolve()),
            "scope_snapshot": str((run_dir / "scope_snapshot.json").resolve()),
        },
        "next_step": next_step,
    }
    payload = _seal(payload)
    json_path = run_dir / "preflight.json"
    report_path = run_dir / "preflight.md"
    write_json(json_path, payload)
    _write_report(report_path, payload)
    return {
        "run_directory": run_dir,
        "preflight": payload,
        "preflight_path": json_path,
        "report_path": report_path,
    }


def verify_geometry_preflight(path: Path, *, current_master: Path | None = None) -> dict:
    """Verify the internal seal, snapshot and optional current MASTER binding."""
    path = Path(path).resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    errors: list[str] = []
    if payload.get("schema") != PREFLIGHT_SCHEMA:
        errors.append("Неизвестная схема предполётной проверки")
    expected = payload.get("preflight_fingerprint")
    unsealed = deepcopy(payload)
    unsealed.pop("preflight_fingerprint", None)
    if expected != sha256_payload(unsealed):
        errors.append("Нарушена цифровая целостность предполётной проверки")
    root = path.parent.resolve()
    relative = Path(str(payload.get("master", {}).get("snapshot_relative_path", "")))
    snapshot = (root / relative).resolve()
    try:
        snapshot.relative_to(root)
    except ValueError:
        errors.append("Путь замороженного MASTER выходит за каталог предполётной проверки")
        snapshot = Path()
    master_sha = payload.get("master", {}).get("sha256")
    if not snapshot.is_file() or sha256_file(snapshot) != master_sha:
        errors.append("Замороженный MASTER отсутствует или изменён")
    if current_master is not None:
        candidate = Path(current_master)
        if not candidate.is_file() or sha256_file(candidate) != master_sha:
            errors.append("Текущий MASTER не совпадает с предполётным допуском")
    errors.extend(corrective_action_plan_errors(payload.get("corrective_action_plan")))
    return {
        "valid": not errors,
        "status": payload.get("status"),
        "full_certification_allowed": bool(payload.get("full_certification_allowed")),
        "preflight_fingerprint": expected,
        "errors": errors,
    }
