#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path
from datetime import datetime

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from tri_mesh import diagnose, format_diagnostics, read_tri, repair, write_tri
from mach_repair import repair_mach_criterion, scan_mach_criterion, summarize_scan, write_scan_csv
from machline_geometry import nascart_freestream
from machline_postprocess import masked_force_coefficients, write_masked_force_record
from aero_hybrid import (
    load_machline_report,
    machline_wind_axes,
    parse_openvsp_results_csv,
    parse_vspaero_polar,
    validate_vspaero_run_outputs,
)
from calculation_scenarios import (
    build_scenario_manifest,
    component_name_policy,
    expand_vspaero_cases,
    geometry_sha256,
    load_scenario_catalog,
    scenario_by_id,
    validate_component_names,
    write_scenario_manifest,
)
from openvsp_runner import (
    find_openvsp_dir,
    generate_parasite_drag_script,
    run_vspscript,
    tool_paths,
)
from vspaero_runner import (
    find_generated_polar,
    generate_vspaero_sweep_script,
    parse_set_report,
    safe_vsp3_name,
    standard_vspaero_cases,
    user_set_to_api_index,
)
from validation_metrics import cy_alpha_per_degree
from blind_study import (
    attach_reference_after_seal,
    prepare_blind_package,
    seal_predictions,
    verify_blind_package,
    verify_prediction_seal,
)
from hybrid_pipeline import (
    REQUEST_SCHEMA as HYBRID_REQUEST_SCHEMA,
    find_node_executable,
    find_node_modules,
    latest_completed_vspaero_study,
    load_hybrid_policy,
    run_hybrid_request,
)
from geometry_certification import certify_geometry
from geometry_certificate import resolve_certificate_artifact, verify_certificate
from geometry_manifest import sha256_payload


def app_root() -> Path:
    return Path(__file__).resolve().parents[1]


ROOT = app_root()
SETTINGS_PATH = ROOT / "config" / "repairmach_settings.json"
SCENARIOS_PATH = ROOT / "config" / "calculation_scenarios.json"
HYBRID_POLICY_PATH = ROOT / "config" / "hybrid_method.json"
GEOMETRY_POLICY_PATH = ROOT / "config" / "geometry_certification.json"


PROJECT_DIRS = [
    "00_original/vsp",
    "00_original/tri",
    "01_geometry_cases/fuselage",
    "01_geometry_cases/wing",
    "01_geometry_cases/fuselage_wing",
    "01_geometry_cases/fuselage_wing_tail",
    "01_geometry_cases/full_aircraft",
    "02_working_meshes/input",
    "02_working_meshes/repaired",
    "02_working_meshes/final",
    "03_repair_logs/scan",
    "03_repair_logs/repair_log",
    "03_repair_logs/final_scan",
    "03_repair_logs/zones",
    "03_repair_logs/summary",
    "03_repair_logs/vtk",
    "04_machline_input/json",
    "04_machline_input/bat",
    "05_machline_results/vtk",
    "05_machline_results/wake",
    "05_machline_results/control_points",
    "05_machline_results/reports",
    "05_machline_results/screenshots",
    "06_vspaero_input/scripts",
    "06_vspaero_input/models",
    "06_vspaero_results/polars",
    "06_vspaero_results/csv",
    "06_vspaero_results/logs",
    "06_vspaero_results/runs",
    "07_parametric_studies",
    "07_parametric_studies/scenarios",
    "08_hybrid_results",
    "09_parasite_drag/scripts",
    "09_parasite_drag/results",
    "09_parasite_drag/logs",
    "09_parasite_drag/runs",
    "10_blind_validation",
    "11_geometry_certification",
]


def load_settings() -> dict:
    if not SETTINGS_PATH.exists():
        raise FileNotFoundError(f"Не найден файл настроек: {SETTINGS_PATH}")
    return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))


def save_settings(settings: dict) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def rel_path(path_text: str) -> Path:
    return ROOT / Path(path_text)


def find_python(settings: dict) -> Path | None:
    preferred = rel_path(settings["python"]["preferred"])
    if preferred.exists():
        return preferred
    return Path(sys.executable)


def find_machline(settings: dict) -> Path:
    return rel_path(settings["machline"]["exe_relative_path"])


def machline_working_dir(settings: dict) -> Path:
    return rel_path(settings["machline"]["working_dir_relative_path"])


def print_header() -> None:
    settings = load_settings()
    version = settings.get("repairmach_version", "unknown")
    print("\n" + "=" * 58)
    print(f"RepairMach {version}")
    print("MachLine как двигатель, RepairMach как оболочка")
    print("=" * 58)
    print(f"ROOT: {ROOT}")
    print("=" * 58 + "\n")


def check_environment() -> None:
    settings = load_settings()
    py = find_python(settings)
    mach = find_machline(settings)
    mach_wd = machline_working_dir(settings)
    openvsp_dir = find_openvsp_dir(settings.get("openvsp", {}).get("install_dir"))
    openvsp_tools = tool_paths(openvsp_dir)
    node = find_node_executable()
    node_modules = find_node_modules()

    print("\nПроверка среды")
    print("-" * 58)
    print(f"RepairMach root : {ROOT}")
    print(f"Python          : {py}")
    print(f"Python exists   : {py.exists() if py else False}")
    print(f"MachLine exe    : {mach}")
    print(f"MachLine exists : {mach.exists()}")
    print(f"MachLine cwd    : {mach_wd}")
    print(f"MachLine cwd ok : {mach_wd.exists()}")
    print(f"OpenVSP dir     : {openvsp_dir or 'не найден'}")
    print(f"vspscript.exe   : {openvsp_tools['vspscript'] or 'не найден'}")
    print(f"vspaero.exe     : {openvsp_tools['vspaero'] or 'не найден'}")
    print(f"Excel runtime   : {node or 'не найден'}")
    print(f"Excel module    : {node_modules or 'не найден'}")

    if not mach.exists():
        print("\n[!] MachLine не найден.")
        print("    Скопируйте рабочий machline.exe и нужные DLL/файлы в:")
        print(f"    {ROOT / 'engines' / 'MachLine'}")
    else:
        print("\nOK: MachLine найден.")

    if openvsp_dir is None:
        print("[!] OpenVSP не найден. Укажите install_dir в config/repairmach_settings.json.")
    elif not openvsp_tools["vspaero"].is_file():
        print("[!] OpenVSP найден, но vspaero.exe отсутствует.")
    else:
        print("OK: OpenVSP/VSPAERO найдены.")

    if node is None or node_modules is None:
        print("[!] Автоматический Excel недоступен. Проверьте Node.js и @oai/artifact-tool.")
    else:
        print("OK: автоматическое построение Excel доступно.")

    print("-" * 58)


def create_project() -> None:
    settings = load_settings()
    name = input("Название проекта, например MIG_29: ").strip()
    if not name:
        print("Отменено: имя проекта пустое.")
        return

    defaults = settings["defaults"]
    print("Задайте опорные величины этого самолёта в единицах его геометрии.")
    area_text = input(f"Sref, Enter = {defaults['area']}: ").strip()
    length_text = input(f"САХ/опорная длина, Enter = {defaults['longitudinal_length']}: ").strip()
    lateral_text = input(f"Размах/поперечная длина, Enter = {defaults['lateral_length']}: ").strip()
    center_text = input(
        "Центр масс X Y Z, Enter = " + " ".join(str(value) for value in defaults["center"]) + ": "
    ).strip()
    try:
        project_reference = {
            "area": float(area_text or defaults["area"]),
            "longitudinal_length": float(length_text or defaults["longitudinal_length"]),
            "lateral_length": float(lateral_text or defaults["lateral_length"]),
            "center": list(parse_flow(center_text)) if center_text else list(defaults["center"]),
        }
    except ValueError as exc:
        print(f"Некорректные опорные величины: {exc}")
        return
    if project_reference["area"] <= 0.0 or project_reference["longitudinal_length"] <= 0.0:
        print("Sref и опорная длина должны быть положительными.")
        return

    projects_root = rel_path(settings["paths"]["projects_root"])
    project = projects_root / name
    if project.exists():
        print(f"Проект уже существует: {project}")
    else:
        for d in PROJECT_DIRS:
            (project / d).mkdir(parents=True, exist_ok=True)

        cfg = {
            "project_name": name,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "default_reference": project_reference,
            "default_solver": {
                "matrix_solver": settings["defaults"]["solver"]
            },
            "openvsp_sets": {
                "fuselage_and_nacelles": {
                    "user_set": 1,
                    "api_index": user_set_to_api_index(1),
                    "vspaero_role": "thick",
                },
                "wing_and_empennage": {
                    "user_set": 2,
                    "api_index": user_set_to_api_index(2),
                    "vspaero_role": "thin",
                },
            },
            "repair_policy": {
                "enabled": True,
                "baseline_mach": 1.5,
                "baseline_alpha_deg": 0.0,
                "max_bad_panels": 100,
                "max_bad_fraction": 0.005,
                "require_final_bad_zero": True
            },
            "geometry_certification": {
                "policy_overrides": {},
                "scope_override": None,
                "tri_path": None,
                "run_vspaero_probes": True
            }
        }
        (project / "project_config.json").write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        print(f"Создан проект: {project}")

    settings["paths"]["default_project"] = name
    save_settings(settings)
    print(f"Проект выбран по умолчанию: {name}")


def list_projects() -> list[Path]:
    settings = load_settings()
    projects_root = rel_path(settings["paths"]["projects_root"])
    projects_root.mkdir(parents=True, exist_ok=True)
    projects = [p for p in projects_root.iterdir() if p.is_dir() and p.name != "_template"]
    if not projects:
        print("Пока нет проектов.")
        return []
    print("\nПроекты:")
    for i, p in enumerate(projects, start=1):
        print(f"[{i}] {p.name}")
    return projects


def select_project() -> Path | None:
    settings = load_settings()
    projects = list_projects()
    if not projects:
        return None
    choice = input("Номер проекта: ").strip()
    try:
        idx = int(choice) - 1
        project = projects[idx]
    except Exception:
        print("Неверный выбор.")
        return None
    settings["paths"]["default_project"] = project.name
    save_settings(settings)
    print(f"Выбран проект: {project.name}")
    return project


def default_project_path() -> Path:
    settings = load_settings()
    return rel_path(settings["paths"]["projects_root"]) / settings["paths"]["default_project"]


def load_project_reference(project: Path, settings: dict) -> dict:
    reference = dict(settings["defaults"])
    project_config = project / "project_config.json"
    if project_config.is_file():
        payload = json.loads(project_config.read_text(encoding="utf-8"))
        reference.update(payload.get("default_reference", {}))
    return {
        "area": float(reference["area"]),
        "longitudinal_length": float(reference["longitudinal_length"]),
        "lateral_length": float(reference["lateral_length"]),
        "center": [float(value) for value in reference["center"]],
    }


