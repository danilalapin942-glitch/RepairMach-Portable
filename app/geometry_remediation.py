#!/usr/bin/env python3
"""Deterministic corrective-action routing for geometry certificates.

The router never edits a MASTER model.  It translates sealed diagnostics and
hybrid replacement requirements into the smallest auditable follow-up scope.
"""

from __future__ import annotations

from copy import deepcopy

from geometry_manifest import sha256_payload


CORRECTIVE_PLAN_SCHEMA = "repairmach.geometry-corrective-action-plan/1.0"

STAGE_ORDER = (
    "inventory",
    "semantic_audit",
    "transformation_plan",
    "solver_twins",
    "mesh_ladder",
    "vspaero_qualification",
    "machline_export",
    "machline_qualification",
    "parasite_binding",
    "hybrid_assembly",
    "workbook",
    "certificate",
)

BACKEND_ORDER = ("vspaero", "machline", "parasite_drag", "hybrid")


_ROUTES = {
    "GEO-REF-001": {
        "priority": 1,
        "classification": "operator_reference",
        "owner": "operator",
        "master_change_required": False,
        "action_code": "CORRECT_REFERENCE_VALUES",
        "action": "Исправить Sref, cref/САХ, bref или координаты CG по первичному источнику; не подбирать их по ожидаемой АДХ.",
        "stages": STAGE_ORDER,
        "backends": BACKEND_ORDER,
        "new_geometry_certificate_required": True,
    },
    "GEO-FILE-001": {
        "priority": 1,
        "classification": "operator_geometry",
        "owner": "operator",
        "master_change_required": True,
        "action_code": "REPAIR_MASTER_READABILITY",
        "action": "Открыть MASTER в OpenVSP, устранить повреждённые или неподдерживаемые детали и сохранить новый вариант модели.",
        "stages": STAGE_ORDER,
        "backends": BACKEND_ORDER,
        "new_geometry_certificate_required": True,
    },
    "GEO-NAME-001": {
        "priority": 1,
        "classification": "operator_geometry",
        "owner": "operator",
        "master_change_required": True,
        "action_code": "ASSIGN_CANONICAL_COMPONENT_NAME",
        "action": "Переименовать деталь по словарю Wing/Fuselage/GO/VO/Gondola либо добавить однозначный alias в политику проекта.",
        "stages": ("inventory", "semantic_audit", "transformation_plan", "solver_twins", "mesh_ladder", "vspaero_qualification", "machline_export", "machline_qualification", "parasite_binding", "hybrid_assembly", "workbook", "certificate"),
        "backends": BACKEND_ORDER,
        "new_geometry_certificate_required": True,
    },
    "GEO-NAME-002": {
        "priority": 1,
        "classification": "operator_geometry",
        "owner": "operator",
        "master_change_required": True,
        "action_code": "MAKE_COMPONENT_NAMES_UNIQUE",
        "action": "Устранить повторяющиеся точные имена; половины или варианты оформить каноническими суффиксами.",
        "stages": STAGE_ORDER,
        "backends": BACKEND_ORDER,
        "new_geometry_certificate_required": True,
    },
    "GEO-NAME-003": {
        "priority": 1,
        "classification": "operator_geometry",
        "owner": "operator",
        "master_change_required": True,
        "action_code": "RESTORE_REQUIRED_COMPONENT",
        "action": "Создать или однозначно назвать обязательный компонент, указанный в диагностике.",
        "stages": STAGE_ORDER,
        "backends": BACKEND_ORDER,
        "new_geometry_certificate_required": True,
    },
    "GEO-SET-002": {
        "priority": 1,
        "classification": "operator_geometry",
        "owner": "operator",
        "master_change_required": True,
        "action_code": "CORRECT_COMPONENT_GEOMETRY_TYPE",
        "action": "Перестроить несущую деталь как OpenVSP Wing; смена только Set не исправляет неверный тип геометрии.",
        "stages": STAGE_ORDER,
        "backends": BACKEND_ORDER,
        "new_geometry_certificate_required": True,
    },
    "GEO-EXCL-002": {
        "priority": 1,
        "classification": "operator_policy",
        "owner": "operator",
        "master_change_required": False,
        "action_code": "CORRECT_EXCLUSION_CONTRACT",
        "action": "Исправить неоднозначное исключение: одна существующая деталь, явные backends и физически допустимый способ замещения.",
        "stages": ("semantic_audit", "transformation_plan", "solver_twins", "mesh_ladder", "vspaero_qualification", "machline_export", "machline_qualification", "parasite_binding", "hybrid_assembly", "workbook", "certificate"),
        "backends": BACKEND_ORDER,
        "new_geometry_certificate_required": True,
    },
    "GEO-TIP-002": {
        "priority": 1,
        "classification": "operator_geometry",
        "owner": "operator",
        "master_change_required": True,
        "action_code": "REBUILD_TERMINAL_CHORD",
        "action": "Задать физически малую, но ненулевую конечную хорду без превышения бюджета площади и проверить форму законцовки.",
        "stages": STAGE_ORDER,
        "backends": BACKEND_ORDER,
        "new_geometry_certificate_required": True,
    },
    "GEO-INT-002": {
        "priority": 1,
        "classification": "operator_geometry",
        "owner": "operator",
        "master_change_required": True,
        "action_code": "CORRECT_COMPONENT_INTERFACE",
        "action": "Исправить сопряжение или задать однозначный малый offset в политике; автоматическое угадывание направления запрещено.",
        "stages": STAGE_ORDER,
        "backends": BACKEND_ORDER,
        "new_geometry_certificate_required": True,
    },
    "GEO-PLAN-001": {
        "priority": 1,
        "classification": "repairmach_internal",
        "owner": "repairmach",
        "master_change_required": False,
        "action_code": "REBUILD_TRANSFORMATION_PLAN",
        "action": "Пересоздать запечатанный план преобразований из неизменного MASTER и повторить проверку его связей.",
        "stages": ("transformation_plan", "solver_twins", "mesh_ladder", "vspaero_qualification", "machline_export", "machline_qualification", "parasite_binding", "hybrid_assembly", "workbook", "certificate"),
        "backends": BACKEND_ORDER,
        "new_geometry_certificate_required": True,
        "allowed_automatic_actions": ("rebuild_transformation_plan",),
    },
    "GEO-TWIN-001": {
        "priority": 1,
        "classification": "repairmach_internal",
        "owner": "repairmach",
        "master_change_required": False,
        "action_code": "REBUILD_SOLVER_TWINS",
        "action": "Удалить только незапечатанные результаты текущего запуска, заново создать расчётные двойники из MASTER и проверить их хэши.",
        "stages": ("solver_twins", "mesh_ladder", "vspaero_qualification", "machline_export", "machline_qualification", "parasite_binding", "hybrid_assembly", "workbook", "certificate"),
        "backends": BACKEND_ORDER,
        "new_geometry_certificate_required": True,
        "allowed_automatic_actions": ("rebuild_solver_twins",),
    },
    "GEO-TWIN-002": {
        "priority": 1,
        "classification": "repairmach_internal",
        "owner": "repairmach",
        "master_change_required": False,
        "action_code": "REBUILD_UNREADABLE_TWIN",
        "action": "Повторно создать нечитабельный расчётный двойник из MASTER и повторить независимую инвентаризацию.",
        "stages": ("solver_twins", "mesh_ladder", "vspaero_qualification", "machline_export", "machline_qualification", "parasite_binding", "hybrid_assembly", "workbook", "certificate"),
        "backends": BACKEND_ORDER,
        "new_geometry_certificate_required": True,
        "allowed_automatic_actions": ("rebuild_solver_twins",),
    },
    "GEO-DELTA-001": {
        "priority": 1,
        "classification": "operator_geometry",
        "owner": "operator",
        "master_change_required": True,
        "action_code": "REDUCE_GEOMETRY_REGULARIZATION",
        "action": "Устранить исходную геометрическую причину, чтобы расчётный двойник не требовал преобразования сверх допустимого бюджета.",
        "stages": STAGE_ORDER,
        "backends": BACKEND_ORDER,
        "new_geometry_certificate_required": True,
    },
    "GEO-MESH-001": {
        "priority": 1,
        "classification": "repairmach_internal",
        "owner": "repairmach",
        "master_change_required": False,
        "action_code": "REBUILD_MESH_LADDER",
        "action": "Пересоздать Coarse/Medium/Fine из одного запечатанного двойника и повторить проверку топологической эквивалентности.",
        "stages": ("mesh_ladder", "vspaero_qualification", "machline_export", "machline_qualification", "hybrid_assembly", "workbook", "certificate"),
        "backends": ("vspaero", "machline", "hybrid"),
        "new_geometry_certificate_required": True,
        "allowed_automatic_actions": ("rebuild_mesh_ladder",),
    },
    "GEO-MESH-002": {
        "priority": 1,
        "classification": "operator_geometry",
        "owner": "operator",
        "master_change_required": True,
        "action_code": "CORRECT_MESH_TOPOLOGY_SOURCE",
        "action": "Исправить источник топологического расхождения уровней сетки в MASTER или его параметрах тесселяции.",
        "stages": STAGE_ORDER,
        "backends": ("vspaero", "machline", "hybrid"),
        "new_geometry_certificate_required": True,
    },
    "GEO-TRI-LINEAGE-001": {
        "priority": 1,
        "classification": "repairmach_internal",
        "owner": "repairmach",
        "master_change_required": False,
        "action_code": "REEXPORT_CERTIFIED_TRI",
        "action": "Экспортировать TRI заново только из запечатанного Fine-двойника MachLine и повторить проверку происхождения.",
        "stages": ("machline_export", "machline_qualification", "hybrid_assembly", "workbook", "certificate"),
        "backends": ("machline", "hybrid"),
        "new_geometry_certificate_required": True,
        "allowed_automatic_actions": ("reexport_machline_tri",),
    },
    "GEO-TRI-001": {
        "priority": 1,
        "classification": "operator_geometry",
        "owner": "operator",
        "master_change_required": True,
        "action_code": "REPAIR_TRI_TOPOLOGY_SOURCE",
        "action": "Устранить вырожденные, открытые или несогласованные поверхности в исходной детали; не ремонтировать итоговую силу подбором отдельных панелей.",
        "stages": ("inventory", "semantic_audit", "transformation_plan", "solver_twins", "mesh_ladder", "machline_export", "machline_qualification", "hybrid_assembly", "workbook", "certificate"),
        "backends": ("machline", "hybrid"),
        "new_geometry_certificate_required": True,
    },
    "GEO-TRI-002": {
        "priority": 1,
        "classification": "operator_geometry",
        "owner": "operator",
        "master_change_required": True,
        "action_code": "REDUCE_TRI_REPAIR_DEMAND",
        "action": "Исправить геометрию, чтобы ограниченный автоматический TRI-ремонт укладывался в бюджет панелей и площади.",
        "stages": ("inventory", "semantic_audit", "transformation_plan", "solver_twins", "mesh_ladder", "machline_export", "machline_qualification", "hybrid_assembly", "workbook", "certificate"),
        "backends": ("machline", "hybrid"),
        "new_geometry_certificate_required": True,
    },
    "GEO-TRI-003": {
        "priority": 1,
        "classification": "operator_geometry",
        "owner": "operator",
        "master_change_required": True,
        "action_code": "CORRECT_TRI_COMPONENT_SEMANTICS",
        "action": "Исправить покомпонентную структуру TRI и соответствие деталям MASTER, затем заново экспортировать сетку.",
        "stages": STAGE_ORDER,
        "backends": ("machline", "hybrid"),
        "new_geometry_certificate_required": True,
    },
    "GEO-MACH-COVERAGE-001": {
        "priority": 1,
        "classification": "operator_geometry",
        "owner": "operator",
        "master_change_required": True,
        "action_code": "RESTORE_MACHLINE_COMPONENT_COVERAGE",
        "action": "Вернуть обязательные толстотельные компоненты в сертифицированную сетку давления MachLine или оформить отдельный проверяемый способ замещения.",
        "stages": STAGE_ORDER,
        "backends": ("machline", "hybrid"),
        "new_geometry_certificate_required": True,
    },
    "GEO-MACH-SCOPE-001": {
        "priority": 1,
        "classification": "methodology",
        "owner": "repairmach",
        "master_change_required": False,
        "action_code": "QUALIFY_OPEN_NOZZLE_SUBSONIC_METHOD",
        "action": "Разработать и независимо проверить дозвуковую постановку открытого сопла. Не повторять неподдерживаемый расчёт автоматически и не менять Mach самолёта ради допуска. Ветка VSPAERO остаётся независимой.",
        "stages": ("machline_qualification", "certificate"),
        "backends": ("machline", "hybrid"),
        "new_geometry_certificate_required": True,
    },
    "GEO-MACH-OUTLET-001": {
        "priority": 1,
        "classification": "operator_geometry",
        "owner": "operator",
        "master_change_required": False,
        "action_code": "DECLARE_MACHLINE_OUTLETS",
        "action": "Проверить явные выходные сечения MachLine и незаявленные отверстия по open_nozzle_audit.json. Не удалять хвост фюзеляжа автоматически; повторять только ветку MachLine.",
        "stages": STAGE_ORDER,
        "backends": ("machline", "hybrid"),
        "new_geometry_certificate_required": True,
    },
    "GEO-MACH-001": {
        "priority": 1,
        "classification": "operator_geometry",
        "owner": "operator",
        "master_change_required": True,
        "action_code": "CORRECT_SUPERSONIC_PANEL_ORIENTATION",
        "action": "Исправить поверхности, нарушающие сверхзвуковой геометрический критерий, и повторить всю сеточную лестницу MachLine.",
        "stages": ("inventory", "semantic_audit", "transformation_plan", "solver_twins", "mesh_ladder", "machline_export", "machline_qualification", "hybrid_assembly", "workbook", "certificate"),
        "backends": ("machline", "hybrid"),
        "new_geometry_certificate_required": True,
    },
    "GEO-MACH-SURROGATE-002": {
        "priority": 1,
        "classification": "operator_geometry",
        "owner": "operator",
        "master_change_required": True,
        "action_code": "CORRECT_AFT_CLOSURE_SOURCE",
        "action": "Исправить кормовую часть толстого тела: численное замыкание не удалось выполнить в допустимом геометрическом бюджете.",
        "stages": STAGE_ORDER,
        "backends": ("machline", "hybrid"),
        "new_geometry_certificate_required": True,
    },
    "GEO-MACH-SOLVER-001": {
        "priority": 1,
        "classification": "solver_numerics",
        "owner": "repairmach",
        "master_change_required": False,
        "action_code": "REQUALIFY_MACHLINE_NUMERICS",
        "action": "Повторить независимые пробы MachLine на полной сеточной лестнице; при повторном отказе передать оператору локализованный компонент.",
        "stages": ("machline_qualification", "hybrid_assembly", "workbook", "certificate"),
        "backends": ("machline", "hybrid"),
        "new_geometry_certificate_required": True,
        "allowed_automatic_actions": ("rerun_machline_qualification",),
    },
    "GEO-VSP-003": {
        "priority": 1,
        "classification": "solver_numerics",
        "owner": "repairmach",
        "master_change_required": False,
        "action_code": "REQUALIFY_VSPAERO_NUMERICS",
        "action": "Повторить VSPAERO на полной сеточной лестнице и усиленных независимых повторах; при устойчивом отказе использовать локализацию по компонентам.",
        "stages": ("mesh_ladder", "vspaero_qualification", "hybrid_assembly", "workbook", "certificate"),
        "backends": ("vspaero", "hybrid"),
        "new_geometry_certificate_required": True,
        "allowed_automatic_actions": ("rerun_vspaero_qualification",),
    },
    "GEO-RUN-001": {
        "priority": 1,
        "classification": "runtime_environment",
        "owner": "repairmach",
        "master_change_required": False,
        "action_code": "RESUME_CERTIFICATION_RUN",
        "action": "Устранить зафиксированный сбой среды и безопасно повторить сертификацию из неизменного MASTER.",
        "stages": STAGE_ORDER,
        "backends": BACKEND_ORDER,
        "new_geometry_certificate_required": True,
        "allowed_automatic_actions": ("restart_certification",),
    },
    "GEO-MASTER-001": {
        "priority": 1,
        "classification": "operator_geometry",
        "owner": "operator",
        "master_change_required": False,
        "action_code": "FREEZE_MASTER_AND_RESTART",
        "action": "Завершить редактирование MASTER, сохранить отдельную версию и запустить новую сертификацию по неизменяемому файлу.",
        "stages": STAGE_ORDER,
        "backends": BACKEND_ORDER,
        "new_geometry_certificate_required": True,
    },
    "GEO-SOLVER-001": {
        "priority": 1,
        "classification": "runtime_environment",
        "owner": "operator",
        "master_change_required": False,
        "action_code": "RESTORE_OPENVSP_EXECUTABLE",
        "action": "Восстановить зафиксированную версию vspscript.exe и повторить сертификацию.",
        "stages": STAGE_ORDER,
        "backends": BACKEND_ORDER,
        "new_geometry_certificate_required": True,
    },
    "GEO-SOLVER-002": {
        "priority": 1,
        "classification": "runtime_environment",
        "owner": "operator",
        "master_change_required": False,
        "action_code": "RESTORE_VSPAERO_EXECUTABLE",
        "action": "Восстановить зафиксированную версию vspaero.exe и повторить VSPAERO-квалификацию.",
        "stages": ("vspaero_qualification", "hybrid_assembly", "workbook", "certificate"),
        "backends": ("vspaero", "hybrid"),
        "new_geometry_certificate_required": True,
    },
    "GEO-SOLVER-003": {
        "priority": 1,
        "classification": "runtime_environment",
        "owner": "operator",
        "master_change_required": False,
        "action_code": "RESTORE_MACHLINE_EXECUTABLE",
        "action": "Восстановить зафиксированную версию machline.exe и повторить MachLine-квалификацию.",
        "stages": ("machline_export", "machline_qualification", "hybrid_assembly", "workbook", "certificate"),
        "backends": ("machline", "hybrid"),
        "new_geometry_certificate_required": True,
    },
}


