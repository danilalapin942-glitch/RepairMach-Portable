#!/usr/bin/env python3
"""Versioned calculation-scenario catalog for RepairMach 9.1."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path


READY_EXECUTIONS = {"tri_diagnostics", "vspaero_study"}
PROTOCOL_EXECUTIONS = {
    "mesh_convergence_protocol",
    "night_hybrid_protocol",
    "verification_protocol",
}


def load_scenario_catalog(path: Path) -> dict:
    """Load and validate a scenario catalog without mutating it."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != "repairmach.calculation-scenarios/1.0":
        raise ValueError("Неизвестная схема каталога расчётных сценариев")
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("Каталог не содержит расчётных сценариев")

    seen: set[str] = set()
    for scenario in scenarios:
        scenario_id = scenario.get("id", "")
        if not scenario_id or scenario_id in seen:
            raise ValueError(f"Пустой или повторяющийся scenario id: {scenario_id!r}")
        seen.add(scenario_id)
        if not scenario.get("title"):
            raise ValueError(f"У сценария {scenario_id} нет названия")
        execution = scenario.get("execution")
        if execution not in READY_EXECUTIONS | PROTOCOL_EXECUTIONS:
            raise ValueError(f"У сценария {scenario_id} неизвестный execution: {execution}")
        availability = scenario.get("availability")
        expected = "ready" if execution in READY_EXECUTIONS else "protocol"
        if availability != expected:
            raise ValueError(
                f"Сценарий {scenario_id}: availability должен быть {expected}"
            )
        if execution == "vspaero_study":
            _validate_vspaero_cases(scenario_id, scenario.get("vspaero_cases"))
    return payload


def _validate_vspaero_cases(scenario_id: str, cases: object) -> None:
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"Сценарий {scenario_id} не содержит VSPAERO cases")
    required = {
        "name",
        "mach_start",
        "mach_end",
        "mach_points",
        "alpha_start",
        "alpha_end",
        "alpha_points",
    }
    for case in cases:
        missing = required - set(case)
        if missing:
            raise ValueError(
                f"Сценарий {scenario_id}, case {case.get('name')}: отсутствуют {sorted(missing)}"
            )
        if int(case["mach_points"]) < 1 or int(case["alpha_points"]) < 1:
            raise ValueError(f"Сценарий {scenario_id}: число точек должно быть положительным")


def scenario_by_id(catalog: dict, scenario_id: str) -> dict:
    for scenario in catalog["scenarios"]:
        if scenario["id"] == scenario_id:
            return deepcopy(scenario)
    raise KeyError(f"Сценарий не найден: {scenario_id}")


def component_name_policy(catalog: dict) -> dict:
    return deepcopy(catalog.get("component_naming", {}))


def validate_component_names(geometries: list[dict], policy: dict) -> dict:
    """Validate canonical OpenVSP names and their Set roles.

    Exact base names are used for the primary component. Additional mirrored
    or repeated components may use ``_<suffix>``.
    """
    bases = list(policy.get("canonical_bases", []))
    required = set(policy.get("required_exact", []))
    roles = policy.get("set_roles", {})
    recognized: list[dict] = []
    unknown: list[str] = []
    errors: list[str] = []
    exact_names = {str(item.get("NAME", "")) for item in geometries}

    for item in geometries:
        name = str(item.get("NAME", ""))
        base = next(
            (candidate for candidate in bases if name == candidate or name.startswith(candidate + "_")),
            None,
        )
        if base is None:
            unknown.append(name or "<без имени>")
            continue
        recognized.append({"name": name, "base": base})
        required_set = roles.get(base)
        if required_set is not None and not bool(item.get(f"IN_SET_{required_set}", False)):
            errors.append(f"{name} должен находиться в Set_{required_set}")

    for name in sorted(required - exact_names):
        errors.append(f"Отсутствует обязательная геометрия с точным именем {name}")

    warnings = [
        f"Имя {name} не соответствует Wing/Fuselage/GO/VO/Gondola[_суффикс]"
        for name in unknown
    ]
    return {
        "valid": not errors,
        "recognized": recognized,
        "unknown": unknown,
        "errors": errors,
        "warnings": warnings,
    }


def geometry_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_scenario_manifest(
    scenario: dict,
    *,
    repairmach_version: str,
    project_name: str,
    geometry_path: Path | None = None,
) -> dict:
    """Create a reproducible scenario record for execution or later continuation."""
    geometry = None
    if geometry_path is not None:
        geometry = {
            "path": str(geometry_path.resolve()),
            "sha256": geometry_sha256(geometry_path),
        }
    return {
        "schema": "repairmach.scenario-run/1.0",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "repairmach_version": repairmach_version,
        "project": project_name,
        "scenario": deepcopy(scenario),
        "geometry": geometry,
        "status": "prepared",
    }


def write_scenario_manifest(path: Path, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
