#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from datetime import datetime


def app_root() -> Path:
    return Path(__file__).resolve().parents[1]


ROOT = app_root()
SETTINGS_PATH = ROOT / "config" / "repairmach_settings.json"


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
    "07_parametric_studies",
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
    print("\n" + "=" * 58)
    print("RepairMach Portable Beta")
    print("MachLine как двигатель, RepairMach как оболочка")
    print("=" * 58)
    print(f"ROOT: {ROOT}")
    print("=" * 58 + "\n")


def check_environment() -> None:
    settings = load_settings()
    py = find_python(settings)
    mach = find_machline(settings)
    mach_wd = machline_working_dir(settings)

    print("\nПроверка среды")
    print("-" * 58)
    print(f"RepairMach root : {ROOT}")
    print(f"Python          : {py}")
    print(f"Python exists   : {py.exists() if py else False}")
    print(f"MachLine exe    : {mach}")
    print(f"MachLine exists : {mach.exists()}")
    print(f"MachLine cwd    : {mach_wd}")
    print(f"MachLine cwd ok : {mach_wd.exists()}")

    if not mach.exists():
        print("\n[!] MachLine не найден.")
        print("    Скопируйте рабочий machline.exe и нужные DLL/файлы в:")
        print(f"    {ROOT / 'engines' / 'MachLine'}")
    else:
        print("\nOK: MachLine найден.")

    print("-" * 58)


def create_project() -> None:
    settings = load_settings()
    name = input("Название проекта, например MIG_29: ").strip()
    if not name:
        print("Отменено: имя проекта пустое.")
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
            "default_reference": {
                "area": settings["defaults"]["area"],
                "longitudinal_length": settings["defaults"]["longitudinal_length"],
                "lateral_length": settings["defaults"]["lateral_length"],
                "center": settings["defaults"]["center"]
            },
            "default_solver": {
                "matrix_solver": settings["defaults"]["solver"]
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

    ml = {
        "flow": {
            "freestream_velocity": [100.0, 0.0, 0.0],
            "gamma": 1.4,
            "freestream_mach_number": mach
        },
        "geometry": {
            "file": str(tri).replace("\\", "/"),
            "spanwise_axis": "+y",
            "wake_model": {
                "trefftz_distance": 20.0
            },
            "reference": ref
        },
        "solver": {
            "matrix_solver": settings["defaults"]["solver"]
        },
        "post_processing": {
            "pressure_rules": {
                "isentropic": True
            }
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


def open_project_folder() -> None:
    project = default_project_path()
    if project.exists():
        os.startfile(project)
    else:
        print("Проект по умолчанию не найден.")


def menu() -> None:
    while True:
        print_header()
        settings = load_settings()
        print(f"Проект по умолчанию: {settings['paths']['default_project']}")
        print("[1] Проверить среду")
        print("[2] Создать проект")
        print("[3] Выбрать проект")
        print("[4] Импортировать TRI")
        print("[5] Создать MachLine JSON")
        print("[6] Запустить MachLine")
        print("[7] Открыть папку проекта")
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
            build_machline_json()
        elif choice == "6":
            run_machline()
        elif choice == "7":
            open_project_folder()
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