def _ordered(values: set[str], canonical: tuple[str, ...]) -> list[str]:
    known = [value for value in canonical if value in values]
    return known + sorted(values.difference(canonical), key=str.casefold)


def _component_tokens(item: dict) -> list[str | None]:
    component = str(item.get("component", "")).strip()
    if component:
        return [component]
    if item.get("code") == "GEO-VSP-004":
        groups = {
            str(group).strip()
            for failure in item.get("evidence", {}).get("localized_failures", [])
            for group in failure.get("groups", [])
            if str(group).strip()
        }
        if groups:
            return sorted(groups, key=str.casefold)
    return [None]


def _route_finding(item: dict) -> list[dict]:
    code = str(item.get("code", "UNKNOWN")).strip() or "UNKNOWN"
    severity = str(item.get("severity", "")).upper()
    if severity not in {"BLOCKER", "REPAIRABLE"} and code != "GEO-VSP-004":
        return []
    # REPAIRABLE findings are already applied to solver twins and remain in
    # the certificate as provenance, not unfinished work.
    if severity == "REPAIRABLE":
        return []
    route = deepcopy(_ROUTES.get(code))
    if code == "GEO-VSP-004":
        route = {
            "priority": 1,
            "classification": "operator_geometry_or_method",
            "owner": "operator",
            "master_change_required": False,
            "action_code": "RESOLVE_LOCALIZED_VSPAERO_FAILURE",
            "action": "Исправить локализованный компонент в MASTER либо оформить физически обоснованное исключение с полной гибридной заменой; подбор одной проходящей сетки запрещён.",
            "stages": STAGE_ORDER,
            "backends": ("vspaero", "hybrid"),
            "new_geometry_certificate_required": True,
        }
    if route is None:
        if severity != "BLOCKER":
            return []
        route = {
            "priority": 1,
            "classification": "unclassified_blocker",
            "owner": "operator",
            "master_change_required": False,
            "action_code": "MANUAL_FAIL_CLOSED_REVIEW",
            "action": "Неизвестный блокирующий код: прекратить расчёт, вручную проверить доказательства и выпустить новый полный сертификат.",
            "stages": STAGE_ORDER,
            "backends": BACKEND_ORDER,
            "new_geometry_certificate_required": True,
        }
    result = []
    for component in _component_tokens(item):
        result.append({
            **route,
            "trigger": {
                "code": code,
                "severity": severity,
                "scope": str(item.get("scope", "master")),
                "message": str(item.get("message", "")),
            },
            "component": component,
            "required_before_calculation": True,
            "new_hybrid_package_required": "hybrid" in route.get("backends", ()),
            "evidence": deepcopy(item.get("evidence", {})),
        })
    return result


