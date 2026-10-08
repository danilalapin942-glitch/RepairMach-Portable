"""Check immutable program/model/config/backend sources before/after scoped deployment."""
import hashlib
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parent
inventory = json.loads((root.parent / 'inventory.json').read_text(encoding='utf-8'))
changed = set(json.loads((root / 'deployment_plan_release.json').read_text(encoding='utf-8'))['files'][i]['file'] for i in range(8))
protected, failures = [], []
for group in inventory:
    source = Path(group['source_root'])
    for item in group['files']:
        name = item['path']
        selected = ((group['name'] == 'installed' and name not in changed and
                     (name.startswith(('app/', 'config/', 'engines/')) or name.endswith('project_config.json')))
                    or (group['name'] == 'uav_models_drawings' and name.lower().endswith('.vsp3')))
        if not selected:
            continue
        path = source / name
        with path.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        protected.append({'path': str(path), 'sha256': actual})
        if actual != item['sha256']:
            failures.append(str(path))
# Explicit masters/release backend not necessarily under the selected inventory root label.
for path, expected in {
    'C:/Multifunctional UAV/Boeing MQ-28/MQ28_body_B.vsp3': '7b88f468d35c67fb04fcab369b488b245831bf8e594d540dc325d8ea2363dd8a',
    'C:/Multifunctional UAV/MQ-20 Avenger/MQ20_for MachLine.vsp3': '5710974615718259376ea2aca54fd14efa1837069e1b031d1461485dac955fbd',
    'C:/OpenVSP-3.51.0-win64-Python3.13/OpenVSP-3.51.0-win64/vspaero.exe': '324756878fa0af84bb0d9157ef0896139b636971360a67b3d5163e641988c1e7',
    'C:/OpenVSP-3.51.0-win64-Python3.13/OpenVSP-3.51.0-win64/vspscript.exe': '79b238c97814e0dd74a6b49839710c8b17406b46d0616f5bd2bf1692a4264088',
}.items():
    with Path(path).open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    protected.append({'path': path, 'sha256': actual})
    if actual != expected:
        failures.append(path)
receipt = root / f'preservation_{sys.argv[1]}.json'
assert not receipt.exists()
receipt.write_text(json.dumps({'checked': protected, 'failures': failures, 'unchanged': not failures}, indent=2), encoding='utf-8')
assert not failures, failures
print(f'{len(protected)} protected files unchanged')
