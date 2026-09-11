#!/usr/bin/env python3
"""Versioned calculation-scenario catalog for RepairMach 9.1."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime
import math
from pathlib import Path


READY_EXECUTIONS = {
    "tri_diagnostics",
    "vspaero_study",
    "geometry_preflight",
    "geometry_certification",
    "geometry_corrective_action",
}
POINT_EXECUTIONS = {"sweep", "independent_alpha", "independent_mach_alpha"}
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
            _validate_tail_policy(scenario_id, scenario)
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
        point_execution = case.get("point_execution", "sweep")
        if point_execution not in POINT_EXECUTIONS:
            raise ValueError(
                f"Сценарий {scenario_id}, case {case.get('name')}: "
                f"неизвестный point_execution {point_execution!r}"
            )


def expand_vspaero_cases(cases: list[dict]) -> list[dict]:
    """Expand points that must run as independent solver processes.

    A combined VSPAERO alpha sweep may carry an unstable solution from one
    angle to the next near the lower supersonic boundary.  The
    ``independent_alpha`` mode preserves the original Mach grid but starts a
    fresh solver process for every requested angle.  The stricter
    ``independent_mach_alpha`` mode also isolates every Mach value, preventing
    a singular point (notably around M=1.4–1.5) from poisoning the remaining
    series.  Parent metadata remains in every expanded case so the resulting
    manifests are auditable.
    """
    expanded: list[dict] = []
    for source in cases:
        case = deepcopy(source)
        mode = case.get("point_execution", "sweep")
        if mode == "sweep":
            case["point_execution"] = mode
            expanded.append(case)
            continue

        alpha_points = int(case["alpha_points"])
        alpha_start = float(case["alpha_start"])
        alpha_end = float(case["alpha_end"])
        if alpha_points == 1:
            alpha_values = [alpha_start]
        else:
            alpha_step = (alpha_end - alpha_start) / (alpha_points - 1)
            alpha_values = [alpha_start + index * alpha_step for index in range(alpha_points)]

        if mode == "independent_mach_alpha":
            mach_points = int(case["mach_points"])
            mach_start = float(case["mach_start"])
            mach_end = float(case["mach_end"])
            if mach_points == 1:
                mach_values = [mach_start]
            else:
                mach_step = (mach_end - mach_start) / (mach_points - 1)
                mach_values = [mach_start + index * mach_step for index in range(mach_points)]
        else:
            mach_values = [None]

        for mach in mach_values:
            for alpha in alpha_values:
                point = deepcopy(case)
                point["parent_case_name"] = case["name"]
                mach_token = "" if mach is None else f"_M{_number_token(mach)}"
                point["name"] = f"{case['name']}{mach_token}_A{_number_token(alpha)}"
                point["alpha_start"] = alpha
                point["alpha_end"] = alpha
                point["alpha_points"] = 1
                point["requested_alpha_step_deg"] = (
                    0.0 if alpha_points == 1 else (alpha_end - alpha_start) / (alpha_points - 1)
                )
                if mach is not None:
                    point["mach_start"] = mach
                    point["mach_end"] = mach
                    point["mach_points"] = 1
                    point["requested_mach_step"] = (
                        0.0 if mach_points == 1 else (mach_end - mach_start) / (mach_points - 1)
                    )
                expanded.append(point)
    return expanded


def _number_token(value: float) -> str:
    text = f"{float(value):.8g}".replace("-", "m").replace(".", "p")
    return text


def _validate_tail_policy(scenario_id: str, scenario: dict) -> None:
    policy = scenario.get("tail_incidence", "optional")
    if policy not in {"optional", "required", "fixed"}:
        raise ValueError(
            f"Сценарий {scenario_id}: tail_incidence должен быть optional, required или fixed"
        )
    if policy != "fixed":
        return
    name = scenario.get("tail_geometry_name")
    angle = scenario.get("tail_incidence_deg")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"Сценарий {scenario_id}: не задано имя фиксированного ГО")
    try:
        finite_angle = math.isfinite(float(angle))
    except (TypeError, ValueError):
        finite_angle = False
    if not finite_angle:
        raise ValueError(f"Сценарий {scenario_id}: задан некорректный угол ГО")


def scenario_by_id(catalog: dict, scenario_id: str) -> dict:
    for scenario in catalog["scenarios"]:
        if scenario["id"] == scenario_id:
            return deepcopy(scenario)
    raise KeyError(f"Сценарий не найден: {scenario_id}")


def component_name_policy(catalog: dict) -> dict:
    return deepcopy(catalog.get("component_naming", {}))


def validate_component_names(
    geometries: list[dict],
    policy: dict,
    *,
    allow_excluded_thick: bool = False,
) -> dict:
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
        thick_is_intentionally_excluded = allow_excluded_thick and required_set == 1
        if (
            required_set is not None
            and not thick_is_intentionally_excluded
            and not bool(item.get(f"IN_SET_{required_set}", False))
        ):
            errors.append(f"{name} должен находиться в Set_{required_set}")

    for name in sorted(required - exact_names):
        errors.append(f"Отсутствует обязательная геометрия с точным именем {name}")

    legacy_names = {
        "WingGeom": "Wing",
        "FuselageGeom": "Fuselage",
        "HorizontalTail": "GO",
        "VerticalTail": "VO",
        "Nacelle": "Gondola",
    }
    warnings = []
    for name in unknown:
        if name in legacy_names:
            warnings.append(
                f"Устаревшее имя {name}: после ручной проверки используйте {legacy_names[name]}"
            )
        else:
            warnings.append(
                f"Имя {name} не соответствует Wing/Fuselage/GO/VO/Gondola[_суффикс]"
            )
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