def _route_replacement(requirement: dict) -> dict | None:
    method = str(requirement.get("method", "")).strip().lower()
    component = str(requirement.get("component", "")).strip() or None
    available = requirement.get("backend_capability_available") is True
    if method == "not_physical":
        return None
    if not available:
        if method == "machline_pressure_wave_all_points":
            stages = ["machline_export", "machline_qualification", "hybrid_assembly", "workbook", "certificate"]
            backends = ["machline", "hybrid"]
        elif method == "parasite_drag_subsonic":
            stages = ["parasite_binding", "hybrid_assembly", "workbook", "certificate"]
            backends = ["parasite_drag", "hybrid"]
        else:
            stages = ["hybrid_assembly", "workbook", "certificate"]
            backends = ["hybrid"]
        return {
            "priority": 1,
            "classification": "operator_geometry_or_method",
            "owner": "operator",
            "master_change_required": False,
            "action_code": "RESTORE_REPLACEMENT_COVERAGE",
            "action": "Включить компонент в сертифицированную геометрию требуемого backend либо оформить другой независимый физически обоснованный метод замещения.",
            "trigger": {
                "code": "HYB-COVERAGE-001",
                "severity": "BLOCKER",
                "scope": "hybrid",
                "message": str(requirement.get("reason", "replacement backend coverage unavailable")),
            },
            "component": component,
            "required_before_calculation": True,
            "allowed_automatic_actions": [],
            "stages": stages,
            "backends": backends,
            "new_geometry_certificate_required": True,
            "new_hybrid_package_required": True,
            "evidence": deepcopy(requirement),
        }
    if method == "base_drag_semiempirical":
        action_code = "CALCULATE_SEMIEMPIRICAL_BASE_DRAG"
        action = "Автоматически рассчитать донное сопротивление по запечатанному паспорту полуэмпирики и добавить его вместо замаскированной силы численного кормового замыкания."
        automatic = ["calculate_semiempirical_base_drag", "seal_method_passport", "rebuild_hybrid_workbook"]
        backends = ["hybrid"]
        stages = ["hybrid_assembly", "workbook"]
    elif method == "machline_pressure_wave_all_points":
        action_code = "BIND_MACHLINE_COMPONENT_SERIES"
        action = "Автоматически привязать запечатанный покомпонентный ряд давления/волнового сопротивления MachLine ко всем расчётным точкам."
        automatic = ["bind_machline_component_series", "rebuild_hybrid_workbook"]
        backends = ["machline", "hybrid"]
        stages = ["machline_qualification", "hybrid_assembly", "workbook"]
    elif method == "parasite_drag_subsonic":
        action_code = "BIND_PARASITE_COMPONENT_SERIES"
        action = "Автоматически привязать запечатанный покомпонентный дозвуковой ряд профильного сопротивления OpenVSP Parasite Drag."
        automatic = ["bind_parasite_component_series", "rebuild_hybrid_workbook"]
        backends = ["parasite_drag", "hybrid"]
        stages = ["parasite_binding", "hybrid_assembly", "workbook"]
    elif method == "semiempirical_component_pressure_wave_all_points":
        action_code = "BIND_SEMIEMPIRICAL_COMPONENT_PRESSURE_SERIES"
        action = (
            "Автоматически вычислить и привязать покомпонентный pressure/wave-ряд "
            "по заранее запечатанному полуэмпирическому паспорту для всех расчётных точек."
        )
        automatic = [
            "calculate_semiempirical_component_pressure_wave",
            "seal_method_passport",
            "rebuild_hybrid_workbook",
        ]
        backends = ["hybrid"]
        stages = ["hybrid_assembly", "workbook"]
    else:
        action_code = "SUPPLY_SUPPORTED_REPLACEMENT_METHOD"
        action = "Заменить неподдерживаемый способ замещения на проверяемый метод с запечатанным источником и паспортом неопределённости."
        automatic = []
        backends = ["hybrid"]
        stages = ["hybrid_assembly", "workbook", "certificate"]
    return {
        "priority": 2,
        "classification": "hybrid_method_input",
        "owner": "repairmach" if automatic else "operator",
        "master_change_required": False,
        "action_code": action_code,
        "action": action,
        "trigger": {
            "code": "HYB-INPUT-001",
            "severity": "REQUIRED",
            "scope": "hybrid",
            "message": str(requirement.get("reason", "sealed replacement source required")),
        },
        "component": component,
        "required_before_calculation": True,
        "allowed_automatic_actions": automatic,
        "stages": stages,
        "backends": backends,
        "new_geometry_certificate_required": False,
        "new_hybrid_package_required": True,
        "evidence": deepcopy(requirement),
    }


