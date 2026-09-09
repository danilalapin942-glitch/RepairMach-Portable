#!/usr/bin/env python3
"""Local OpenVSP/vspscript integration used by RepairMach 9."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def find_openvsp_dir(configured: str | None = None) -> Path | None:
    candidates = []
    if configured:
        candidates.append(Path(configured))
    if os.environ.get("OPENVSP_HOME"):
        candidates.append(Path(os.environ["OPENVSP_HOME"]))
    discovered = shutil.which("vspscript.exe")
    if discovered:
        candidates.append(Path(discovered).resolve().parent)
    try:
        for outer in Path("C:/").glob("OpenVSP-*"):
            candidates.append(outer)
            candidates.extend(path for path in outer.glob("OpenVSP-*") if path.is_dir())
    except OSError:
        pass
    for candidate in candidates:
        if (candidate / "vspscript.exe").is_file():
            return candidate.resolve()
    return None


def tool_paths(openvsp_dir: Path | None) -> dict[str, Path | None]:
    if openvsp_dir is None:
        return {name: None for name in ("vsp", "vspscript", "vspaero")}
    return {
        "vsp": openvsp_dir / "vsp.exe",
        "vspscript": openvsp_dir / "vspscript.exe",
        "vspaero": openvsp_dir / "vspaero.exe",
    }


def run_vspscript(
    executable: Path,
    script: Path,
    log_path: Path,
    working_dir: Path | None = None,
    timeout_seconds: float | None = None,
) -> int:
    executable = executable.resolve()
    script = script.resolve()
    log_path = log_path.resolve()
    if working_dir is not None:
        working_dir = working_dir.resolve()
    if not executable.is_file():
        raise FileNotFoundError(f"Не найден vspscript.exe: {executable}")
    if not script.is_file():
        raise FileNotFoundError(f"Не найден сценарий OpenVSP: {script}")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if timeout_seconds is not None and timeout_seconds <= 0.0:
        raise ValueError("Лимит времени vspscript должен быть положительным")
    baseline_vspaero = _windows_process_ids("vspaero.exe")
    process = subprocess.Popen(
        [str(executable), "-script", str(script)],
        cwd=str(working_dir or script.parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    try:
        output, _ = process.communicate(timeout=timeout_seconds)
        return_code = process.returncode
    except subprocess.TimeoutExpired as exc:
        partial = exc.output or ""
        _terminate_exact_process(process.pid)
        for pid in sorted(_windows_process_ids("vspaero.exe") - baseline_vspaero):
            _terminate_exact_process(pid)
        try:
            output, _ = process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            output, _ = process.communicate()
        output = f"{partial}{output or ''}\nREPAIRMACH_VSPSCRIPT_TIMEOUT={timeout_seconds:g}\n"
        return_code = 124
    log_path.write_text(output or "", encoding="utf-8")
    return int(return_code)


def _windows_process_ids(image_name: str) -> set[int]:
    if os.name != "nt":
        return set()
    process_name = Path(image_name).stem
    if not process_name or any(
        char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
        for char in process_name
    ):
        raise ValueError(f"Недопустимое имя процесса: {image_name}")
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            (
                f"Get-Process -Name '{process_name}' -ErrorAction SilentlyContinue "
                "| ForEach-Object { $_.Id }"
            ),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        errors="replace",
        check=False,
    )
    result: set[int] = set()
    for line in (completed.stdout or "").splitlines():
        try:
            result.add(int(line.strip()))
        except ValueError:
            continue
    return result


def _terminate_exact_process(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                f"Stop-Process -Id {int(pid)} -Force -ErrorAction SilentlyContinue",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    try:
        os.kill(int(pid), 9)
    except ProcessLookupError:
        pass


def _vsp_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace('"', '\\"')


def generate_parasite_drag_script(
    script_path: Path,
    vsp3_path: Path,
    results_path: Path,
    mach: float,
    altitude_ft: float,
    reference_area: float,
    geometry_set: int = 0,
) -> None:
    if not 0.0 < mach < 1.0:
        raise ValueError(
            "OpenVSP Parasite Drag в RepairMach 9 разрешён только для 0 < Mach < 1"
        )
    if reference_area <= 0.0:
        raise ValueError("Опорная площадь должна быть положительной")
    if not vsp3_path.is_file():
        raise FileNotFoundError(f"Не найден VSP3: {vsp3_path}")

    script_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.parent.mkdir(parents=True, exist_ok=True)
    analysis_output = results_path.with_suffix("")
    text = f'''void main()
{{
    ClearVSPModel();
    ReadVSPFile( "{_vsp_path(vsp3_path)}" );
    Update();
    SetAnalysisInputDefaults( "ParasiteDrag" );

    array<int> velocity_unit = GetIntAnalysisInput( "ParasiteDrag", "VelocityUnit" );
    velocity_unit[0] = V_UNIT_MACH;
    SetIntAnalysisInput( "ParasiteDrag", "VelocityUnit", velocity_unit );

    array<double> speed = GetDoubleAnalysisInput( "ParasiteDrag", "Vinf" );
    speed[0] = {mach:.17g};
    SetDoubleAnalysisInput( "ParasiteDrag", "Vinf", speed );

    array<double> altitude = GetDoubleAnalysisInput( "ParasiteDrag", "Altitude" );
    altitude[0] = {altitude_ft:.17g};
    SetDoubleAnalysisInput( "ParasiteDrag", "Altitude", altitude );

    array<int> ref_flag = GetIntAnalysisInput( "ParasiteDrag", "RefFlag" );
    ref_flag[0] = 0;
    SetIntAnalysisInput( "ParasiteDrag", "RefFlag", ref_flag );

    array<double> sref = GetDoubleAnalysisInput( "ParasiteDrag", "Sref" );
    sref[0] = {reference_area:.17g};
    SetDoubleAnalysisInput( "ParasiteDrag", "Sref", sref );

    array<int> geom_set = GetIntAnalysisInput( "ParasiteDrag", "GeomSet" );
    geom_set[0] = {int(geometry_set)};
    SetIntAnalysisInput( "ParasiteDrag", "GeomSet", geom_set );

    array<int> recompute = GetIntAnalysisInput( "ParasiteDrag", "RecomputeGeom" );
    recompute[0] = 1;
    SetIntAnalysisInput( "ParasiteDrag", "RecomputeGeom", recompute );

    array<string> output_name = GetStringAnalysisInput( "ParasiteDrag", "FileName" );
    output_name[0] = "{_vsp_path(analysis_output)}";
    SetStringAnalysisInput( "ParasiteDrag", "FileName", output_name );

    string result_id = ExecAnalysis( "ParasiteDrag" );
    WriteResultsCSVFile( result_id, "{_vsp_path(results_path)}" );

    while ( GetNumTotalErrors() > 0 )
    {{
        ErrorObj err = PopLastError();
        Print( err.GetErrorString() );
    }}
}}
'''
    script_path.write_text(text, encoding="utf-8")