def unique_import_path(directory: Path, source_name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stem = safe_vsp3_name(source_name)
    candidate = directory / f"{stem}.vsp3"
    if not candidate.exists():
        return candidate
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return directory / f"{stem}_{timestamp}.vsp3"


def save_report(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def output_sha256(paths: dict[str, Path]) -> dict[str, str]:
    """Hash existing run artifacts so downstream consumers can detect changes."""
    return {
        role: geometry_sha256(Path(path))
        for role, path in paths.items()
        if Path(path).is_file()
    }


def executable_fingerprints(paths: dict[str, Path | None]) -> dict[str, dict[str, str]]:
    """Bind a run to the exact executables that produced its outputs."""
    result: dict[str, dict[str, str]] = {}
    for role, value in paths.items():
        if value is None:
            continue
        path = Path(value).resolve()
        if not path.is_file():
            continue
        result[role] = {"path": str(path), "sha256": geometry_sha256(path)}
    return result


def certified_solver_geometry(
    project: Path,
    master_path: Path,
    backend_name: str,
    *,
    runtime_executables: dict[str, Path] | None = None,
    scenario: dict | None = None,
    scenario_fingerprint: str | None = None,
) -> tuple[Path, dict, Path] | None:
    """Return the exact certified solver twin for *master_path* when available.

    An unrelated latest certificate is ignored.  A certificate for the same
    MASTER is fail-closed: an invalid backend binding must be corrected by a
    fresh certification instead of silently falling back to the user's file.
    """
    pointer_path = project / "11_geometry_certification" / "latest_certificate.json"
    if not pointer_path.is_file():
        return None
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    certificate_path = Path(pointer["certificate_path"]).resolve()
    if not certificate_path.is_file():
        raise FileNotFoundError(
            f"Последний сертификат ссылается на отсутствующий файл: {certificate_path}"
        )
    certificate = json.loads(certificate_path.read_text(encoding="utf-8"))
    master_hash = geometry_sha256(master_path)
    if master_hash != certificate.get("master", {}).get("sha256_before"):
        return None
    verification = verify_certificate(
        certificate_path,
        master_path=master_path,
        backend=backend_name,
        runtime_executables=runtime_executables,
        scenario_id=str(scenario.get("id")) if isinstance(scenario, dict) else None,
        scenario=scenario,
        scenario_fingerprint=scenario_fingerprint,
    )
    if not verification["valid"]:
        raise ValueError(
            f"Сертификат для backend {backend_name} не принят: "
            + "; ".join(verification["errors"])
        )
    backend = certificate.get("backends", {}).get(backend_name, {})
    solver_geometry = backend.get("solver_geometry", {})
    solver_path = resolve_certificate_artifact(
        certificate_path,
        solver_geometry,
        label=f"Расчётная геометрия backend {backend_name}",
    )
    expected_hash = solver_geometry.get("sha256")
    if not solver_path.is_file() or geometry_sha256(solver_path) != expected_hash:
        raise ValueError(
            f"Сертифицированный двойник {backend_name} отсутствует или изменён"
        )
    return solver_path, certificate, certificate_path


def _certified_vspaero_study_geometry(
    project: Path,
    requested_source: Path,
    tools: dict,
    scenario: dict | None,
) -> tuple[Path, dict, Path] | None:
    """Bind only an immutable catalog scenario to a VSPAERO certificate.

    The interactive path accepts arbitrary ranges and tail incidence after
    this decision point, so it cannot truthfully inherit a certificate issued
    for another setup.  It deliberately remains an uncertified MASTER run.
    """
    if scenario is None:
        return None
    return certified_solver_geometry(
        project,
        requested_source,
        "vspaero",
        runtime_executables={
            "openvsp": tools["vspscript"],
            "vspaero": tools["vspaero"],
        },
        scenario=scenario,
    )


def parse_flow(text: str) -> tuple[float, float, float]:
    if not text.strip():
        return (1.0, 0.0, 0.0)
    values = [float(value) for value in text.replace(",", " ").replace(";", " ").split()]
    if len(values) != 3:
        raise ValueError("Нужно ввести три компоненты направления потока")
    return tuple(values)


def freestream_vector(alpha_deg: float, beta_deg: float, speed: float = 100.0) -> list[float]:
    """Return MachLine XYZ velocity for aerodynamic alpha and beta angles."""
    alpha = math.radians(alpha_deg)
    beta = math.radians(beta_deg)
    return [
        speed * math.cos(alpha) * math.cos(beta),
        speed * math.sin(beta),
        speed * math.sin(alpha) * math.cos(beta),
    ]


def mach_diagnostics_workflow(mesh_path: Path, project: Path) -> Path | None:
    settings = load_settings()
    project_cfg_path = project / "project_config.json"
    project_cfg = {}
    if project_cfg_path.exists():
        project_cfg = json.loads(project_cfg_path.read_text(encoding="utf-8"))
    policy = project_cfg.get("repair_policy", {})
    default_mach = policy.get("baseline_mach", settings["defaults"]["mach"])
    default_safety = policy.get("safety_margin", settings["defaults"]["safety_margin"])
    default_max = policy.get("max_bad_panels", 100)

    answer = input("Запустить сверхзвуковую Mach-диагностику? [Y/n]: ").strip().lower()
    if answer not in ("", "y", "yes", "д", "да"):
        return None
    try:
        mach_text = input(f"Число Маха, Enter = {default_mach}: ").strip()
        mach = float(mach_text) if mach_text else float(default_mach)
        flow = parse_flow(input("Направление потока vx vy vz, Enter = 1 0 0: "))
        mesh = read_tri(mesh_path)
        scan = scan_mach_criterion(mesh, mach, flow)
    except Exception as exc:
        print(f"[ОШИБКА] Mach-диагностика не выполнена: {exc}")
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary = summarize_scan(scan)
    scan_csv = project / "03_repair_logs" / "scan" / f"{mesh_path.stem}_{timestamp}_mach_scan.csv"
    summary_json = project / "03_repair_logs" / "summary" / f"{mesh_path.stem}_{timestamp}_mach_summary.json"
    write_scan_csv(scan_csv, scan)
    save_report(summary_json, {
        "file": str(mesh_path), "mach": mach, "flow_direction": flow, "summary": summary
    })
    print("\nСверхзвуковая Mach-диагностика")
    print("-" * 58)
    print(f"Панелей всего                 : {summary['panels']}")
    print(f"Панелей за пределом критерия : {summary['bad_panels']}")
    print(f"Панелей рядом с пределом     : {summary['near_limit_panels']}")
    print(f"Максимальный запас нарушения : {summary['maximum_margin']:.8g}")
    print(f"CSV скан: {scan_csv}")
    print(f"Сводка  : {summary_json}")
    if summary["bad_panels"] == 0:
        print("OK: нарушений сверхзвукового критерия не найдено.")
        return None

    answer = input("Запустить консервативную автоправку панелей? [y/N]: ").strip().lower()
    if answer not in ("y", "yes", "д", "да"):
        print("Автоправка пропущена.")
        return None
    safety_text = input(f"Безопасный запас, Enter = {default_safety}: ").strip()
    max_text = input(f"Максимум правок, Enter = {default_max}: ").strip()
    safety = float(safety_text) if safety_text else float(default_safety)
    max_repairs = int(max_text) if max_text else int(default_max)

    repaired_mesh, repair_log, final_scan = repair_mach_criterion(
        mesh, mach, flow, safety, max_repairs
    )
    repaired_path = project / "02_working_meshes" / "repaired" / f"{mesh_path.stem}_auto.tri"
    repair_log_path = project / "03_repair_logs" / "repair_log" / f"{mesh_path.stem}_{timestamp}_mach_repair.json"
    final_csv = project / "03_repair_logs" / "final_scan" / f"{mesh_path.stem}_{timestamp}_mach_final.csv"
    write_tri(repaired_path, repaired_mesh)
    write_scan_csv(final_csv, final_scan)
    final_summary = summarize_scan(final_scan)
    save_report(repair_log_path, {
        "source": str(mesh_path), "output": str(repaired_path), "mach": mach,
        "flow_direction": flow, "safety_margin": safety, "repairs": repair_log,
        "final_summary": final_summary,
    })
    print(f"Исправлено панелей             : {sum(item['status'] == 'REPAIRED' for item in repair_log)}")
    print(f"Осталось нарушающих панелей    : {final_summary['bad_panels']}")
    print(f"Исправленный TRI               : {repaired_path}")
    print(f"Лог автоправки                 : {repair_log_path}")
    print(f"Финальный CSV                  : {final_csv}")
    return repaired_path


def tri_diagnostics_workflow(tri_path: Path, project: Path, offer_repair: bool = True) -> Path | None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    print("\nДиагностика TRI")
    print("-" * 58)
    try:
        mesh = read_tri(tri_path)
        before = diagnose(mesh)
    except Exception as exc:
        print(f"[ОШИБКА] TRI не удалось прочитать: {exc}")
        error_report = project / "03_repair_logs" / "scan" / f"{tri_path.stem}_{timestamp}_read_error.json"
        save_report(error_report, {"file": str(tri_path), "error": str(exc)})
        print(f"Отчёт: {error_report}")
        return None

    for line in format_diagnostics(before):
        print(line)
    scan_report = project / "03_repair_logs" / "scan" / f"{tri_path.stem}_{timestamp}_scan.json"
    save_report(scan_report, {"file": str(tri_path), "diagnostics": before})
    print(f"Первичный отчёт: {scan_report}")

    if before["boundary_edges"]:
        print("[!] Обнаружены открытые границы. Они отмечены в отчёте, но автоматически не закрываются.")
    if before["nonmanifold_edges"]:
        print("[!] Обнаружены неманифолдные рёбра. Требуется проверка геометрии.")
    current_path = tri_path
    if offer_repair and before["safe_repairs_available"]:
        answer = input("Выполнить безопасное структурное исправление? [Y/n]: ").strip().lower()
        if answer in ("", "y", "yes", "д", "да"):
            repaired_mesh, changes = repair(mesh)
            repaired_path = project / "02_working_meshes" / "repaired" / f"{tri_path.stem}_repaired.tri"
            write_tri(repaired_path, repaired_mesh)
            after = diagnose(repaired_mesh)
            repair_report = project / "03_repair_logs" / "repair_log" / f"{tri_path.stem}_{timestamp}_repair.json"
            final_report = project / "03_repair_logs" / "final_scan" / f"{tri_path.stem}_{timestamp}_final_scan.json"
            save_report(repair_report, {"source": str(tri_path), "output": str(repaired_path), "changes": changes})
            save_report(final_report, {"file": str(repaired_path), "diagnostics": after})
            print("\nРезультат безопасного структурного исправления")
            print("-" * 58)
            for key, value in changes.items():
                print(f"{key}: {value}")
            for line in format_diagnostics(after):
                print(line)
            print(f"Исправленный TRI: {repaired_path}")
            print(f"Лог исправления : {repair_report}")
            print(f"Финальный отчёт : {final_report}")
            current_path = repaired_path
        else:
            print("Структурное исправление пропущено. Исходный TRI сохранён без изменений.")
    elif before["safe_repairs_available"] == 0:
        print("Безопасных структурных исправлений не требуется.")

    if offer_repair:
        aerodynamic_path = mach_diagnostics_workflow(current_path, project)
        return aerodynamic_path or (current_path if current_path != tri_path else None)
    return None


def diagnose_existing_tri() -> None:
    project = default_project_path()
    if not project.exists():
        print("Проект по умолчанию не найден.")
        return
    tri_text = input("Путь к TRI относительно проекта или полный путь: ").strip().strip('"')
    if not tri_text:
        print("Отменено.")
        return
    tri_path = Path(tri_text)
    if not tri_path.is_absolute():
        tri_path = project / tri_path
    if not tri_path.exists():
        print(f"Файл не найден: {tri_path}")
        return
    tri_diagnostics_workflow(tri_path, project, offer_repair=True)


def import_tri() -> None:
    project = default_project_path()
    if not project.exists():
        print("Проект по умолчанию не найден. Сначала создайте или выберите проект.")
        return

    src_text = input("Полный путь к TRI-файлу: ").strip().strip('"')
    if not src_text:
        print("Отменено.")
        return

    src = Path(src_text)
    if not src.exists():
        print(f"Файл не найден: {src}")
        return

    case = input("Имя кейса, например MIG_full_M15_a0_raw: ").strip()
    if not case:
        case = src.stem
    if not case.lower().endswith(".tri"):
        dst_name = case + ".tri"
    else:
        dst_name = case

    dst = project / "02_working_meshes" / "input" / dst_name
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    print(f"TRI импортирован: {dst}")
    tri_diagnostics_workflow(dst, project, offer_repair=True)


def certified_machline_force_contract(tri_path: Path) -> tuple[dict, Path] | None:
    """Validate the force mask adjacent to a certified MachLine TRI.

    No mask means a legacy, uncertified TRI.  A present mask is fail-closed:
    stale or incomplete certification evidence must never become a legacy run.
    """
    mask_path = tri_path.parent / "force_integration_mask.json"
    if not mask_path.is_file():
        return None
    payload = json.loads(mask_path.read_text(encoding="utf-8"))
    if payload.get("schema") != "repairmach.machline-force-mask/1.0":
        raise ValueError("Рядом с TRI найден файл маски неизвестной версии")
    expected_hash = str(payload.get("certified_tri_sha256") or "")
    if not expected_hash or geometry_sha256(tri_path) != expected_hash:
        raise ValueError("Маска сил не соответствует выбранному certified_mesh.tri")
    include = {int(value) for value in payload.get("include_component_ids", [])}
    exclude = {int(value) for value in payload.get("exclude_component_ids", [])}
    if not include or include & exclude:
        raise ValueError("Маска сил пуста или содержит пересекающиеся компоненты")
    components = set(read_tri(tri_path).components)
    if include | exclude != components:
        raise ValueError("Маска сил не классифицирует все компоненты certified_mesh.tri")
    if payload.get("output_kind") != "masked_thick_body_pressure_wave":
        raise ValueError("Сертификат не разрешает требуемый вид результата MachLine")
    return payload, mask_path


def _resolve_machline_path(value: object, working_dir: Path) -> Path:
    path = Path(str(value or ""))
    if not path.is_absolute():
        path = (working_dir / path).resolve()
    return path


def _nascart_conditions(report: dict) -> dict[str, float]:
    velocity = [float(value) for value in report["input"]["flow"]["freestream_velocity"]]
    if len(velocity) != 3 or not all(math.isfinite(value) for value in velocity):
        raise ValueError("Некорректный freestream_velocity сертифицированного MachLine")
    speed = math.sqrt(sum(value * value for value in velocity))
    if speed <= 0.0:
        raise ValueError("Нулевая скорость сертифицированного MachLine")
    return {
        "mach": float(report["input"]["flow"]["freestream_mach_number"]),
        "alpha_deg": math.degrees(math.atan2(velocity[1], velocity[0])),
        "beta_deg": math.degrees(
            math.asin(max(-1.0, min(1.0, -velocity[2] / speed)))
        ),
    }


def build_machline_json() -> None:
    settings = load_settings()
    project = default_project_path()
    if not project.exists():
        print("Проект по умолчанию не найден.")
        return

    tri_text = input("Путь к ready TRI относительно проекта или полный путь: ").strip().strip('"')
    if not tri_text:
        print("Отменено.")
        return
    tri = Path(tri_text)
    if not tri.is_absolute():
        tri = project / tri
    tri = tri.resolve()
    if not tri.is_file():
        print(f"TRI не найден: {tri}")
        return
    try:
        certified_contract = certified_machline_force_contract(tri)
    except Exception as exc:
        print(f"Сертифицированный TRI отклонён: {exc}")
        return

    case = input("Имя расчёта, например MIG_full_M15_a0: ").strip()
    if not case:
        case = tri.stem.replace("_ready", "")
    if certified_contract:
        case_token = "".join(
            char if char.isalnum() or char in "_-" else "_" for char in case
        )[:80] or "case"
        staged_dir = project / "04_machline_input" / "certified" / case_token
        staged_dir.mkdir(parents=True, exist_ok=True)
        staged_tri = staged_dir / "certified_mesh.tri"
        staged_mask = staged_dir / "force_integration_mask.json"
        shutil.copy2(tri, staged_tri)
        shutil.copy2(certified_contract[1], staged_mask)
        tri = staged_tri.resolve()
        certified_contract = certified_machline_force_contract(tri)

    mach = input(f"Mach, Enter = {settings['defaults']['mach']}: ").strip()
    alpha = input(f"alpha_deg, Enter = {settings['defaults']['alpha_deg']}: ").strip()
    beta = input(f"beta_deg, Enter = {settings['defaults']['beta_deg']}: ").strip()

    mach = float(mach) if mach else float(settings["defaults"]["mach"])
    alpha = float(alpha) if alpha else float(settings["defaults"]["alpha_deg"])
    beta = float(beta) if beta else float(settings["defaults"]["beta_deg"])
    default_solver = settings["defaults"].get("solver", "GMRES")
    default_formulation = settings["defaults"].get("formulation", "dirichlet-morino")
    default_offset = settings["defaults"].get("control_point_offset", 1.0e-5)
    default_trefftz = settings["defaults"].get("trefftz_distance", 20.0)
    default_wake_angle = settings["defaults"].get("wake_shedding_angle", 169.0)
    if certified_contract:
        solver = "GMRES"
        formulation = "neumann-doublet-only-mass-flux"
        control_offset = 0.001
        wake_present = False
        print(
            "Распознан сертифицированный MachLine-двойник: "
            "строгая постановка и маска сил включены автоматически."
        )
    else:
        solver = input(f"Решатель, Enter = {default_solver}: ").strip() or default_solver
        formulation = input(f"Формулировка, Enter = {default_formulation}: ").strip() or default_formulation
        offset_text = input(f"control_point_offset, Enter = {default_offset:g}: ").strip()
        wake_answer = input("Добавить след? [Y/n]: ").strip().lower()
        wake_present = wake_answer in ("", "y", "yes", "д", "да")
        control_offset = float(offset_text) if offset_text else float(default_offset)
    trefftz = default_trefftz
    wake_angle = default_wake_angle
    if wake_present:
        trefftz_text = input(f"Trefftz distance, Enter = {default_trefftz:g}: ").strip()
        angle_text = input(f"Wake shedding angle, Enter = {default_wake_angle:g}: ").strip()
        trefftz = float(trefftz_text) if trefftz_text else float(default_trefftz)
        wake_angle = float(angle_text) if angle_text else float(default_wake_angle)

    out_vtk = project / "05_machline_results" / "vtk" / f"{case}_body.vtk"
    out_wake = project / "05_machline_results" / "wake" / f"{case}_wake.vtk"
    out_cp = project / "05_machline_results" / "control_points" / f"{case}_control_points.vtk"
    out_report = project / "05_machline_results" / "reports" / f"{case}_report.json"
    for output in (out_vtk, out_wake, out_cp, out_report):
        output.parent.mkdir(parents=True, exist_ok=True)

    ref = {}
    project_cfg_path = project / "project_config.json"
    if project_cfg_path.exists():
        ref = json.loads(project_cfg_path.read_text(encoding="utf-8")).get("default_reference", {})
    if not ref:
        ref = {
            "area": settings["defaults"]["area"],
            "length": settings["defaults"]["longitudinal_length"],
            "CG": settings["defaults"]["center"]
        }
    else:
        # Normalize RepairMach project keys to the names expected by MachLine.
        if "length" not in ref and "longitudinal_length" in ref:
            ref["length"] = ref["longitudinal_length"]
        if "CG" not in ref and "center" in ref:
            ref["CG"] = ref["center"]

    area_default = float(ref.get("area", settings["defaults"]["area"]))
    length_default = float(ref.get("length", settings["defaults"]["longitudinal_length"]))
    cg_default = ref.get("CG", settings["defaults"]["center"])
    area_text = input(f"Опорная площадь Sref, Enter = {area_default:g}: ").strip()
    length_text = input(f"Опорная длина (САХ), Enter = {length_default:g}: ").strip()
    cg_text = input(
        "Центр масс X Y Z, Enter = " + " ".join(f"{float(value):g}" for value in cg_default) + ": "
    ).strip()
    area = float(area_text) if area_text else area_default
    length = float(length_text) if length_text else length_default
    cg = list(parse_flow(cg_text)) if cg_text else [float(value) for value in cg_default]
    if area <= 0.0 or length <= 0.0:
        raise ValueError("Опорные площадь и длина должны быть положительными")
    ref = {"area": area, "length": length, "CG": cg}
    if certified_contract:
        # OpenVSP XYZ -> NASCART X,Z,-Y.
        ref["CG"] = [cg[0], cg[2], -cg[1]]

    wake_model = {
        "wake_present": wake_present,
        "append_wake": wake_present,
    }
    if wake_present:
        wake_model.update({
            "trefftz_distance": trefftz,
            "wake_shedding_angle": wake_angle,
        })

    ml = {
        "flow": {
            "freestream_velocity": (
                [100.0 * value for value in nascart_freestream(alpha, beta)]
                if certified_contract else freestream_vector(alpha, beta)
            ),
            "gamma": 1.4,
            "freestream_mach_number": mach
        },
        "geometry": {
            "file": (
                tri.relative_to(project.resolve()).as_posix()
                if certified_contract else str(tri).replace("\\", "/")
            ),
            "spanwise_axis": "+z" if certified_contract else "+y",
            "wake_model": wake_model,
            "reference": ref
        },
        "solver": {
            "formulation": formulation,
            "matrix_solver": solver,
            "control_point_offset": control_offset,
        },
        "post_processing": {
            "pressure_rules": {
                "isentropic": True
            },
            "pressure_for_forces": "isentropic",
        },
        "output": {
            "body_file": (
                out_vtk.relative_to(project).as_posix()
                if certified_contract else str(out_vtk).replace("\\", "/")
            ),
            "wake_file": (
                "none" if certified_contract else str(out_wake).replace("\\", "/")
            ),
            "control_point_file": (
                "none" if certified_contract else str(out_cp).replace("\\", "/")
            ),
            "report_file": (
                out_report.relative_to(project).as_posix()
                if certified_contract else str(out_report).replace("\\", "/")
            )
        }
    }

    dst = project / "04_machline_input" / "json" / f"{case}.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(ml, ensure_ascii=False, indent=2), encoding="utf-8")
    if certified_contract:
        contract, mask_path = certified_contract
        save_report(dst.with_name(dst.stem + "_force_contract.json"), {
            "schema": "repairmach.machline-input-contract/1.0",
            "input": str(dst.resolve()),
            "input_sha256": geometry_sha256(dst),
            "tri": str(tri),
            "tri_sha256": geometry_sha256(tri),
            "force_mask": str(mask_path.resolve()),
            "force_mask_sha256": geometry_sha256(mask_path),
            "working_directory": str(project.resolve()),
            "base_drag_replacement_required": bool(
                contract.get("base_drag_replacement_required")
            ),
        })
    print(f"MachLine JSON создан: {dst}")


def run_machline() -> None:
    settings = load_settings()
    mach = find_machline(settings)
    cwd = machline_working_dir(settings)

    if not mach.exists():
        print(f"MachLine не найден: {mach}")
        return

    json_text = input("Полный путь к MachLine JSON: ").strip().strip('"')
    if not json_text:
        print("Отменено.")
        return
    json_path = Path(json_text)
    if not json_path.exists():
        print(f"JSON не найден: {json_path}")
        return
    execution_cwd = cwd
    input_contract_path = json_path.with_name(json_path.stem + "_force_contract.json")
    if input_contract_path.is_file():
        try:
            input_contract = json.loads(input_contract_path.read_text(encoding="utf-8"))
            if input_contract.get("schema") != "repairmach.machline-input-contract/1.0":
                raise ValueError("неизвестная схема контракта")
            if input_contract.get("input_sha256") != geometry_sha256(json_path):
                raise ValueError("MachLine JSON изменён после построения контракта")
            execution_cwd = Path(str(input_contract.get("working_directory", ""))).resolve()
            if not execution_cwd.is_dir():
                raise ValueError("рабочая папка сертифицированного запуска отсутствует")
        except Exception as exc:
            print(f"Сертифицированный запуск отклонён: {exc}")
            return

    print("\nЗапускаем MachLine как двигатель...")
    print(f"EXE : {mach}")
    print(f"JSON: {json_path}")
    print(f"CWD : {execution_cwd}\n")

    log_dir = ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"machline_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    try:
        child_env = os.environ.copy()
        child_env.setdefault("OMP_NUM_THREADS", "4")
        p = subprocess.run(
            [str(mach)],
            input=str(json_path) + "\n",
            text=True,
            cwd=str(execution_cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=None,
            env=child_env
        )
        log_path.write_text(p.stdout or "", encoding="utf-8", errors="ignore")
        print(p.stdout)
        print(f"\nЛог сохранён: {log_path}")
        print(f"Код завершения: {p.returncode}")
        if p.returncode != 0:
            raise RuntimeError(f"MachLine завершился с кодом {p.returncode}")
        input_payload = json.loads(json_path.read_text(encoding="utf-8"))
        report_value = input_payload.get("output", {}).get("report_file")
        if not report_value:
            raise RuntimeError("В MachLine JSON не указан output.report_file")
        report_path = Path(report_value)
        if not report_path.is_absolute():
            report_path = (execution_cwd / report_path).resolve()
        if not report_path.is_file():
            raise RuntimeError(f"MachLine не создал отчёт: {report_path}")
        report = load_machline_report(report_path)
        status_code = int(report["solver_results"]["solver_status_code"])
        residual = report["solver_results"]["residual"]
        residual_norm = float(residual["norm"])
        residual_max = float(residual["max"])
        if (
            status_code != 0
            or not all(math.isfinite(value) for value in (residual_norm, residual_max))
        ):
            raise RuntimeError("MachLine report не прошёл контроль статуса и невязки")
        geometry_value = report.get("input", {}).get("geometry", {}).get("file")
        geometry_path = _resolve_machline_path(geometry_value, execution_cwd)
        if not geometry_path.is_file():
            raise RuntimeError("TRI из MachLine report отсутствует")
        certified_contract = certified_machline_force_contract(geometry_path)
        masked_record_path = None
        masked = None
        if certified_contract:
            if residual_norm > 0.05 or residual_max > 0.01:
                raise RuntimeError(
                    "Сертифицированный MachLine не прошёл пределы невязки "
                    "(norm <= 0.05, max <= 0.01)"
                )
            force_contract, force_mask_path = certified_contract
            body_value = input_payload.get("output", {}).get("body_file")
            body_path = _resolve_machline_path(body_value, execution_cwd)
            if not body_path.is_file():
                raise RuntimeError("MachLine не создал body VTK для маскированных сил")
            mesh = read_tri(geometry_path)
            reference = report.get("input", {}).get("geometry", {}).get("reference", {})
            masked = masked_force_coefficients(
                mesh=mesh,
                body_vtk_path=body_path,
                reference_area=float(reference.get("area", 0.0)),
                include_component_ids=force_contract.get("include_component_ids", []),
                exclude_component_ids=force_contract.get("exclude_component_ids", []),
                report_total_forces=report.get("total_forces", {}),
                freestream_velocity=report["input"]["flow"]["freestream_velocity"],
                spanwise_axis="+z",
            )
            masked_record_path = report_path.with_name(
                report_path.stem + "_masked_force.json"
            )
            write_masked_force_record(
                masked_record_path,
                mesh_path=geometry_path,
                body_vtk_path=body_path,
                report_path=report_path,
                force_mask_path=force_mask_path,
                result=masked,
            )
            conditions = _nascart_conditions(report)
            axes = {
                **conditions,
                **masked["masked_wind_axes"],
                **{
                    key.lower(): value
                    for key, value in masked["masked_mesh_axes"].items()
                },
            }
        else:
            axes = machline_wind_axes(report)
        manifest_path = report_path.with_name(report_path.stem + "_manifest.json")
        manifest = {
            "schema": "repairmach.machline-run/1.0",
            "repairmach_version": settings.get("repairmach_version"),
            "status": "completed",
            "solver": {
                "path": str(mach.resolve()),
                "sha256": geometry_sha256(mach),
            },
            "input": {
                "path": str(json_path.resolve()),
                "sha256": geometry_sha256(json_path),
            },
            "geometry": {
                "path": str(geometry_path.resolve()),
                "sha256": geometry_sha256(geometry_path),
            },
            "conditions": {
                "mach": axes["mach"],
                "alpha_deg": axes["alpha_deg"],
                "beta_deg": axes.get("beta_deg", 0.0),
            },
            "outputs": {
                "report": str(report_path.resolve()),
                "log": str(log_path.resolve()),
            },
            "output_sha256": {
                "report": geometry_sha256(report_path),
                "log": geometry_sha256(log_path),
            },
            "output_quality": {
                "valid": True,
                "solver_status_code": status_code,
                "residual_norm": residual_norm,
                "residual_max": residual_max,
                "certified_force_mask_applied": bool(certified_contract),
                "force_mask_alignment_verified": bool(
                    masked and masked.get("alignment", {}).get("verified")
                ),
            },
        }
        if masked and masked_record_path:
            force_contract, force_mask_path = certified_contract
            manifest["force_output"] = {
                "kind": "masked_thick_body_pressure_wave",
                "mesh_axes": masked["masked_mesh_axes"],
                "wind_axes": masked["masked_wind_axes"],
                "base_drag_replacement_required": bool(
                    force_contract.get("base_drag_replacement_required")
                ),
                "thin_surfaces_delegated_to_vspaero": True,
                "usable_as_standalone_total_force": not bool(
                    force_contract.get("base_drag_replacement_required")
                ),
            }
            manifest["outputs"]["masked_force"] = str(masked_record_path.resolve())
            manifest["outputs"]["force_mask"] = str(force_mask_path.resolve())
            manifest["output_sha256"]["masked_force"] = geometry_sha256(masked_record_path)
            manifest["output_sha256"]["force_mask"] = geometry_sha256(force_mask_path)
        save_report(manifest_path, manifest)
        print(f"Манифест запуска: {manifest_path}")
        if masked:
            wind = masked["masked_wind_axes"]
            print(
                "Сертифицированные маскированные силы: "
                f"CD={wind['cd']:.8g}; CL={wind['cl']:.8g}; "
                f"CY={wind['cy_span']:.8g}"
            )
            if certified_contract[0].get("base_drag_replacement_required"):
                print(
                    "Важно: это вклад давления/волнового сопротивления толстого тела; "
                    "донное сопротивление добавляет гибридная методика."
                )
    except Exception as e:
        print(f"Ошибка запуска MachLine: {e}")


def import_vsp3_and_run_vspaero() -> None:
    settings = load_settings()
    project = default_project_path()
    if not project.exists():
        print("Проект по умолчанию не найден. Сначала создайте или выберите проект.")
        return

    openvsp_dir = find_openvsp_dir(settings.get("openvsp", {}).get("install_dir"))
    tools = tool_paths(openvsp_dir)
    executable = tools["vspscript"]
    if executable is None or not executable.is_file() or tools["vspaero"] is None or not tools["vspaero"].is_file():
        print("OpenVSP/VSPAERO не найдены. Проверьте раздел openvsp в настройках.")
        return

    source_text = input("Полный путь к модели OpenVSP .vsp3: ").strip().strip('"')
    if not source_text:
        print("Отменено.")
        return
    source = Path(source_text)
    if not source.is_file() or source.suffix.lower() != ".vsp3":
        print(f"Не найден корректный файл .vsp3: {source}")
        return

    defaults = settings.get("vspaero", {})
    reference = load_project_reference(project, settings)
    print("\nСоглашение по OpenVSP Sets:")
    print("- Set_1: фюзеляж и мотогондолы — толстые поверхности VSPAERO")
    print("- Set_2: крыло и оперение — тонкие несущие поверхности VSPAERO")
    print(f"  В API это индексы {user_set_to_api_index(1)} и {user_set_to_api_index(2)}.")
    print("Перед расчётом программа проверит пустые, пересекающиеся и неразмеченные детали.")

    tail_geometry_name = None
    tail_incidence_deg = 0.0
    try:
        mach_start = float(input(f"Mach начальный, Enter = {defaults.get('mach_start', 0.8)}: ").strip() or defaults.get("mach_start", 0.8))
        mach_end = float(input(f"Mach конечный, Enter = {defaults.get('mach_end', 0.8)}: ").strip() or defaults.get("mach_end", 0.8))
        mach_points = int(input(f"Число точек Mach, Enter = {defaults.get('mach_points', 1)}: ").strip() or defaults.get("mach_points", 1))
        alpha_start = float(input(f"Alpha начальный, град, Enter = {defaults.get('alpha_start', 0.0)}: ").strip() or defaults.get("alpha_start", 0.0))
        alpha_end = float(input(f"Alpha конечный, град, Enter = {defaults.get('alpha_end', 24.0)}: ").strip() or defaults.get("alpha_end", 24.0))
        alpha_points = int(input(f"Число точек alpha, Enter = {defaults.get('alpha_points', 13)}: ").strip() or defaults.get("alpha_points", 13))
        beta_deg = float(input(f"Beta, град, Enter = {defaults.get('beta_deg', 0.0)}: ").strip() or defaults.get("beta_deg", 0.0))
        ncpu = int(input(f"Потоков VSPAERO, Enter = {defaults.get('ncpu', 4)}: ").strip() or defaults.get("ncpu", 4))
        tail_answer = input("Задать балансировочный угол ГО? [y/N]: ").strip().lower()
        if tail_answer in ("y", "yes", "д", "да"):
            tail_geometry_name = input(
                f"Точное имя геометрии ГО, Enter = {defaults.get('tail_geometry_name', 'GO')}: "
            ).strip() or defaults.get("tail_geometry_name", "GO")
            tail_incidence_deg = float(input("Абсолютный угол установки ГО, град: ").strip())

        print("\nОпорные величины проекта; подтвердите или исправьте их для импортируемого самолёта.")
        sref = float(input(f"Sref, Enter = {reference['area']}: ").strip() or reference["area"])
        cref = float(input(f"cref/САХ, Enter = {reference['longitudinal_length']}: ").strip() or reference["longitudinal_length"])
        bref = float(input(f"bref/размах, Enter = {reference['lateral_length']}: ").strip() or reference["lateral_length"])
        center_text = input("CG X Y Z, Enter = " + " ".join(str(value) for value in reference["center"]) + ": ").strip()
        center = list(parse_flow(center_text)) if center_text else reference["center"]
    except ValueError as exc:
        print(f"Некорректные параметры VSPAERO: {exc}")
        return

    imported = unique_import_path(project / "00_original" / "vsp", source.name)
    shutil.copy2(source, imported)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    model_token = safe_vsp3_name(source.name)[:24]
    run_name = f"{model_token}_vspaero_{timestamp}"
    run_dir = project / "06_vspaero_results" / "runs" / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    working_model = run_dir / "model.vsp3"
    shutil.copy2(imported, working_model)

    script = project / "06_vspaero_input" / "scripts" / f"{run_name}.vspscript"
    results_csv = project / "06_vspaero_results" / "csv" / f"{run_name}.csv"
    log_path = project / "06_vspaero_results" / "logs" / f"{run_name}.log"
    set_report_path = run_dir / "set_validation.json"
    manifest_path = run_dir / "run_manifest.json"

    try:
        generate_vspaero_sweep_script(
            script,
            working_model,
            results_csv,
            mach_start=mach_start,
            mach_end=mach_end,
            mach_points=mach_points,
            alpha_start=alpha_start,
            alpha_end=alpha_end,
            alpha_points=alpha_points,
            beta_deg=beta_deg,
            reference_area=sref,
            reference_chord=cref,
            reference_span=bref,
            center=center,
            fuselage_user_set=1,
            wing_user_set=2,
            ncpu=ncpu,
            tail_geometry_name=tail_geometry_name,
            tail_incidence_deg=tail_incidence_deg,
            require_canonical_components=True,
        )
    except Exception as exc:
        print(f"Не удалось подготовить VSPAERO: {exc}")
        return

    print(f"\nVSP3 импортирован: {imported}")
    print("Проверяем Set_1/Set_2 и автоматически запускаем VSPAERO...")
    try:
        return_code = run_vspscript(executable, script, log_path, run_dir)
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
        set_report = parse_set_report(log_text)
        save_report(set_report_path, set_report)
    except Exception as exc:
        print(f"Ошибка запуска VSPAERO: {exc}")
        return

    print("\nПроверка наборов OpenVSP")
    print("-" * 58)
    for role, item in set_report["roles"].items():
        print(
            f"{role}: Set_{item['USER_SET']} / API {item['API_INDEX']} / "
            f"{item['NAME']} / деталей: {item['COUNT']}"
        )
    for item in set_report["geometries"]:
        print(
            f"- {item.get('NAME')} [{item.get('TYPE')}]: "
            f"Set_1={item.get('IN_SET_1')}, Set_2={item.get('IN_SET_2')}"
        )
    if not set_report["valid"]:
        print("\n[ОСТАНОВЛЕНО] Разметка Set_1/Set_2 не прошла проверку:")
        for error in set_report["errors"]:
            print(f"- {error}")
        print("Исправьте наборы в OpenVSP и импортируйте VSP3 повторно.")
        print(f"Отчёт: {set_report_path}")
        print(f"Лог   : {log_path}")
        return

    polar_in_run = find_generated_polar(run_dir, working_model.stem)
    polar_output = project / "06_vspaero_results" / "polars" / f"{run_name}.polar"
    if not set_report["calculation_complete"] or polar_in_run is None or not results_csv.is_file():
        print("\n[ОШИБКА] Наборы корректны, но VSPAERO не создал полный комплект результатов.")
        print(f"Лог: {log_path}")
        return
    if return_code not in (0, 1):
        print(f"\n[ОШИБКА] VSPAERO завершился с недопустимым кодом {return_code}.")
        print(f"Лог: {log_path}")
        return
    try:
        output_quality = validate_vspaero_run_outputs(
            polar_in_run,
            log_path,
            mach_start=mach_start,
            mach_end=mach_end,
            mach_points=mach_points,
            alpha_start=alpha_start,
            alpha_end=alpha_end,
            alpha_points=alpha_points,
        )
    except (OSError, ValueError) as exc:
        print(f"\n[ОШИБКА] Результат VSPAERO не прошёл контроль полноты: {exc}")
        print(f"Лог: {log_path}")
        return
    polar_output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(polar_in_run, polar_output)

    manifest = {
        "schema": "repairmach.vspaero-run/1.0",
        "repairmach_version": settings.get("repairmach_version"),
        "executables": executable_fingerprints({
            "openvsp": executable,
            "vspaero": executable.with_name("vspaero.exe"),
        }),
        "source_vsp3": str(source.resolve()),
        "source_vsp3_sha256": geometry_sha256(source),
        "imported_vsp3": str(imported.resolve()),
        "imported_vsp3_sha256": geometry_sha256(imported),
        "working_vsp3": str(working_model.resolve()),
        "working_vsp3_sha256": geometry_sha256(working_model),
        "sets": {
            "fuselage_and_nacelles": {"user_set": 1, "api_index": user_set_to_api_index(1)},
            "wing_and_empennage": {"user_set": 2, "api_index": user_set_to_api_index(2)},
        },
        "conditions": {
            "mach": {"start": mach_start, "end": mach_end, "points": mach_points},
            "alpha_deg": {"start": alpha_start, "end": alpha_end, "points": alpha_points},
            "beta_deg": beta_deg,
            "horizontal_tail": {
                "mode": "fixed_incidence" if tail_geometry_name else "model_default",
                "geometry_name": tail_geometry_name,
                "incidence_deg": tail_incidence_deg if tail_geometry_name else None,
            },
        },
        "reference": {"area": sref, "cref": cref, "bref": bref, "center": center},
        "outputs": {
            "polar": str(polar_output.resolve()),
            "results_csv": str(results_csv.resolve()),
            "set_validation": str(set_report_path.resolve()),
            "log": str(log_path.resolve()),
            "run_directory": str(run_dir.resolve()),
        },
        "output_quality": output_quality,
        "output_sha256": output_sha256({
            "polar": polar_output,
            "results_csv": results_csv,
            "set_validation": set_report_path,
            "log": log_path,
            "script": script,
        }),
        "vspscript_return_code": return_code,
        "status": "completed",
    }
    save_report(manifest_path, manifest)

    print("\nVSPAERO завершён успешно")
    print("-" * 58)
    print(f"POLAR    : {polar_output}")
    print(f"CSV      : {results_csv}")
    print(f"Манифест : {manifest_path}")
    print(f"Лог      : {log_path}")
    if return_code not in (0, 1):
        print(f"[!] Диагностический код vspscript: {return_code}")


def _run_standard_vspaero_case(
    *,
    settings: dict,
    project: Path,
    executable: Path,
    master_source: Path,
    solver_source: Path,
    imported: Path,
    case: dict,
    beta_deg: float,
    ncpu: int,
    reference: dict,
    study_timestamp: str,
    tail_geometry_name: str | None,
    tail_incidence_deg: float,
    scenario_id: str,
    scenario_sha256: str | None,
    naming_policy: dict,
    solver_controls: dict,
    solver_mode: str = "mixed",
    geometry_certificate: dict | None = None,
) -> dict:
    model_token = safe_vsp3_name(master_source.name)[:24]
    run_name = f"{model_token}_{case['name']}_{study_timestamp}"
    run_dir = project / "06_vspaero_results" / "runs" / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    working_model = run_dir / "model.vsp3"
    shutil.copy2(imported, working_model)

    script = project / "06_vspaero_input" / "scripts" / f"{run_name}.vspscript"
    results_csv = project / "06_vspaero_results" / "csv" / f"{run_name}.csv"
    log_path = project / "06_vspaero_results" / "logs" / f"{run_name}.log"
    set_report_path = run_dir / "set_validation.json"
    manifest_path = run_dir / "run_manifest.json"

    manifest = {
        "schema": "repairmach.vspaero-run/1.0",
        "repairmach_version": settings.get("repairmach_version"),
        "executables": executable_fingerprints({
            "openvsp": executable,
            "vspaero": executable.with_name("vspaero.exe"),
        }),
        "scenario_id": scenario_id,
        "scenario_sha256": scenario_sha256,
        "preset": case["name"],
        "parent_case_name": case.get("parent_case_name"),
        "point_execution": case.get("point_execution", "sweep"),
        "engine_boundary": case.get("engine_boundary"),
        "vspaero_mode": solver_mode,
        "geometry_certificate": geometry_certificate,
        "source_vsp3": str(master_source.resolve()),
        "source_vsp3_sha256": geometry_sha256(master_source),
        "solver_geometry": {
            "path": str(solver_source.resolve()),
            "sha256": geometry_sha256(solver_source),
        },
        "solver_geometry_sha256": geometry_sha256(solver_source),
        "imported_vsp3": str(imported.resolve()),
        "imported_vsp3_sha256": geometry_sha256(imported),
        "working_vsp3": str(working_model.resolve()),
        "working_vsp3_sha256": geometry_sha256(working_model),
        "sets": {
            "fuselage_and_nacelles": {
                "user_set": 1 if solver_mode == "mixed" else 3,
                "api_index": user_set_to_api_index(1 if solver_mode == "mixed" else 3),
                "included": solver_mode == "mixed",
            },
            "wing_and_empennage": {"user_set": 2, "api_index": user_set_to_api_index(2)},
        },
        "conditions": {
            "mach": {
                "start": case["mach_start"],
                "end": case["mach_end"],
                "points": case["mach_points"],
                "step": (
                    (case["mach_end"] - case["mach_start"]) / (case["mach_points"] - 1)
                    if case["mach_points"] > 1 else 0.0
                ),
            },
            "alpha_deg": {
                "start": case["alpha_start"],
                "end": case["alpha_end"],
                "points": case["alpha_points"],
                "step": (
                    (case["alpha_end"] - case["alpha_start"]) / (case["alpha_points"] - 1)
                    if case["alpha_points"] > 1 else 0.0
                ),
            },
            "beta_deg": beta_deg,
            "horizontal_tail": {
                "mode": "fixed_incidence" if tail_geometry_name else "model_default",
                "geometry_name": tail_geometry_name,
                "incidence_deg": tail_incidence_deg if tail_geometry_name else None,
            },
        },
        "reference": reference,
        "numerical_controls": {
            "forward_gmres_convergence_factor": float(
                solver_controls.get("forward_gmres_convergence_factor", 1.0)
            ),
            "wake_num_iter": int(solver_controls.get("wake_num_iter", 8)),
            "num_wake_nodes": int(solver_controls.get("num_wake_nodes", 24)),
            "wake_relax": float(solver_controls.get("wake_relax", 0.8)),
            "point_timeout_seconds": float(solver_controls.get("point_timeout_seconds", 900.0)),
        },
        "outputs": {
            "script": str(script.resolve()),
            "results_csv": str(results_csv.resolve()),
            "set_validation": str(set_report_path.resolve()),
            "log": str(log_path.resolve()),
            "run_directory": str(run_dir.resolve()),
        },
        "status": "prepared",
    }

    try:
        generate_vspaero_sweep_script(
            script,
            working_model,
            results_csv,
            mach_start=case["mach_start"],
            mach_end=case["mach_end"],
            mach_points=case["mach_points"],
            alpha_start=case["alpha_start"],
            alpha_end=case["alpha_end"],
            alpha_points=case["alpha_points"],
            beta_deg=beta_deg,
            reference_area=reference["area"],
            reference_chord=reference["cref"],
            reference_span=reference["bref"],
            center=reference["center"],
            fuselage_user_set=1 if solver_mode == "mixed" else 3,
            wing_user_set=2,
            ncpu=ncpu,
            forward_gmres_convergence_factor=manifest["numerical_controls"]["forward_gmres_convergence_factor"],
            wake_num_iter=manifest["numerical_controls"]["wake_num_iter"],
            num_wake_nodes=manifest["numerical_controls"]["num_wake_nodes"],
            wake_relax=manifest["numerical_controls"]["wake_relax"],
            tail_geometry_name=tail_geometry_name,
            tail_incidence_deg=tail_incidence_deg,
            engine_boundary=case.get("engine_boundary"),
            require_canonical_components=True,
            require_nonempty_fuselage_set=(solver_mode == "mixed"),
            require_all_geometries_assigned=(solver_mode == "mixed"),
        )
        print(
            f"\nЗапуск {case['name']}: M={case['mach_start']:.1f}…{case['mach_end']:.1f} "
            f"({case['mach_points']} точек), "
            f"alpha={case['alpha_start']:g}…{case['alpha_end']:g}° "
            f"({case['alpha_points']} точек)"
        )
        return_code = run_vspscript(
            executable,
            script,
            log_path,
            run_dir,
            timeout_seconds=manifest["numerical_controls"]["point_timeout_seconds"],
        )
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
        set_report = parse_set_report(log_text)
        set_report["component_names"] = validate_component_names(
            set_report["geometries"], naming_policy,
            allow_excluded_thick=(solver_mode == "lifting"),
        )
        save_report(set_report_path, set_report)
        manifest["vspscript_return_code"] = return_code
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["error"] = str(exc)
        save_report(manifest_path, manifest)
        return manifest

    print("Проверка Set_1/Set_2:")
    for item in set_report["geometries"]:
        print(
            f"- {item.get('NAME')} [{item.get('TYPE')}]: "
            f"Set_1={item.get('IN_SET_1')}, Set_2={item.get('IN_SET_2')}"
        )
    if not set_report["valid"]:
        manifest["status"] = "set_validation_failed"
        manifest["errors"] = set_report["errors"]
        save_report(manifest_path, manifest)
        print("[ОСТАНОВЛЕНО] Разметка Set_1/Set_2 некорректна:")
        for error in set_report["errors"]:
            print(f"- {error}")
        return manifest

    name_report = set_report["component_names"]
    manifest["component_name_validation"] = name_report
    if name_report["warnings"]:
        print("\nПредупреждения по именам компонентов:")
        for warning in name_report["warnings"]:
            print(f"- {warning}")
    if not name_report["valid"]:
        manifest["status"] = "component_name_validation_failed"
        manifest["errors"] = name_report["errors"]
        save_report(manifest_path, manifest)
        print("[ОСТАНОВЛЕНО] Имена или наборы компонентов не соответствуют методике 9.1:")
        for error in name_report["errors"]:
            print(f"- {error}")
        return manifest

    polar_in_run = find_generated_polar(run_dir, working_model.stem)
    if not set_report["calculation_complete"] or polar_in_run is None or not results_csv.is_file():
        manifest["status"] = "incomplete"
        manifest["error"] = "VSPAERO не создал POLAR и CSV"
        save_report(manifest_path, manifest)
        return manifest
    if return_code not in (0, 1):
        manifest["status"] = "solver_failed"
        manifest["error"] = f"Недопустимый код vspscript: {return_code}"
        save_report(manifest_path, manifest)
        return manifest

    polar_output = project / "06_vspaero_results" / "polars" / f"{run_name}.polar"
    polar_output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(polar_in_run, polar_output)
    manifest["outputs"]["polar"] = str(polar_output.resolve())
    try:
        manifest["output_quality"] = validate_vspaero_run_outputs(
            polar_output,
            log_path,
            mach_start=case["mach_start"],
            mach_end=case["mach_end"],
            mach_points=case["mach_points"],
            alpha_start=case["alpha_start"],
            alpha_end=case["alpha_end"],
            alpha_points=case["alpha_points"],
            max_log10_l2_residual=float(
                solver_controls.get("max_log10_l2_residual", -0.3)
            ),
        )
    except (OSError, ValueError) as exc:
        manifest["status"] = "output_validation_failed"
        manifest["error"] = str(exc)
        manifest["output_sha256"] = output_sha256({
            "polar": polar_output,
            "results_csv": results_csv,
            "set_validation": set_report_path,
            "log": log_path,
            "script": script,
        })
        save_report(manifest_path, manifest)
        return manifest
    manifest["output_sha256"] = output_sha256({
        "polar": polar_output,
        "results_csv": results_csv,
        "set_validation": set_report_path,
        "log": log_path,
        "script": script,
    })
    manifest["status"] = "completed"
    save_report(manifest_path, manifest)
    manifest["manifest_path"] = str(manifest_path.resolve())
    print(f"Готово: {polar_output}")
    return manifest


def run_standard_vspaero_study(scenario: dict | None = None) -> None:
    settings = load_settings()
    project = default_project_path()
    if not project.exists():
        print("Проект по умолчанию не найден. Сначала создайте или выберите проект.")
        return
    openvsp_dir = find_openvsp_dir(settings.get("openvsp", {}).get("install_dir"))
    tools = tool_paths(openvsp_dir)
    executable = tools["vspscript"]
    if executable is None or not executable.is_file() or tools["vspaero"] is None or not tools["vspaero"].is_file():
        print("OpenVSP/VSPAERO не найдены. Проверьте раздел openvsp в настройках.")
        return

    source_text = input("Полный путь к модели OpenVSP .vsp3: ").strip().strip('"')
    requested_source = Path(source_text) if source_text else Path()
    if not source_text or not requested_source.is_file() or requested_source.suffix.lower() != ".vsp3":
        print("Не найден корректный файл .vsp3.")
        return
    requested_source = requested_source.resolve()
    source = requested_source
    solver_mode = "mixed"
    certificate_binding = None
    try:
        certified = _certified_vspaero_study_geometry(
            project, requested_source, tools, scenario
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"[ОСТАНОВЛЕНО] Сертификат VSPAERO не принят: {exc}")
        return
    if certified is not None:
        source, certificate, certificate_path = certified
        backend = certificate["backends"]["vspaero"]
        expected = backend.get("solver_geometry", {}).get("sha256")
        solver_mode = str(backend.get("mode", "mixed"))
        certificate_binding = {
            "certificate_id": certificate.get("certificate_id"),
            "certificate_path": str(certificate_path.resolve()),
            "certificate_sha256": geometry_sha256(certificate_path),
            "master_path": str(requested_source),
            "master_sha256": certificate.get("master", {}).get("sha256_before"),
            "solver_geometry_path": str(source),
            "solver_geometry_sha256": expected,
            "selected_mode": solver_mode,
            "vspaero_mode": solver_mode,
            "reference": certificate.get("reference"),
            "scenario_id": scenario.get("id") if scenario else None,
            "scenario_sha256": sha256_payload(scenario) if scenario else None,
        }
        print(
            f"Сертификат {certificate.get('certificate_id')} принят: "
            f"автоматически выбран Fine-двойник VSPAERO ({solver_mode})."
        )
    elif scenario is None:
        print(
            "[НЕСЕРТИФИЦИРОВАННЫЙ ЗАПУСК] Интерактивные Mach/alpha/GO "
            "могут отличаться от запечатанной постановки. Расчёт выполняется "
            "на выбранном MASTER без certificate_binding."
        )

    if scenario is None:
        print("\nСтандартный сценарий VSPAERO:")
        print("[1] Дозвук: M=0,0…0,8, шаг 0,1")
        print("[2] Сверхзвук: M=1,2…2,2, шаг 0,1")
        print("[3] Всё вместе: два последовательных диапазона без M=0,9–1,1")
        print("Для всех вариантов: alpha=0…5°, шаг 1°; beta=0°.")
    else:
        print(f"\nСценарий: {scenario['title']}")
        print(scenario["description"])
        for case in scenario["vspaero_cases"]:
            print(
                f"- {case['name']}: M={case['mach_start']:g}…{case['mach_end']:g}; "
                f"alpha={case['alpha_start']:g}…{case['alpha_end']:g}°"
            )
    tail_geometry_name = None
    tail_incidence_deg = 0.0
    try:
        cases = (
            standard_vspaero_cases(input("Режим [1/2/3]: "))
            if scenario is None
            else expand_vspaero_cases([dict(item) for item in scenario["vspaero_cases"]])
        )
        defaults = settings.get("vspaero", {})
        ncpu = int(input(f"Потоков VSPAERO, Enter = {defaults.get('ncpu', 4)}: ").strip() or defaults.get("ncpu", 4))
        tail_policy = scenario.get("tail_incidence", "optional") if scenario else "optional"
        tail_answer = "y" if tail_policy == "required" else ""
        if tail_policy == "fixed":
            tail_geometry_name = str(scenario["tail_geometry_name"])
            tail_incidence_deg = float(scenario["tail_incidence_deg"])
            print(
                f"Фиксированный угол ГО из сценария: "
                f"{tail_geometry_name} = {tail_incidence_deg:g}°"
            )
        if tail_policy == "optional":
            tail_answer = input("Использовать заданный балансировочный угол ГО? [y/N]: ").strip().lower()
        if tail_answer in ("y", "yes", "д", "да"):
            tail_geometry_name = input(
                f"Точное имя геометрии ГО, Enter = {defaults.get('tail_geometry_name', 'GO')}: "
            ).strip() or defaults.get("tail_geometry_name", "GO")
            tail_incidence_deg = float(input("Абсолютный угол установки ГО, град: ").strip())
        if certificate_binding:
            certified_reference = certificate_binding["reference"]
            sref = float(certified_reference["area"])
            cref = float(certified_reference["cref"])
            bref = float(certified_reference["bref"])
            center = [float(value) for value in certified_reference["center"]]
            print("\nОпорные величины взяты из сертификата и заблокированы для этого запуска:")
            print(f"Sref={sref:g}; cref={cref:g}; bref={bref:g}; CG={center}")
        else:
            project_ref = load_project_reference(project, settings)
            print("\nПодтвердите опорные величины импортируемого самолёта.")
            sref = float(input(f"Sref, Enter = {project_ref['area']}: ").strip() or project_ref["area"])
            cref = float(input(f"cref/САХ, Enter = {project_ref['longitudinal_length']}: ").strip() or project_ref["longitudinal_length"])
            bref = float(input(f"bref/размах, Enter = {project_ref['lateral_length']}: ").strip() or project_ref["lateral_length"])
            center_text = input("CG X Y Z, Enter = " + " ".join(str(value) for value in project_ref["center"]) + ": ").strip()
            center = list(parse_flow(center_text)) if center_text else project_ref["center"]
    except ValueError as exc:
        print(f"Некорректные параметры: {exc}")
        return

    imported_master = unique_import_path(project / "00_original" / "vsp", requested_source.name)
    shutil.copy2(requested_source, imported_master)
    if source == requested_source:
        imported = imported_master
    else:
        imported = unique_import_path(
            project / "06_vspaero_input" / "models",
            f"{requested_source.stem}_certified_solver.vsp3",
        )
        shutil.copy2(source, imported)
    study_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    reference = {"area": sref, "cref": cref, "bref": bref, "center": center}
    study_results = []
    scenario_id = scenario["id"] if scenario else "standard_vspaero_interactive"
    scenario_fingerprint = sha256_payload(scenario) if scenario else None
    catalog = load_scenario_catalog(SCENARIOS_PATH)
    naming_policy = component_name_policy(catalog)
    solver_controls = dict(scenario.get("solver_controls", {})) if scenario else {}
    print(f"\nMASTER VSP3 импортирован: {imported_master}")
    if imported != imported_master:
        print(f"Сертифицированный расчётный двойник: {imported}")
    print("Set_1 = фюзеляж и мотогондолы; Set_2 = крыло и оперение.")
    for case in cases:
        result = _run_standard_vspaero_case(
            settings=settings,
            project=project,
            executable=executable,
            master_source=requested_source,
            solver_source=source,
            imported=imported,
            case=case,
            beta_deg=0.0,
            ncpu=ncpu,
            reference=reference,
            study_timestamp=study_timestamp,
            tail_geometry_name=tail_geometry_name,
            tail_incidence_deg=tail_incidence_deg,
            scenario_id=scenario_id,
            scenario_sha256=scenario_fingerprint,
            naming_policy=naming_policy,
            solver_controls=solver_controls,
            solver_mode=solver_mode,
            geometry_certificate=certificate_binding,
        )
        study_results.append(result)
        if result["status"] != "completed" and case.get("point_execution", "sweep") == "sweep":
            break

    cy_alpha_points = []
    postprocess_errors = []
    polar_sources = []
    polar_rows = []
    for result in study_results:
        if result.get("status") != "completed":
            continue
        polar_path = Path(result["outputs"]["polar"])
        polar_sources.append(str(polar_path.resolve()))
        try:
            polar_rows.extend(parse_vspaero_polar(polar_path))
        except (OSError, ValueError) as exc:
            postprocess_errors.append(f"{polar_path.name}: {exc}")

    if polar_rows and not postprocess_errors:
        try:
            cy_alpha_points = cy_alpha_per_degree(polar_rows)
        except ValueError as exc:
            postprocess_errors.append(str(exc))

    run_complete = (
        len(study_results) == len(cases)
        and all(item["status"] == "completed" for item in study_results)
    )
    derived_complete = bool(cy_alpha_points) and not postprocess_errors
    study_stem = f"{safe_vsp3_name(requested_source.name)[:24]}_standard_{study_timestamp}"
    cy_alpha_path = project / "06_vspaero_results" / "runs" / f"{study_stem}_cy_alpha.json"
    cy_alpha_report = {
        "schema": "repairmach.cy-alpha/1.0",
        "definition": "Cy_alpha=[Cy(1deg)-Cy(0deg)]/1deg",
        "coefficient_mapping": "VSPAERO CLtot = RepairMach Cy",
        "interpolation_used": False,
        "independent_alpha_supported": True,
        "independent_mach_alpha_supported": True,
        "hybrid_method": scenario.get("cy_alpha_method") if scenario else None,
        "polar_sources": polar_sources,
        "points": sorted(cy_alpha_points, key=lambda item: item["Mach"]),
        "errors": postprocess_errors,
        "status": "completed" if run_complete and derived_complete else "incomplete",
    }
    if certified_contract:
        mesh = read_tri(tri)
        ml["solver"].update({
            "preconditioner": "DIAG",
            "tolerance": 1.0e-10,
            "max_iterations": min(25000, max(1000, len(mesh.vertices) + 100)),
        })
    save_report(cy_alpha_path, cy_alpha_report)

    study_manifest = {
        "schema": "repairmach.vspaero-standard-study/1.0",
        "repairmach_version": settings.get("repairmach_version"),
        "scenario_id": scenario_id,
        "scenario_sha256": scenario_fingerprint,
        "scenario_title": scenario.get("title") if scenario else "Стандартный VSPAERO",
        "requested_master_vsp3": str(requested_source),
        "requested_master_sha256": geometry_sha256(requested_source),
        "geometry_certificate": certificate_binding,
        "vspaero_mode": solver_mode,
        "source_vsp3": str(requested_source.resolve()),
        "source_vsp3_sha256": geometry_sha256(requested_source),
        "solver_geometry": {
            "path": str(source.resolve()),
            "sha256": geometry_sha256(source),
        },
        "solver_geometry_sha256": geometry_sha256(source),
        "imported_vsp3": str(imported.resolve()),
        "imported_vsp3_sha256": geometry_sha256(imported),
        "reference": reference,
        "standard": {
            "mach_step": 0.1,
            "alpha_start_deg": min(case["alpha_start"] for case in cases),
            "alpha_end_deg": max(case["alpha_end"] for case in cases),
            "alpha_step_deg": min(
                (
                    case.get("requested_alpha_step_deg")
                    if case.get("requested_alpha_step_deg") is not None
                    else (
                        (case["alpha_end"] - case["alpha_start"]) / (case["alpha_points"] - 1)
                        if case["alpha_points"] > 1 else 0.0
                    )
                )
                for case in cases
                if case.get("requested_alpha_step_deg", 1.0) > 0.0 or case["alpha_points"] > 1
            ),
            "excluded_transonic_interval": [0.9, 1.1],
            "horizontal_tail": {
                "mode": "fixed_incidence" if tail_geometry_name else "model_default",
                "geometry_name": tail_geometry_name,
                "incidence_deg": tail_incidence_deg if tail_geometry_name else None,
            },
            "acceptance_criteria": (
                scenario.get("acceptance_criteria") if scenario else None
            ),
            "cy_alpha_method": scenario.get("cy_alpha_method") if scenario else None,
            "solver_controls": solver_controls,
        },
        "derived_outputs": {
            "cy_alpha": str(cy_alpha_path.resolve()),
            "cy_alpha_status": cy_alpha_report["status"],
            "postprocess_errors": postprocess_errors,
        },
        "runs": study_results,
        "status": "completed" if run_complete and derived_complete else "incomplete",
    }
    study_path = project / "06_vspaero_results" / "runs" / f"{study_stem}.json"
    save_report(study_path, study_manifest)
    print(f"\nСводный манифест: {study_path}")
    print(f"Cyα(M), прямые точки: {cy_alpha_path}")
    print(f"Статус: {study_manifest['status']}")


def run_openvsp_script_workflow() -> None:
    settings = load_settings()
    openvsp_dir = find_openvsp_dir(settings.get("openvsp", {}).get("install_dir"))
    tools = tool_paths(openvsp_dir)
    executable = tools["vspscript"]
    if executable is None or not executable.is_file():
        print("vspscript.exe не найден. Проверьте раздел openvsp в настройках.")
        return

    script_text = input("Полный путь к .vspscript: ").strip().strip('"')
    if not script_text:
        print("Отменено.")
        return
    script = Path(script_text)
    if not script.is_file():
        print(f"Сценарий не найден: {script}")
        return

    project = default_project_path()
    log_path = (
        project / "06_vspaero_results" / "logs"
        / f"{script.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    )
    print("Запускается сценарий OpenVSP/VSPAERO...")
    try:
        code = run_vspscript(executable, script, log_path, script.parent)
    except Exception as exc:
        print(f"Ошибка запуска OpenVSP: {exc}")
        return
    print(log_path.read_text(encoding="utf-8", errors="replace"))
    print(f"Лог: {log_path}")
    print(f"Код завершения: {code}")


def run_parasite_drag_workflow() -> None:
    settings = load_settings()
    project = default_project_path()
    if not project.exists():
        print("Проект по умолчанию не найден.")
        return
    openvsp_dir = find_openvsp_dir(settings.get("openvsp", {}).get("install_dir"))
    executable = tool_paths(openvsp_dir)["vspscript"]
    if executable is None or not executable.is_file():
        print("vspscript.exe не найден. Проверьте раздел openvsp в настройках.")
        return

    vsp3_text = input("Полный путь к модели .vsp3: ").strip().strip('"')
    if not vsp3_text:
        print("Отменено.")
        return
    requested_vsp3 = Path(vsp3_text).resolve()
    if not requested_vsp3.is_file() or requested_vsp3.suffix.lower() != ".vsp3":
        print(f"Не найден корректный файл .vsp3: {requested_vsp3}")
        return
    solver_vsp3 = requested_vsp3
    certificate_binding = None
    try:
        certified = certified_solver_geometry(
            project,
            requested_vsp3,
            "parasite_drag",
            runtime_executables={"openvsp": executable},
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"[ОСТАНОВЛЕНО] Сертификат Parasite Drag не принят: {exc}")
        return
    if certified is not None:
        solver_vsp3, certificate, certificate_path = certified
        backend = certificate["backends"]["parasite_drag"]
        certificate_binding = {
            "certificate_id": certificate.get("certificate_id"),
            "certificate_path": str(certificate_path),
            "certificate_sha256": geometry_sha256(certificate_path),
            "master_path": str(requested_vsp3),
            "master_sha256": certificate.get("master", {}).get("sha256_before"),
            "solver_geometry_path": str(solver_vsp3),
            "solver_geometry_sha256": backend.get("solver_geometry", {}).get("sha256"),
            "selected_mode": backend.get("mode"),
            "reference": certificate.get("reference"),
        }
        print(
            f"Сертификат {certificate.get('certificate_id')} принят: "
            "автоматически выбран отдельный двойник Parasite Drag."
        )
    defaults = settings.get("parasite_drag", {})
    mach_text = input(f"Mach (только < 1), Enter = {defaults.get('mach', 0.8)}: ").strip()
    altitude_text = input(f"Высота, ft, Enter = {defaults.get('altitude_ft', 0.0)}: ").strip()
    if certificate_binding:
        sref_text = ""
        set_text = ""
        certified_area = float(certificate_binding["reference"]["area"])
        print(
            f"Сертификат фиксирует Sref={certified_area:g} и полный OpenVSP Set=0; "
            "изменение в этом запуске запрещено."
        )
    else:
        sref_text = input(
            f"Опорная площадь в единицах VSP3, Enter = {defaults.get('reference_area', settings['defaults']['area'])}: "
        ).strip()
        set_text = input(f"Номер OpenVSP Set, Enter = {defaults.get('geometry_set', 0)}: ").strip()
    try:
        mach = float(mach_text or defaults.get("mach", 0.8))
        altitude = float(altitude_text or defaults.get("altitude_ft", 0.0))
        sref = (
            float(certificate_binding["reference"]["area"])
            if certificate_binding
            else float(sref_text or defaults.get("reference_area", settings["defaults"]["area"]))
        )
        geometry_set = 0 if certificate_binding else int(set_text or defaults.get("geometry_set", 0))
    except ValueError:
        print("Некорректное числовое значение.")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = f"{requested_vsp3.stem}_M{mach:g}_{timestamp}"
    script = project / "09_parasite_drag" / "scripts" / f"{stem}.vspscript"
    results = project / "09_parasite_drag" / "results" / f"{stem}.csv"
    log = project / "09_parasite_drag" / "logs" / f"{stem}.log"
    manifest_path = project / "09_parasite_drag" / "runs" / f"{stem}.json"
    try:
        generate_parasite_drag_script(
            script, solver_vsp3, results, mach, altitude, sref, geometry_set
        )
        code = run_vspscript(executable, script, log, script.parent)
        if code not in (0, 1):
            raise RuntimeError(f"Недопустимый код vspscript: {code}")
        parsed = parse_openvsp_results_csv(results, strict=True)
        if abs(float(parsed["mach"]) - mach) > 1.0e-6:
            raise RuntimeError(
                f"Parasite Drag вернул M={float(parsed['mach']):g} вместо M={mach:g}"
            )
        if abs(float(parsed["reference_area"]) - sref) > 1.0e-9 * max(1.0, abs(sref)):
            raise RuntimeError(
                "Parasite Drag вернул FC_Sref, не совпадающий с заданным Sref"
            )
    except Exception as exc:
        print(f"Parasite Drag не выполнен: {exc}")
        return

    manifest = {
        "schema": "repairmach.parasite-drag-run/1.0",
        "repairmach_version": settings.get("repairmach_version"),
        "executables": executable_fingerprints({"openvsp": executable}),
        "source_vsp3": str(requested_vsp3),
        "source_vsp3_sha256": geometry_sha256(requested_vsp3),
        "solver_geometry": {
            "path": str(solver_vsp3),
            "sha256": geometry_sha256(solver_vsp3),
        },
        "solver_geometry_sha256": geometry_sha256(solver_vsp3),
        "geometry_mode": (
            certificate_binding.get("selected_mode")
            if certificate_binding else "unsealed_full_geometry"
        ),
        "geometry_certification": certificate_binding,
        "conditions": {
            "mach": mach,
            "altitude_ft": altitude,
            "reference_area": sref,
            "geometry_set": geometry_set,
        },
        "outputs": {
            "results_csv": str(results.resolve()),
            "script": str(script.resolve()),
            "log": str(log.resolve()),
        },
        "output_quality": {
            "valid": True,
            "component_count": len(parsed["components"]),
            "total_cd": float(parsed["total_cd"]),
            "reported_mach": float(parsed["mach"]),
            "reported_reference_area": float(parsed["reference_area"]),
        },
        "output_sha256": output_sha256({
            "results_csv": results,
            "script": script,
            "log": log,
        }),
        "vspscript_return_code": code,
        "status": "completed",
    }
    # Seal the complete producer record after all actual input/output hashes
    # have been captured.  Hybrid consumers reject edited or relabelled
    # manifests instead of trusting their descriptive fields.
    manifest["record_fingerprint"] = sha256_payload(manifest)
    save_report(manifest_path, manifest)

    print("\nКомпонентный расчёт Parasite Drag")
    print("-" * 58)
    print(f"CD0 всего          : {parsed['total_cd']:.9g}")
    print(f"Компонентов        : {len(parsed['components'])}")
    print(f"Модель Cf          : {parsed['turbulent_cf_method']}")
    for item in parsed["components"]:
        print(
            f"- {item['label']}: Cf={item['cf']}, FF={item['form_factor']}, "
            f"Q={item['interference_factor']}, Swet={item['wetted_area']}, CD={item['cd']}"
        )
    if not parsed["components"]:
        print("[!] OpenVSP не включил ни одного компонента. См. лог и настройки VSP3.")
    print(f"Результат: {results}")
    print(f"Сценарий : {script}")
    print(f"Лог      : {log}")
    print(f"Манифест : {manifest_path}")
    print(f"Код      : {code}")


def build_hybrid_report_workflow() -> None:
    """Create one repeatable request and run the complete hybrid post-process."""
    project = default_project_path()
    if not project.exists():
        print("Проект по умолчанию не найден.")
        return
    latest = latest_completed_vspaero_study(project / "06_vspaero_results" / "runs")
    latest_hint = str(latest) if latest else "не найден"
    vspaero_text = input(
        f"Манифест исследования VSPAERO или POLAR, Enter = {latest_hint}: "
    ).strip().strip('"')
    if not vspaero_text and latest is None:
        print("Не найдено завершённое исследование VSPAERO.")
        return
    vspaero_source = Path(vspaero_text) if vspaero_text else latest

    default_machline = project / "05_machline_results" / "reports"
    machline_text = input(
        f"Папка отчётов MachLine, Enter = {default_machline}: "
    ).strip().strip('"')
    machline_dir = Path(machline_text) if machline_text else default_machline
    pattern = input("Маска отчётов MachLine, Enter = *full*_report.json: ").strip()
    pattern = pattern or "*full*_report.json"

    default_parasite = project / "09_parasite_drag" / "runs"
    parasite_text = input(
        f"Папка OpenVSP Parasite Drag, Enter = {default_parasite}: "
    ).strip().strip('"')
    parasite_dir = Path(parasite_text) if parasite_text else default_parasite
    policy_text = input(
        f"Файл гибридной методики, Enter = {HYBRID_POLICY_PATH}: "
    ).strip().strip('"')
    policy_path = Path(policy_text) if policy_text else HYBRID_POLICY_PATH

    latest_certificate = None
    latest_pointer = project / "11_geometry_certification" / "latest_certificate.json"
    if latest_pointer.is_file():
        try:
            candidate = Path(
                json.loads(latest_pointer.read_text(encoding="utf-8"))["certificate_path"]
            )
            if candidate.is_file():
                latest_certificate = candidate
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            latest_certificate = None
    certificate_hint = str(latest_certificate) if latest_certificate else "не найден"
    certificate_text = input(
        f"Сертификат геометрии или Enter = {certificate_hint}: "
    ).strip().strip('"')
    certificate_path = Path(certificate_text) if certificate_text else latest_certificate
    if certificate_path is not None and not certificate_path.is_file():
        print(f"Не найден сертификат геометрии: {certificate_path}")
        return
    if certificate_path is None:
        print("[!] Сертификат не приложен: результат совместим с 9.1, но не годится как полностью запечатанный слепой пакет.")

    scenario_id = None
    scenario_fingerprint = None
    if certificate_path is not None:
        try:
            source_payload = json.loads(Path(vspaero_source).read_text(encoding="utf-8"))
            scenario_id = str(source_payload.get("scenario_id", "")).strip()
            declared_fingerprint = str(
                source_payload.get("scenario_sha256", "")
            ).strip().lower()
            if not scenario_id or len(declared_fingerprint) != 64 or any(
                char not in "0123456789abcdef" for char in declared_fingerprint
            ):
                raise ValueError(
                    "манифест VSPAERO не содержит scenario_id/scenario_sha256"
                )
            catalog = load_scenario_catalog(SCENARIOS_PATH)
            current_scenario = scenario_by_id(catalog, scenario_id)
            scenario_fingerprint = sha256_payload(current_scenario)
            if declared_fingerprint != scenario_fingerprint:
                raise ValueError(
                    "сценарий изменён после расчёта; запустите VSPAERO заново"
                )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            print(
                "Запечатанный гибридный расчёт не может использовать выбранный "
                f"источник VSPAERO: {exc}"
            )
            return

    try:
        policy = load_hybrid_policy(policy_path)
    except Exception as exc:
        print(f"Файл методики не принят: {exc}")
        return

    thin_source = None
    if policy.get("lift", {}).get("cy_alpha", {}).get("method") != "direct":
        thin_text = input(
            "Манифест/POLAR тонкостенной конфигурации для гибридного Cyα: "
        ).strip().strip('"')
        if not thin_text:
            print("Для выбранной методики Cyα нужен тонкостенный расчёт.")
            return
        thin_source = str(Path(thin_text).resolve())

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = project / "08_hybrid_results" / f"hybrid_{timestamp}"
    request_path = output_dir / "hybrid_request.json"
    request = {
        "schema": HYBRID_REQUEST_SCHEMA,
        "vspaero_source": str(Path(vspaero_source).resolve()),
        "thin_vspaero_source": thin_source,
        "machline_reports_dir": str(machline_dir.resolve()),
        "machline_report_glob": pattern,
        "parasite_results_dir": str(parasite_dir.resolve()),
        "parasite_result_glob": "*.json",
        "policy": str(policy_path.resolve()),
        "geometry_certificate": str(certificate_path.resolve()) if certificate_path else None,
        "scenario_id": scenario_id,
        "scenario_sha256": scenario_fingerprint,
        "replacement_coverage": "auto" if certificate_path else [],
        "auto_build_excel": True,
    }
    save_report(request_path, request)
    try:
        result = run_hybrid_request(request_path, output_dir=output_dir, build_workbook=True)
    except Exception as exc:
        print(f"Автоматический гибридный расчёт не завершён: {exc}")
        print(f"Задание сохранено для повторного запуска: {request_path}")
        return

    bundle = result["bundle"]
    outputs = result["outputs"]
    print("\nАвтоматический гибридный результат")
    print("-" * 58)
    print(f"Статус              : {bundle['status']}")
    print(f"Точек АДХ           : {bundle['summary']['points']}")
    print(f"Полных точек        : {bundle['summary']['complete_points']}")
    print(f"Точек Cyα           : {bundle['summary']['cy_alpha_points']}")
    print(f"Ошибок              : {bundle['summary']['errors']}")
    print("Поточечная подстройка и эталонная кривая: не используются")
    for error in bundle["errors"][:12]:
        print(f"[!] {error}")
    if len(bundle["errors"]) > 12:
        print(f"[!] Ещё ошибок: {len(bundle['errors']) - 12}; см. JSON")
    print(f"JSON                : {outputs['json']}")
    print(f"CSV АДХ             : {outputs['points_csv']}")
    print(f"CSV Cyα             : {outputs['cy_alpha_csv']}")
    print(f"Excel с графиками   : {outputs['workbook']}")
    print(f"Проверка Excel      : {outputs['workbook_verification']}")


def _prompt_existing_files(prompt: str) -> list[Path]:
    text = input(prompt).strip()
    if not text:
        return []
    paths = [Path(item.strip().strip('"')) for item in text.split(";") if item.strip()]
    missing = [path for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError("Не найдены файлы: " + "; ".join(str(path) for path in missing))
    return paths


def prepare_blind_study_workflow() -> None:
    """Create a sealed calculation package before reference data is opened."""
    settings = load_settings()
    project = default_project_path()
    project_config = project / "project_config.json"
    if not project_config.is_file():
        print("Проект или project_config.json не найден. Сначала создайте/выберите проект.")
        return
    try:
        catalog = load_scenario_catalog(SCENARIOS_PATH)
    except Exception as exc:
        print(f"Каталог сценариев не загружен: {exc}")
        return

    candidates = [
        scenario for scenario in catalog["scenarios"]
        if scenario.get("availability") == "ready"
        and scenario.get("execution") == "vspaero_study"
        and scenario.get("tail_incidence") == "fixed"
        and scenario.get("acceptance_criteria")
    ]
    print("\nСценарий слепого расчёта")
    print("-" * 58)
    for index, scenario in enumerate(candidates, start=1):
        print(f"[{index}] {scenario['title']}")
    choice = input("Сценарий: ").strip()
    try:
        scenario = candidates[int(choice) - 1]
    except (ValueError, IndexError):
        print("Неверный номер сценария.")
        return

    geometry_text = input("Полный путь к окончательной модели VSP3: ").strip().strip('"')
    geometry = Path(geometry_text)
    if not geometry.is_file() or geometry.suffix.lower() != ".vsp3":
        print(f"Не найдена модель VSP3: {geometry}")
        return
    tri_text = input("Полный путь к TRI для MachLine или Enter: ").strip().strip('"')
    additional_inputs = []
    if tri_text:
        tri = Path(tri_text)
        if not tri.is_file():
            print(f"Не найден TRI: {tri}")
            return
        additional_inputs.append(("machline_geometry", tri))
    for module_name in (
        "validation_metrics.py",
        "aero_hybrid.py",
        "hybrid_pipeline.py",
        "hybrid_workbook.mjs",
        "vspaero_runner.py",
        "calculation_scenarios.py",
        "repairmach_beta.py",
    ):
        method_source = APP_DIR / module_name
        if method_source.is_file():
            additional_inputs.append((f"method_source_{method_source.stem}", method_source))
    if HYBRID_POLICY_PATH.is_file():
        additional_inputs.append(("hybrid_method_policy", HYBRID_POLICY_PATH))

    method_default = scenario.get("cy_alpha_method", "predeclared_repairmach_method")
    method_text = input(f"Версия методики, Enter = {method_default}: ").strip()
    method_declaration = {
        "method_version": method_text or method_default,
        "hybrid_pipeline_version": "RM91-HYBRID-AUTO-2",
        "scenario_id": scenario["id"],
        "pointwise_tuning": False,
        "geometry_adjustment_after_seal": False,
        "semiempirical_policy": "only terms declared before the blind run",
        "transonic_policy": "exclude M=0.9...1.1 unless a separate declared method exists",
        "horizontal_tail": {
            "mode": scenario.get("tail_incidence", "optional"),
            "name": scenario.get("tail_geometry_name"),
            "incidence_deg": scenario.get("tail_incidence_deg"),
        },
        "acceptance_criteria": scenario.get("acceptance_criteria", {
            "numerical_percent": 5.0,
            "total_mean_percent": 11.0,
        }),
    }

    solver_paths = []
    python = find_python(settings)
    if python and python.is_file():
        solver_paths.append(("Python", python))
    machline = find_machline(settings)
    if machline.is_file():
        solver_paths.append(("MachLine", machline))
    openvsp_dir = find_openvsp_dir(settings.get("openvsp", {}).get("install_dir"))
    tools = tool_paths(openvsp_dir)
    for role, key in (("VSPscript", "vspscript"), ("VSPAERO", "vspaero")):
        candidate = tools.get(key)
        if candidate and candidate.is_file():
            solver_paths.append((role, candidate))

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    package_dir = project / "10_blind_validation" / f"{timestamp}_{scenario['id']}"
    try:
        manifest = prepare_blind_package(
            package_dir,
            repairmach_version=str(settings.get("repairmach_version", "9.1")),
            project_name=project.name,
            geometry_path=geometry,
            project_config_path=project_config,
            scenario_catalog_path=SCENARIOS_PATH,
            settings_path=SETTINGS_PATH,
            selected_scenario=scenario,
            method_declaration=method_declaration,
            additional_inputs=additional_inputs,
            solver_paths=solver_paths,
        )
        verification = verify_blind_package(manifest)
    except Exception as exc:
        print(f"Слепой пакет не создан: {exc}")
        return

    print("\nВходной пакет слепого расчёта запечатан")
    print("-" * 58)
    print(f"Папка       : {package_dir}")
    print(f"Манифест    : {manifest}")
    print(f"Отпечаток   : {verification['package_fingerprint']}")
    print(f"Целостность : {'OK' if verification['valid'] else 'ОШИБКА'}")
    print("Эталонные данные пока подключать нельзя. Сначала выполните и запечатайте прогноз.")


def blind_study_control_workflow() -> None:
    print("\nСлепая верификация")
    print("-" * 58)
    print("[1] Подготовить и запечатать входной пакет")
    print("[2] Проверить целостность входного пакета")
    print("[3] Запечатать файлы прогноза")
    print("[4] Подключить эталон после запечатывания прогноза")
    print("[0] Назад")
    choice = input("Выбор: ").strip()
    if choice == "1":
        prepare_blind_study_workflow()
        return
    if choice == "0" or not choice:
        return

    try:
        if choice == "2":
            manifest = Path(input("Путь к blind_manifest.json: ").strip().strip('"'))
            result = verify_blind_package(manifest)
            print(f"Целостность: {'OK' if result['valid'] else 'ОШИБКА'}")
            print(f"Состояние: {result['state']}")
            print(f"Отпечаток: {result['package_fingerprint']}")
            for error in result["errors"]:
                print(f"[!] {error}")
        elif choice == "3":
            manifest = Path(input("Путь к blind_manifest.json: ").strip().strip('"'))
            predictions = _prompt_existing_files(
                "Файлы прогноза через точку с запятой (CSV/JSON/XLSX): "
            )
            output = seal_predictions(manifest, predictions)
            result = verify_prediction_seal(output)
            print(f"Прогноз запечатан: {output}")
            print(f"Целостность: {'OK' if result['valid'] else 'ОШИБКА'}")
        elif choice == "4":
            prediction_seal = Path(
                input("Путь к prediction_seal.json: ").strip().strip('"')
            )
            references = _prompt_existing_files(
                "Эталонные файлы через точку с запятой: "
            )
            output = attach_reference_after_seal(prediction_seal, references)
            print(f"Эталон подключён после фиксации прогноза: {output}")
        else:
            print("Неизвестная команда.")
    except Exception as exc:
        print(f"Операция слепой верификации не выполнена: {exc}")


def open_project_folder() -> None:
    project = default_project_path()
    if project.exists():
        os.startfile(project)
    else:
        print("Проект по умолчанию не найден.")


def prepare_protocol_scenario(scenario: dict) -> None:
    """Save a versioned protocol for scenarios whose full runner is not in 9.1."""
    settings = load_settings()
    project = default_project_path()
    if not project.exists():
        print("Проект по умолчанию не найден. Сначала создайте или выберите проект.")
        return

    geometry_text = input(
        "Путь к основной VSP3/TRI-геометрии для привязки протокола, Enter = без файла: "
    ).strip().strip('"')
    geometry_path = None
    if geometry_text:
        geometry_path = Path(geometry_text)
        if not geometry_path.is_absolute():
            geometry_path = project / geometry_path
        if not geometry_path.is_file():
            print(f"Файл не найден: {geometry_path}")
            return

    manifest = build_scenario_manifest(
        scenario,
        repairmach_version=str(settings.get("repairmach_version", "9.1")),
        project_name=project.name,
        geometry_path=geometry_path,
    )
    manifest["status"] = "protocol_prepared"
    manifest["automation"] = {
        "available_in_9_1": False,
        "note": (
            "Версия 9.1 фиксирует воспроизводимый протокол. Автоматическая генерация "
            "трёх CFD-сеток и единая очередь MachLine+VSPAERO будут подключены в следующем этапе."
        ),
    }
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = (
        project
        / "07_parametric_studies"
        / "scenarios"
        / f"{timestamp}_{scenario['id']}"
        / "scenario_manifest.json"
    )
    write_scenario_manifest(output, manifest)
    print("\nПротокол сценария подготовлен")
    print("-" * 58)
    print(f"Сценарий : {scenario['title']}")
    print(f"Статус   : {manifest['status']}")
    print(f"Манифест : {output}")
    print("[!] Это протокол, а не выполненный аэродинамический расчёт.")


def geometry_certification_workflow(scenario: dict | None = None) -> None:
    """Certify one immutable VSP3 MASTER and create backend-specific twins."""
    settings = load_settings()
    project = default_project_path()
    project_config_path = project / "project_config.json"
    if not project_config_path.is_file():
        print("Проект или project_config.json не найден. Сначала создайте/выберите проект.")
        return
    openvsp_dir = find_openvsp_dir(settings.get("openvsp", {}).get("install_dir"))
    tools = tool_paths(openvsp_dir)
    if tools["vspscript"] is None or not tools["vspscript"].is_file():
        print("OpenVSP/vspscript не найден. Сначала выполните проверку среды.")
        return
    if not GEOMETRY_POLICY_PATH.is_file():
        print(f"Не найдена политика сертификации: {GEOMETRY_POLICY_PATH}")
        return

    print("\nАвтоматическая сертификация геометрии G0–G8")
    print("-" * 58)
    print("MASTER останется неизменным; все исправления выполняются только в двойниках.")
    source_text = input("Полный путь к MASTER OpenVSP .vsp3: ").strip().strip('"')
    source = Path(source_text) if source_text else Path()
    if not source_text or not source.is_file() or source.suffix.lower() != ".vsp3":
        print("Не найден корректный MASTER .vsp3.")
        return

    try:
        project_payload = json.loads(project_config_path.read_text(encoding="utf-8"))
        cert_config = project_payload.get("geometry_certification", {})
        if not isinstance(cert_config, dict):
            raise ValueError("project_config.geometry_certification должен быть объектом")
        policy_overrides = cert_config.get("policy_overrides") or {}
        scope_override = cert_config.get("scope_override")
        if not isinstance(policy_overrides, dict):
            raise ValueError("geometry_certification.policy_overrides должен быть объектом")
        if scope_override is not None and not isinstance(scope_override, dict):
            raise ValueError("geometry_certification.scope_override должен быть объектом или null")

        tri_default = cert_config.get("tri_path")
        tri_prompt = "TRI для сертификации MachLine или Enter"
        if tri_default:
            tri_prompt += f", по умолчанию {tri_default}"
        tri_text = input(tri_prompt + ": ").strip().strip('"') or str(tri_default or "")
        tri_path = None
        if tri_text:
            tri_path = Path(tri_text)
            if not tri_path.is_absolute():
                tri_path = project / tri_path
            if not tri_path.is_file():
                raise FileNotFoundError(f"Не найден TRI: {tri_path}")

        project_reference = load_project_reference(project, settings)
        reference = {
            "area": project_reference["area"],
            "cref": project_reference["longitudinal_length"],
            "bref": project_reference["lateral_length"],
            "center": project_reference["center"],
        }
        run_probes = bool(cert_config.get("run_vspaero_probes", True))
        machline = find_machline(settings)
        executables = {
            "vspscript": tools["vspscript"],
            "vspaero": tools["vspaero"] if tools["vspaero"] and tools["vspaero"].is_file() else None,
            "machline": machline if machline.is_file() else None,
        }
        print(f"Проект : {project.name}")
        print(
            f"Опоры  : Sref={reference['area']:g}; cref={reference['cref']:g}; "
            f"bref={reference['bref']:g}; CG={reference['center']}"
        )
        print(f"Проба VSPAERO: {'да' if run_probes else 'нет (диагностический пакет без допуска)'}")
        result = certify_geometry(
            master_path=source,
            output_root=project / "11_geometry_certification",
            project_name=project.name,
            reference=reference,
            policy_path=GEOMETRY_POLICY_PATH,
            executables=executables,
            tri_path=tri_path,
            scope_override=scope_override,
            policy_overrides=policy_overrides,
            run_vspaero_probes=run_probes,
        )
    except Exception as exc:
        print(f"[ОШИБКА] Сертификация не запущена или аварийно завершилась: {exc}")
        return

    certificate = result["certificate"]
    pointer_name = "latest_certificate.json" if certificate.get("verdict") != "FAIL" else "latest_failed_certificate.json"
    latest_path = project / "11_geometry_certification" / pointer_name
    save_report(latest_path, {
        "certificate_id": certificate.get("certificate_id"),
        "verdict": certificate.get("verdict"),
        "master_sha256": certificate.get("master", {}).get("sha256_before"),
        "certificate_path": str(result["certificate_path"].resolve()),
        "report_path": str(result["report_path"].resolve()),
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    })
    flags = certificate.get("flags", {})
    blockers = [item for item in certificate.get("findings", []) if item.get("severity") == "BLOCKER"]
    replacement_contract = (
        certificate.get("backends", {}).get("hybrid", {})
        .get("replacement_contract", {})
    )
    unavailable_replacements = replacement_contract.get(
        "unavailable_requirements", []
    ) if isinstance(replacement_contract, dict) else []
    print("\nСертификация завершена")
    print("-" * 58)
    print(f"Вердикт      : {certificate.get('verdict')}")
    print(f"ID           : {certificate.get('certificate_id')}")
    print(f"MASTER цел   : {'да' if flags.get('master_unchanged') else 'НЕТ'}")
    print(f"Решатель     : {'допущен' if flags.get('solver_eligible') else 'не допущен'}")
    print(f"Гибрид нужен : {'да' if flags.get('hybrid_substitution_required') else 'нет'}")
    if replacement_contract.get("required"):
        print(
            "Контракт замен: "
            + (
                "backend-покрытие полное; нужны запечатанные расчётные источники"
                if not unavailable_replacements
                else f"неполный ({len(unavailable_replacements)} отсутствующих вкладов)"
            )
        )
        for item in unavailable_replacements:
            print(
                f"  - {item.get('component')}: {item.get('method')} — "
                "нет покомпонентного покрытия"
            )
    print(f"Блокирующих  : {len(blockers)}")
    print(f"Сертификат   : {result['certificate_path']}")
    print(f"Отчёт        : {result['report_path']}")
    print(f"Последний ID : {latest_path}")


def run_calculation_scenario() -> None:
    try:
        catalog = load_scenario_catalog(SCENARIOS_PATH)
    except Exception as exc:
        print(f"Каталог сценариев не загружен: {exc}")
        return

    print(f"\nГотовые сценарии RepairMach {catalog.get('catalog_version', '')}")
    print("-" * 58)
    for index, item in enumerate(catalog["scenarios"], start=1):
        marker = "ГОТОВ" if item["availability"] == "ready" else "ПРОТОКОЛ"
        print(f"[{index}] {item['title']} [{marker}]")
        print(f"    {item['description']}")
    print("[0] Назад")

    choice = input("\nСценарий: ").strip()
    if choice == "0" or not choice:
        return
    try:
        selected = catalog["scenarios"][int(choice) - 1]
    except (ValueError, IndexError):
        print("Неверный номер сценария.")
        return

    scenario = scenario_by_id(catalog, selected["id"])
    execution = scenario["execution"]
    if execution == "tri_diagnostics":
        diagnose_existing_tri()
    elif execution == "vspaero_study":
        run_standard_vspaero_study(scenario)
    elif execution == "geometry_certification":
        geometry_certification_workflow(scenario)
    else:
        prepare_protocol_scenario(scenario)


def menu() -> None:
    while True:
        print_header()
        settings = load_settings()
        print(f"Проект по умолчанию: {settings['paths']['default_project']}")
        print("[1] Проверить среду")
        print("[2] Создать проект")
        print("[3] Выбрать проект")
        print("[4] Импортировать TRI")
        print("[5] Диагностировать/исправить TRI")
        print("[6] Создать MachLine JSON")
        print("[7] Запустить MachLine")
        print("[8] Импортировать VSP3 и запустить VSPAERO с ручным диапазоном")
        print("[9] Готовые сценарии расчёта RepairMach 9.1")
        print("[10] Рассчитать OpenVSP Parasite Drag")
        print("[11] Автоматический гибридный расчёт и Excel с графиками")
        print("[12] Открыть папку проекта")
        print("[13] Запустить готовый .vspscript (расширенный режим)")
        print("[14] Слепая верификация: запечатать входы и прогноз")
        print("[0] Выход")
        choice = input("\nВыбор: ").strip()

        if choice == "1":
            check_environment()
        elif choice == "2":
            create_project()
        elif choice == "3":
            select_project()
        elif choice == "4":
            import_tri()
        elif choice == "5":
            diagnose_existing_tri()
        elif choice == "6":
            build_machline_json()
        elif choice == "7":
            run_machline()
        elif choice == "8":
            import_vsp3_and_run_vspaero()
        elif choice == "9":
            run_calculation_scenario()
        elif choice == "10":
            run_parasite_drag_workflow()
        elif choice == "11":
            build_hybrid_report_workflow()
        elif choice == "12":
            open_project_folder()
        elif choice == "13":
            run_openvsp_script_workflow()
        elif choice == "14":
            blind_study_control_workflow()
        elif choice == "0":
            return
        else:
            print("Неизвестная команда.")

        input("\nEnter для продолжения...")


if __name__ == "__main__":
    try:
        menu()
    except KeyboardInterrupt:
        print("\nВыход.")