def build_corrective_action_plan(
    *,
    findings: list[dict],
    replacement_contract: dict | None = None,
    verdict: str | None = None,
) -> dict:
    """Build a deterministic, fail-closed post-certification action plan."""
    items: list[dict] = []
    for finding in findings if isinstance(findings, list) else []:
        if isinstance(finding, dict):
            items.extend(_route_finding(finding))
    contract = replacement_contract if isinstance(replacement_contract, dict) else {}
    for requirement in contract.get("requirements", []):
        if isinstance(requirement, dict):
            routed = _route_replacement(requirement)
            if routed is not None:
                items.append(routed)

    # A code/component/action triple identifies one physical correction even
    # if it was reported by more than one backend stage.
    unique: dict[tuple[str, str, str], dict] = {}
    for item in items:
        key = (
            str(item.get("action_code", "")),
            str(item.get("component") or ""),
            str(item.get("trigger", {}).get("code", "")),
        )
        unique.setdefault(key, item)
    items = sorted(
        unique.values(),
        key=lambda item: (
            int(item.get("priority", 99)),
            str(item.get("component") or "").casefold(),
            str(item.get("action_code", "")),
            str(item.get("trigger", {}).get("code", "")),
        ),
    )
    for index, item in enumerate(items, start=1):
        item["id"] = f"CA-{index:03d}"
        item["allowed_automatic_actions"] = list(item.get("allowed_automatic_actions", []))
        item["stages"] = list(item.get("stages", []))
        item["backends"] = list(item.get("backends", []))

    required = [item for item in items if item.get("required_before_calculation")]
    blocker_count = sum(
        1 for item in required
        if item.get("trigger", {}).get("severity") == "BLOCKER"
    )
    operator_count = sum(1 for item in required if item.get("owner") == "operator")
    automatic_count = sum(1 for item in required if item.get("allowed_automatic_actions"))
    stage_set = {stage for item in required for stage in item.get("stages", [])}
    backend_set = {backend for item in required for backend in item.get("backends", [])}
    component_set = {
        str(item.get("component")) for item in required if item.get("component")
    }
    if blocker_count:
        status = "blocked"
    elif required:
        status = "conditional_actions_required"
    else:
        status = "no_action_required"
    plan = {
        "schema": CORRECTIVE_PLAN_SCHEMA,
        "method": "deterministic_fail_closed_routing",
        "verdict": verdict,
        "status": status,
        "calculation_release_ready": not required,
        "backend_action_free": {
            backend: not any(
                backend in item.get("backends", []) for item in required
            )
            for backend in BACKEND_ORDER
        },
        "summary": {
            "item_count": len(items),
            "required_count": len(required),
            "blocker_count": blocker_count,
            "operator_action_count": operator_count,
            "automatic_action_count": automatic_count,
        },
        "minimum_retest": {
            "stages": _ordered(stage_set, STAGE_ORDER),
            "backends": _ordered(backend_set, BACKEND_ORDER),
            "components": sorted(component_set, key=str.casefold),
            "new_geometry_certificate_required": any(
                item.get("new_geometry_certificate_required") for item in required
            ),
            "new_hybrid_package_required": any(
                item.get("new_hybrid_package_required") for item in required
            ),
        },
        "items": items,
    }
    plan["plan_fingerprint"] = sha256_payload(plan)
    return plan


