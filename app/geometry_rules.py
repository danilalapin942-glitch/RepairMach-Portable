#!/usr/bin/env python3
"""Deterministic policy and semantic rules for VSP3 certification.

The module intentionally contains no solver calls.  Every decision is made
from an explicit policy plus an OpenVSP inventory, which keeps the PLAN stage
repeatable and straightforward to test.
"""

from __future__ import annotations

import json
import math
from copy import deepcopy
from pathlib import Path


POLICY_SCHEMA = "repairmach.geometry-certification-policy/1.0"
METHOD_VERSION = "RM91-GEOMETRY-CERT-1"

VERDICTS = {
    "PASS_NATIVE",
    "PASS_REGULARIZED",
    "PASS_WITH_DECLARED_EXCLUSIONS",
    "FAIL",
}
SEVERITIES = {"INFO", "WARNING", "REPAIRABLE", "BLOCKER"}

# These values are a persisted, machine-readable contract.  Do not silently
# accept aliases here: every downstream consumer must see the same spelling.
DECLARED_EXCLUSION_BACKENDS = ("vspaero", "hybrid")
DECLARED_EXCLUSION_REPLACEMENTS = {
    "machline_pressure_wave_all_points",
    "parasite_drag_subsonic",
    "not_physical",
}
MESH_LEVEL_KEYS = (
    "thin_tess_w",
    "thin_sect_tess_u",
    "thin_tess_u",
    "thick_tess_w",
    "thick_sect_tess_u",
    "thick_tess_u",
)


