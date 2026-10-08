"""Prepare then deploy only verified feature files, retaining originals and unrelated edits."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent / 'repository'
WORKSPACE = ROOT.parent.parent
DESTINATIONS = [Path('C:/RepairMach-Portable'), WORKSPACE / 'github_publish/RepairMach-Portable']
FILES = ['app/pressure_export.py', 'app/visualization_output.py', 'app/geometry_certification.py',
         'app/repairmach_beta.py', 'docs/PARAVIEW_PRESSURE.md', 'tests/test_pressure_export.py',
         'tests/test_visualization_output.py', 'tests/test_repairmach_certified_machline.py']

def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def normalized(data):
    return data.replace(b'\r\n', b'\n')

def write(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

parser = argparse.ArgumentParser()
parser.add_argument('--prepare', action='store_true')
args = parser.parse_args()
plan_path = ROOT / 'deployment_plan_release.json'
if args.prepare:
    assert not plan_path.exists()
    plan = {'files': [], 'solver_runs': 0, 'scope': 'visualization only; no policy, model or backend changes'}
    for name in FILES:
        baseline = subprocess.run(['git', 'show', f'HEAD:{name}'], cwd=REPO, capture_output=True)
        entry = {'file': name, 'new_sha256': sha(REPO / name), 'destinations': []}
        for destination in DESTINATIONS:
            target = destination / name
            if baseline.returncode == 0:
                assert target.is_file() and normalized(target.read_bytes()) == normalized(baseline.stdout), f'Unrelated changes: {target}'
            else:
                assert not target.exists(), f'New-file collision: {target}'
            entry['destinations'].append({'path': str(target), 'old_sha256': sha(target) if target.exists() else None})
        plan['files'].append(entry)
    write(plan_path, plan)
    print('Deployment plan verified against baseline; no files changed')
    sys.exit()

assert not (ROOT / 'deployment.json').exists()
plan = json.loads(plan_path.read_text(encoding='utf-8'))
processes = subprocess.run(['powershell.exe', '-NoProfile', '-Command',
    "Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(vspaero|vspscript|machline)\\.exe$' } | Select-Object Name,ProcessId | ConvertTo-Json"],
    capture_output=True, text=True, check=True)
assert not processes.stdout.strip(), 'Solver processes active; deployment blocked'
for entry in plan['files']:
    assert sha(REPO / entry['file']) == entry['new_sha256'], 'Stage changed since verification'
    for record in entry['destinations']:
        target = Path(record['path'])
        assert (sha(target) if target.exists() else None) == record['old_sha256'], f'Destination changed: {target}'

stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
backups = [DESTINATIONS[0] / 'backups' / f'visualization_output_{stamp}', ROOT / f'checkout_backup_{stamp}']
for backup in backups:
    backup.mkdir(parents=True, exist_ok=False)
for entry in plan['files']:
    for index, record in enumerate(entry['destinations']):
        target = Path(record['path'])
        if target.exists():
            backup = backups[index] / entry['file']
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
            assert sha(backup) == record['old_sha256']
# All originals secured before any installed/checkout write.
for entry in plan['files']:
    for record in entry['destinations']:
        target = Path(record['path'])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / entry['file'], target)
        assert sha(target) == entry['new_sha256']
write(ROOT / 'deployment.json', {'time': stamp, 'backups': [str(p) for p in backups], 'plan': plan,
                               'no_active_solvers': True, 'production_models_configs_backends_modified': False})
print('Scoped visualization feature deployed; backups:', *backups)