def corrective_action_plan_errors(plan: dict) -> list[str]:
    """Validate the internal seal and essential machine-readable fields."""
    errors: list[str] = []
    if not isinstance(plan, dict):
        return ["План корректирующих действий отсутствует или повреждён"]
    if plan.get("schema") != CORRECTIVE_PLAN_SCHEMA:
        errors.append("Неизвестная схема плана корректирующих действий")
    items = plan.get("items")
    if not isinstance(items, list):
        errors.append("План корректирующих действий не содержит список items")
        items = []
    expected_ids = [f"CA-{index:03d}" for index in range(1, len(items) + 1)]
    actual_ids = [item.get("id") if isinstance(item, dict) else None for item in items]
    if actual_ids != expected_ids:
        errors.append("Нарушен порядок идентификаторов корректирующих действий")
    for item in items:
        if not isinstance(item, dict):
            errors.append("Элемент плана корректирующих действий повреждён")
            continue
        for key in ("action_code", "action", "owner", "trigger", "stages", "backends"):
            if key not in item:
                errors.append(f"Элемент {item.get('id', '?')} не содержит поле {key}")
    expected = plan.get("plan_fingerprint")
    unsealed = deepcopy(plan)
    unsealed.pop("plan_fingerprint", None)
    actual = sha256_payload(unsealed)
    if expected != actual:
        errors.append("Нарушена цифровая целостность плана корректирующих действий")
    return errors