def canonical_json_bytes(payload: object) -> bytes:
    """Serialize JSON in the one canonical form used for fingerprints."""
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def load_geometry_policy(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    validate_geometry_policy(payload)
    return payload


def validate_geometry_policy(policy: dict) -> None:
    if policy.get("schema") != POLICY_SCHEMA:
        raise ValueError("Неизвестная схема политики сертификации геометрии")
    if policy.get("method_version") != METHOD_VERSION:
        raise ValueError("Неподдерживаемая версия методики сертификации геометрии")

    semantics = policy.get("semantics", {})
    roles = semantics.get("roles")
    if not isinstance(roles, dict) or not roles:
        raise ValueError("Политика не содержит семантических ролей")
    for name, role in roles.items():
        if not name or role not in {"thin", "thick"}:
            raise ValueError(f"Некорректная роль компонента {name!r}: {role!r}")
    aliases = semantics.get("aliases", {})
    if not isinstance(aliases, dict):
        raise ValueError("semantics.aliases должен быть объектом")
    unknown_alias_targets = set(aliases.values()) - set(roles)
    if unknown_alias_targets:
        raise ValueError(
            "Alias ссылается на неизвестную роль: " + ", ".join(sorted(unknown_alias_targets))
        )

    exclusions = semantics.get("declared_exclusions", [])
    if not isinstance(exclusions, list):
        raise ValueError("semantics.declared_exclusions должен быть списком")
    exclusion_components: set[str] = set()
    for index, exclusion in enumerate(exclusions):
        label = f"semantics.declared_exclusions[{index}]"
        if not isinstance(exclusion, dict):
            raise ValueError(f"{label} должен быть объектом")
        component = exclusion.get("component")
        if (
            not isinstance(component, str)
            or not component.strip()
            or component != component.strip()
        ):
            raise ValueError(f"{label}.component должен быть непустым точным именем")
        if component in exclusion_components:
            raise ValueError(f"Компонент {component!r} исключён более одного раза")
        exclusion_components.add(component)

        # The legacy singular ``backend`` and a string-valued ``backends``
        # were ambiguous (and set("vspaero") split the name into letters).
        if "backend" in exclusion:
            raise ValueError(f"{label}.backend запрещён; используйте список backends")
        backends = exclusion.get("backends")
        if not isinstance(backends, list) or not backends:
            raise ValueError(f"{label}.backends должен быть непустым списком")
        if any(not isinstance(value, str) for value in backends):
            raise ValueError(f"{label}.backends должен содержать только строки")
        if any(value != value.strip().lower() for value in backends):
            raise ValueError(f"{label}.backends должен содержать канонические значения")
        if len(backends) != len(set(backends)):
            raise ValueError(f"{label}.backends содержит повторные значения")
        unknown_backends = set(backends) - set(DECLARED_EXCLUSION_BACKENDS)
        if unknown_backends:
            raise ValueError(
                f"{label}.backends содержит неподдерживаемые значения: "
                + ", ".join(sorted(unknown_backends))
            )
        if backends != list(DECLARED_EXCLUSION_BACKENDS):
            raise ValueError(
                f"{label}.backends в версии 1 должен быть ровно "
                f"{list(DECLARED_EXCLUSION_BACKENDS)!r}"
            )

        reason = exclusion.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"{label}.reason должен быть непустой строкой")

        replacements = exclusion.get("replacement_required")
        if not isinstance(replacements, list) or not replacements:
            raise ValueError(
                f"{label}.replacement_required должен быть непустым списком"
            )
        if any(not isinstance(value, str) for value in replacements):
            raise ValueError(
                f"{label}.replacement_required должен содержать только строки"
            )
        if any(value != value.strip().lower() for value in replacements):
            raise ValueError(
                f"{label}.replacement_required должен содержать канонические значения"
            )
        if len(replacements) != len(set(replacements)):
            raise ValueError(f"{label}.replacement_required содержит повторы")
        unknown_replacements = set(replacements) - DECLARED_EXCLUSION_REPLACEMENTS
        if unknown_replacements:
            raise ValueError(
                f"{label}.replacement_required содержит неподдерживаемые значения: "
                + ", ".join(sorted(unknown_replacements))
            )
        if "not_physical" in replacements and len(replacements) != 1:
            raise ValueError(
                f"{label}.replacement_required: not_physical должен быть единственным значением"
            )

    sets = policy.get("sets", {})
    for key in ("thick_user_set", "thin_user_set", "empty_thick_user_set"):
        if not isinstance(sets.get(key), int) or sets[key] < 0:
            raise ValueError(f"Некорректный номер набора {key}")
    if sets["thick_user_set"] == sets["thin_user_set"]:
        raise ValueError("Толстые и тонкие поверхности не могут использовать один Set")

    scope = policy.get("scope", {})
    intervals = scope.get("mach_intervals", [])
    if not isinstance(intervals, list) or not intervals:
        raise ValueError("Политика не содержит области Mach")
    for interval in intervals:
        if not isinstance(interval, (list, tuple)) or len(interval) != 2:
            raise ValueError("Каждый Mach-интервал должен иметь две границы")
        lo, hi = float(interval[0]), float(interval[1])
        if not all(math.isfinite(value) for value in (lo, hi)) or lo < 0.0 or hi < lo:
            raise ValueError("Некорректный Mach-интервал политики")
    alpha_scope = scope.get("alpha_deg", [])
    if not isinstance(alpha_scope, (list, tuple)) or len(alpha_scope) != 2:
        raise ValueError("scope.alpha_deg должен содержать две границы")
    alpha_lo, alpha_hi = float(alpha_scope[0]), float(alpha_scope[1])
    if not all(math.isfinite(value) for value in (alpha_lo, alpha_hi)) or alpha_hi < alpha_lo:
        raise ValueError("Некорректный диапазон alpha политики")
    tail = scope.get("horizontal_tail", {})
    if tail.get("mode") == "fixed":
        if not str(tail.get("geometry_name", "")).strip():
            raise ValueError("Для фиксированного ГО требуется geometry_name")
        if not math.isfinite(float(tail.get("incidence_deg", math.nan))):
            raise ValueError("Угол установки фиксированного ГО должен быть конечным")

    regularization = policy.get("regularization", {})
    tip = regularization.get("terminal_chord", {})
    for key in ("zero_ratio", "floor_root_ratio", "max_area_delta_fraction"):
        value = float(tip.get(key, -1.0))
        if not math.isfinite(value) or value < 0.0:
            raise ValueError(f"Некорректный предел terminal_chord.{key}")
    if tip["floor_root_ratio"] <= tip["zero_ratio"]:
        raise ValueError("Минимальная законцовка должна быть больше порога нулевой хорды")

    levels = policy.get("mesh_levels", {})
    level_names = list(levels)
    allowed_level_orders = [
        ["coarse", "medium", "fine"],
        ["coarse", "medium", "fine", "extra_fine"],
        ["coarse", "medium", "fine", "extra_fine", "ultra_fine"],
    ]
    if level_names not in allowed_level_orders:
        raise ValueError(
            "mesh_levels должны быть заданы в порядке coarse, medium, fine "
            "с необязательными extra_fine и ultra_fine"
        )
    previous = None
    for level, values in levels.items():
        if not isinstance(values, dict):
            raise ValueError(f"mesh_levels.{level} должен быть объектом")
        missing = [key for key in MESH_LEVEL_KEYS if key not in values]
        if missing:
            raise ValueError(
                f"mesh_levels.{level} не содержит потребляемые параметры: "
                + ", ".join(missing)
            )
        signature_values = []
        for key in MESH_LEVEL_KEYS:
            value = values[key]
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"mesh_levels.{level}.{key} должен быть целым числом")
            signature_values.append(value)
        signature = tuple(signature_values)
        if min(signature) < 2:
            raise ValueError(f"Слишком грубые параметры сетки {level}")
        if previous and any(current <= old for current, old in zip(signature, previous)):
            raise ValueError("Лестница Coarse/Medium/Fine должна строго возрастать")
        previous = signature

    convergence = policy.get("convergence", {})
    tolerance = float(convergence.get("relative_tolerance", -1.0))
    if not 0.0 < tolerance < 1.0:
        raise ValueError("Допуск сеточной сходимости должен лежать между 0 и 1")
    oscillation_fraction = float(
        convergence.get("oscillation_significance_fraction", 0.10)
    )
    if not 0.0 < oscillation_fraction <= 1.0:
        raise ValueError(
            "Доля значимости осцилляции должна лежать между 0 и 1"
        )
    adaptive = convergence.get("adaptive_refinement", {})
    if adaptive:
        if not isinstance(adaptive.get("enabled", False), bool):
            raise ValueError("adaptive_refinement.enabled должен быть логическим")
        adaptive_level = str(adaptive.get("level", "extra_fine"))
        if adaptive.get("enabled") and adaptive_level not in levels:
            raise ValueError("Адаптивный уровень сетки отсутствует в mesh_levels")
        resolution_level = str(
            adaptive.get("oscillation_resolution_level", "ultra_fine")
        )
        if adaptive.get("enabled") and resolution_level not in levels:
            raise ValueError("Арбитражный уровень осцилляции отсутствует в mesh_levels")
        if adaptive.get("enabled") and resolution_level == adaptive_level:
            raise ValueError("Адаптивный и арбитражный уровни должны различаться")
        retry_limit = float(adaptive.get("max_initial_relative_for_retry", -1.0))
        if not tolerance < retry_limit < 1.0:
            raise ValueError(
                "Порог адаптивного уточнения должен быть больше основного допуска и меньше 1"
            )

    probe = policy.get("probes", {}).get("vspaero", {})
    probe_mach = float(probe.get("mach", math.nan))
    probe_alpha = float(probe.get("alpha_deg", math.nan))
    try:
        max_log10_l2_residual = float(probe["max_log10_l2_residual"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            "probes.vspaero.max_log10_l2_residual должен быть конечным числом"
        ) from exc
    if not math.isfinite(max_log10_l2_residual):
        raise ValueError(
            "probes.vspaero.max_log10_l2_residual должен быть конечным числом"
        )
    if not any(float(lo) <= probe_mach <= float(hi) for lo, hi in intervals):
        raise ValueError("Контрольный Mach VSPAERO вне области сертификата")
    if not alpha_lo <= probe_alpha <= alpha_hi:
        raise ValueError("Контрольный alpha VSPAERO вне области сертификата")
    profile_name = str(probe.get("qualification_profile", "screening_exact"))
    profiles = probe.get("qualification_profiles", {})
    if profiles:
        if profile_name not in profiles:
            raise ValueError(f"Неизвестный квалификационный профиль VSPAERO: {profile_name}")
        for name, profile in profiles.items():
            if profile.get("coverage_kind") not in {"exact_points", "anchor_envelope"}:
                raise ValueError(f"Некорректный coverage_kind профиля {name}")
            points = profile.get("points", [])
            if not isinstance(points, list) or not points:
                raise ValueError(f"Профиль {name} не содержит опорных точек")
            seen = set()
            for point in points:
                mach = float(point.get("mach", math.nan))
                alpha = float(point.get("alpha_deg", math.nan))
                boundary = str(point.get("engine_boundary", "model"))
                if boundary not in {"model", "to_face", "auto"}:
                    raise ValueError(f"Профиль {name} содержит неизвестный engine_boundary={boundary}")
                if not any(float(lo) <= mach <= float(hi) for lo, hi in intervals):
                    raise ValueError(f"Mach={mach:g} профиля {name} вне области сертификата")
                if not alpha_lo <= alpha <= alpha_hi:
                    raise ValueError(f"alpha={alpha:g} профиля {name} вне области сертификата")
                key = (mach, alpha, boundary)
                if key in seen:
                    raise ValueError(f"Профиль {name} содержит повторную опорную точку {key}")
                seen.add(key)


def effective_policy(base: dict, overrides: dict | None = None) -> dict:
    """Return a recursively merged policy and validate the result."""
    merged = deepcopy(base)
    _deep_merge(merged, overrides or {})
    validate_geometry_policy(merged)
    return merged


def _deep_merge(target: dict, source: dict) -> None:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = deepcopy(value)


def component_base(name: str, policy: dict) -> tuple[str | None, str]:
    """Resolve a component only by canonical name/suffix or explicit alias."""
    semantics = policy["semantics"]
    roles = semantics["roles"]
    if name in semantics.get("aliases", {}):
        return semantics["aliases"][name], "explicit_alias"
    for base in roles:
        if name == base:
            return base, "canonical"
        if name.startswith(base + "_"):
            return base, "canonical_suffix"
    return None, "unrecognized"


def finding(
    code: str,
    severity: str,
    message: str,
    *,
    scope: str = "master",
    component: str | None = None,
    evidence: dict | None = None,
    disposition: str | None = None,
) -> dict:
    if severity not in SEVERITIES:
        raise ValueError(f"Неизвестный уровень finding: {severity}")
    item = {
        "code": code,
        "severity": severity,
        "scope": scope,
        "message": message,
        "evidence": evidence or {},
    }
    if component is not None:
        item["component"] = component
    if disposition is not None:
        item["disposition"] = disposition
    return item


def validate_reference(reference: dict) -> list[dict]:
    findings: list[dict] = []
    labels = {
        "area": "Sref",
        "cref": "cref",
        "bref": "bref",
    }
    for key, label in labels.items():
        try:
            value = float(reference[key])
        except (KeyError, TypeError, ValueError):
            value = math.nan
        if not math.isfinite(value) or value <= 0.0:
            findings.append(
                finding(
                    "GEO-REF-001",
                    "BLOCKER",
                    f"{label} отсутствует, не является конечным или не положителен",
                    evidence={"field": key, "value": reference.get(key)},
                )
            )
    center = reference.get("center")
    valid_center = isinstance(center, (list, tuple)) and len(center) == 3
    if valid_center:
        try:
            valid_center = all(math.isfinite(float(value)) for value in center)
        except (TypeError, ValueError):
            valid_center = False
    if not valid_center:
        findings.append(
            finding(
                "GEO-REF-001",
                "BLOCKER",
                "CG должен содержать три конечные координаты X, Y, Z",
                evidence={"field": "center", "value": center},
            )
        )
    return findings


def semantic_audit(inventory: dict, reference: dict, policy: dict) -> dict:
    """Audit names, component types, Sets and reference values.

    Set defects for unambiguous canonical components are REPAIRABLE because
    they can be fixed in a solver twin.  Ambiguity is always a BLOCKER.
    """
    findings = validate_reference(reference)
    components = deepcopy(inventory.get("components", []))
    thick_set = int(policy["sets"]["thick_user_set"])
    thin_set = int(policy["sets"]["thin_user_set"])
    required = set(policy["semantics"].get("required_exact", []))
    declared_exclusions = {
        item["component"]: deepcopy(item)
        for item in policy["semantics"].get("declared_exclusions", [])
    }
    exact_counts: dict[str, int] = {}
    for component in components:
        name = str(component.get("name", ""))
        exact_counts[name] = exact_counts.get(name, 0) + 1

    for name, exclusion in declared_exclusions.items():
        count = exact_counts.get(name, 0)
        if count != 1:
            findings.append(
                finding(
                    "GEO-EXCL-002",
                    "BLOCKER",
                    "Явное исключение должно соответствовать ровно одному компоненту модели",
                    component=name,
                    evidence={"count": count, "declaration": exclusion},
                )
            )
    recognized: list[dict] = []

    for component in components:
        name = str(component.get("name", ""))
        if name in declared_exclusions:
            component["declared_exclusion"] = declared_exclusions[name]
            if exact_counts.get(name) == 1:
                findings.append(
                    finding(
                        "GEO-EXCL-001",
                        "WARNING",
                        "Компонент исключён явным правилом проекта; гибридная замена обязательна",
                        component=name,
                        evidence=declared_exclusions[name],
                        disposition="declared_exclusion",
                    )
                )
            continue
        base, source = component_base(name, policy)
        component["semantic_base"] = base
        component["semantic_source"] = source
        if base is None:
            severity = "BLOCKER" if policy["semantics"].get("unknown_is_blocker", True) else "WARNING"
            findings.append(
                finding(
                    "GEO-NAME-001",
                    severity,
                    "Имя компонента не распознано; автоматическое угадывание запрещено",
                    component=name or "<без имени>",
                    evidence={"geom_id": component.get("id"), "type": component.get("type")},
                )
            )
            continue
        recognized.append(component)
        expected_role = policy["semantics"]["roles"][base]
        component["semantic_role"] = expected_role
        expected_set = thin_set if expected_role == "thin" else thick_set
        other_set = thick_set if expected_role == "thin" else thin_set
        in_expected = bool(component.get("sets", {}).get(str(expected_set), False))
        in_other = bool(component.get("sets", {}).get(str(other_set), False))

        expected_type = "Wing" if expected_role == "thin" else None
        if expected_type and component.get("type") != expected_type:
            findings.append(
                finding(
                    "GEO-SET-002",
                    "BLOCKER",
                    f"Тонкая роль {base} ожидает тип OpenVSP Wing",
                    component=name,
                    evidence={"expected_type": expected_type, "actual_type": component.get("type")},
                )
            )
        if not in_expected or in_other:
            findings.append(
                finding(
                    "GEO-SET-001",
                    "REPAIRABLE",
                    f"Компонент должен принадлежать только Set_{expected_set}",
                    component=name,
                    evidence={
                        "geom_id": component.get("id"),
                        "expected_role": expected_role,
                        "expected_set": expected_set,
                        "other_set": other_set,
                        "in_expected": in_expected,
                        "in_other": in_other,
                    },
                    disposition="fix_in_solver_twins",
                )
            )

    for name, count in exact_counts.items():
        if name and count > 1:
            findings.append(
                finding(
                    "GEO-NAME-002",
                    "BLOCKER",
                    "Повторяющееся точное имя делает преобразование неоднозначным",
                    component=name,
                    evidence={"count": count},
                )
            )
    for required_name in sorted(required):
        if exact_counts.get(required_name, 0) != 1:
            findings.append(
                finding(
                    "GEO-NAME-003",
                    "BLOCKER",
                    f"Требуется ровно один компонент с точным именем {required_name}",
                    component=required_name,
                    evidence={"count": exact_counts.get(required_name, 0)},
                )
            )

    return {
        "schema": "repairmach.geometry-semantic-audit/1.0",
        "valid": not any(item["severity"] == "BLOCKER" for item in findings),
        "recognized_components": recognized,
        "declared_exclusions": list(declared_exclusions.values()),
        "findings": findings,
        "reference": deepcopy(reference),
        "sets": {"thick_user_set": thick_set, "thin_user_set": thin_set},
    }
