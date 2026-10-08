"""Verify exporters on synthetic and preserved native output, without a solver."""
import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent / 'repository'
sys.path[:0] = [str(REPO / 'app'), str(REPO / 'tests')]
from pressure_export import export_vspaero_pressure, export_machline_pressure
from test_pressure_export import adb_fixture, vtk_fixture

def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

artifact = ROOT / 'outputs_release'
artifact.mkdir(exist_ok=False)
adb = artifact / 'fixture.adb'
adb.write_bytes(adb_fixture())
Path(str(adb) + '.cases').write_text('0.4 0 0 fixture\n0.4 5 0 fixture\n')
source_vtk = artifact / 'body.vtk'
source_vtk.write_text(vtk_fixture())
viewer = Path('C:/OpenVSP-3.51.0-win64-Python3.13/OpenVSP-3.51.0-win64/vspviewer.exe')
synthetic = export_vspaero_pressure(adb, artifact / 'synthetic_vspaero', viewer_executable=viewer)
assert synthetic['status'] == 'exported' and synthetic['viewer_export']['status'] == 'exported'
vtk = export_machline_pressure(source_vtk, artifact / 'synthetic_machline', condition={'mach': 1.2}, quality={'valid': False})
assert vtk['status'] == 'exported'
native_adb = Path('C:/RepairMach-Portable/projects/Plane_Naca/06_vspaero_results/runs/PlaneNaca_v12_wing_only_vlm_vspaero_20260907_103723/model.adb')
native_vtk = Path('C:/RepairMach-Portable/projects/Plane_Naca/11_geometry_certification/20260911_234948_Plane_Naca_PlaneNaca_VO_merge_a3973da3/machline/solver_probes/M2p2_A5_body.vtk')
preserved = {str(p): sha(p) for p in (native_adb, Path(str(native_adb) + '.cases'), native_vtk)}
native = export_vspaero_pressure(native_adb, artifact / 'native_vspaero', viewer_executable=viewer)
assert native['status'] == 'exported' and native['viewer_export']['status'] == 'exported', native
machline = export_machline_pressure(native_vtk, artifact / 'native_machline',
    condition={'mach': 2.2, 'alpha_deg': 5, 'beta_deg': 0}, quality={'valid': False, 'purpose': 'export-only verification'})
assert machline['status'] == 'exported', machline
assert all(sha(p) == h for p, h in preserved.items())
(ROOT / 'export_verification_release.json').write_text(json.dumps({
    'aerodynamic_runs': 0, 'native_sources_unchanged': preserved,
    'synthetic': synthetic, 'synthetic_machline': vtk,
    'native_vspaero': native, 'native_machline': machline,
}, ensure_ascii=False, indent=2), encoding='utf-8')
print('Synthetic + preserved native ADB/VTK exporters verified; sources unchanged')
