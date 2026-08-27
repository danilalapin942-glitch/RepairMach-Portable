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
from aero_hybrid import build_hybrid_report, parse_openvsp_results_csv
from calculation_scenarios import (
    build_scenario_manifest,
    component_name_policy,
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


def app_root() -> Path:
    return Path(__file__).resolve().parents[1]


ROOT = app_root()
SETTINGS_PATH = ROOT / "config" / "repairmach_settings.json"
SCENARIOS_PATH = ROOT / "config" / "calculation_scenarios.json"


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

    case = input("Имя расчёта, например MIG_full_M15_a0: ").strip()
    if not case:
        case = tri.stem.replace("_ready", "")

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
    solver = input(f"Решатель, Enter = {default_solver}: ").strip() or default_solver
    formulation = input(f"Формулировка, Enter = {default_formulation}: ").strip() or default_formulation
    offset_text = input(f"control_point_offset, Enter = {default_offset:g}: ").strip()
    wake_answer = input("Добавить след? [Y/n]: ").strip().lower()
    wake_present = wake_answer in ("", "y", "yes", "д", "да")
    trefftz = default_trefftz
    wake_angle = default_wake_angle
    if wake_present:
        trefftz_text = input(f"Trefftz distance, Enter = {default_trefftz:g}: ").strip()
        angle_text = input(f"Wake shedding angle, Enter = {default_wake_angle:g}: ").strip()
        trefftz = float(trefftz_text) if trefftz_text else float(default_trefftz)
        wake_angle = float(angle_text) if angle_text else float(default_wake_angle)
    control_offset = float(offset_text) if offset_text else float(default_offset)

    out_vtk = project / "05_machline_results" / "vtk" / f"{case}_body.vtk"
    out_wake = project / "05_machline_results" / "wake" / f"{case}_wake.vtk"
    out_cp = project / "05_machline_results" / "control_points" / f"{case}_control_points.vtk"
    out_report = project / "05_machline_results" / "reports" / f"{case}_report.json"

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
            "freestream_velocity": freestream_vector(alpha, beta),
            "gamma": 1.4,
            "freestream_mach_number": mach
        },
        "geometry": {
            "file": str(tri).replace("\\", "/"),
            "spanwise_axis": "+y",
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
            "body_file": str(out_vtk).replace("\\", "/"),
            "wake_file": str(out_wake).replace("\\", "/"),
            "control_point_file": str(out_cp).replace("\\", "/"),
            "report_file": str(out_report).replace("\\", "/")
        }
    }

    dst = project / "04_machline_input" / "json" / f"{case}.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(ml, ensure_ascii=False, indent=2), encoding="utf-8")
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

    print("\nЗапускаем MachLine как двигатель...")
    print(f"EXE : {mach}")
    print(f"JSON: {json_path}")
    print(f"CWD : {cwd}\n")

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
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=None,
            env=child_env
        )
        log_path.write_text(p.stdout or "", encoding="utf-8", errors="ignore")
        print(p.stdout)
        print(f"\nЛог сохранён: {log_path}")
        print(f"Код завершения: {p.returncode}")
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
    run_name = f"{imported.stem}_vspaero_{timestamp}"
    run_dir = project / "06_vspaero_results" / "runs" / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    working_model = run_dir / f"{imported.stem}.vsp3"
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
    shutil.copy2(polar_in_run, polar_output)

    manifest = {
        "schema": "repairmach.vspaero-run/1.0",
        "repairmach_version": settings.get("repairmach_version"),
        "source_vsp3": str(source.resolve()),
        "imported_vsp3": str(imported.resolve()),
        "working_vsp3": str(working_model.resolve()),
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
    source: Path,
    imported: Path,
    case: dict,
    beta_deg: float,
    ncpu: int,
    reference: dict,
    study_timestamp: str,
    tail_geometry_name: str | None,
    tail_incidence_deg: float,
    scenario_id: str,
    naming_policy: dict,
) -> dict:
    run_name = f"{imported.stem}_{case['name']}_{study_timestamp}"
    run_dir = project / "06_vspaero_results" / "runs" / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    working_model = run_dir / f"{imported.stem}.vsp3"
    shutil.copy2(imported, working_model)

    script = project / "06_vspaero_input" / "scripts" / f"{run_name}.vspscript"
    results_csv = project / "06_vspaero_results" / "csv" / f"{run_name}.csv"
    log_path = project / "06_vspaero_results" / "logs" / f"{run_name}.log"
    set_report_path = run_dir / "set_validation.json"
    manifest_path = run_dir / "run_manifest.json"

    manifest = {
        "schema": "repairmach.vspaero-run/1.0",
        "repairmach_version": settings.get("repairmach_version"),
        "scenario_id": scenario_id,
        "preset": case["name"],
        "source_vsp3": str(source.resolve()),
        "imported_vsp3": str(imported.resolve()),
        "working_vsp3": str(working_model.resolve()),
        "sets": {
            "fuselage_and_nacelles": {"user_set": 1, "api_index": user_set_to_api_index(1)},
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
        "outputs": {
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
            fuselage_user_set=1,
            wing_user_set=2,
            ncpu=ncpu,
            tail_geometry_name=tail_geometry_name,
            tail_incidence_deg=tail_incidence_deg,
            require_canonical_components=True,
        )
        print(
            f"\nЗапуск {case['name']}: M={case['mach_start']:.1f}…{case['mach_end']:.1f} "
            f"({case['mach_points']} точек), alpha=0…5° (6 точек)"
        )
        return_code = run_vspscript(executable, script, log_path, run_dir)
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
        set_report = parse_set_report(log_text)
        set_report["component_names"] = validate_component_names(
            set_report["geometries"], naming_policy
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

    polar_output = project / "06_vspaero_results" / "polars" / f"{run_name}.polar"
    shutil.copy2(polar_in_run, polar_output)
    manifest["outputs"]["polar"] = str(polar_output.resolve())
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
    source = Path(source_text) if source_text else Path()
    if not source_text or not source.is_file() or source.suffix.lower() != ".vsp3":
        print("Не найден корректный файл .vsp3.")
        return

    if scenario is None:
        print("\nСтандартный сценарий VSPAERO:")
        print("[1] Дозвук: M=0,0…0,8, шаг 0,1")
        print("[2] Сверхзвук: M=1,1…2,2, шаг 0,1")
        print("[3] Всё вместе: два последовательных диапазона без M=0,9–1,0")
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
            else [dict(item) for item in scenario["vspaero_cases"]]
        )
        defaults = settings.get("vspaero", {})
        ncpu = int(input(f"Потоков VSPAERO, Enter = {defaults.get('ncpu', 4)}: ").strip() or defaults.get("ncpu", 4))
        tail_policy = scenario.get("tail_incidence", "optional") if scenario else "optional"
        tail_answer = "y" if tail_policy == "required" else ""
        if tail_policy == "optional":
            tail_answer = input("Использовать заданный балансировочный угол ГО? [y/N]: ").strip().lower()
        if tail_answer in ("y", "yes", "д", "да"):
            tail_geometry_name = input(
                f"Точное имя геометрии ГО, Enter = {defaults.get('tail_geometry_name', 'GO')}: "
            ).strip() or defaults.get("tail_geometry_name", "GO")
            tail_incidence_deg = float(input("Абсолютный угол установки ГО, град: ").strip())
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

    imported = unique_import_path(project / "00_original" / "vsp", source.name)
    shutil.copy2(source, imported)
    study_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    reference = {"area": sref, "cref": cref, "bref": bref, "center": center}
    study_results = []
    scenario_id = scenario["id"] if scenario else "standard_vspaero_interactive"
    catalog = load_scenario_catalog(SCENARIOS_PATH)
    naming_policy = component_name_policy(catalog)
    print(f"\nVSP3 импортирован: {imported}")
    print("Set_1 = фюзеляж и мотогондолы; Set_2 = крыло и оперение.")
    for case in cases:
        result = _run_standard_vspaero_case(
            settings=settings,
            project=project,
            executable=executable,
            source=source,
            imported=imported,
            case=case,
            beta_deg=0.0,
            ncpu=ncpu,
            reference=reference,
            study_timestamp=study_timestamp,
            tail_geometry_name=tail_geometry_name,
            tail_incidence_deg=tail_incidence_deg,
            scenario_id=scenario_id,
            naming_policy=naming_policy,
        )
        study_results.append(result)
        if result["status"] != "completed":
            break

    study_manifest = {
        "schema": "repairmach.vspaero-standard-study/1.0",
        "repairmach_version": settings.get("repairmach_version"),
        "scenario_id": scenario_id,
        "scenario_title": scenario.get("title") if scenario else "Стандартный VSPAERO",
        "source_vsp3": str(source.resolve()),
        "imported_vsp3": str(imported.resolve()),
        "standard": {
            "mach_step": 0.1,
            "alpha_start_deg": 0.0,
            "alpha_end_deg": 5.0,
            "alpha_step_deg": 1.0,
            "excluded_transonic_interval": [0.9, 1.0],
            "horizontal_tail": {
                "mode": "fixed_incidence" if tail_geometry_name else "model_default",
                "geometry_name": tail_geometry_name,
                "incidence_deg": tail_incidence_deg if tail_geometry_name else None,
            },
        },
        "runs": study_results,
        "status": "completed" if len(study_results) == len(cases) and all(item["status"] == "completed" for item in study_results) else "incomplete",
    }
    study_path = project / "06_vspaero_results" / "runs" / f"{imported.stem}_standard_{study_timestamp}.json"
    save_report(study_path, study_manifest)
    print(f"\nСводный манифест: {study_path}")
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
    vsp3 = Path(vsp3_text)
    defaults = settings.get("parasite_drag", {})
    mach_text = input(f"Mach (только < 1), Enter = {defaults.get('mach', 0.8)}: ").strip()
    altitude_text = input(f"Высота, ft, Enter = {defaults.get('altitude_ft', 0.0)}: ").strip()
    sref_text = input(
        f"Опорная площадь в единицах VSP3, Enter = {defaults.get('reference_area', settings['defaults']['area'])}: "
    ).strip()
    set_text = input(f"Номер OpenVSP Set, Enter = {defaults.get('geometry_set', 0)}: ").strip()
    try:
        mach = float(mach_text or defaults.get("mach", 0.8))
        altitude = float(altitude_text or defaults.get("altitude_ft", 0.0))
        sref = float(sref_text or defaults.get("reference_area", settings["defaults"]["area"]))
        geometry_set = int(set_text or defaults.get("geometry_set", 0))
    except ValueError:
        print("Некорректное числовое значение.")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = f"{vsp3.stem}_M{mach:g}_{timestamp}"
    script = project / "09_parasite_drag" / "scripts" / f"{stem}.vspscript"
    results = project / "09_parasite_drag" / "results" / f"{stem}.csv"
    log = project / "09_parasite_drag" / "logs" / f"{stem}.log"
    try:
        generate_parasite_drag_script(
            script, vsp3, results, mach, altitude, sref, geometry_set
        )
        code = run_vspscript(executable, script, log, script.parent)
        parsed = parse_openvsp_results_csv(results)
    except Exception as exc:
        print(f"Parasite Drag не выполнен: {exc}")
        return

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
    print(f"Код      : {code}")


def build_hybrid_report_workflow() -> None:
    project = default_project_path()
    if not project.exists():
        print("Проект по умолчанию не найден.")
        return
    machline_text = input("Полный путь к отчёту MachLine *_report.json: ").strip().strip('"')
    polar_text = input("Полный путь к VSPAERO *.polar: ").strip().strip('"')
    parasite_text = input("Путь к OpenVSP Parasite Drag CSV или Enter, если его нет: ").strip().strip('"')
    if not machline_text or not polar_text:
        print("Нужны отчёт MachLine и POLAR VSPAERO.")
        return
    induced_answer = input("Добавить VSPAERO CDi? [Y/n]: ").strip().lower()
    base_text = input("CD базового сопротивления, Enter = 0: ").strip()
    external_text = input("CD внешних элементов, Enter = 0: ").strip()
    try:
        report = build_hybrid_report(
            Path(machline_text),
            Path(polar_text),
            Path(parasite_text) if parasite_text else None,
            include_vspaero_induced_drag=induced_answer in ("", "y", "yes", "д", "да"),
            base_drag=float(base_text or 0.0),
            external_drag=float(external_text or 0.0),
        )
    except Exception as exc:
        print(f"Гибридный отчёт не создан: {exc}")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = project / "08_hybrid_results" / f"hybrid_{timestamp}.json"
    save_report(output, report)
    terms = report["drag_build_up"]
    print("\nГибридный результат")
    print("-" * 58)
    print(f"CL (VSPAERO)        : {report['coefficients']['CL']:.9g}")
    print(f"CD MachLine         : {terms['machline_pressure_wave_candidate']:.9g}")
    print(f"CDi VSPAERO         : {terms['vspaero_induced']:.9g}")
    print(f"CD0 OpenVSP         : {terms['openvsp_viscous_form_factor']:.9g}")
    print(f"CD base             : {terms['base']:.9g}")
    print(f"CD external         : {terms['external']:.9g}")
    print(f"CD total            : {report['coefficients']['CD']:.9g}")
    print("Калибровочная константа: не используется")
    for warning in report["warnings"]:
        print(f"[!] {warning}")
    print(f"Отчёт: {output}")


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
        print("[11] Собрать гибридный отчёт")
        print("[12] Открыть папку проекта")
        print("[13] Запустить готовый .vspscript (расширенный режим)")
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
