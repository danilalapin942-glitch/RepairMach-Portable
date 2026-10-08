"""Read-only visualization packages and opt-in launchers; never launch a solver."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import shutil


def _sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def validate_machline_vtk(path):
    """Validate complete native legacy ASCII POLYDATA, without modifying its fields."""
    lines = Path(path).read_text(encoding="utf-8", errors="strict").splitlines()
    if len(lines) < 4 or not lines[0].startswith("# vtk DataFile Version") or lines[2].strip() != "ASCII":
        raise ValueError("Only native legacy ASCII MachLine VTK is supported")
    tokens, pos = " ".join(lines[3:]).split(), 0
    def take():
        nonlocal pos
        if pos >= len(tokens):
            raise ValueError("Truncated MachLine VTK")
        value = tokens[pos]
        pos += 1
        return value
    def expect(value):
        if take() != value:
            raise ValueError(f"MachLine VTK expected {value}")
    def count():
        value = int(take())
        if value <= 0 or value > 100_000_000:
            raise ValueError("Invalid MachLine VTK count")
        return value
    def numbers(n):
        for _ in range(n):
            if not math.isfinite(float(take())):
                raise ValueError("Non-finite MachLine VTK field or coordinate")
    expect("DATASET")
    expect("POLYDATA")
    expect("POINTS")
    nodes = count()
    take()  # numeric type
    numbers(3 * nodes)
    expect("POLYGONS")
    cells, total = count(), count()
    used = 0
    for _ in range(cells):
        size = count()
        if size < 3:
            raise ValueError("Invalid MachLine polygon")
        used += 1 + size
        for _ in range(size):
            if not 0 <= int(take()) < nodes:
                raise ValueError("MachLine polygon index out of range")
    if used != total:
        raise ValueError("MachLine polygon connectivity count mismatch")
    association, length, pressure = None, 0, []
    while pos < len(tokens):
        tag = take()
        if tag in ("CELL_DATA", "POINT_DATA"):
            association, length = tag, count()
            if length != (cells if tag == "CELL_DATA" else nodes):
                raise ValueError("MachLine VTK field count mismatch")
        elif tag in ("SCALARS", "VECTORS", "NORMALS") and association:
            name = take()
            take()  # numeric type
            components = 3
            if tag == "SCALARS":
                components = count() if tokens[pos:pos + 1] != ["LOOKUP_TABLE"] else 1
                expect("LOOKUP_TABLE")
                take()
            numbers(components * length)
            if tag == "SCALARS" and association == "CELL_DATA" and ("cp" in name.lower() or "c_p" in name.lower()):
                pressure.append(name)
        else:
            raise ValueError(f"Unsupported MachLine VTK block: {tag}")
    if not pressure:
        raise ValueError("MachLine VTK contains no cell pressure scalar")
    return {"nodes": nodes, "cells": cells, "pressure_fields": pressure}


# Data paths and executable hints are JSON, never interpolated into shell code.
# The viewer hint is the sibling of the actual vspscript used for this run.
LAUNCHER = r'''param([string]$Executable)
$ErrorActionPreference = 'Stop'
$config = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'open.json') -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($entry in $config.files) {
    $file = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot $entry.file))
    if (!(Test-Path -LiteralPath $file -PathType Leaf) -or
        (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.sha256) {
        throw "Visualization file missing or changed: $file"
    }
}
if (!$Executable) { $Executable = $config.executable_hint }
if (!$Executable -and $config.kind -eq 'paraview') {
    $found = Get-Command paraview.exe -ErrorAction SilentlyContinue
    if ($found) { $Executable = $found.Source }
    elseif ($env:ProgramFiles) {
        $found = Get-ChildItem -LiteralPath $env:ProgramFiles -Directory -Filter 'ParaView*' |
            Sort-Object Name -Descending | ForEach-Object { Join-Path $_.FullName 'bin\paraview.exe' } |
            Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
        $Executable = $found
    }
}
if (!$Executable -or !(Test-Path -LiteralPath $Executable -PathType Leaf)) {
    throw 'Application not found. Run open.ps1 -Executable "full path to executable".'
}
Write-Host $config.notice
if ($config.kind -eq 'viewer') {
    & $Executable (Join-Path $PSScriptRoot 'model.adb')
} else {
    & $Executable '--script' (Join-Path $PSScriptRoot 'scene.py')
}
'''


def _launchers(folder, *, kind, files, executable=None, notice=""):
    folder = Path(folder)
    config = {"kind": kind, "files": files, "executable_hint": str(Path(executable).resolve()) if executable else None,
              "notice": notice}
    if executable and Path(executable).is_file():
        config["executable_sha256"] = _sha(executable)
    _write_json(folder / "open.json", config)
    # UTF-8 BOM for Windows PowerShell 5.1's script encoding.
    (folder / "open.ps1").write_text(LAUNCHER, encoding="utf-8-sig")
    name = "Open_Viewer.cmd" if kind == "viewer" else "Open_ParaView.cmd"
    (folder / name).write_text(
        '@echo off\r\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0open.ps1"\r\n'
        'if errorlevel 1 pause\r\n', encoding="ascii")
    return str((folder / name).resolve())


def prepare_paraview_open(folder, data_file, *, accepted=False, condition=None, field=None):
    """Create a relocatable scene with native cell fields and no point averaging."""
    folder, data_file = Path(folder), Path(data_file)
    if data_file.parent.resolve() != folder.resolve():
        raise ValueError("ParaView dataset must be inside its point folder")
    notice = ("Solver-accepted; physical qualification is separate." if accepted else
              "DIAGNOSTIC / NOT ACCEPTED. Export is not proof of convergence.")
    shown = {k: v for k, v in (condition or {}).items() if k in {"mach", "alpha_deg", "beta_deg"}}
    label = ("SOLVER-ACCEPTED / NOT PHYSICALLY QUALIFIED" if accepted else "DIAGNOSTIC / NOT ACCEPTED")
    label += ("\n" + "  ".join(f"{k}={float(v):.6g}" for k, v in shown.items())) if shown else "\nRun details: point.json"
    scene = (
        "from pathlib import Path\nimport hashlib\nimport json\n"
        "from paraview.simple import *\n"
        "root = Path(__file__).resolve().parent\n"
        "config = json.loads((root / 'open.json').read_text(encoding='utf-8'))\n"
        "for item in config['files']:\n"
        "    if item['file'] == 'scene.py':\n        continue\n"
        "    path = root / item['file']\n"
        "    if hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:\n"
        "        raise RuntimeError('Visualization dataset changed')\n"
        f"source = OpenDataFile(str(root / {data_file.name!r}))\n"
        "source.UpdatePipeline()\nview = GetActiveViewOrCreate('RenderView')\n"
        "display = Show(source, view)\ndisplay.Representation = 'Surface'\n"
        "names = list(source.CellData.keys())\n"
        f"preferred = {field!r}\n"
        "chosen = preferred if preferred in names else next((n for n in names if 'cp' in n.lower() or 'c_p' in n.lower()), None)\n"
        "if chosen:\n"
        "    ColorBy(display, ('CELLS', chosen))\n"
        "    display.RescaleTransferFunctionToDataRange(True, False)\n"
        "    display.SetScalarBarVisibility(view, True)\n"
        "    if hasattr(display, 'InterpolateScalarsBeforeMapping'):\n"
        "        display.InterpolateScalarsBeforeMapping = 0\n"
        f"title = Text()\ntitle.Text = {label!r}\n"
        "title_display = Show(title, view)\ntitle_display.FontSize = 12\n"
        "view.ResetCamera()\nRender(view)\n"
    )
    (folder / "scene.py").write_text(scene, encoding="utf-8")
    files = [{"file": p.name, "sha256": _sha(p)} for p in (data_file, folder / "scene.py")]
    return _launchers(folder, kind="paraview", files=files, notice=notice)


def prepare_viewer(adb_path, folder, cases, *, accepted=False, executable=None):
    """Copy exact native ADB + mandatory native case list, validated in file order.

    OpenVSP_3.51.0 Viewer/glviewer.C LoadSolutionCaseList requires .adb.cases;
    vspaero_viewer.C accepts the ADB path as its last command-line argument.
    Missing/inconsistent case lists are never synthesized or guessed.
    """
    adb_path, folder = Path(adb_path), Path(folder)
    result = {"status": "unavailable", "solver_accepted": bool(accepted), "qualification": False,
              "errors": [], "files": []}
    try:
        sidecar = Path(str(adb_path) + ".cases")
        rows = sidecar.read_text(encoding="utf-8").splitlines()
        if len(rows) != len(cases):
            raise ValueError("Native Viewer case count differs from complete ADB solutions")
        for row, case in zip(rows, cases):
            values = [float(v) for v in row.split()[:3]]
            expected = [case["condition"][k] for k in ("mach", "alpha_deg", "beta_deg")]
            if len(values) != 3 or any(not math.isfinite(v) or abs(v - e) > 1e-5 for v, e in zip(values, expected)):
                raise ValueError("Native Viewer case order/conditions differ from ADB")
        folder.mkdir(exist_ok=False)
        sources = [(adb_path, "model.adb"), (sidecar, "model.adb.cases")]
        # Exact same-run context, not used to replace missing pressure solutions.
        for suffix in (".vsp3", ".vspgeom", ".vkey", ".vspaero", ".polar", ".history"):
            source = adb_path.with_suffix(suffix)
            if source.is_file():
                sources.append((source, "model" + suffix))
        for source, name in sources:
            target = folder / name
            before = _sha(source)
            shutil.copy2(source, target)
            if _sha(target) != before or _sha(source) != before:
                raise ValueError("Native Viewer source changed during packaging")
            result["files"].append({"file": name, "sha256": before, "source": str(source.resolve())})
        result["launcher"] = _launchers(folder, kind="viewer", files=result["files"], executable=executable,
            notice="Native Cp/DeltaCp. Solver acceptance is not physical qualification." if accepted else
                   "DIAGNOSTIC / NOT ACCEPTED. Inspect native Cp/DeltaCp; no physical qualification.")
        result["status"] = "exported"
    except (OSError, ValueError) as exc:
        result["errors"].append(str(exc))
    return result
